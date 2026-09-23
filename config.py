import os

from dotenv import load_dotenv


load_dotenv()


def _parse_ids(value: str | None) -> list[int]:
    """Convert a comma-separated string of Telegram IDs to a list of integers."""
    if not value:
        return []

    return [
        int(item)
        for item in value.replace(" ", "").split(",")
        if item
    ]


def _get_required_env(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set."
        )

    return value


# Telegram
BOT_TOKEN = _get_required_env("BOT_TOKEN")

CHANNEL_ID = os.getenv("CHANNEL_ID")

ADMIN_IDS = _parse_ids(os.getenv("ADMIN_IDS"))
SCANNER_ADMIN_IDS = _parse_ids(os.getenv("SCANNER_ADMIN_IDS"))

payments_admin_id = os.getenv("PAYMENTS_ADMIN_ID")
PAYMENTS_ADMIN_ID = int(payments_admin_id) if payments_admin_id else None

ADMIN_CONTACT = os.getenv("ADMIN_CONTACT")


# Web application and webhook
WEBHOOK_URL = os.getenv(
    "WEBHOOK_URL",
    "https://telegram-qr-bot-6hs0.onrender.com",
)

SCAN_WEBAPP_URL = os.getenv("SCAN_WEBAPP_URL")
INSTAGRAM_LINK = os.getenv("INSTAGRAM_LINK")


# Database
POSTGRES_URL = _get_required_env("POSTGRES_URL")


# Payments and promo codes
PAYMENT_LINK = os.getenv("PAYMENT_LINK")
SBP_PAYMENT_DETAILS = os.getenv("SBP_PAYMENT_DETAILS")

_raw_promocodes = os.getenv("PROMOCODES", "")
PROMOCODES = [
    code.strip().upper()
    for code in _raw_promocodes.split(",")
    if code.strip()
]


# Event configuration
EVENT_CODE = os.getenv("EVENT_CODE", "default_event")
EVENT_TITLE = os.getenv("EVENT_TITLE", EVENT_CODE)

ADMIN_EVENT_PASSWORD = _get_required_env("ADMIN_EVENT_PASSWORD")
ADMIN_BROADCAST_PASSWORD = os.getenv(
    "ADMIN_BROADCAST_PASSWORD",
    ADMIN_EVENT_PASSWORD,
)
