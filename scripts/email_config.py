"""Centralized configuration for email delivery (claude-market-agents).

Pattern mirrors dividend-stock-screener/scripts/config.py.
"""

# Email defaults (override via SENDER_EMAIL env var or --recipient flag).
DEFAULT_RECIPIENT = "taku.saotome@gmail.com"
DEFAULT_SENDER = "taku.saotome@gmail.com"

# Gmail SMTP. Requires GMAIL_APP_PASSWORD env var (Google App Password,
# 16-char) on a 2FA-enabled Google account. Generate at:
# https://myaccount.google.com/apppasswords
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
