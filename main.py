import os
from aiohttp import web
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import BotCommand, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest
from aiogram.types.error_event import ErrorEvent
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

# Глобальный лог ошибок aiogram
@dp.error()
async def _on_error(event: ErrorEvent):
    try:
        print(f"[ERROR] Handler exception: {event.exception}")
        if getattr(event, "update", None):
            print(f"[ERROR] Update: {event.update}")
    except Exception as e:
        print(f"[ERROR] Failed to log error: {e}")

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
            print(f"[DEBUG] Telegram current webhook before: '{info.url or ''}' (pending updates: {getattr(info, 'pending_update_count', 'n/a')})")

            # Полный ресет: удаляем текущий вебхук (и подвисшие апдейты), затем ставим заново
            try:
                await bot.delete_webhook(drop_pending_updates=True, request_timeout=10)
                print("ℹ️ Webhook deleted (reset)", flush=True)
            except Exception as de:
                print(f"[WARN] delete_webhook failed: {de}", flush=True)

            # Всегда переустанавливаем вебхук (на случай рассинхронизации у Telegram)
            await bot.set_webhook(
                FULL_WEBHOOK_URL,
                allowed_updates=["message", "callback_query", "channel_post"],
                drop_pending_updates=True,
                request_timeout=10,
            )
            print("✅ Webhook set (forced)", flush=True)

            # Подтвердим
            info_after = await bot.get_webhook_info(request_timeout=10)
            print(f"[DEBUG] Telegram current webhook after: '{info_after.url or ''}' (pending updates: {getattr(info_after, 'pending_update_count', 'n/a')})")
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
    try:
        me = await bot.get_me(request_timeout=10)
        print(f"[INIT] Bot: @{getattr(me, 'username', '?')} (id={getattr(me, 'id', '?')})", flush=True)
        masked = (BOT_TOKEN[:10] + "…") if BOT_TOKEN else "(none)"
        print(f"[INIT] BOT_TOKEN prefix: {masked}", flush=True)
    except Exception as e:
        print(f"[WARN] get_me failed: {e}", flush=True)
    print(f"[INIT] WEBHOOK_URL base: '{WEBHOOK_URL}' | FULL: '{FULL_WEBHOOK_URL}'", flush=True)
    asyncio.create_task(_set_webhook_background())
    print("✅ Startup finished (server will bind now)", flush=True)

async def on_shutdown(app: web.Application):
    # Оставляем вебхук, чтобы URL не очищался между рестартами
    try:
        await disconnect_db()
    except Exception:
        pass

async def healthcheck(request):
    return web.Response(text="OK")

async def root(request):
    return web.Response(text="Bot is up. See /healthcheck")

async def set_webhook_now(request):
    # ручной триггер установки вебхука
    asyncio.create_task(_set_webhook_background())
    return web.Response(text="Webhook setup triggered")

async def diag(request):
    try:
        me = await bot.get_me(request_timeout=10)
        info = await bot.get_webhook_info(request_timeout=10)
        data = {
            "bot": {
                "id": getattr(me, "id", None),
                "username": getattr(me, "username", None),
            },
            "configured_full_url": FULL_WEBHOOK_URL,
            "telegram_webhook": {
                "url": getattr(info, "url", None),
                "pending": getattr(info, "pending_update_count", None),
                "ip_address": getattr(info, "ip_address", None),
                "last_error_message": getattr(info, "last_error_message", None),
            },
        }
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response(data)

def create_app():
    app = web.Application(middlewares=[request_logger])
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    app.router.add_get("/healthcheck", healthcheck)
    app.router.add_get("/", root)
    app.router.add_get("/set-webhook", set_webhook_now)
    app.router.add_get("/diag", diag)
    return app

@web.middleware
async def request_logger(request, handler):
    try:
        response = await handler(request)
        try:
            print(f"[HTTP] {request.method} {request.path} -> {response.status}")
        except Exception:
            pass
        return response
    except Exception as e:
        try:
            print(f"[HTTP][ERR] {request.method} {request.path}: {e}")
        except Exception:
            pass
        raise

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    print(f"🔊 Binding HTTP server on 0.0.0.0:{port}", flush=True)
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=port)
