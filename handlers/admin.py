from aiogram import Router, F
import config
import asyncio
from config import PAYMENTS_ADMIN_ID, SCANNER_ADMIN_IDS, INSTAGRAM_LINK
import re
from openpyxl import Workbook
from io import BytesIO
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
    get_status_by_id, update_status_by_id, get_status, update_status,
    # отчёты / списки
    count_registered, count_activated, count_paid,
    get_registered_users, get_paid_users,
    # обслуживание
    clear_database, get_unique_one_plus_one_attempters_for_event,
    get_all_subscribers, set_meta, get_meta, get_all_recipient_ids,
    set_one_plus_one_limit, get_one_plus_one_limit,
    count_one_plus_one_taken, remaining_one_plus_one_for_event,
    get_ticket_stats_grouped, get_ticket_stats_for_event,
    get_all_users_full,
)
from config import SCAN_WEBAPP_URL, ADMIN_IDS, CHANNEL_ID, PAYMENT_LINK, ADMIN_EVENT_PASSWORD

router = Router()


def is_full_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def is_scanner_admin(uid: int) -> bool:
    # сканер-доступ у сканер-админов и у полноценных админов
    return uid in SCANNER_ADMIN_IDS or uid in ADMIN_IDS

# =========================
# /admin — панель
# =========================
@router.message(lambda msg: msg.text == "/admin")
async def admin_panel(message: Message):
    uid = message.from_user.id

    if is_full_admin(uid):

        await message.bot.set_my_commands([
            BotCommand(command="report", description="📊 Статистика"),
            BotCommand(command="scanner", description="📷 Открыть сканер"),
            BotCommand(command="change_event", description="🔁 Сменить мероприятие"),
            BotCommand(command="broadcast_last", description="📣 Разослать последний пост"),  # <-- добавили
            BotCommand(command="wishers", description="📝 Кто хотел 1+1"),
            BotCommand(command="/stats_this", description="📊 Cтатистика о количестве проданных билетов"),
            BotCommand(command="export_users", description="📤 Выгрузить базу (все)"),
            BotCommand(command="export_users_this", description="📤 Выгрузить базу (текущее)"),
            BotCommand(command="clear_db", description="Очистить базу"),
            BotCommand(command="exit_admin", description="Вернуться в пользовательское меню"),
        ], scope={"type": "chat", "chat_id": message.from_user.id})

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📣 Разослать последний пост", callback_data="broadcast_last")]
        ])
        await message.answer("🛡 Режим администратора включён.")
        return
    
    if uid in SCANNER_ADMIN_IDS:
        # Только сканер
        await message.bot.set_my_commands([
            BotCommand(command="scanner", description="📷 Открыть сканер"),
            BotCommand(command="exit_admin", description="Вернуться в пользовательское меню"),
        ], scope={"type": "chat", "chat_id": uid})


        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📷 Открыть сканер", url=SCAN_WEBAPP_URL)]
        ])
        await message.answer("🛡 Режим сканера включён.", reply_markup=kb)
        return

    await message.answer("🚫 У вас нет доступа к панели администратора.")

# =========================
# Сканирование через WebApp
# Ожидаем payload вида "row_id:ticket_type"
# =========================
@router.message(lambda msg: msg.web_app_data is not None)
async def handle_webapp_data(message: Message):
    if not is_scanner_admin(message.from_user.id):
        await message.answer("🚫 Нет прав на сканирование.")
        return
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
# /export_users — выгрузить ВСЕ покупки в Excel
# /export_users_this — выгрузить покупки ТЕКУЩЕГО мероприятия
# =========================
@router.message(lambda m: m.text in ("/export_users", "/export_users_this"))
async def export_users_excel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для этой команды.")
        return

    only_this = (message.text == "/export_users_this")
    rows = await get_all_users_full(config.EVENT_CODE if only_this else None)
    if not rows:
        await message.answer("Данных нет.")
        return

    # Готовим Excel
    wb = Workbook()
    ws = wb.active
    ws.title = "users"

    # Шапка
    headers = [
        "id", "user_id", "username", "event_code",
        "ticket_type", "paid", "status", "purchase_date"
    ]
    ws.append(headers)

    # Данные
    for r in rows:
        ws.append([
            r["id"],
            r["user_id"],
            r["username"],
            r["event_code"],
            r["ticket_type"],
            r["paid"],
            r["status"],
            r["purchase_date"],  # это date из БД — openpyxl съест нормально
        ])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    fname = "users.xlsx" if not only_this else f"users_{config.EVENT_CODE}.xlsx"
    await message.answer_document(
        document=BufferedInputFile(buf.getvalue(), filename=fname),
        caption="📄 Выгрузка базы users"
    )
        
# =========================
# /stats — витрина продаж (только оплаченные)
# /stats_all — оплаченные + на проверке
# =========================

# По текущему мероприятию из config.EVENT_CODE
@router.message(lambda m: m.text == "/stats_this")
async def ticket_stats_this(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для этой команды.")
        return

    ev = config.EVENT_CODE
    rows = await get_ticket_stats_for_event(ev, paid_statuses=("оплатил",))
    if not rows:
        await message.answer(f"Для «{ev}» оплаченных билетов нет.")
        return

    total = sum(int(r["count"]) for r in rows)
    parts = [f"📊 «{ev}»: только оплаченные", ""]
    for r in rows:
        parts.append(f"• {r['ticket_type']}: {int(r['count'])}")
    parts.append("")
    parts.append(f"ИТОГО: {total}")

    await message.answer("\n".join(parts))


# =========================
# /exit_admin — выйти из режима админа
# =========================
@router.message(lambda msg: msg.text == "/exit_admin")
async def exit_admin_mode(message: Message):
    if not is_scanner_admin(message.from_user.id):
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
    if not is_scanner_admin(message.from_user.id):
        await message.answer("🚫 Нет прав на использование сканера.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📷 Открыть сканер", url=SCAN_WEBAPP_URL)]
    ])
    await message.answer("Сканируйте QR-код участника:", reply_markup=keyboard)    

    
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
            f"Ваш билет №{row_id}\n"
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
    sent = await callback.bot.send_message(
        chat_id=row["user_id"],
        text=(
            "🚫 Оплата не подтверждена.\n"
            "Проверьте платёж или свяжитесь с администратором.\n\n"
            "Если всё исправили — нажмите «Я оплатил»."
        ),
        reply_markup=kb
    )
    
    # ⏱️ Запускаем новый 5-минутный таймер после отклонения
    asyncio.create_task(
        _expire_payment_after_admin(
            bot=callback.bot,
            chat_id=row["user_id"],
            message_id=sent.message_id,
            row_id=row_id,
            timeout_sec=300  # 5 минут
        )
    )
    
    await callback.message.edit_text(f"❌ Оплата по билету #{row_id} отклонена. Пользователь уведомлён.")



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
    waiting_for_event_name = State()
    waiting_for_1p1_limit = State()   # <— новое состояние

def _normalize_event_name(raw: str) -> str:
    # Прибираем лишние пробелы, убираем перевод строки по краям
    return " ".join((raw or "").strip().split())

@router.message(lambda msg: msg.text == "/change_event")
async def change_event_command(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет доступа к панели администратора.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Сменить мероприятие", callback_data="change_event")],
    ])
    await message.answer(
        f"Текущее мероприятие: {config.EVENT_CODE}",
        reply_markup=kb
    )

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

    await state.set_state(ChangeEventStates.waiting_for_event_name)
    await message.answer("✍️ Введите *название мероприятия* (видно пользователям).", parse_mode="Markdown")

@router.message(ChangeEventStates.waiting_for_event_name)
async def change_event_set_name(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return

    title = _normalize_event_name(message.text)
    if not title:
        await message.answer("⚠️ Пустое название. Введите ещё раз или /admin для отмены.")
        return

    old = (config.EVENT_CODE or "").strip().lower()
    new = (title or "").strip()

    # Меняем активное событие "на лету"
    config.EVENT_CODE = new


    # Сохраним во FSM, нужно ли потом делать рассылку
    await state.update_data(
        _broadcast_needed=(old == "none" and new.strip().lower() != "none"),
        _new_event_code=new
    )

    # Переходим к вводу лимита 1+1
    await state.set_state(ChangeEventStates.waiting_for_1p1_limit)
    await message.answer(
        "Введите число — сколько билетов *1+1* доступно на это мероприятие?\n"
        "_0 — отключить 1+1; положительное число — разрешить._",
        parse_mode="Markdown",
    )

@router.message(ChangeEventStates.waiting_for_1p1_limit)
async def change_event_set_limit(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return

    raw = (message.text or "").strip()
    try:
        qty = int(raw)
        if qty < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите целое число ≥ 0 (например: 0, 3, 10).")
        return

    # Сохраняем лимит для текущего мероприятия
    await set_one_plus_one_limit(config.EVENT_CODE, qty)
    used = await count_one_plus_one_taken(config.EVENT_CODE)
    left = max(qty - used, 0)

    data = await state.get_data()
    await state.clear()

    await message.answer(
        "✅ Мероприятие обновлено!\n"
        f"Текущее: {config.EVENT_CODE}\n"
        f"Лимит 1+1: {qty}\n"
        f"Уже занято: {used}\n"
        f"Осталось: {left}"
    )

    # Если раньше было none → стало не none — запускаем рассылку сейчас
    if data.get("_broadcast_needed"):
        await message.answer("📣 Делаю рассылку подписчикам о новом мероприятии…")
        # _broadcast_new_event(bot, event_code) — оставь твою реализацию
        asyncio.create_task(_broadcast_new_event(message.bot, config.EVENT_CODE))



# =========================
# Счётчик желающих 1+1
# =========================

@router.message(lambda msg: msg.text == "/wishers")
async def list_1plus1_wishers(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для этой команды.")
        return

    rows = await get_unique_one_plus_one_attempters_for_event(config.EVENT_CODE)
    if not rows:
        await message.answer("Пока никто не пытался купить 1+1 после исчерпания лимита.")
        return

    lines = ["📝 Кто хотел 1+1, но не успел (уникальные пользователи):\n"]
    for r in rows:
        uid = r["user_id"]
        uname = r["username"] or f"id:{uid}"
        when = r["last_try"].strftime("%Y-%m-%d %H:%M")
        lines.append(f"• @{uname} (id:{uid}) — {when}")

    text = "\n".join(lines)
    # если вдруг получится очень длинно — отправим файлом
    if len(text) > 4000:
        with open("wishers_1plus1.txt", "w", encoding="utf-8") as f:
            f.write(text)
        await message.answer_document(FSInputFile("wishers_1plus1.txt"), caption="📝 Список желающих 1+1")
    else:
        await message.answer(text)


# =========================
# Локальный хелпер таймера
# =========================

async def _expire_payment_after_admin(bot, chat_id: int, message_id: int, row_id: int, timeout_sec: int = 300):
    await asyncio.sleep(timeout_sec)

    from database import get_paid_status_by_id
    status = await get_paid_status_by_id(row_id)

    if status in ("не оплатил", "отклонено"):
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except Exception:
            pass

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎫 Билет 1+1", callback_data="ticket_1plus1")],
            [InlineKeyboardButton(text="🎫 1 билет", callback_data="ticket_single")],
            [InlineKeyboardButton(text="🎟 У меня есть промокод", callback_data="ticket_promocode")]
        ])
        await bot.send_message(
            chat_id,
            "⏰ Время оплаты истекло.\nВыберите тип билета заново:",
            reply_markup=kb
        )


# =========================
# Хелпер для рассылки:
# =========================

async def _broadcast_new_event(bot, event_title: str):
    subs = await get_all_subscribers()
    if not subs:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подписаться на Telegram", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
        [InlineKeyboardButton(text="📷 Подписаться на Instagram", url=INSTAGRAM_LINK)],
        [InlineKeyboardButton(text="🎟 Оплатить билет", callback_data="buy_ticket_menu")]        
    ])
    text = (
        f"🔥 Новое мероприятие: {event_title}\n\n"
        "Билеты уже доступны — не забудь купить👇"
    )
    # Telegram: не чаще ~30 сообщений/сек. Пойдём мягко — 20/сек.
    for uid, _uname in subs:
        try:
            await bot.send_message(uid, text, reply_markup=kb)
            await asyncio.sleep(0.05)
        except Exception:
            # игнорируем блокировки и пр.
            await asyncio.sleep(0.05)



# =========================
# Рассылки поста:
# =========================

LAST_POST_KEY = "last_channel_post_id"

@router.channel_post()
async def remember_last_channel_post(msg: Message):
    # Поддерживаем @username и numeric id
    is_same_channel = False
    try:
        is_same_channel = (
            str(msg.chat.id) == str(CHANNEL_ID)
            or (msg.chat.username and ("@" + msg.chat.username).lower() == str(CHANNEL_ID).lower())
        )
    except Exception:
        pass
    if not is_same_channel:
        return

    await set_meta(LAST_POST_KEY, msg.message_id)

async def _broadcast_last_post(bot, reply_target):
    post_id = await get_meta(LAST_POST_KEY)
    if not post_id:
        await reply_target.answer(
            "⚠️ Я ещё не видел постов канала. "
            "Опубликуйте новый пост (бот должен быть админом канала), затем попробуйте снова."
        )
        return

    subs = await get_all_subscribers()
    if not subs:
        await reply_target.answer("Сейчас нет подписчиков для рассылки.")
        return

    sent, skipped = 0, 0
    for uid, _uname in subs:
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=CHANNEL_ID,    # может быть @username
                message_id=int(post_id)
            )
            sent += 1
        except Exception:
            skipped += 1

    await reply_target.answer(f"📣 Готово. Отправлено: {sent}, пропущено: {skipped}.")

@router.message(lambda m: m.text == "/broadcast_last")
async def broadcast_last_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await _broadcast_last_post(message.bot, message)

@router.callback_query(F.data == "broadcast_last")
async def broadcast_last_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав.", show_alert=True)
        return
    await _broadcast_last_post(callback.bot, callback.message)

