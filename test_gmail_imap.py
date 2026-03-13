import imaplib
import os
from dotenv import load_dotenv

load_dotenv()

# We use the Dummy Gmail account credentials that we already set up for SMTP
EMAIL = os.getenv("NOTIFICATION_SENDER_EMAIL") # personaltester329@gmail.com
PASSWORD = os.getenv("NOTIFICATION_SENDER_PASSWORD")
IMAP_SERVER = "imap.gmail.com"

def test_gmail_imap():
    print(f"🔄 Attempting to connect to {IMAP_SERVER}...")
    print(f"👤 User: {EMAIL}")
    
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993)
        mail.login(EMAIL, PASSWORD)
        print("\n✅ SUCCESS: Logged in to the Gmail dummy account via IMAP!")
        
        status, messages = mail.select("INBOX")
        if status == "OK":
            email_count = messages[0].decode()
            print(f"✅ SUCCESS: Accessed INBOX. Found {email_count} total emails.")
        
        mail.logout()
        print("🔌 Connection closed safely.")
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")

if __name__ == "__main__":
    test_gmail_imap()