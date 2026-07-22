"""
Demo helper: triggers the internal review fallback pipeline directly,
bypassing the Stanford paperreview.ai flow and its email/token wait.

Usage:
    python run_internal_review.py --project "Age of Information Minimization..."
"""
import argparse
from config import Config
from agents.research_enhancement_agent import ResearchEnhancementAgent
from agents.notification_agent import NotificationAgent
from utils.database_manager import DatabaseManager


def main():
    parser = argparse.ArgumentParser(description="Run internal peer review pipeline directly")
    parser.add_argument("--project", required=True, help="Exact Overleaf project name")
    args = parser.parse_args()

    Config.validate()
    db = DatabaseManager()
    notifier = NotificationAgent(db=db)

    agent = ResearchEnhancementAgent(
        overleaf_projects=[args.project],
        notifier=notifier,
        db=db,
    )

    print(f"\n🧠 Running internal peer review for: {args.project}\n")
    success = agent._run_internal_review(args.project)

    if success:
        import os
        safe_name = args.project.replace(" ", "_")
        save_path = os.path.join(Config.LIBRARY_DIR, "project_enhancement", safe_name, "stanford_tasks.md")
        print(f"\n✅ Internal review complete.")
        print(f"📄 Saved to: {save_path}")
        print("📧 Notification email sent to researcher.")
    else:
        print("\n❌ Internal review failed. Check that ingestion has already run for this project.")


if __name__ == "__main__":
    main()
