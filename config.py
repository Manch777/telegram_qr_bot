import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
INSTAGRAM_LINK = os.getenv("INSTAGRAM_LINK")
SCAN_WEBAPP_URL = os.getenv("SCAN_WEBAPP_URL")
ADMIN_IDS = [int(id_) for id_ in os.getenv("ADMIN_IDS", "").split(",") if id_]
PAYMENT_LINK = os.getenv("PAYMENT_LINK")
POSTGRES_URL = os.getenv("POSTGRES_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
_raw_promocodes = os.getenv("PROMOCODES", "")
PROMOCODES = [c.strip().upper() for c in _raw_promocodes.split(",") if c.strip()]
EVENT_CODE = os.getenv("EVENT_CODE", "default_event")
EVENT_TITLE = os.getenv("EVENT_TITLE", EVENT_CODE)

# Пароль на смену события (можно тот же, что и для очистки БД)
ADMIN_EVENT_PASSWORD = os.getenv("ADMIN_EVENT_PASSWORD", "12345")
