import os
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import BotCommand, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

from config import BOT_TOKEN, WEBHOOK_URL
from database import connect_db, disconnect_db, get_status_by_id, update_status_by_id
from handlers import user, admin

WEBHOOK_PATH = "/webhook"
FULL_WEBHOOK_URL = WEBHOOK_URL + WEBHOOK_PATH

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# /start <payload> — deep-link обработчик
# Поддерживаем:
#   - "R:<row_id>"
#   - "<row_id>"
#   - "<row_id>:<что-угодно>"
#   - "QR:R:<row_id>" или "QR:<row_id>[:...]"
async def deep_link_start_handler(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        return

    payload = parts[1].strip()

    # Снимаем возможный префикс "QR:"
    if payload.lower().startswith("qr:"):
        payload = payload[3:].lstrip()

    # Снимаем возможный префикс "R:"
    if payload.lower().startswith("r:"):
        payload = payload[2:].lstrip()

    # Берём только число до первого двоеточия (если формат "<row_id>:<...>")
    row_id_str = payload.split(":", 1)[0]

    try:
        row_id = int(row_id_str)
    except ValueError:
        await message.answer("❌ Недопустимый QR-код.")
        return

    status = await get_status_by_id(row_id)
    if status is None:
        await message.answer("❌ Билет не найден.")
    elif status == "не активирован":
        await update_status_by_id(row_id, "активирован")
        await message.answer("✅ Пропуск активирован. Добро пожаловать!")
    else:
        await message.answer("⚠️ Этот билет уже использован.")


# Регистрация роутеров (важен порядок: админ выше пользователя)
dp.include_router(admin.router)
dp.include_router(user.router)

# Регистрируем deep-link обработчик
dp.message.register(deep_link_start_handler, F.text.startswith("/start ") & F.text.len() > 7)


async def on_startup(app: web.Application):
    await connect_db()
    await bot.set_webhook(FULL_WEBHOOK_URL)
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="help", description="ℹ️ Помощь / Связь с админом"),
    ])
    print("✅ Бот запущен (webhook)")


async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
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
