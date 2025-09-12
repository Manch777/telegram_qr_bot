import os
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import BotCommand, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest
from config import BOT_TOKEN, WEBHOOK_URL
from database import connect_db, disconnect_db, get_status, update_status, get_status_by_id, update_status_by_id, get_row, get_ticket_type
from handlers import user, admin

WEBHOOK_PATH = "/webhook"
FULL_WEBHOOK_URL = WEBHOOK_URL + WEBHOOK_PATH

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
dp.message.register(deep_link_start_handler, F.text.startswith("/start ") & F.text.len() > 7)


async def on_startup(app: web.Application):
    await connect_db()

    try:
        info = await bot.get_webhook_info(request_timeout=20)
        need_set = (info.url or "") != FULL_WEBHOOK_URL
    except TelegramNetworkError as e:
        # не получилось узнать — попробуем один раз поставить вебхук
        print(f"[WARN] get_webhook_info timeout: {e}. Will try set_webhook anyway.")
        need_set = True

    if need_set:
        for attempt in range(3):
            try:
                await bot.set_webhook(
                    FULL_WEBHOOK_URL,
                    allowed_updates=["message", "callback_query", "channel_post"],
                    request_timeout=60,   # ↑ больше таймаут
                )
                print("✅ Webhook set")
                break
            except TelegramNetworkError as e:
                print(f"[WARN] set_webhook timeout (try {attempt+1}/3): {e}")
                if attempt == 2:
                    print("[WARN] Continue startup without resetting webhook.")
            except TelegramBadRequest as e:
                # Обычно сюда попадают ошибки в URL (должен быть https, без лишних пробелов и т.п.)
                print(f"[ERROR] set_webhook bad request: {e}")
                # На таких ошибках действительно лучше не продолжать — они не «временные».
                raise

    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="help", description="ℹ️ Помощь / Связь с админом"),
    ])
    print("✅ Бот запущен (webhook)")


async def on_shutdown(app: web.Application):
    try:
        await bot.delete_webhook(request_timeout=20)
    except TelegramNetworkError as e:
        print(f"[WARN] delete_webhook timeout: {e}")
    await disconnect_db()


async def healthcheck(request):
    return web.Response(text="OK")


def create_app():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Обработка webhook-запросов
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)

    # Для Render Healthcheck
    app.router.add_get("/healthcheck", healthcheck)
    return app


if __name__ == "__main__":
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
