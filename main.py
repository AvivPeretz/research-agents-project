import os
from agents.literature_research_agent import LiteratureResearchAgent
from agents.progress_tracking_agent import ProgressTrackingAgent
from agents.research_enhancement_agent import ResearchEnhancementAgent
from ingestion.data_ingestion_agent import DataIngestionAgent

def get_all_active_projects() -> list:
    """
    Utility function to get a list of all locally downloaded projects.
    Acts as the 'Source of Truth' for the Literature Research Agent.
    """
    base_dir = "overleaf_projects"
    if not os.path.exists(base_dir):
        return []
    # Return a list of directory names inside overleaf_projects
    return [name for name in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, name))]

def main():
    """
    The main Scheduler and Router of the application.
    Triggers agents based on available data and acts as a central control hub.
    """
    
    print("--- 0. Running Data Ingestion (Delta Sync) ---")
    scraper = DataIngestionAgent()
    # The agent connects, downloads only deltas, and returns updated project names
    updated_projects = scraper.sync_all_projects()
    
    # Get the master list of ALL projects we currently have on disk
    all_projects = get_all_active_projects()
    
    print("\n--- Initializing AI Agents ---")
    
    # --- 1. Literature Research Agent (Runs on ALL projects) ---
    if all_projects:
        print(f"\n--- 1. Running Literature Research Agent for all active projects ---")
        lit_agent = LiteratureResearchAgent(active_projects=all_projects)
        lit_agent.run()
    else:
        print("\n--- No projects available for Literature Research. ---")
    
    # --- 2. Progress Tracking Agent (Depends on Overleaf updates) ---
    if updated_projects:
        print(f"\n--- 2. Running Progress Tracking Agent for: {updated_projects} 🚀 ---")
        prog_agent = ProgressTrackingAgent(overleaf_projects=updated_projects)
        prog_agent.run()
    else:
        print("\n--- No Overleaf projects were updated. Skipping Progress Tracking Agent. 😴 ---")
    
    # --- 3. Research Enhancement Agent (Always runs to check async emails) ---
    if all_projects:
        print(f"\n--- 3. Running Research Enhancement Agent for all active projects 🚀 ---")
        enhancement_agent = ResearchEnhancementAgent(overleaf_projects=all_projects)
        enhancement_agent.run()
    else:
        print("\n--- No projects available for Research Enhancement. ---")
    
    print("\n--- All Executions Finished Successfully ---")

if __name__ == "__main__":
    main()