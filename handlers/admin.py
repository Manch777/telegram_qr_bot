from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    FSInputFile, BufferedInputFile, BotCommand, BotCommandScopeChat
)
from qr_generator import generate_qr
from database import (
    # работа по row_id
    get_row, get_paid_status_by_id, set_paid_status_by_id,
    get_status_by_id, update_status_by_id,
    # отчёты / списки
    count_registered, count_activated, count_paid,
    get_registered_users, get_paid_users,
    # обслуживание
    clear_database,
)
from config import SCAN_WEBAPP_URL, ADMIN_IDS, CHANNEL_ID, PAYMENT_LINK, ADMIN_EVENT_PASSWORD

router = Router()

# =========================
# /admin — панель
# =========================
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
        BotCommand(command="change_event", description="🔁 Сменить мероприятие"),
        BotCommand(command="clear_db", description="Очистить базу"),
        BotCommand(command="exit_admin", description="Вернуться в пользовательское меню"),
    ], scope={"type": "chat", "chat_id": message.from_user.id})

    await message.answer("🛡 Вы вошли в режим администратора.")

# =========================
# Сканирование через WebApp
# Ожидаем payload вида "row_id:ticket_type"
# =========================
@router.message(lambda msg: msg.web_app_data is not None)
async def handle_webapp_data(message: Message):
    payload = (message.web_app_data.data or "").strip()
    if not payload:
        await message.answer("⚠️ Пустые данные из сканера.")
        return

    # 1) Старый формат (как раньше): в payload чистое число (user_id)
    if payload.isdigit():
        user_id = int(payload)
        status = await get_status(user_id)
        if status is None:
            await message.answer("❌ QR-код не найден.")
        elif status == "не активирован":
            await update_status(user_id, "активирован")
            await message.answer("✅ Пропуск активирован. Удачного мероприятия!")
        else:
            await message.answer("⚠️ Этот QR-код уже был использован.")
        return

    # 2) Совместимость с новым форматом: R:<row_id>, QR:<...>, <row_id>:что-угодно
    p = payload.lstrip()
    if p.lower().startswith("qr:"):
        p = p[3:].lstrip()
    if p.lower().startswith("r:"):
        p = p[2:].lstrip()

    num_str = p.split(":", 1)[0]
    try:
        candidate = int(num_str)
    except ValueError:
        await message.answer("⚠️ Неверный формат QR.")
        return

    # Сначала попробуем, как раньше, трактовать число как user_id (если сканер всё ещё шлёт user_id с префиксом)
    status = await get_status(candidate)
    if status is not None:
        if status == "не активирован":
            await update_status(candidate, "активирован")
            await message.answer("✅ Пропуск активирован. Удачного мероприятия!")
        else:
            await message.answer("⚠️ Этот QR-код уже был использован.")
        return

    # Иначе это row_id — новая схема (одна покупка = одна строка)
    row = await get_row(candidate)
    if row is None:
        await message.answer("❌ QR-код не найден.")
        return

    status_by_id = await get_status_by_id(candidate)
    if status_by_id == "не активирован":
        await update_status_by_id(candidate, "активирован")
        await message.answer("✅ Пропуск активирован. Удачного мероприятия!")
    else:
        await message.answer("⚠️ Этот QR-код уже был использован.")

# =========================
# /report — статистика
# =========================
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
        f"👤 Создано покупок: {total}\n"
        f"💰 Оплачено: {paid_count}\n"
        f"✅ Пришли: {active}\n"
        f"❌ Не пришли: {inactive}"
    )

# =========================
# /users — список всех записей
# =========================
@router.message(lambda msg: msg.text == "/users")
async def list_users(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для этой команды.")
        return

    users = await get_registered_users()
    if not users:
        await message.answer("Пока никто не зарегистрировался.")
        return

    text = "📄 Записи покупок:\n\n"
    for user_id, username, paid, status in users:
        name = f"@{username}" if username else f"(id: {user_id})"
        text += f"{name} — {status} / {paid}\n"

    if len(text) > 4000:
        with open("registered_users.txt", "w", encoding="utf-8") as f:
            f.write(text)
        file = FSInputFile("registered_users.txt")
        await message.answer_document(file, caption="📄 Список покупок")
    else:
        await message.answer(text)

# =========================
# /exit_admin — выйти из режима админа
# =========================
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

# =========================
# /scanner — открыть веб-сканер
# =========================
@router.message(lambda msg: msg.text == "/scanner")
async def scanner_command(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📷 Открыть сканер", url=SCAN_WEBAPP_URL)]
    ])
    await message.answer("Сканируйте QR-код участника:", reply_markup=keyboard)
    
# =========================
# /change_event — 🔁 Сменить мероприятие
# =========================
@router.message(lambda msg: msg.text == "/change_event")
async def scanner_command(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Сменить мероприятие", callback_data="change_event")],
    ])
    await message.answer(
        f"Текущее мероприятие:\n• code: {config.EVENT_CODE}\n• title: {getattr(config, 'EVENT_TITLE', config.EVENT_CODE)}",
        reply_markup=keyboard
    )
    
# =========================
# Подтверждение оплаты по row_id
# =========================
@router.callback_query(F.data.startswith("approve_row:"))
async def approve_payment(callback: CallbackQuery):
    await callback.answer("Обрабатываю…", show_alert=False)
    row_id = int(callback.data.split(":")[1])

    row = await get_row(row_id)
    if not row:
        await callback.message.edit_text("❌ Запись не найдена.")
        return

    # ставим оплату и генерим QR
    await set_paid_status_by_id(row_id, "оплатил")

    ticket_type = row["ticket_type"]
    event_code = row["event_code"] or "-"   # <-- вместо row.get(...)

    png_bytes = await generate_qr(row_id)
    photo = BufferedInputFile(png_bytes, filename=f"ticket_{row_id}.png")

    await callback.bot.send_photo(
        chat_id=row["user_id"],
        photo=photo,
        caption=(
            f"🎉 Оплата подтверждена!\n"
            f"Ваш билет #{row_id}\n"
            f"Тип: {ticket_type}\n"
            f"Мероприятие: {event_code}"
        )
    )

    await callback.message.edit_text(f"✅ Подтверждено. QR по билету #{row_id} отправлен пользователю.")

# =========================
# Отклонение оплаты по row_id
# =========================
@router.callback_query(F.data.startswith("reject_row:"))
async def reject_payment(callback: CallbackQuery):
    row_id = int(callback.data.split(":")[1])
    row = await get_row(row_id)
    if not row:
        await callback.message.edit_text("❌ Запись не найдена.")
        return

    await set_paid_status_by_id(row_id, "отклонено")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=PAYMENT_LINK)],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid_row:{row_id}")]
    ])
    await callback.bot.send_message(
        chat_id=row["user_id"],
        text=(
            "🚫 Оплата не подтверждена.\n"
            "Проверьте платёж или свяжитесь с администратором.\n\n"
            "Если всё исправили — нажмите «Я оплатил»."
        ),
        reply_markup=kb
    )

    await callback.message.edit_text(f"❌ Оплата по билету #{row_id} отклонена. Пользователь уведомлён.")

# =========================
# /paid_users — список оплаченных записей
# =========================
@router.message(lambda msg: msg.text == "/paid_users")
async def list_paid_users(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для этой команды.")
        return

    users = await get_paid_users()
    if not users:
        await message.answer("❌ Пока никто не оплатил.")
        return

    text = "💰 Оплаченные покупки:\n\n"
    for user_id, username, status, paid in users:
        name = f"@{username}" if username else f"(id: {user_id})"
        text += f"{name} — {paid} / {status}\n"

    if len(text) > 4000:
        with open("paid_users.txt", "w", encoding="utf-8") as f:
            f.write(text)
        file = FSInputFile("paid_users.txt")
        await message.answer_document(file, caption="💰 Список оплативших")
    else:
        await message.answer(text)

# =========================
# Очистка базы (с паролем)
# =========================
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
    PASSWORD = "12345"  # замени на свой
    if message.text == PASSWORD:
        await clear_database()
        await message.answer("✅ База данных успешно очищена.")
    else:
        await message.answer("❌ Неверный пароль. Доступ запрещён.")
    await state.clear()

# =========================
# FSM для смены мероприятия
# =========================

class ChangeEventStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_title = State()

def _slugify_title_as_code(title: str) -> str:
    # Делаем аккуратный code из произвольной строки (ASCII-only)
    code = re.sub(r"[^A-Za-z0-9_-]+", "-", title.strip())
    code = re.sub(r"-{2,}", "-", code).strip("-")
    if not code:
        from datetime import date
        code = f"event-{date.today().isoformat()}"
    return code.lower()

@router.callback_query(F.data == "change_event")
async def change_event_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.set_state(ChangeEventStates.waiting_for_password)
    await callback.message.answer("🔒 Введите пароль для смены мероприятия:")

@router.message(ChangeEventStates.waiting_for_password)
async def change_event_check_password(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    if (message.text or "").strip() != ADMIN_EVENT_PASSWORD:
        await message.answer("❌ Неверный пароль. Доступ запрещён.")
        await state.clear()
        return
    await state.set_state(ChangeEventStates.waiting_for_title)
    await message.answer("✍️ Введите *название* мероприятия (отображаемое).", parse_mode="Markdown")

@router.message(ChangeEventStates.waiting_for_title)
async def change_event_set_title(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return

    title = (message.text or "").strip()
    if not title:
        await message.answer("⚠️ Пустое название. Введите ещё раз или /admin для отмены.")
        return

    code = _slugify_title_as_code(title)

    # Меняем активное событие "на лету"
    config.EVENT_TITLE = title
    config.EVENT_CODE = code

    await state.clear()
    await message.answer(
        "✅ Мероприятие обновлено!\n"
        f"• code: `{code}`\n"
        f"• title: {title}\n\n"
        "Акция *1+1* снова доступна для этого мероприятия (лимит 5 оплаченных).",
        parse_mode="Markdown"
    )
