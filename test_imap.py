import imaplib
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# We use the official University email and the NEW App Password
EMAIL = os.getenv("OVERLEAF_EMAIL") # aviv.peretz1@msmail.ariel.ac.il
PASSWORD = os.getenv("UNIVERSITY_APP_PASSWORD")
IMAP_SERVER = "outlook.office365.com"

def test_imap_connection():
    print(f"🔄 Attempting to connect to {IMAP_SERVER}...")
    print(f"👤 User: {EMAIL}")
    
    if not PASSWORD:
        print("\n❌ ERROR: UNIVERSITY_APP_PASSWORD is missing in .env file!")
        return

    try:
        # 1. Connect to the server securely
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993)
        
        # 2. Attempt login
        mail.login(EMAIL, PASSWORD)
        print("\n✅ SUCCESS: Logged in to the University email via IMAP!")
        
        # 3. Attempt to access the INBOX
        status, messages = mail.select("INBOX")
        if status == "OK":
            email_count = messages[0].decode()
            print(f"✅ SUCCESS: Accessed INBOX. Found {email_count} total emails.")
        else:
            print(f"⚠️ WARNING: Logged in, but couldn't select INBOX. Status: {status}")
            
        # 4. Safely logout
        mail.logout()
        print("🔌 Connection closed safely.")
        
    except imaplib.IMAP4.error as e:
        print(f"\n❌ IMAP ERROR: Authentication failed or IMAP is disabled for this account.")
        print(f"Server response: {e}")
    except Exception as e:
        print(f"\n❌ GENERAL ERROR: {e}")

if __name__ == "__main__":
    test_imap_connection()