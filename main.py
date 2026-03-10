from agents.literature_research_agent import LiteratureResearchAgent
from agents.progress_tracking_agent import ProgressTrackingAgent
from agents.research_enhancement_agent import ResearchEnhancementAgent
from ingestion.overleaf_scraper import OverleafScraper

def main():
    """
    The main entry point of the application.
    Integrates the Data Ingestion Agent (Delta Sync) with the AI Agents.
    """
    
    # General lab research topics (for the Literature Research Agent)
    lab_topics = ["AI In Modern World"]
    
    print("--- 0. Running Data Ingestion (Delta Sync) ---")
    scraper = OverleafScraper()
    # The agent connects, checks for changes, downloads, and returns a list of updated project names
    updated_projects = scraper.sync_all_projects()
    
    print("\n--- Initializing AI Agents ---")
    # Initialize the Literature Research Agent
    lit_agent = LiteratureResearchAgent(research_topics=lab_topics)
    
    print("\n--- 1. Running Literature Research Agent ---")
    # This agent always runs to fetch the latest global advancements
    lit_agent.run()
    
    # Smart logic: Run the analysis agents ONLY if there are updated projects!
    if not updated_projects:
        print("\n--- No Overleaf projects were updated. Skipping project analysis agents. 😴 ---")
    else:
        print(f"\n--- Waking up project analysis agents for: {updated_projects} 🚀 ---")
        prog_agent = ProgressTrackingAgent(overleaf_projects=updated_projects)
        enhancement_agent = ResearchEnhancementAgent(overleaf_projects=updated_projects)
        
        print("\n--- 2. Running Progress Tracking Agent ---")
        prog_agent.run()
        
        print("\n--- 3. Running Research Enhancement Agent ---")
        enhancement_agent.run()
    
    print("\n--- All Executions Finished Successfully ---")

if __name__ == "__main__":
    main()