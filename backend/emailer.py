"""
Deliver one-time codes to a donor's inbox.

The delivery mode is decided by backend/.env, not by a code change:

smtp
    SMTP_HOST, SMTP_USER and SMTP_PASSWORD are set. The code is sent over TLS
    with the standard library, so no extra package is required.

demo
    Nothing is configured. No mail leaves the machine: the code is printed in
    the API console and handed back to the page so the sign-up and password
    reset flows can be demonstrated without a mailbox.

Port 465 uses implicit TLS. Any other port (587 by default) upgrades the
connection with STARTTLS.

Relevant settings::

    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=your.address@gmail.com
    SMTP_PASSWORD=your-app-password
    SMTP_FROM=your.address@gmail.com     # optional, defaults to SMTP_USER
    SMTP_FROM_NAME=FoodShare             # optional
    SMTP_SSL=false                       # true for port 465
"""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

BRAND_COLOUR = "#198754"
BRAND_INK = "#14261b"

SUBJECTS = {
    "signup": "Your FoodShare verification code",
    "reset": "Reset your FoodShare password",
}

HEADLINES = {
    "signup": "Confirm your email address",
    "reset": "Set a new password",
}

EXPLANATIONS = {
    "signup": (
        "Enter this code on the FoodShare registration page to finish "
        "creating your donor account."
    ),
    "reset": (
        "Enter this code on the FoodShare password reset page. If you did not "
        "ask for a reset, you can ignore this message; your password stays as "
        "it is."
    ),
}


class EmailDeliveryError(RuntimeError):
    """Raised when a configured SMTP server refuses the message."""


def _setting(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _port() -> int:
    try:
        return int(_setting("SMTP_PORT", "587") or 587)
    except ValueError:
        return 587


def _uses_ssl() -> bool:
    return _setting("SMTP_SSL").lower() in {"1", "true", "yes"}


def missing_settings() -> list:
    """Return the settings that stop real delivery from working.

    A host on its own is enough for a relay that needs no login (MailHog,
    a local test server). A username without a password, or the other way
    round, is always a mistake and is reported as missing.
    """
    user = _setting("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD", "").strip()
    missing = []

    if not _setting("SMTP_HOST"):
        missing.append("SMTP_HOST")

    if user and not password:
        missing.append("SMTP_PASSWORD")

    if password and not user:
        missing.append("SMTP_USER")

    return missing


def smtp_settings():
    """Return the SMTP configuration, or None when it cannot deliver mail."""
    if missing_settings():
        return None

    host = _setting("SMTP_HOST")

    return {
        "host": host,
        "port": _port(),
        "user": _setting("SMTP_USER"),
        "password": os.getenv("SMTP_PASSWORD", "").strip(),
        "sender": _setting("SMTP_FROM") or _setting("SMTP_USER") or f"no-reply@{host}",
        "sender_name": _setting("SMTP_FROM_NAME", "FoodShare"),
        "use_ssl": _uses_ssl(),
    }


def is_configured() -> bool:
    return not missing_settings()


def delivery_mode() -> str:
    """Return "smtp" when real mail is configured, otherwise "demo"."""
    return "smtp" if is_configured() else "demo"


def status() -> dict:
    """Describe delivery for the admin console, never the password itself."""
    missing = missing_settings()
    live = not missing
    host = _setting("SMTP_HOST")

    if live:
        reason = None
    elif host or len(missing) > 1:
        reason = "SMTP settings are incomplete: add " + ", ".join(missing)
    else:
        reason = (
            "No mail server is configured. Add SMTP_HOST, SMTP_USER and "
            "SMTP_PASSWORD to backend/.env and restart the API."
        )

    return {
        "mode": "smtp" if live else "demo",
        "host": host or None,
        "port": _port(),
        "sender": (_setting("SMTP_FROM") or _setting("SMTP_USER")) if live else None,
        "sender_name": _setting("SMTP_FROM_NAME", "FoodShare"),
        "transport": "TLS on port 465" if _uses_ssl() else "STARTTLS",
        "requires_login": bool(_setting("SMTP_USER")),
        "missing": missing,
        "partial": bool(host) and bool(missing),
        "reason": reason,
    }


def _plain_text(code: str, purpose: str, recipient_name: str, ttl_minutes: int) -> str:
    greeting = f"Hello {recipient_name}," if recipient_name else "Hello,"

    return "\n".join(
        [
            greeting,
            "",
            f"Your FoodShare verification code is: {code}",
            "",
            EXPLANATIONS.get(purpose, EXPLANATIONS["signup"]),
            f"The code expires in {ttl_minutes} minutes and can be used once.",
            "",
            "FoodShare - food donation and collection",
        ]
    )


def _html(code: str, purpose: str, recipient_name: str, ttl_minutes: int) -> str:
    greeting = f"Hello {recipient_name}," if recipient_name else "Hello,"
    headline = HEADLINES.get(purpose, HEADLINES["signup"])
    explanation = EXPLANATIONS.get(purpose, EXPLANATIONS["signup"])

    return f"""\
<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:24px;background:#f6faf7;font-family:system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;color:{BRAND_INK};">
  <div style="max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #dce7df;border-radius:14px;padding:28px;">
    <p style="margin:0 0 20px;font-size:15px;font-weight:700;color:{BRAND_COLOUR};letter-spacing:.02em;">FoodShare</p>
    <h1 style="margin:0 0 10px;font-size:20px;color:{BRAND_INK};">{headline}</h1>
    <p style="margin:0 0 18px;font-size:15px;color:#203027;">{greeting}</p>
    <p style="margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#68776e;">Your code</p>
    <p style="margin:0 0 20px;font-size:32px;font-weight:700;letter-spacing:.32em;color:{BRAND_INK};">{code}</p>
    <p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#203027;">{explanation}</p>
    <p style="margin:0 0 4px;font-size:15px;line-height:1.6;color:#203027;">The code expires in {ttl_minutes} minutes and can be used once.</p>
    <p style="margin:18px 0 0;font-size:12px;color:#68776e;border-top:1px solid #dce7df;padding-top:14px;">
      FoodShare - food donation and collection. This message was sent automatically; replies are not read.
    </p>
  </div>
</body>
</html>
"""


def _deliver(settings, message: EmailMessage) -> None:
    context = ssl.create_default_context()

    if settings["use_ssl"]:
        with smtplib.SMTP_SSL(
            settings["host"], settings["port"], context=context, timeout=20
        ) as server:
            if settings["user"] and settings["password"]:
                server.login(settings["user"], settings["password"])
            server.send_message(message)
        return

    with smtplib.SMTP(settings["host"], settings["port"], timeout=20) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        if settings["user"] and settings["password"]:
            server.login(settings["user"], settings["password"])
        server.send_message(message)


def _print_demo_code(to_email: str, code: str, purpose: str, ttl_minutes: int) -> None:
    label = {"signup": "sign-up", "reset": "password reset"}.get(purpose, purpose)

    print(
        "\n"
        "=====================================================================\n"
        " FoodShare demo mode - SMTP is not configured, so no email was sent.\n"
        f" {label} code for {to_email}: {code}\n"
        f" It expires in {ttl_minutes} minutes.\n"
        " Add SMTP_HOST, SMTP_USER and SMTP_PASSWORD to backend/.env to send\n"
        " this message for real.\n"
        "=====================================================================\n"
    )


def send_otp(
    to_email: str,
    code: str,
    purpose: str,
    recipient_name: str = None,
    ttl_minutes: int = 15,
) -> str:
    """Send a verification code and return the mode used ("smtp" or "demo").

    Raises EmailDeliveryError when SMTP is configured but the message could
    not be handed to the server, so the API can tell the caller instead of
    claiming a code is on its way.
    """
    settings = smtp_settings()

    if settings is None:
        _print_demo_code(to_email, code, purpose, ttl_minutes)
        return "demo"

    message = EmailMessage()
    message["Subject"] = SUBJECTS.get(purpose, SUBJECTS["signup"])
    message["From"] = formataddr((settings["sender_name"], settings["sender"]))
    message["To"] = to_email
    message.set_content(_plain_text(code, purpose, recipient_name, ttl_minutes))
    message.add_alternative(
        _html(code, purpose, recipient_name, ttl_minutes),
        subtype="html",
    )

    try:
        _deliver(settings, message)
    except Exception as exc:  # noqa: BLE001 - any SMTP failure must surface
        print(f"WARNING: could not send {purpose} code to {to_email}: {exc}")
        raise EmailDeliveryError(str(exc)) from exc

    print(f"{purpose} code emailed to {to_email}.")
    return "smtp"


def send_test(to_email: str) -> str:
    """Send a plain test message so an admin can confirm delivery works.

    Raises EmailDeliveryError both when SMTP is not configured and when the
    server refuses the message, so the caller has one failure path to handle.
    """
    settings = smtp_settings()

    if settings is None:
        raise EmailDeliveryError(
            "SMTP is not configured yet. Add SMTP_HOST, SMTP_USER and "
            "SMTP_PASSWORD to backend/.env, then restart the API."
        )

    message = EmailMessage()
    message["Subject"] = "FoodShare test message"
    message["From"] = formataddr((settings["sender_name"], settings["sender"]))
    message["To"] = to_email
    message.set_content(
        "\n".join(
            [
                "This is a test message from your FoodShare installation.",
                "",
                "If you are reading it in this mailbox, signup codes and",
                "password reset codes will reach donors too.",
                "",
                f"Sent through {settings['host']}:{settings['port']}.",
                "FoodShare - food donation and collection",
            ]
        )
    )

    try:
        _deliver(settings, message)
    except Exception as exc:  # noqa: BLE001 - surface the provider's reason
        print(f"WARNING: test message to {to_email} failed: {exc}")
        raise EmailDeliveryError(str(exc)) from exc

    print(f"Test message accepted by {settings['host']} for {to_email}.")
    return "smtp"
