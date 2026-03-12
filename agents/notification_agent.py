import os
import smtplib
import json
import markdown
import re
from email.message import EmailMessage
from dotenv import load_dotenv
from agents.base_agent import BaseAgent

load_dotenv()

class NotificationAgent(BaseAgent):
    """
    Agent responsible for communicating with the human researchers.
    Uses a dedicated GMAIL relay account to send beautiful HTML emails 
    to the official University emails of the researchers.
    """
    def __init__(self, updated_projects: list):
        super().__init__(agent_name="NotificationAgent")
        self.projects = updated_projects
        
        # The GMAIL account that physically SENDS the email
        self.sender_email = os.getenv("NOTIFICATION_SENDER_EMAIL") 
        self.sender_password = os.getenv("NOTIFICATION_SENDER_PASSWORD")
        
        # The UNIVERSITY account that RECEIVES the email (default fallback)
        self.target_email = os.getenv("OVERLEAF_EMAIL") 
        
        self.map_file = "researchers_map.json"
        self._ensure_map_file()

    def _ensure_map_file(self):
        """Creates a default mapping file pointing to the University email."""
        if not os.path.exists(self.map_file):
            default_map = {
                "AI Reasearch Project": self.target_email,
                "Machine Learning Research": self.target_email
            }
            with open(self.map_file, 'w', encoding='utf-8') as f:
                json.dump(default_map, f, indent=4)

    def get_researcher_email(self, project_name: str) -> str:
        """Looks up the target email (University email) for a specific project."""
        try:
            with open(self.map_file, 'r', encoding='utf-8') as f:
                mapping = json.load(f)
            return mapping.get(project_name, self.target_email) 
        except Exception:
            return self.target_email

    def _get_latest_file(self, folder_path: str) -> str:
        if not os.path.exists(folder_path): 
            return None
        files = [os.path.join(folder_path, f) for f in os.listdir(folder_path) if f.endswith('.md')]
        if not files: 
            return None
        return max(files, key=os.path.getctime)

    def _format_display_title(self, filename: str) -> str:
        raw_name = filename.replace('.md', '')
        clean_name = re.sub(r'\d{4}-\d{2}-\d{2}(_\d{2}-\d{2}-\d{2})?_', '', raw_name)
        return clean_name.replace('_', ' ').title()

    def collect_attachments(self, project_name: str) -> list:
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
        if not self.sender_email or not self.sender_password:
            self.logger.error("Sender credentials missing in .env file!")
            return

        msg = EmailMessage()
        msg['Subject'] = f"📊 Research Auto-Review Update: {project_name}"
        msg['From'] = f"AI Research Copilot 🤖 <{self.sender_email}>"
        msg['To'] = recipient # This will be your University email

        # 1. Compile the reports into HTML
        reports_html = ""
        for file_path in attachments:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    md_content = f.read()
                
                html_body = markdown.markdown(md_content)
                filename = os.path.basename(file_path)
                display_title = self._format_display_title(filename)
                
                reports_html += f"""
                <div style="margin-top: 30px; padding: 25px; border-left: 5px solid #3498db; background-color: #f8f9fa; border-radius: 4px;">
                    <h3 style="color: #2980b9; margin-top: 0; font-size: 18px; border-bottom: 1px solid #ddd; padding-bottom: 10px;">📄 {display_title}</h3>
                    <div style="font-size: 15px; color: #444;">
                        {html_body}
                    </div>
                </div>
                """
            except Exception as e:
                self.logger.error("Could not process %s: %s", file_path, str(e))

        # 2. Build Email layout
        email_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #333; line-height: 1.6; background-color: #eaeff2; padding: 20px; }}
                .container {{ max-width: 800px; margin: 0 auto; background-color: #ffffff; padding: 40px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.05); }}
                h2 {{ color: #2c3e50; font-size: 24px; }}
                h1, h2, h3, h4 {{ color: #2c3e50; font-weight: 600; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>Hello Researcher,</h2>
                <p style="font-size: 16px;">The <b>AI Research Copilot</b> has completed a new analysis cycle for your project: <strong>{project_name}</strong>.</p>
                <p style="font-size: 16px;">Below is your detailed AI feedback, literature summaries, and enhancement tasks:</p>
                
                {reports_html}
                
                <hr style="border: none; border-top: 1px solid #eee; margin: 40px 0 20px 0;">
                <p style="font-size: 14px; color: #7f8c8d; text-align: center;">
                    Keep pushing the boundaries of science! 🚀<br>
                    <i>- Your Automated Lab Assistant</i>
                </p>
            </div>
        </body>
        </html>
        """
        
        msg.set_content("Please enable HTML to view this message.")
        msg.add_alternative(email_html, subtype='html')

        # 3. Attach backup Markdown files
        for file_path in attachments:
            try:
                with open(file_path, 'rb') as f:
                    file_data = f.read()
                
                clean_title = self._format_display_title(os.path.basename(file_path))
                clean_filename = f"{clean_title.replace(' ', '_')}.md"
                msg.add_attachment(file_data, maintype='text', subtype='plain', filename=clean_filename)
            except Exception as e:
                self.logger.error(f"Could not attach {file_path}: {e}")

        # 4. Send the Email using the robust GMAIL SMTP server
        try:
            self.logger.info("Connecting to Gmail SMTP server as a relay...")
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                smtp.login(self.sender_email, self.sender_password)
                smtp.send_message(msg)
            self.logger.info("✅ Email successfully sent to %s with embedded reports.", recipient)
        except Exception as e:
            self.logger.error("❌ Failed to send email: %s", str(e))

    def run(self):
        self.logger.info("Starting Notification Agent cycle.")
        for project in self.projects:
            attachments = self.collect_attachments(project)
            if not attachments:
                continue
            
            recipient = self.get_researcher_email(project)
            self.send_email(project, recipient, attachments)
            
        self.logger.info("Notification cycle completed.")