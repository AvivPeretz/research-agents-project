import os
import smtplib
import json
import markdown
from email.message import EmailMessage
from dotenv import load_dotenv
from agents.base_agent import BaseAgent

load_dotenv()

class NotificationAgent(BaseAgent):
    """
    A utility service agent responsible for formatting and sending
    agent-specific emails to the human researchers.
    """
    def __init__(self):
        # Notice we don't take lists of projects anymore. It's a stateless service.
        super().__init__(agent_name="NotificationAgent")
        
        # The GMAIL account that physically SENDS the email
        self.sender_email = os.getenv("NOTIFICATION_SENDER_EMAIL") 
        self.sender_password = os.getenv("NOTIFICATION_SENDER_PASSWORD")
        
        # The UNIVERSITY account that RECEIVES the email (default fallback)
        self.target_email = os.getenv("OVERLEAF_EMAIL") 
        
        self.map_file = "researchers_map.json"
        self._ensure_map_file()

    def _ensure_map_file(self):
        if not os.path.exists(self.map_file):
            default_map = {
                "AI Reasearch Project": self.target_email,
                "Machine Learning Research": self.target_email
            }
            with open(self.map_file, 'w', encoding='utf-8') as f:
                json.dump(default_map, f, indent=4)

    def get_researcher_email(self, project_name: str) -> str:
        try:
            with open(self.map_file, 'r', encoding='utf-8') as f:
                mapping = json.load(f)
            return mapping.get(project_name, self.target_email) 
        except Exception:
            return self.target_email

    def _dispatch_email(self, msg: EmailMessage, recipient: str):
        """Helper method to handle the physical SMTP sending."""
        if not self.sender_email or not self.sender_password:
            self.logger.error("Sender credentials missing in .env file!")
            return

        try:
            self.logger.info("Connecting to Gmail SMTP server as a relay...")
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                smtp.login(self.sender_email, self.sender_password)
                smtp.send_message(msg)
            self.logger.info("✅ Email successfully sent to %s.", recipient)
        except Exception as e:
            self.logger.error("❌ Failed to send email: %s", str(e))

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

        # Attach the CSV if provided
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

    def run(self):
        # We keep this just to satisfy the BaseAgent abstract method, but it won't be used directly anymore.
        pass