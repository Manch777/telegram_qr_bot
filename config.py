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
