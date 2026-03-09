from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
import datetime

class LiteratureResearchAgent(BaseAgent):
    """
    Agent responsible for conducting literature reviews, summarizing new articles, 
    and updating comparison tables for the lab's research topics.
    """
    
    def __init__(self, research_topics: list):
        super().__init__(agent_name="LiteratureResearchAgent")
        self.research_topics = research_topics
        self.library = LibraryManager()  # Instantiate the library manager
        self.logger.info("LiteratureResearchAgent initialized with %d topics.", len(self.research_topics))

    def search_new_articles(self, topic: str) -> str:
        """
        Query the LLM to find recent advancements or key papers on the given topic.
        """
        self.logger.info("Searching for new articles on topic: %s", topic)
        prompt = f"""
        You are an expert academic research assistant. 
        Please provide a brief summary of the most recent and significant advancements 
        or key papers related to the following research topic: '{topic}'.
        Keep the response concise (up to 2-3 paragraphs) and focus strictly on academic innovation.
        """
        response_text = self.ask_llm(prompt)
        print(f"\n{'='*40}\n🔬 Results for: {topic}\n{'='*40}\n{response_text}\n")
        return response_text

    def summarize_and_save(self, topic: str, article_data: str):
        """
        Summarize the found article and save it to the research library.
        """
        self.logger.info("Saving article summary for topic: %s", topic)
        # Use the library manager to save the LLM's text to a markdown file
        self.library.save_summary(topic, article_data)

    def update_comparison_table(self, topic: str):
        """
        Build or update a rolling comparison table for the research articles.
        """
        self.logger.info("Updating the rolling comparison table for %s...", topic)
        
        # Create some structured data to save in our CSV table
        new_data = {
            "Date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "Topic": topic,
            "Action": "Literature Review Conducted",
            "Status": "Success"
        }
        self.library.update_comparison_table(topic, new_data)

    def run(self):
        """
        The main execution flow of the agent.
        """
        self.logger.info("Starting the literature research cycle.")
        for topic in self.research_topics:
            llm_result = self.search_new_articles(topic)
            self.summarize_and_save(topic, llm_result)
            self.update_comparison_table(topic)
            
        self.logger.info("Literature research cycle completed successfully.")