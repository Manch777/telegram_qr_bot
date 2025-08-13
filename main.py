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
    payload = message.text.split(maxsplit=1)[1].strip()

    # Совместимость: допускаем префиксы "QR:" и "R:"
    p = payload
    if p.lower().startswith("qr:"):
        p = p[3:].lstrip()
    if p.lower().startswith("r:"):
        p = p[2:].lstrip()

    # Берём число ДО первого двоеточия
    number_part = p.split(":", 1)[0]
    try:
        num = int(number_part)
    except ValueError:
        await message.answer("❌ Недопустимый QR-код.")
        return

    # 1) Сначала пробуем как row_id (новый формат)
    row = await get_row(num)
    if row:
        paid = row["paid"]
        status = row["status"]

        if paid != "оплатил":
            await message.answer("❌ Билет не оплачен.")
            return

        if status == "активирован":
            await message.answer("⚠️ Этот билет уже использован.")
            return

        await update_status_by_id(num, "активирован")
        await message.answer("✅ Пропуск активирован. Добро пожаловать!")
        return

    # 2) Иначе — пробуем как старый формат (user_id)
    legacy_status = await get_status(num)
    if legacy_status is None:
        await message.answer("❌ QR-код не найден.")
    elif legacy_status == "не активирован":
        await update_status(num, "активирован")
        await message.answer("✅ Пропуск активирован. Добро пожаловать!")
    else:
        await message.answer("⚠️ Этот QR-код уже был использован.")

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
