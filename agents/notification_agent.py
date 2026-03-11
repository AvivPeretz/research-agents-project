import os
import smtplib
import json
from email.message import EmailMessage
from dotenv import load_dotenv
from agents.base_agent import BaseAgent

load_dotenv()

class NotificationAgent(BaseAgent):
    """
    Agent responsible for communicating with the human researchers.
    It collects the generated Markdown reports and sends them as attachments 
    via a beautifully formatted HTML email.
    """
    def __init__(self, updated_projects: list):
        super().__init__(agent_name="NotificationAgent")
        self.projects = updated_projects
        self.sender_email = os.getenv("OVERLEAF_EMAIL") # Using the dummy account to send
        self.sender_password = os.getenv("EMAIL_APP_PASSWORD")
        self.map_file = "researchers_map.json"
        
        self._ensure_map_file()

    def _ensure_map_file(self):
        """Creates a default mapping file if it doesn't exist."""
        if not os.path.exists(self.map_file):
            # Defaulting to the sender email for testing purposes
            default_map = {
                "AI TEST RESEARCH": self.sender_email,
                "Machine Learning Research": self.sender_email
            }
            with open(self.map_file, 'w', encoding='utf-8') as f:
                json.dump(default_map, f, indent=4)

    def get_researcher_email(self, project_name: str) -> str:
        """Looks up the target email for a specific project."""
        try:
            with open(self.map_file, 'r', encoding='utf-8') as f:
                mapping = json.load(f)
            return mapping.get(project_name, self.sender_email) # Fallback to dummy
        except Exception:
            return self.sender_email

    def _get_latest_file(self, folder_path: str) -> str:
        """Utility to find the most recently created file in a given directory."""
        if not os.path.exists(folder_path): 
            return None
            
        files = [os.path.join(folder_path, f) for f in os.listdir(folder_path) if f.endswith('.md')]
        if not files: 
            return None
            
        # Return the file with the newest creation/modification time
        return max(files, key=os.path.getctime)

    def collect_attachments(self, project_name: str) -> list:
        """Gathers the latest reports (Literature, Tracking, Enhancement) for the project."""
        safe_name = project_name.replace(" ", "_")
        attachments = []
        base_lib = "research_library"
        
        paths_to_check = [
            os.path.join(base_lib, "literature_reviews", safe_name),
            os.path.join(base_lib, "project_tracking", safe_name),
            os.path.join(base_lib, "project_enhancement", safe_name)
        ]
        
        for folder in paths_to_check:
            latest_file = self._get_latest_file(folder)
            if latest_file: 
                attachments.append(latest_file)
                
        return attachments

    def send_email(self, project_name: str, recipient: str, attachments: list):
        """Constructs and sends the HTML email with attachments via Gmail SMTP."""
        if not self.sender_email or not self.sender_password:
            self.logger.error("Email credentials missing in .env file!")
            return

        msg = EmailMessage()
        msg['Subject'] = f"📊 Research Auto-Review Update: {project_name}"
        msg['From'] = f"AI Research Copilot 🤖 <{self.sender_email}>"
        msg['To'] = recipient

        # Beautiful HTML Body
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <h2 style="color: #2c3e50;">Hello Researcher,</h2>
                <p>The <b>AI Research Copilot</b> has completed a new analysis cycle for your project: <strong>{project_name}</strong>.</p>
                <p>We detected recent modifications in your Overleaf manuscript or found new relevant literature in your field.</p>
                <p>Please find your detailed AI feedback, literature summaries, and enhancement tasks attached to this email.</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                <p style="font-size: 0.9em; color: #7f8c8d;">
                    Keep pushing the boundaries of science! 🚀<br>
                    <i>- Your Automated Lab Assistant</i>
                </p>
            </body>
        </html>
        """
        msg.set_content("Please enable HTML to view this message.")
        msg.add_alternative(html_content, subtype='html')

        # Attach the Markdown files
        for file_path in attachments:
            try:
                with open(file_path, 'rb') as f:
                    file_data = f.read()
                    file_name = os.path.basename(file_path)
                # Attach as a general binary file so the user can download it easily
                msg.add_attachment(file_data, maintype='application', subtype='octet-stream', filename=file_name)
            except Exception as e:
                self.logger.error(f"Could not attach {file_path}: {e}")

        # Send the Email using Google's secure SMTP server
        try:
            self.logger.info("Connecting to Gmail SMTP server...")
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                smtp.login(self.sender_email, self.sender_password)
                smtp.send_message(msg)
            self.logger.info("✅ Email successfully sent to %s with %d attachments.", recipient, len(attachments))
        except Exception as e:
            self.logger.error("❌ Failed to send email: %s", str(e))

    def run(self):
        self.logger.info("Starting Notification Agent cycle.")
        for project in self.projects:
            attachments = self.collect_attachments(project)
            
            if not attachments:
                self.logger.info("No new reports found for %s. Skipping email.", project)
                continue
            
            recipient = self.get_researcher_email(project)
            self.logger.info("Preparing to send update for '%s' to '%s'...", project, recipient)
            self.send_email(project, recipient, attachments)
            
        self.logger.info("Notification cycle completed.")