import os
import logging
import argparse
import time

# Import the centralized configuration
from config import Config
from agents.literature_research_agent import LiteratureResearchAgent
from agents.progress_tracking_agent import ProgressTrackingAgent
from agents.research_enhancement_agent import ResearchEnhancementAgent
from agents.supervisor_status_agent import SupervisorStatusAgent
from ingestion.data_ingestion_agent import DataIngestionAgent
from utils.garbage_collector import GarbageCollector
from agents.notification_agent import NotificationAgent
from agents.supervisor_status_agent import SupervisorStatusAgent
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


_pipeline_logger = logging.getLogger("Pipeline")

def run_agent_safely(agent, dry_run: bool = False, notifier=None, *args, **kwargs) -> bool:
    """Runs one agent's full cycle. An agent-level exception here means the whole
    agent crashed (as opposed to a single project failing inside it) — that's the
    highest-severity failure mode possible for a scheduled run, so it must never be
    reported by log file alone. If a notifier is available, send an admin alert with
    the same guarantee: either the agent completed, or someone was told it didn't."""
    name = agent.__class__.__name__
    if dry_run:
        print(f"--- [DRY RUN] Would run: {name} ---")
        return True
    try:
        t0 = time.time()
        print(f"--- Running agent: {name} ---")
        agent.run(*args, **kwargs)
        elapsed = time.time() - t0
        print(f"--- Agent {name} finished in {elapsed:.1f}s ---")
        return True
    except Exception as e:
        _pipeline_logger.error("Agent %s failed: %s", name, str(e), exc_info=True)
        print(f"!!! Agent {name} failed: {e}")
        if notifier:
            try:
                notifier.send_admin_alert(
                    subject=f"Agent Crashed: {name}",
                    message=(
                        f"{name} raised an unhandled exception and did not complete "
                        f"this scheduled run:\n\n{e}\n\n"
                        f"See {name}.log for the full traceback."
                    )
                )
            except Exception as alert_err:
                _pipeline_logger.error("Failed to send crash alert for %s: %s", name, str(alert_err))
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
        choices=['all', 'ingestion', 'literature', 'progress', 'enhancement', 'supervisor', 'gc'],
        default='all', 
        help="Specify which agent to run (default: 'all')"
    )
    
    parser.add_argument(
        "--project",
        type=str,
        default='all',
        help="Specify a single project name to process (default: 'all' projects)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would run without executing any agents."
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
    
    if db.get_project_count() == 0:
        db.migrate_from_json(str(Config.RESEARCHERS_MAP_PATH))
        print("--- DB Migration Completed ---\n")
    else:
        print("--- DB already populated, skipping JSON migration ---\n")

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

        # A brand-new project's first-ever sync downloads it here, but all_projects
        # was computed before this block ran, so it wouldn't otherwise be considered
        # a "valid target" for any downstream phase in this same run. Union it in.
        all_projects = list(set(all_projects) | set(updated_projects))

    # Validate that the targeted projects actually exist before giving them to AI agents
    valid_targets = [p for p in target_projects if p in all_projects]
    if not valid_targets and args.agent not in ['gc', 'ingestion', 'supervisor']:
        print(f"--- ⚠️ No valid projects found matching '{args.project}' in overleaf_projects/. Exiting. ---")
        return

    # --- 1. Literature Research Agent ---
    if args.agent in ['all', 'literature']:
        # When running 'all', only search literature for projects that actually changed
        lit_targets = valid_targets if args.agent == 'literature' else [p for p in updated_projects if p in all_projects]
        if lit_targets:
            print(f"\n--- 1. Running Literature Research Agent for: {lit_targets} ---")
            lit_agent = LiteratureResearchAgent(active_projects=lit_targets, notifier=notifier, db=db)
            run_agent_safely(lit_agent, dry_run=args.dry_run, notifier=notifier)
        else:
            print("\n--- No updated projects for Literature Research. Skipping. ---")
    
    # --- 2. Progress Tracking Agent ---
    if args.agent in ['all', 'progress']:
        # If running explicitly via CLI, override the "must be updated" rule to allow force-testing
        projects_to_track = valid_targets if args.agent == 'progress' else updated_projects
        
        if projects_to_track:
            print(f"\n--- 2. Running Progress Tracking Agent for: {projects_to_track} 🚀 ---")
            prog_agent = ProgressTrackingAgent(overleaf_projects=projects_to_track, notifier=notifier, db=db)
            run_agent_safely(prog_agent, dry_run=args.dry_run, notifier=notifier)
        else:
            print("\n--- No Overleaf projects were updated. Skipping Progress Tracking Agent. 😴 ---")
    
    # --- 3. Research Enhancement Agent ---
    if args.agent in ['all', 'enhancement']:
        enh_targets = valid_targets if args.agent == 'enhancement' else [p for p in updated_projects if p in all_projects]
        if enh_targets:
            print(f"\n--- 3. Running Research Enhancement Agent for: {enh_targets} 🚀 ---")
            enhancement_agent = ResearchEnhancementAgent(overleaf_projects=enh_targets, notifier=notifier, db=db)
            run_agent_safely(enhancement_agent, dry_run=args.dry_run, notifier=notifier)
        else:
            print("\n--- No updated projects for Research Enhancement. Skipping. ---")
    
    # --- 4. Supervisor Status Agent ---
    if args.agent in ['all', 'supervisor']:
        print(f"\n--- 4. Running Supervisor Status Agent ---")
        supervisor_agent = SupervisorStatusAgent(db=db, notifier=notifier)
        run_agent_safely(supervisor_agent, dry_run=args.dry_run, notifier=notifier)

    # --- 5. System Cleanup (Garbage Collector) ---
    if args.agent in ['all', 'gc']:
        print(f"\n--- 5. Running System Cleanup (Retention Policy: {Config.GARBAGE_COLLECTION_TTL_DAYS} days) 🧹 ---")
        gc = GarbageCollector(db=db, retention_days=Config.GARBAGE_COLLECTION_TTL_DAYS, notifier=notifier)
        run_agent_safely(gc, dry_run=args.dry_run, notifier=notifier)

    print(f"\n--- Pipeline completed | Agent={args.agent} | Project={args.project} | Dry-run={args.dry_run} ---")
    print("\n--- ✅ All Executions Finished Successfully ---")

if __name__ == "__main__":
    main()