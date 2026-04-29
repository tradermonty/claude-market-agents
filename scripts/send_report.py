"""Email sender for claude-market-agents reports / alerts.

Mirrors dividend-stock-screener/scripts/send_report.py but generalized:
the report file path is given via --report-html (rather than inferred from
a fixed naming convention), so the same script can deliver
earnings_trade, after-market, signal-generator, and executor outputs.
"""

import argparse
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

# Auto-load .env from project root before reading SENDER_EMAIL / GMAIL_APP_PASSWORD.
try:
    from dotenv import load_dotenv  # python-dotenv (already in pyproject deps)

    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass  # python-dotenv missing -> rely on shell env

# Local config (sibling file in scripts/).
sys.path.insert(0, os.path.dirname(__file__))
from email_config import DEFAULT_RECIPIENT, DEFAULT_SENDER, SMTP_HOST, SMTP_PORT


def _create_html_message(
    html_content: str, subject: str, sender: str, recipient: str
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(
        MIMEText(
            "This email contains an HTML report. Please view in an HTML-capable client.",
            "plain",
        )
    )
    msg.attach(MIMEText(html_content, "html"))
    return msg


def _create_text_message(
    text_content: str, subject: str, sender: str, recipient: str
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(text_content, "plain"))
    return msg


def _resolve_credentials(
    sender_email: Optional[str], sender_password: Optional[str]
) -> tuple[str, str]:
    sender_email = sender_email or os.getenv("SENDER_EMAIL") or DEFAULT_SENDER
    sender_password = sender_password or os.getenv("GMAIL_APP_PASSWORD")
    if not sender_password:
        raise ValueError(
            "GMAIL_APP_PASSWORD environment variable not set "
            "(generate at https://myaccount.google.com/apppasswords)"
        )
    if not sender_email:
        raise ValueError("SENDER_EMAIL environment variable not set and no default")
    return sender_email, sender_password


def _send(msg: MIMEMultipart, sender_email: str, sender_password: str, recipient: str) -> None:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient, msg.as_string())
    print(f"Email sent: {sender_email} -> {recipient} (subject: {msg['Subject']})")


def send_html_report(
    html_content: str,
    subject: str,
    recipient: str,
    sender_email: Optional[str] = None,
    sender_password: Optional[str] = None,
) -> None:
    """Send an HTML report email."""
    sender_email, sender_password = _resolve_credentials(sender_email, sender_password)
    msg = _create_html_message(html_content, subject, sender_email, recipient)
    _send(msg, sender_email, sender_password, recipient)


def send_text_alert(
    text_content: str,
    subject: str,
    recipient: str,
    sender_email: Optional[str] = None,
    sender_password: Optional[str] = None,
) -> None:
    """Send a plain-text alert email."""
    sender_email, sender_password = _resolve_credentials(sender_email, sender_password)
    msg = _create_text_message(text_content, subject, sender_email, recipient)
    _send(msg, sender_email, sender_password, recipient)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a claude-market-agents report or alert email via Gmail SMTP."
    )
    parser.add_argument(
        "--report-html",
        help="Path to HTML report file to attach as the email body. "
        "Mutually exclusive with --alert-text.",
    )
    parser.add_argument(
        "--alert-text",
        help="Plain-text alert body. Mutually exclusive with --report-html.",
    )
    parser.add_argument(
        "--subject",
        required=True,
        help="Email subject line (e.g. 'Earnings Trade Report - 2026-04-29').",
    )
    parser.add_argument(
        "--recipient",
        default=DEFAULT_RECIPIENT,
        help=f"Recipient email (default: {DEFAULT_RECIPIENT}).",
    )
    args = parser.parse_args()

    if bool(args.report_html) == bool(args.alert_text):
        parser.error("Specify exactly one of --report-html or --alert-text.")

    if args.alert_text:
        send_text_alert(args.alert_text, args.subject, args.recipient)
        return

    if not os.path.isfile(args.report_html):
        print(f"Report file not found: {args.report_html}", file=sys.stderr)
        sys.exit(1)
    with open(args.report_html, encoding="utf-8") as f:
        html_content = f.read()
    send_html_report(html_content, args.subject, args.recipient)


if __name__ == "__main__":
    main()
