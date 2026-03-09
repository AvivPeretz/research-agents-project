from agents.base_agent import BaseAgent

class LiteratureResearchAgent(BaseAgent):
    """
    Agent responsible for conducting literature reviews, summarizing new articles, 
    and updating comparison tables for the lab's research topics.
    """
    
    def __init__(self, research_topics: list):
        # Initialize the parent class
        super().__init__(agent_name="LiteratureResearchAgent")
        self.research_topics = research_topics
        self.logger.info("LiteratureResearchAgent initialized with %d topics.", len(self.research_topics))

    def search_new_articles(self, topic: str) -> str:
        """
        Query the LLM to find recent advancements or key papers on the given topic.
        Note: For a fully autonomous lab agent, this would later be connected to an 
        academic database API (e.g., Semantic Scholar). For now, we use the LLM's knowledge.
        """
        self.logger.info("Searching for new articles on topic: %s", topic)
        
        # Craft a clear and specific prompt for the LLM
        prompt = f"""
        You are an expert academic research assistant. 
        Please provide a brief summary of the most recent and significant advancements 
        or key papers related to the following research topic: '{topic}'.
        Keep the response concise (up to 2-3 paragraphs) and focus strictly on academic innovation.
        """
        
        # Send the prompt to the LLM using the inherited method from BaseAgent
        response_text = self.ask_llm(prompt)
        
        self.logger.info("Received response from LLM for topic: %s", topic)
        
        # Print the results nicely to the console so we can read them
        print(f"\n{'='*40}")
        print(f"🔬 Results for: {topic}")
        print(f"{'='*40}")
        print(f"{response_text}\n")
        
        return response_text

    def summarize_and_save(self, article_data: str):
        """
        Summarize the found article and save it to the research library.
        Currently a placeholder.
        """
        self.logger.info("Summarizing and saving article data...")
        # TODO: Implement file saving logic (e.g., saving to a .txt or .md file)

    def update_comparison_table(self):
        """
        Build or update a rolling comparison table for the research articles.
        Currently a placeholder.
        """
        self.logger.info("Updating the rolling comparison table...")
        # TODO: Implement table generation logic (e.g., using Pandas to create a CSV)

    def run(self):
        """
        The main execution flow of the agent.
        Overrides the abstract method from BaseAgent.
        """
        self.logger.info("Starting the literature research cycle.")
        
        for topic in self.research_topics:
            # 1. Search for articles (now connected to LLM)
            llm_result = self.search_new_articles(topic)
            
            # 2. In future steps, we will pass 'llm_result' to the saving and table functions
            self.summarize_and_save(llm_result)
            
        self.update_comparison_table()
        
        self.logger.info("Literature research cycle completed successfully.")