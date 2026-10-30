
import os
import smtplib
import ssl
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

host = os.getenv("SMTP_HOST", "smtp.gmail.com")
port = int(os.getenv("SMTP_PORT", "587"))
username = os.getenv("SMTP_USER")
password = os.getenv("SMTP_PASSWORD")
use_ssl = os.getenv("SMTP_SSL", "false").lower() == "true"

print("FoodShare SMTP Diagnostic")
print("-------------------------")
print("Host:", host)
print("Port:", port)
print("Username configured:", bool(username))
print("Password configured:", bool(password))

if not username or not password:
    print("FAILED: SMTP username or password is missing.")
    raise SystemExit(1)

try:
    if use_ssl:
        server = smtplib.SMTP_SSL(
            host,
            port,
            timeout=20,
            context=ssl.create_default_context()
        )
    else:
        server = smtplib.SMTP(host, port, timeout=20)

    with server:
        print("1. SMTP connection: SUCCESS")

        if not use_ssl:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()

        print("2. TLS connection: SUCCESS")

        server.login(username, password)
        print("3. Gmail authentication: SUCCESS")

    print("\nSMTP diagnostic completed successfully.")

except smtplib.SMTPAuthenticationError as e:
    print("FAILED: Gmail authentication rejected.")
    print("SMTP response:", e.smtp_code, e.smtp_error)

except Exception as e:
    print("FAILED:", type(e).__name__)
    print("Details:", str(e))
