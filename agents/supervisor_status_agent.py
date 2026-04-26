import json
import logging
from datetime import datetime
from pydantic import ValidationError

from agents.base_agent import BaseAgent
from utils.database_manager import DatabaseManager
from agents.notification_agent import NotificationAgent
from domain.schemas import SupervisorReport

class SupervisorStatusAgent(BaseAgent):
    """
    High-level overview agent that acts as a Lab Manager.
    Reads progress snapshots from SQLite, calculates baseline metrics, 
    uses the LLM to determine student status, and sends a weekly report to the supervisor.
    """
    def __init__(self, db: DatabaseManager, notifier: NotificationAgent):
        super().__init__(agent_name="SupervisorStatusAgent")
        self.db = db
        self.notifier = notifier
        self.logger.info("SupervisorStatusAgent initialized.")

    def _fetch_supervisor_projects(self) -> dict:
        """
        Groups all active projects by supervisor email.
        Returns: { 'supervisor@x.com': [ {project_data}, ... ] }
        """
        if not self.db:
            raise ValueError("Database connection is required for SupervisorStatusAgent.")

        projects = self.db.get_projects_by_supervisor()
        projects_by_supervisor = {}
        for proj in projects:
            sup_email = proj['supervisor_email']
            if sup_email not in projects_by_supervisor:
                projects_by_supervisor[sup_email] = []
            projects_by_supervisor[sup_email].append(proj)
        return projects_by_supervisor

    def _calculate_project_metrics(self, project_name: str, created_at_str: str) -> dict:
        """
        Extracts the last 28 days of snapshots and calculates objective metrics.
        """
        metrics = {
            "project_name": project_name,
            "days_since_creation": 0,
            "total_active_days": 0,
            "total_silent_days": 0,
            "average_chars_per_active_day": 0,
            "current_silent_streak": 0
        }

        # Calculate days since creation (to handle the "New Project" edge case)
        if created_at_str:
            try:
                created_dt = None
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                    try:
                        created_dt = datetime.strptime(created_at_str.split('.')[0], fmt)
                        break
                    except ValueError:
                        continue
                if created_dt:
                    metrics["days_since_creation"] = (datetime.utcnow() - created_dt).days
            except Exception as e:
                self.logger.warning("Could not parse created_at for %s: %s", project_name, str(e))

        snapshots = self.db.get_project_snapshots(project_name, days=28)

        if not snapshots:
            return metrics

        active_chars = []
        streak_broken = False

        for snap in snapshots:
            # Calculate consecutive silent days from TODAY backwards
            if not snap['had_changes'] and not streak_broken:
                metrics["current_silent_streak"] += 1
            else:
                streak_broken = True

            if snap['had_changes']:
                metrics["total_active_days"] += 1
                active_chars.append(snap['delta_char_count'])
            else:
                metrics["total_silent_days"] += 1

        if active_chars:
            metrics["average_chars_per_active_day"] = sum(active_chars) / len(active_chars)

        return metrics

    def _generate_report_via_llm(self, supervisor_email: str, projects_metrics: list) -> SupervisorReport:
        """
        Sends the raw metrics to the LLM to classify student statuses using Pydantic Schemas.
        """
        self.logger.info("Generating LLM report for supervisor: %s", supervisor_email)
        
        # Convert metrics to a formatted string for the prompt
        metrics_str = json.dumps(projects_metrics, indent=2)
        
        prompt = f"""
        You are an expert Academic Lab Manager. Analyze the following 28-day activity metrics for students under supervisor '{supervisor_email}'.
        
        CRITICAL RULE: If 'days_since_creation' is less than 14, automatically classify the status as 'ON_TRACK', set rationale to 'New Project - Learning Baseline', and action_item to 'Allow time to establish writing habits.'
        
        For all other projects, evaluate the baseline (average chars) vs the current silent streak.
        Classify each project strictly into one of these statuses: ON_TRACK, NEEDS_ATTENTION, STALLED.
        
        Metrics Data:
        {metrics_str}
        
        You MUST respond ONLY with a valid JSON object that matches the following schema:
        {{
            "executive_summary": "A brief overview of the lab's health.",
            "evaluations": [
                {{
                    "project_name": "string",
                    "student_name": "string",
                    "status": "ON_TRACK" | "NEEDS_ATTENTION" | "STALLED",
                    "rationale": "string",
                    "action_item": "string"
                }}
            ]
        }}
        Do NOT wrap the JSON in markdown blocks (e.g., no ```json). Just output the raw JSON string.
        """

        try:
            # We use the BaseAgent's waterfall ask_llm method
            response_text = self.ask_llm(prompt)
            
            # Clean potential markdown wrapping from LLM just in case
            response_text = response_text.replace("```json", "").replace("```", "").strip()
            
            # Validate and parse the JSON string into our strict Pydantic model
            report_data = SupervisorReport.model_validate_json(response_text)
            return report_data
            
        except ValidationError as e:
            self.logger.error("LLM output failed Pydantic validation: %s", str(e))
            raise RuntimeError("LLM returned invalid schema format.") from e
        except Exception as e:
            self.logger.error("Failed to generate LLM report: %s", str(e))
            raise RuntimeError(f"Failed to generate report: {str(e)}") from e

    def _format_to_markdown(self, report: SupervisorReport) -> str:
        """
        Converts the Pydantic object into a beautiful Markdown report for the email.
        """
        md = f"### Executive Summary\n{report.executive_summary}\n\n---\n\n"
        md += "### 📋 Student Progress Breakdown\n\n"
        
        # Status indicators mapping
        status_emoji = {
            "ON_TRACK": "🟢 **ON TRACK**",
            "NEEDS_ATTENTION": "🟡 **NEEDS ATTENTION**",
            "STALLED": "🔴 **STALLED**"
        }

        for eval in report.evaluations:
            badge = status_emoji.get(eval.status, "⚪ UNKNOWN")
            md += f"#### 🧑‍🎓 {eval.student_name} | 📄 {eval.project_name}\n"
            md += f"- **Status:** {badge}\n"
            md += f"- **Analysis:** {eval.rationale}\n"
            md += f"- **Recommended Action:** {eval.action_item}\n\n"

        return md

    def run(self):
        """
        Main execution flow: Group -> Calculate -> Analyze -> Notify.
        """
        self.logger.info("Starting Supervisor Status cycle.")
        
        try:
            projects_by_sup = self._fetch_supervisor_projects()
            
            if not projects_by_sup:
                self.logger.info("No projects with assigned supervisors found. Exiting.")
                return

            for sup_email, projects in projects_by_sup.items():
                if self.db:
                    self.db.log_agent_run(
                        agent_name=self.agent_name,
                        project_name=sup_email,
                        status="STARTED",
                        started_at=datetime.now().isoformat()
                    )
                self.logger.info("Processing %d projects for supervisor: %s", len(projects), sup_email)

                metrics_list = []
                for proj in projects:
                    metrics = self._calculate_project_metrics(proj['project_name'], proj['created_at'])
                    # Append student name so the LLM knows who it is
                    metrics['student_name'] = proj['student_name'] or 'Unknown Student'
                    metrics_list.append(metrics)
                
                # 1. Send metrics to LLM to get the Pydantic structured report
                report_obj = self._generate_report_via_llm(sup_email, metrics_list)
                
                # 2. Convert Pydantic object to Markdown
                markdown_content = self._format_to_markdown(report_obj)
                
                # 3. Send email via NotificationAgent
                self.logger.info("Dispatching email report to %s...", sup_email)
                self.notifier.send_supervisor_report(
                    supervisor_email=sup_email,
                    md_content=markdown_content
                )
                if self.db:
                    self.db.log_agent_run(
                        agent_name=self.agent_name,
                        project_name=sup_email,
                        status="SUCCESS",
                        finished_at=datetime.now().isoformat()
                    )

        except Exception as e:
            self.logger.error("SupervisorStatusAgent encountered a critical error: %s", str(e), exc_info=True)
            try:
                self.notifier.send_admin_alert(
                    subject="SupervisorStatusAgent — Critical Failure",
                    message=(
                        f"SupervisorStatusAgent encountered a critical error and could not "
                        f"complete the weekly report cycle.\n\nError: {str(e)}"
                    )
                )
            except Exception:
                pass  # Do not let alert failure mask the original error

        self.logger.info("Supervisor Status cycle completed.")