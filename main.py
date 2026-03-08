from agents.literature_research_agent import LiteratureResearchAgent
from agents.progress_tracking_agent import ProgressTrackingAgent
from agents.research_enhancement_agent import ResearchEnhancementAgent

def main():
    """
    The main entry point of the application.
    Simulates the Scheduler activating the agents.
    """
    
    # Dummy data for testing
    lab_topics = ["Autonomous AI Agents", "Machine Learning"]
    active_projects = ["Project_Alpha_Overleaf", "Project_Beta_Overleaf"]
    
    print("--- Initializing All Agents ---")
    lit_agent = LiteratureResearchAgent(research_topics=lab_topics)
    prog_agent = ProgressTrackingAgent(overleaf_projects=active_projects)
    enhancement_agent = ResearchEnhancementAgent(overleaf_projects=active_projects)
    
    print("\n--- 1. Running Literature Research Agent ---")
    lit_agent.run()
    
    print("\n--- 2. Running Progress Tracking Agent ---")
    prog_agent.run()
    
    print("\n--- 3. Running Research Enhancement Agent ---")
    enhancement_agent.run()
    
    print("\n--- All Executions Finished Successfully ---")

if __name__ == "__main__":
    main()