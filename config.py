import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://telegram-qr-bot-6hs0.onrender.com")
CHANNEL_ID = os.getenv("CHANNEL_ID")
INSTAGRAM_LINK = os.getenv("INSTAGRAM_LINK")
SCAN_WEBAPP_URL = os.getenv("SCAN_WEBAPP_URL")

def _parse_ids(s: str):
    return [int(x) for x in (s or "").replace(" ", "").split(",") if x]

ADMIN_IDS = _parse_ids(os.getenv("ADMIN_IDS"))
SCANNER_ADMIN_IDS = _parse_ids(os.getenv("SCANNER_ADMIN_IDS"))

PAYMENTS_ADMIN_ID = int(os.getenv("PAYMENTS_ADMIN_ID", "0")) or None
PAYMENT_LINK = os.getenv("PAYMENT_LINK")
POSTGRES_URL = os.getenv("POSTGRES_URL")
_raw_promocodes = os.getenv("PROMOCODES", "")
PROMOCODES = [c.strip().upper() for c in _raw_promocodes.split(",") if c.strip()]
EVENT_CODE = os.getenv("EVENT_CODE", "default_event")
EVENT_TITLE = os.getenv("EVENT_TITLE", EVENT_CODE)

# Пароль на смену события (можно тот же, что и для очистки БД)
ADMIN_EVENT_PASSWORD = os.getenv("ADMIN_EVENT_PASSWORD", "12345")

ADMIN_BROADCAST_PASSWORD = os.getenv("ADMIN_BROADCAST_PASSWORD", ADMIN_EVENT_PASSWORD)
