import os
from aiohttp import web
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import BotCommand, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest
from config import BOT_TOKEN, WEBHOOK_URL
from database import connect_db, disconnect_db, get_status, update_status, get_status_by_id, update_status_by_id, get_row, get_ticket_type
from handlers import user, admin
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest
WEBHOOK_PATH = "/webhook"
FULL_WEBHOOK_URL = (WEBHOOK_URL or "").rstrip("/") + WEBHOOK_PATH

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# /start <payload> — deep-link обработчик
# Поддерживаем:
#   - "R:<row_id>"
async def deep_link_start_handler(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        return

    payload = (parts[1] or "").strip()
    if not payload:
        await message.answer("❌ Недопустимый QR-код.")
        return

    # Совместимый парсинг: убираем префиксы и «хвост» после двоеточия
    p = payload
    if p.lower().startswith("qr:"):
        p = p[3:].lstrip()
    if p.lower().startswith("r:"):
        p = p[2:].lstrip()
    num_str = p.split(":", 1)[0]

    try:
        candidate = int(num_str)
    except ValueError:
        await message.answer("❌ Недопустимый QR-код.")
        return

    # 1) СТАРЫЙ QR: число = user_id (берём последнюю покупку пользователя)
    status = await get_status(candidate)
    if status is not None:
        ticket_type = await get_ticket_type(candidate) or "-"
        if status == "не активирован":
            await update_status(candidate, "активирован")
            await message.answer(f"✅ Пропуск активирован.\nТип билета: {ticket_type}")
        else:
            await message.answer(f"⚠️ Этот QR-код уже использован.\nТип билета: {ticket_type}")
        return

    # 2) НОВЫЙ QR: число = row_id (одна строка = один билет)
    row = await get_row(candidate)
    if row is None:
        await message.answer("❌ QR-код не найден.")
        return

    ticket_type = row["ticket_type"] or "-"
    status_by_id = row["status"]  # можно и await get_status_by_id(candidate)

    if status_by_id == "не активирован":
        await update_status_by_id(candidate, "активирован")
        await message.answer(f"✅ Пропуск активирован.\nТип билета: {ticket_type}")
    else:
        await message.answer(f"⚠️ Этот QR-код уже использован.\nТип билета: {ticket_type}")

# Регистрация роутеров (важен порядок: админ выше пользователя)
dp.include_router(admin.router)
dp.include_router(user.router)

# Регистрируем deep-link обработчик
dp.message.register(deep_link_start_handler, F.text.startswith("/start "))


async def _set_webhook_background():
    """Устанавливаем вебхук с несколькими ретраями в фоне."""
    max_attempts = 5
    delay_seconds = 2
    if not WEBHOOK_URL:
        print("[ERROR] WEBHOOK_URL is empty; cannot set webhook. Set ENV WEBHOOK_URL to public https base (e.g. https://<service>.onrender.com)")
        return
    print(f"[INIT] Target webhook URL: {FULL_WEBHOOK_URL}")
    for attempt in range(1, max_attempts + 1):
        try:
            info = await bot.get_webhook_info(request_timeout=10)
            print(f"[DEBUG] Telegram current webhook: '{info.url or ''}' (pending updates: {getattr(info, 'pending_update_count', 'n/a')})")
            if (info.url or "") != FULL_WEBHOOK_URL:
                await bot.set_webhook(
                    FULL_WEBHOOK_URL,
                    allowed_updates=["message", "callback_query", "channel_post"],
                    drop_pending_updates=False,
                    request_timeout=10,
                )
                print("✅ Webhook set", flush=True)
            else:
                print("ℹ️ Webhook already set", flush=True)
            return
        except (TelegramNetworkError, TelegramBadRequest) as e:
            print(f"[WARN] set_webhook attempt {attempt}/{max_attempts} failed: {e}", flush=True)
            if attempt < max_attempts:
                await asyncio.sleep(delay_seconds)
                delay_seconds = min(delay_seconds * 2, 30)
            else:
                print("[ERROR] Unable to set webhook after retries", flush=True)

async def on_startup(app: web.Application):
    # Если подлючение к БД может быть долгим — тоже можно вынести в фон:
    await connect_db()
    # Команды можно выставить быстро
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Начать"),
            BotCommand(command="help", description="ℹ️ Помощь / Связь с админом"),
        ])
    except Exception as e:
        print(f"[WARN] set_my_commands: {e}")

    # Критично: не ждём Telegram — запускаем фоном
    print(f"[INIT] WEBHOOK_URL base: '{WEBHOOK_URL}' | FULL: '{FULL_WEBHOOK_URL}'", flush=True)
    asyncio.create_task(_set_webhook_background())
    print("✅ Startup finished (server will bind now)", flush=True)

async def on_shutdown(app: web.Application):
    # Не мешаем корректному завершению из-за Telegram-вызовов
    try:
        await bot.delete_webhook(request_timeout=5)
    except TelegramNetworkError as e:
        print(f"[WARN] delete_webhook timeout: {e}")
    await disconnect_db()

async def healthcheck(request):
    return web.Response(text="OK")

async def root(request):
    return web.Response(text="Bot is up. See /healthcheck")

async def set_webhook_now(request):
    # ручной триггер установки вебхука
    asyncio.create_task(_set_webhook_background())
    return web.Response(text="Webhook setup triggered")

def create_app():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    app.router.add_get("/healthcheck", healthcheck)
    app.router.add_get("/", root)
    app.router.add_get("/set-webhook", set_webhook_now)
    return app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    print(f"🔊 Binding HTTP server on 0.0.0.0:{port}", flush=True)
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=port)
