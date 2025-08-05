import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.types import BotCommand, Message

from config import BOT_TOKEN
#from database import init_db, get_status, update_status
#from database import database  # подключаем объект database
from database import connect_db, disconnect_db, get_status, update_status
from handlers import user, admin



async def deep_link_start_handler(message: Message):
    parts = message.text.split()

    if len(parts) == 2:
        qr_code = parts[1]

        try:
            user_id = int(qr_code)
        except ValueError:
            await message.answer("❌ Недопустимый QR-код.")
            return

        status = await get_status(user_id)

        if status is None:
            await message.answer("❌ QR-код не найден.")
        elif status == "не активирован":
            await update_status(user_id, "активирован")
            await message.answer("✅ Пропуск активирован. Добро пожаловать!")
        else:
            await message.answer("⚠️ Этот QR-код уже использован.")
    # ВАЖНО: не обрабатываем здесь обычный /start — пусть он идёт в user.py
    # else:
    #     await message.answer("Привет! Используй /admin для сканирования QR-кодов.")


async def main():

    await connect_db()
    
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # подключаем модули
    dp.include_router(user.router)
    dp.include_router(admin.router)

    # обрабатываем только /start <код> — а не обычный /start
    dp.message.register(deep_link_start_handler, F.text.startswith("/start ") & F.text.len() > 7)

    # инициализация
    os.makedirs("qrs", exist_ok=True)

    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="help", description="ℹ️ Помощь / Связь с админом"),

    ])

    print("✅ Бот запущен")
    #await dp.start_polling(bot)
    await disconnect_db()

if __name__ == "__main__":
    asyncio.run(main())
