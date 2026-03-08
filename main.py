from agents.literature_research_agent import LiteratureResearchAgent

def main():
    """
    The main entry point of the application.
    Eventually, this will evolve into the Scheduler that manages all agents.
    """
    
    # Define some dummy research topics for testing purposes
    lab_topics = [
        "Autonomous AI Agents in Healthcare",
        "Machine Learning for Climate Change"
    ]
    
    print("--- Initializing Agents ---")
    
    # Instantiate the literature research agent
    lit_agent = LiteratureResearchAgent(research_topics=lab_topics)
    
    print("\n--- Running Literature Research Agent ---")
    
    # Execute the agent's main flow
    lit_agent.run()
    
    print("\n--- Execution Finished ---")

# Standard Python idiom to ensure the script runs only when executed directly
if __name__ == "__main__":
    main()