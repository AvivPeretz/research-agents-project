import os
import argparse

# Import the centralized configuration
from config import Config
from agents.literature_research_agent import LiteratureResearchAgent
from agents.progress_tracking_agent import ProgressTrackingAgent
from agents.research_enhancement_agent import ResearchEnhancementAgent
from ingestion.data_ingestion_agent import DataIngestionAgent
from utils.garbage_collector import GarbageCollector
from agents.notification_agent import NotificationAgent
from utils.database_manager import DatabaseManager

def get_all_active_projects() -> list:
    """
    Utility function to get a list of all locally downloaded projects.
    Acts as the 'Source of Truth' for the Literature Research Agent.
    """
    base_dir = Config.OVERLEAF_DIR
    if not os.path.exists(base_dir):
        return []
    return [name for name in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, name))]


def run_agent_safely(agent, *args, **kwargs) -> bool:
    """
    Helper to run an agent and catch/log exceptions so the main loop continues.
    Returns True on success, False on failure.
    """
    try:
        print(f"--- Running agent: {agent.__class__.__name__} ---")
        agent.run(*args, **kwargs)
        print(f"--- Agent {agent.__class__.__name__} finished successfully ---")
        return True
    except Exception as e:
        print(f"!!! Agent {agent.__class__.__name__} failed: {e}")
        return False

def main():
    """
    The main Scheduler and Router of the application.
    Triggers agents based on available data and CLI arguments.
    """
    # ==========================================
    # 1. CLI Arguments Parsing (argparse)
    # ==========================================
    parser = argparse.ArgumentParser(description="Academic Research Multi-Agent System")
    
    parser.add_argument(
        "--agent", 
        type=str, 
        choices=['all', 'ingestion', 'literature', 'progress', 'enhancement', 'gc'], 
        default='all', 
        help="Specify which agent to run (default: 'all')"
    )
    
    parser.add_argument(
        "--project", 
        type=str, 
        default='all', 
        help="Specify a single project name to process (default: 'all' projects)"
    )
    
    args = parser.parse_args()
    print(f"\n🚀 Starting ResearchAgents Pipeline | Target Agent: [{args.agent.upper()}] | Target Project: [{args.project}]\n")

    # ==========================================
    # 2. Setup & Validation
    # ==========================================
    Config.validate()

    print("--- Initializing Shared Services (Notification Agent) ---")
    db = DatabaseManager()
    notifier = NotificationAgent(db=db)
    
    db.migrate_from_json(str(Config.RESEARCHERS_MAP_PATH))
    print("--- DB Migration Completed / Verified ---\n")

    # Determine scope based on CLI args
    all_projects = get_all_active_projects()
    target_projects = all_projects if args.project == 'all' else [args.project]
    updated_projects = []

    # ==========================================
    # 3. Agent Execution Logic
    # ==========================================

    # --- 0. Data Ingestion (Delta Sync) ---
    if args.agent in ['all', 'ingestion']:
        print("--- 0. Running Data Ingestion (Delta Sync) ---")
        scraper = DataIngestionAgent(db=db, notifier=notifier)
        updated_projects = scraper.sync_all_projects()
        
        # If a specific project was requested, filter the updated list
        if args.project != 'all':
            updated_projects = [p for p in updated_projects if p == args.project]

    # Validate that the targeted projects actually exist before giving them to AI agents
    valid_targets = [p for p in target_projects if p in all_projects]
    if not valid_targets and args.agent not in ['gc', 'ingestion']:
        print(f"--- ⚠️ No valid projects found matching '{args.project}' in overleaf_projects/. Exiting. ---")
        return

    # --- 1. Literature Research Agent ---
    if args.agent in ['all', 'literature']:
        if valid_targets:
            print(f"\n--- 1. Running Literature Research Agent for: {valid_targets} ---")
            lit_agent = LiteratureResearchAgent(active_projects=valid_targets, notifier=notifier, db=db)
            run_agent_safely(lit_agent)
        else:
            print("\n--- No projects available for Literature Research. ---")
    
    # --- 2. Progress Tracking Agent ---
    if args.agent in ['all', 'progress']:
        # If running explicitly via CLI, override the "must be updated" rule to allow force-testing
        projects_to_track = valid_targets if args.agent == 'progress' else updated_projects
        
        if projects_to_track:
            print(f"\n--- 2. Running Progress Tracking Agent for: {projects_to_track} 🚀 ---")
            prog_agent = ProgressTrackingAgent(overleaf_projects=projects_to_track, notifier=notifier, db=db)
            run_agent_safely(prog_agent)
        else:
            print("\n--- No Overleaf projects were updated. Skipping Progress Tracking Agent. 😴 ---")
    
    # --- 3. Research Enhancement Agent ---
    if args.agent in ['all', 'enhancement']:
        if valid_targets:
            print(f"\n--- 3. Running Research Enhancement Agent for: {valid_targets} 🚀 ---")
            enhancement_agent = ResearchEnhancementAgent(overleaf_projects=valid_targets, notifier=notifier, db=db)
            run_agent_safely(enhancement_agent)
        else:
            print("\n--- No projects available for Research Enhancement. ---")
    
    # --- 4. System Cleanup (Garbage Collector) ---
    if args.agent in ['all', 'gc']:
        print(f"\n--- 4. Running System Cleanup (Retention Policy: {Config.GARBAGE_COLLECTION_TTL_DAYS} days) 🧹 ---")
        gc = GarbageCollector(retention_days=Config.GARBAGE_COLLECTION_TTL_DAYS)
        run_agent_safely(gc)

    print("\n--- ✅ All Executions Finished Successfully ---")

if __name__ == "__main__":
    main()