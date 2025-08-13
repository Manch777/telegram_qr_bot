from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, CallbackQuery, FSInputFile
from aiogram.types import WebAppData, BotCommand, BotCommandScopeChat
from qr_generator import generate_qr
from database import (
    add_user, update_status, get_status,
    get_paid_status, set_paid_status,
    count_registered, count_activated,
    get_registered_users, get_paid_users,
    clear_database, mark_as_paid, count_paid,
    get_ticket_type
)
from config import SCAN_WEBAPP_URL, ADMIN_IDS, CHANNEL_ID, PAYMENT_LINK
from openpyxl import Workbook

router = Router()

# Админ-панель
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

# Обработка данных из WebApp сканера
@router.message(lambda msg: msg.web_app_data is not None)
async def handle_webapp_data(message: Message):
    try:
        data = message.web_app_data.data.strip()
        user_id_str, ticket_type = data.split(":")
        user_id = int(user_id_str)

        status = await get_status(user_id)
        if status is None:
            await message.answer(f"❌ QR-код не найден.\nТип билета: {ticket_type}")
        elif status == "не активирован":
            await update_status(user_id, "активирован")
            await message.answer(f"✅ Пропуск активирован. Удачного мероприятия!\nТип билета: {ticket_type}")
        else:
            await message.answer(f"⚠️ Этот QR-код уже был использован.\nТип билета: {ticket_type}")
    except Exception:
        await message.answer("⚠️ Ошибка чтения QR-кода.")

# Текстовая команда для сканирования (если сканер выдаёт текст)
@router.message(F.text.startswith("QR:"))
async def process_qr_scan_text(message: Message):
    try:
        data = message.text.replace("QR:", "").strip()
        user_id_str, ticket_type = data.split(":")
        user_id = int(user_id_str)

        paid_status = await get_paid_status(user_id)
        current_status = await get_status(user_id)

        if paid_status != "оплатил":
            await message.answer(f"❌ Билет не оплачен.\nТип билета: {ticket_type}")
            return

        if current_status == "активирован":
            await message.answer(f"⚠️ Билет уже использован!\nТип билета: {ticket_type}")
            return

        await update_status(user_id, "активирован")
        await message.answer(f"✅ Проход разрешён!\nТип билета: {ticket_type}")

    except Exception:
        await message.answer("⚠️ Ошибка чтения QR-кода.")

# Статистика
@router.message(lambda msg: msg.text == "/report")
async def report(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для этой команды.")
        return

    total = await count_registered()
    active = await count_activated()
    inactive = total - active
    chat_count = await message.bot.get_chat_member_count(CHANNEL_ID)
    paid_count = await count_paid()
        
    await message.answer(
        f"📊 Статистика:\n"
        f"👥 Подписчиков в канале: {chat_count}\n"
        f"👤 Зарегистрировались в боте: {total}\n"
        f"💰 Оплатили: {paid_count}\n"
        f"✅ Пришли: {active}\n"
        f"❌ Не пришли: {inactive}"
    )

# Список всех пользователей
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

    if len(text) > 4000:
        with open("registered_users.txt", "w", encoding="utf-8") as f:
            f.write(text)
        file = FSInputFile("registered_users.txt")
        await message.answer_document(file, caption="📄 Список пользователей")
    else:
        await message.answer(text)

# Выход из режима админа
@router.message(lambda msg: msg.text == "/exit_admin")
async def exit_admin_mode(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=message.from_user.id))
    await message.bot.set_my_commands([
        BotCommand(command="start", description="Получить QR"),
        BotCommand(command="help", description="ℹ️ Помощь / Связь с админом"),
    ])

    await message.answer("↩️ Вы вышли из режима администратора. Команды обновлены.")

# Открыть сканер
@router.message(lambda msg: msg.text == "/scanner")
async def scanner_command(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📷 Открыть сканер", url=SCAN_WEBAPP_URL)]
    ])
    await message.answer("Сканируйте QR-код участника:", reply_markup=keyboard)

# Подтверждение оплаты
@router.callback_query(F.data.startswith("approve:"))
async def approve_payment(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    ticket_type = await get_ticket_type(user_id) or "обычный"

    await mark_as_paid(user_id)
    await update_status(user_id, "не активирован")
    qr_buffer = await generate_qr(user_id, ticket_type)
    qr_file = FSInputFile(qr_buffer, filename="ticket.png")

    await callback.bot.send_photo(
        chat_id=user_id,
        photo=qr_file,
        caption=f"🎉 Оплата подтверждена! Вот ваш QR-код.\nТип билета: {ticket_type}"
    )

    await callback.message.edit_text(f"✅ Оплата подтверждена, QR отправлен пользователю.\nТип билета: {ticket_type}")

# Отклонение оплаты
@router.callback_query(F.data.startswith("reject:"))
async def reject_payment(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    await set_paid_status(user_id, "не оплатил")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=PAYMENT_LINK)],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid:{user_id}")]
    ])
    await callback.bot.send_message(
        chat_id=user_id,
        text="🚫 Оплата не подтверждена. Проверьте платёж или свяжитесь с администратором.",
        reply_markup=kb
    )

    await callback.message.edit_text("❌ Оплата отклонена. Пользователь уведомлён.")

# Оплатившие пользователи
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
        file = FSInputFile("paid_users.txt")
        await message.answer_document(file, caption="💰 Список оплативших")
    else:
        await message.answer(text)

# Очистка базы
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
    PASSWORD = "12345"
    if message.text == PASSWORD:
        await clear_database()
        await message.answer("✅ База данных успешно очищена.")
    else:
        await message.answer("❌ Неверный пароль. Доступ запрещён.")
    await state.clear()
