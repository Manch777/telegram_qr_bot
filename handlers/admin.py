from aiogram import Router, F
import aiosqlite
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,CallbackQuery 
from aiogram.types import WebAppData
from qr_generator import generate_qr
from database import (
    add_user, update_status, get_status,
    get_paid_status, set_paid_status,
    count_registered, count_activated,
    get_registered_users, get_paid_users,
    clear_database, mark_as_paid
)
from config import SCAN_WEBAPP_URL, ADMIN_IDS, CHANNEL_ID, PAYMENT_LINK
from aiogram.types import BotCommand,BotCommandScopeChat, FSInputFile
from openpyxl import Workbook

router = Router()

@router.message(lambda msg: msg.text == "/admin")
async def admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет доступа к панели администратора.")
        return
    
    await message.bot.set_my_commands([
        BotCommand(command="report", description="📊 Статистика"),
        BotCommand(command="users", description="📋 Список пользователей"),
        BotCommand(command="scanner", description="📷 Открыть сканер"),
        BotCommand(command="paid_users", description="💰 Оплатившие пользователи"),
        BotCommand(command="clear_db", description="Очистить базу"),
        BotCommand(command="exit_admin", description="Вернуться в пользовательское меню"),
    ],
    scope={"type": "chat", "chat_id": message.from_user.id}
)

    await message.answer("🛡 Вы вошли в режим администратора.")

@router.message(lambda msg: msg.web_app_data is not None)
async def handle_webapp_data(message: Message):
    user_id = int(message.web_app_data.data.strip())
    status = await get_status(user_id)

    if status is None:
        await message.answer("❌ QR-код не найден.")
    elif status == "не активирован":
        await update_status(user_id, "активирован")
        await message.answer("✅ Пропуск активирован. Удачного мероприятия!")
    else:
        await message.answer("⚠️ Этот QR-код уже был использован.")

@router.message(lambda msg: msg.text == "/report")
async def report(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для этой команды.")
        return

    total = await count_registered()
    active = await count_activated()
    inactive = total - active
    chat_count = await message.bot.get_chat_member_count(CHANNEL_ID)

    # Новая строчка — считаем оплативших
    async with aiosqlite.connect("users.db") as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE paid = 'оплатил'")
        paid_count = (await cursor.fetchone())[0]
        
    await message.answer(
        f"📊 Статистика:\n"
        f"👥 Подписчиков в канале: {chat_count}\n"
        f"👤 Зарегистрировались в боте: {total}\n"
        f"💰 Оплатили: {paid_count}\n"
        f"✅ Пришли: {active}\n"
        f"❌ Не пришли: {inactive}"
    )
    
@router.message(lambda msg: msg.text == "/users")
async def list_users(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для этой команды.")
        return

    users = await get_registered_users()

    if not users:
        await message.answer("Пока никто не зарегистрировался.")
        return

    text = "📄 Зарегистрированные пользователи:\n\n"
    for user_id, username, paid, status in users:
        name = f"@{username}" if username else f"(id: {user_id})"
        text += f"{name} — {status}\n"

    # если список слишком длинный — отправим как файл
    if len(text) > 4000:
        with open("registered_users.txt", "w", encoding="utf-8") as f:
            f.write(text)

        from aiogram.types import FSInputFile
        file = FSInputFile("registered_users.txt")
        await message.answer_document(file, caption="📄 Список пользователей")
    else:
        await message.answer(text)

@router.message(lambda msg: msg.text == "/exit_admin")
async def exit_admin_mode(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    # Удаляем команды ТОЛЬКО для этого админа
    await message.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=message.from_user.id))

    # (опционально) — можешь восстановить глобальные, если нужно
    await message.bot.set_my_commands([
        BotCommand(command="start", description="Получить QR"),
        BotCommand(command="help", description="ℹ️ Помощь / Связь с админом"),
    ])

    await message.answer("↩️ Вы вышли из режима администратора. Команды обновлены.")

@router.message(lambda msg: msg.text == "/scanner")
async def scanner_command(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📷 Открыть сканер", url=SCAN_WEBAPP_URL)]
    ])
    await message.answer("Сканируйте QR-код участника:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("approve:"))
async def approve_payment(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])

    # Получаем username пользователя
    user = await callback.bot.get_chat(user_id)
    username = user.username or "Без ника"

    # Генерация QR
    await add_user(user_id, username)
    await mark_as_paid(user_id)
    await update_status(user_id, "не активирован")
    path = generate_qr(user_id)
    file = FSInputFile(path)

    # Отправить пользователю QR
    await callback.bot.send_photo(
        chat_id=user_id,
        photo=file,
        caption="🎉 Оплата подтверждена! Вот ваш QR-код."
    )

    await callback.message.edit_text("✅ Оплата подтверждена, QR отправлен пользователю.")    


@router.callback_query(F.data.startswith("reject:"))
async def reject_payment(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    await set_paid_status(user_id, "не оплатил")

    user = await callback.bot.get_chat(user_id)
    username = user.username or "Без ника"

    # Устанавливаем статус оплаты обратно в "не оплатил"
    await set_paid_status(user_id, "не оплатил")

    # Уведомляем пользователя
    await callback.bot.send_message(
        chat_id=user_id,
        text=(
            "🚫 Ваша оплата не была подтверждена.\n"
            "Пожалуйста, проверьте корректность платежа или свяжитесь с администратором: @Manch7"
        )
    )

    # Отправляем заново кнопки на оплату
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=PAYMENT_LINK)],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid:{user_id}")]
    ])
    await callback.bot.send_message(
        chat_id=user_id,
        text="✅ Подписка подтверждена.\nТеперь оплатите участие:",
        reply_markup=kb
    )

    # Подтверждаем админу
    await callback.message.edit_text("❌ Оплата отклонена. Пользователь уведомлён.")

@router.message(lambda msg: msg.text == "/paid_users")
async def list_paid_users(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для этой команды.")
        return

    users = await get_paid_users()

    if not users:
        await message.answer("❌ Пока никто не оплатил.")
        return

    text = "💰 Оплатившие пользователи:\n\n"
    for user_id, username, status, paid in users:
        name = f"@{username}" if username else f"(id: {user_id})"
        text += f"{name} — {paid}\n"

    if len(text) > 4000:
        with open("paid_users.txt", "w", encoding="utf-8") as f:
            f.write(text)
        from aiogram.types import FSInputFile
        file = FSInputFile("paid_users.txt")
        await message.answer_document(file, caption="💰 Список оплативших")
    else:
        await message.answer(text)

# Шаги подтверждения пароля
class ClearDBStates(StatesGroup):
    waiting_for_password = State()

@router.message(lambda msg: msg.text == "/clear_db")
async def start_clear_db(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для этой команды.")
        return

    await message.answer("❗️Введите пароль для очистки базы данных:")
    await state.set_state(ClearDBStates.waiting_for_password)

@router.message(ClearDBStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    PASSWORD = "12345"  # ← здесь задай свой секретный пароль

    if message.text == PASSWORD:
        await clear_database()
        await message.answer("✅ База данных успешно очищена.")
    else:
        await message.answer("❌ Неверный пароль. Доступ запрещён.")

    await state.clear()
