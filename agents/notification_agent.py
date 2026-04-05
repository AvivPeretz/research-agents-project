import os
import smtplib
import markdown
import logging
from email.message import EmailMessage

# Import centralized configuration
from config import Config
from agents.base_agent import BaseAgent
# Import DatabaseManager type for type hinting
from utils.database_manager import DatabaseManager

class NotificationAgent(BaseAgent):
    """
    A utility service agent responsible for formatting and sending
    agent-specific emails to the human researchers, using the SQLite DB for routing.
    """
    def __init__(self, db: DatabaseManager = None):
        # Do NOT call BaseAgent.__init__() because NotificationAgent does not use the LLM.
        # Initialize logger and minimal attributes manually.
        self.agent_name = "NotificationAgent"
        self.logger = logging.getLogger(self.agent_name)
        if not self.logger.handlers:
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)
            self.logger.setLevel(logging.INFO)

        # We now accept the DatabaseManager instance via Dependency Injection
        self.db = db

        # Use Config for credentials
        self.sender_email = Config.NOTIFICATION_SENDER_EMAIL
        self.sender_password = Config.NOTIFICATION_SENDER_PASSWORD

        # The UNIVERSITY account that RECEIVES the email (default fallback)
        self.target_email = Config.OVERLEAF_EMAIL

    def get_researcher_email(self, project_name: str) -> str:
        """
        Queries the SQLite database to find the assigned researcher's email.
        Falls back to the default university email if not found.
        """
        if not self.db:
            self.logger.warning("No database connection provided. Using fallback email.")
            return self.target_email
            
        try:
            config = self.db.get_project_state(project_name) # Changed to get_project_state
            # Use canonical snake_case key from DB schema
            if config and config.get('researcher_email'):
                return config['researcher_email']
            else:
                self.logger.info("Project '%s' not found in DB. Routing to default email.", project_name)
                return self.target_email
        except Exception as e:
            self.logger.error("Database query failed: %s", str(e))
            return self.target_email

    def _dispatch_email(self, msg: EmailMessage, recipient: str):
        """Helper method to handle the physical SMTP sending."""
        if not self.sender_email or not self.sender_password:
            self.logger.error("Sender credentials missing in configuration!")
            return False

        try:
            self.logger.info("Connecting to Gmail SMTP server as a relay...")
            with smtplib.SMTP_SSL(Config.SMTP_SERVER, Config.SMTP_PORT) as smtp:
                smtp.login(self.sender_email, self.sender_password)
                smtp.send_message(msg)
            self.logger.info("✅ Email successfully sent to %s.", recipient)
            return True
        except Exception as e:
            self.logger.error("❌ Failed to send email: %s", str(e))
            return False

    # ==========================================
    # AGENT-SPECIFIC EMAIL FUNCTIONS
    # ==========================================

    def send_literature_update(self, project_name: str, md_content: str, csv_path: str = None):
        """Formats and sends the Daily Literature Search email."""
        recipient = self.get_researcher_email(project_name)
        msg = EmailMessage()
        msg['Subject'] = f"📚 Daily Literature Update: {project_name}"
        msg['From'] = f"Literature Search Agent 🤖 <{self.sender_email}>"
        msg['To'] = recipient

        html_body = markdown.markdown(md_content)
        
        email_html = f"""
        <!DOCTYPE html>
        <html>
        <head><style>body {{ font-family: Arial, sans-serif; color: #333; line-height: 1.6; background-color: #f4f7f6; padding: 20px; }} .container {{ max-width: 800px; margin: 0 auto; background-color: #fff; padding: 30px; border-radius: 8px; border-top: 5px solid #27ae60; }}</style></head>
        <body>
            <div class="container">
                <h2 style="color: #27ae60;">Hello Researcher,</h2>
                <p>I have completed my daily scan for new academic papers related to <strong>{project_name}</strong>.</p>
                <div style="background-color: #eafaf1; padding: 20px; border-radius: 5px;">
                    {html_body}
                </div>
                <p style="font-size: 14px; color: #7f8c8d; margin-top: 30px;">Attached is the updated rolling comparison CSV.<br>- Your Literature Agent</p>
            </div>
        </body>
        </html>
        """
        msg.set_content("Please enable HTML to view this message.")
        msg.add_alternative(email_html, subtype='html')

        if csv_path and os.path.exists(csv_path):
            try:
                with open(csv_path, 'rb') as f:
                    file_data = f.read()
                msg.add_attachment(file_data, maintype='text', subtype='csv', filename=f"{project_name.replace(' ', '_')}_literature.csv")
            except Exception as e:
                self.logger.error("Could not attach CSV: %s", str(e))

        self._dispatch_email(msg, recipient)

    def send_progress_feedback(self, project_name: str, md_content: str):
        """Formats and sends the Tri-daily Progress Tracking email."""
        recipient = self.get_researcher_email(project_name)
        msg = EmailMessage()
        msg['Subject'] = f"📝 Writing Progress Feedback: {project_name}"
        msg['From'] = f"Progress Tracking Agent 🤖 <{self.sender_email}>"
        msg['To'] = recipient

        html_body = markdown.markdown(md_content)
        
        email_html = f"""
        <!DOCTYPE html>
        <html>
        <head><style>body {{ font-family: Arial, sans-serif; color: #333; line-height: 1.6; background-color: #f4f7f6; padding: 20px; }} .container {{ max-width: 800px; margin: 0 auto; background-color: #fff; padding: 30px; border-radius: 8px; border-top: 5px solid #2980b9; }}</style></head>
        <body>
            <div class="container">
                <h2 style="color: #2980b9;">Hello Researcher,</h2>
                <p>I have analyzed the recent changes you made to the manuscript for <strong>{project_name}</strong>.</p>
                <div style="background-color: #ebf5fb; padding: 20px; border-radius: 5px;">
                    {html_body}
                </div>
                <p style="font-size: 14px; color: #7f8c8d; margin-top: 30px;">Keep up the great writing!<br>- Your Progress Tracking Agent</p>
            </div>
        </body>
        </html>
        """
        msg.set_content("Please enable HTML to view this message.")
        msg.add_alternative(email_html, subtype='html')
        self._dispatch_email(msg, recipient)

    def send_stanford_tasks(self, project_name: str, md_content: str):
        """Formats and sends the Monthly Stanford Review & Task List email."""
        recipient = self.get_researcher_email(project_name)
        msg = EmailMessage()
        msg['Subject'] = f"🎯 Monthly Action Plan: Stanford Peer-Review for {project_name}"
        msg['From'] = f"Research Enhancement Agent 🤖 <{self.sender_email}>"
        msg['To'] = recipient

        html_body = markdown.markdown(md_content)
        
        email_html = f"""
        <!DOCTYPE html>
        <html>
        <head><style>body {{ font-family: Arial, sans-serif; color: #333; line-height: 1.6; background-color: #f4f7f6; padding: 20px; }} .container {{ max-width: 800px; margin: 0 auto; background-color: #fff; padding: 30px; border-radius: 8px; border-top: 5px solid #8e44ad; }}</style></head>
        <body>
            <div class="container">
                <h2 style="color: #8e44ad;">Hello Researcher,</h2>
                <p>The Stanford ML Group has reviewed your manuscript for <strong>{project_name}</strong>.</p>
                <p>Based on their feedback, I have estimated the required effort and prepared your action plan for the upcoming month:</p>
                <div style="background-color: #f4ecf7; padding: 20px; border-radius: 5px;">
                    {html_body}
                </div>
                <p style="font-size: 14px; color: #7f8c8d; margin-top: 30px;">I will check the status of these tasks in the next review cycle.<br>- Your Research Enhancement Agent</p>
            </div>
        </body>
        </html>
        """
        msg.set_content("Please enable HTML to view this message.")
        msg.add_alternative(email_html, subtype='html')
        self._dispatch_email(msg, recipient)

    def send_admin_alert(self, subject: str, message: str):
        """Sends a critical system alert directly to the system administrator."""
        msg = EmailMessage()
        msg['Subject'] = f"🚨 System Alert: {subject}"
        msg['From'] = f"System Administrator 🤖 <{self.sender_email}>"
        msg['To'] = self.sender_email # Sending to the dummy/personal email itself

        msg.set_content(message)
        self._dispatch_email(msg, self.sender_email)

    def run(self):
        pass