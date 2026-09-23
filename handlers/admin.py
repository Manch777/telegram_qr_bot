from aiogram import Router, F
import config
import json
import asyncio
from config import (
    ADMIN_BROADCAST_PASSWORD,
    ADMIN_CONTACT,
    INSTAGRAM_LINK,
)
import re
from openpyxl import Workbook
from io import BytesIO
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter, Command
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
    get_all_users_full, has_role, add_role, remove_role, get_role_user_ids,
)
from config import SCAN_WEBAPP_URL, CHANNEL_ID, PAYMENT_LINK, ADMIN_EVENT_PASSWORD

router = Router()


async def is_full_admin(uid: int) -> bool:
    return await has_role(uid, "admin")

async def _can_use_scanner(uid: int) -> bool:
    # сканер-доступ у сканер-админов и у полноценных админов
    return await has_role(uid, "admin") or await has_role(uid, "scanner")


# =========================
# /admin — панель
# =========================
@router.message(lambda msg: msg.text == "/admin")
async def admin_panel(message: Message):
    uid = message.from_user.id

    if await is_full_admin(uid):
        # Группы команд для полноценных админов
        await message.bot.set_my_commands(
            [
                BotCommand(command="analytics", description="📊 Аналитическая сводка мероприятия"),
                BotCommand(command="event_tool_set", description="🛠 Управление мероприятием"),
                BotCommand(command="admin_tool_set", description="🧰 Администрирование"),
                BotCommand(command="exit_admin", description="↩️ Вернуться в пользовательское меню"),
            ],
            scope=BotCommandScopeChat(chat_id=uid),
        )
        await message.answer("🛡 Режим администратора включён. Выбери нужный набор команд в меню.")
        return

    elif await _can_use_scanner(uid):
        # Только сканер-доступ
        await message.bot.set_my_commands(
            [
                BotCommand(command="scanner", description="📷 Открыть сканер"),
                BotCommand(command="exit_admin", description="↩️ Вернуться в пользовательское меню"),
            ],
            scope=BotCommandScopeChat(chat_id=uid),
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📷 Открыть сканер", url=SCAN_WEBAPP_URL)]
        ])
        await message.answer("🛡 Режим сканера включён.", reply_markup=kb)
        return

    await message.answer("🚫 У вас нет доступа к панели администратора.")




def _kb_analytics() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="an:report")],
        [InlineKeyboardButton(text="📊 Проданные билеты (текущее)", callback_data="an:stats_this")],
        [InlineKeyboardButton(text="📝 Кто хотел 1+1", callback_data="an:wishers")],
        [InlineKeyboardButton(text="💰 Выручка", callback_data="an:revenue")],   # ← добавили
        [InlineKeyboardButton(text="📤 Выгрузить (текущее)", callback_data="an:export_this")],
        [InlineKeyboardButton(text="📤 Выгрузить (все)", callback_data="an:export_all")],
    ])

def _kb_event_tools() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Сменить мероприятие", callback_data="change_event_menu")],
        [InlineKeyboardButton(text="📣 Разослать последний пост", callback_data="broadcast_last")],
        [InlineKeyboardButton(text="📷 Открыть сканер", url=SCAN_WEBAPP_URL)],
    ])

def _kb_admin_tools() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔐 Управление доступом к сканеру", callback_data="scan_access_menu")],
        [InlineKeyboardButton(text="🧹 Очистить базу", callback_data="adm:clear_db")],
    ])


@router.message(lambda m: m.text == "/analytics")
async def admin_menu_analytics(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 Недостаточно прав.")
        return
    await message.answer("Выберите сводку:", reply_markup=_kb_analytics())

@router.message(lambda m: m.text == "/event_tool_set")
async def admin_menu_event_tools(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 Недостаточно прав.")
        return
    await message.answer("Инструменты мероприятия:", reply_markup=_kb_event_tools())

@router.message(lambda m: m.text == "/admin_tool_set")
async def admin_menu_admin_tools(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 Недостаточно прав.")
        return
    await message.answer("Администрирование:", reply_markup=_kb_admin_tools())

@router.callback_query(F.data == "an:report")
async def cb_an_report(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True); return
    await callback.answer()
    await _send_report_to(callback.bot, callback.from_user.id)

@router.callback_query(F.data == "an:stats_this")
async def cb_an_stats_this(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True); return
    await callback.answer()
    await _send_stats_this_to(callback.bot, callback.from_user.id)

@router.callback_query(F.data == "an:wishers")
async def cb_an_wishers(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True); return
    await callback.answer()
    await _send_wishers_to(callback.bot, callback.from_user.id)

@router.callback_query(F.data == "an:export_this")
async def cb_an_export_this(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True); return
    await callback.answer("Готовлю выгрузку…", show_alert=False)
    await _send_export_to(callback.bot, callback.from_user.id, only_this=True)

@router.callback_query(F.data == "an:export_all")
async def cb_an_export_all(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True); return
    await callback.answer("Готовлю выгрузку…", show_alert=False)
    await _send_export_to(callback.bot, callback.from_user.id, only_this=False)

    
@router.callback_query(F.data == "adm:clear_db")
async def cb_adm_clear_db(callback: CallbackQuery, state: FSMContext):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await callback.answer()
    await start_clear_db(callback.message, state)


# =========================
# Сканирование через WebApp
# Ожидаем payload вида "row_id:ticket_type"
# =========================
@router.message(lambda msg: msg.web_app_data is not None)
async def handle_webapp_data(message: Message):
    if not await _can_use_scanner(message.from_user.id):
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
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 У вас нет прав для этой команды.")
        return
    await _send_report_to(message.bot, message.chat.id)

# =========================
# /export_users — выгрузить ВСЕ покупки в Excel
# /export_users_this — выгрузить покупки ТЕКУЩЕГО мероприятия
# =========================
async def export_users_excel(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 У вас нет прав для этой команды.")
        return
    await _send_export_to(message.bot, message.chat.id, only_this=(message.text == "/export_users_this"))
        
# =========================
# /stats — витрина продаж (только оплаченные)
# /stats_all — оплаченные + на проверке
# =========================

# По текущему мероприятию из config.EVENT_CODE
@router.message(lambda m: m.text == "/stats_this")
async def ticket_stats_this(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 У вас нет прав для этой команды.")
        return
    await _send_stats_this_to(message.bot, message.chat.id)


# =========================
# /exit_admin — выйти из режима админа
# =========================
@router.message(lambda msg: msg.text == "/exit_admin")
async def exit_admin_mode(message: Message):
    uid = message.from_user.id

    # Сбрасываем команды для этого чата
    try:
        await message.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=uid))
    except Exception:
        pass

    # Базовые команды доступны всем
    cmds = [
        BotCommand(command="start", description="Начать"),
        BotCommand(command="help", description="ℹ️ Помощь / Связь с админом"),
    ]

    # «Админ»-кнопку даём только супер-админу или сканер-админу
    if await is_full_admin(uid) or await _can_use_scanner(uid):
        cmds.append(BotCommand(command="admin", description="🛡 Режим администратора"))

    await message.bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=uid))
    await message.answer("↩️ Вы вышли из режима администратора. Команды обновлены.")

    
# =========================
# /scanner — открыть веб-сканер
# =========================
@router.message(lambda msg: msg.text == "/scanner")
async def scanner_command(message: Message):
    if not await _can_use_scanner(message.from_user.id):
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
            f"🎉 Оплата подтверждена! Покажи этот QR на входе\n\n"
            f"Твой билет №{row_id}\n"
            f"Тип: {ticket_type}\n"
            f"Мероприятие: {event_code}\n\n"
            f"До встречи на тусовке 🫶"
        )
    )

    await callback.message.edit_text(f"✅ Подтверждено. QR по билету #{row_id} отправлен пользователю.")
    
    # Снимем «защиту» и удалим экран ожидания, если он ещё висит
    uid = row["user_id"]
    protected_id_raw = await get_meta(f"review_msg:{uid}")
    if protected_id_raw:
        try:
            await callback.bot.delete_message(uid, int(protected_id_raw))
        except Exception:
            pass
# очистим мету (сигнал, что защита снята)
    await set_meta(f"review_msg:{uid}", "")
    
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
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid_row:{row_id}")],
        [InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data=f"back_to_menu:{row_id}")],
    ])
    
    admin_contact_text = (
        f" или свяжись с администратором: {ADMIN_CONTACT}"
        if ADMIN_CONTACT
        else ""
    )

    sent = await callback.bot.send_message(
        chat_id=row["user_id"],
        text=(
            "💔 Упс! Кажется, оплата не прошла\n"
            "Пожалуйста, проверь правильность платежа"
            f"{admin_contact_text}"
        ),
        reply_markup=kb,
    )
    
    uid = row["user_id"]
    protected_id_raw = await get_meta(f"review_msg:{uid}")
    if protected_id_raw:
        try:
            await callback.bot.delete_message(uid, int(protected_id_raw))
        except Exception:
            pass
    await set_meta(f"review_msg:{uid}", "")
    
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
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 У вас нет прав для этой команды.")
        return

    await message.answer("❗️Введите пароль для очистки базы данных:")
    await state.set_state(ClearDBStates.waiting_for_password)

@router.message(ClearDBStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    from config import ADMIN_EVENT_PASSWORD
    if (message.text or "").strip() == (ADMIN_EVENT_PASSWORD or ""):
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
    waiting_for_price_1p1 = State()
    waiting_for_price_single = State()
    waiting_for_price_promocode = State()
    waiting_for_promocode_list = State()
    
def _normalize_event_name(raw: str) -> str:
    # Прибираем лишние пробелы, убираем перевод строки по краям
    return " ".join((raw or "").strip().split())


@router.callback_query(F.data == "change_event_menu")
async def change_event_menu_cb(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True); return
    await callback.message.answer(
        f"Текущее мероприятие: {config.EVENT_CODE}",
        reply_markup=_change_event_menu_kb()
    )


def _change_event_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Сменить мероприятие", callback_data="change_event")],
        [InlineKeyboardButton(text="🛑 Остановить продажи (нет мероприятия)", callback_data="event_off")],
    ])

@router.message(lambda msg: msg.text == "/change_event")
async def change_event_command(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 У вас нет доступа к панели администратора.")
        return

    await message.answer(
        f"Текущее мероприятие: {config.EVENT_CODE}",
        reply_markup=_change_event_menu_kb()
    )


@router.callback_query(F.data == "change_event")
async def change_event_start(callback: CallbackQuery, state: FSMContext):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.update_data(_mode="change")  # режим: смена на новое название
    await state.set_state(ChangeEventStates.waiting_for_password)
    await callback.message.answer("🔒 Введите пароль для смены мероприятия:")

@router.callback_query(F.data == "event_off")
async def event_off_start(callback: CallbackQuery, state: FSMContext):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.update_data(_mode="off")  # режим: выключить продажи (EVENT_CODE="none")
    await state.set_state(ChangeEventStates.waiting_for_password)
    await callback.message.answer("🔒 Введите пароль для отключения продаж (нет мероприятия):")


@router.message(ChangeEventStates.waiting_for_password)
async def change_event_check_password(message: Message, state: FSMContext):
    if not await is_full_admin(message.from_user.id):
        await state.clear()
        return
    if (message.text or "").strip() != ADMIN_EVENT_PASSWORD:
        await message.answer("❌ Неверный пароль. Доступ запрещён.")
        await state.clear()
        return

    data = await state.get_data()
    mode = data.get("_mode", "change")

    # Режим: выключить продажи — просто ставим EVENT_CODE = "none"
    if mode == "off":
        config.EVENT_CODE = "none"
        await set_meta("active_event_code", "none")
        await state.clear()
        await message.answer(
            "🛑 Продажи остановлены.\n"
            "Текущее мероприятие: none\n\n"
            "Покупка билетов пользователям недоступна."
        )
        return

    # Режим: сменить на новое название
    await state.set_state(ChangeEventStates.waiting_for_event_name)
    await message.answer("✍️ Введите *название мероприятия* (видно пользователям).", parse_mode="Markdown")


@router.message(ChangeEventStates.waiting_for_event_name)
async def change_event_set_name(message: Message, state: FSMContext):
    if not await is_full_admin(message.from_user.id):
        await state.clear()
        return

    title = _normalize_event_name(message.text)
    if not title:
        await message.answer("⚠️ Пустое название. Введите ещё раз или /admin для отмены.")
        return

    old = (config.EVENT_CODE or "").strip().lower()
    new = (title or "").strip()

    # Update the active event in memory and persist it across restarts.
    config.EVENT_CODE = new
    await set_meta("active_event_code", new)

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
    if not await is_full_admin(message.from_user.id):
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

    # Короткий фидбек и переходим к ценам
    await message.answer(
        "✅ Лимит 1+1 сохранён.\n"
        f"Лимит: {qty}"
    )

    await state.update_data(_limit_qty=qty)
    await state.set_state(ChangeEventStates.waiting_for_price_1p1)
    await message.answer("💵 Введите цену для билета *1+1* (целое число):", parse_mode="Markdown")

@router.message(ChangeEventStates.waiting_for_price_1p1)
async def change_event_price_1p1(message: Message, state: FSMContext):
    try:
        price = int((message.text or "").strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Цена должна быть целым числом ≥ 0. Попробуйте ещё раз.")
        return

    await state.update_data(price_1p1=price)
    await state.set_state(ChangeEventStates.waiting_for_price_single)
    await message.answer("💵 Введите цену для билета *single* (целое число):", parse_mode="Markdown")


@router.message(ChangeEventStates.waiting_for_price_single)
async def change_event_price_single(message: Message, state: FSMContext):
    try:
        price = int((message.text or "").strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Цена должна быть целым числом ≥ 0. Попробуйте ещё раз.")
        return

    await state.update_data(price_single=price)
    await state.set_state(ChangeEventStates.waiting_for_price_promocode)
    await message.answer("💵 Введите цену для билета *promocode* (целое число):", parse_mode="Markdown")


@router.message(ChangeEventStates.waiting_for_price_promocode)
async def change_event_price_promocode(message: Message, state: FSMContext):
    try:
        price = int((message.text or "").strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Цена должна быть целым числом ≥ 0. Попробуйте ещё раз.")
        return

    await state.update_data(price_promocode=price)
    await state.set_state(ChangeEventStates.waiting_for_promocode_list)
    await message.answer(
        "🧾 Отправьте *список промокодов* через запятую (например: VIP10, EARLY, TEST).\n"
        "Если промокодов нет — отправьте «-».",
        parse_mode="Markdown",
    )

@router.message(ChangeEventStates.waiting_for_promocode_list)
async def change_event_promocodes(message: Message, state: FSMContext):
    data = await state.get_data()
    new_event = data.get("_new_event_code", config.EVENT_CODE)

    raw = (message.text or "").strip()
    if raw in ("-", "—", "нет", "Нет", "no", "No", ""):
        codes = []
    else:
        codes = [c.strip().upper() for c in raw.split(",") if c.strip()]

    # Соберём цены
    prices = {
        "1+1": int(data.get("price_1p1", 0)),
        "single": int(data.get("price_single", 0)),
        "promocode": int(data.get("price_promocode", 0)),
    }

    # Сохраняем в bot_meta (per-event)
    # ключи: prices:<EVENT_CODE> и promocodes:<EVENT_CODE>
    try:
        await set_meta(f"prices:{new_event}", json.dumps(prices, ensure_ascii=False))
        await set_meta(f"promocodes:{new_event}", json.dumps(codes, ensure_ascii=False))
    except Exception:
        # не падаем в случае мелких проблем БД
        pass

    limit_qty = int(data.get("_limit_qty", 0))
    used = await count_one_plus_one_taken(new_event)
    left = max(limit_qty - used, 0)

    # подчистим FSM
    broadcast_needed = bool(data.get("_broadcast_needed"))
    await state.clear()

    # Итог
    pretty_codes = (", ".join(codes) if codes else "—")
    await message.answer(
        "✅ Мероприятие обновлено!\n"
        f"Текущее: {new_event}\n\n"
        f"Лимит 1+1: {limit_qty}\n\n"
        f"Цены:\n"
        f"• 1+1: {prices['1+1']}\n"
        f"• single: {prices['single']}\n"
        f"• promocode: {prices['promocode']}\n\n"
        f"Промокоды: {pretty_codes}"
    )

    # Если раньше было none → стало не none — шлём анонс (как раньше)
    if broadcast_needed:
        await message.answer("📣 Сначала рассылаю последний пост канала, затем уведомление с кнопкой…")
        asyncio.create_task(_broadcast_last_post_then_notice(message.bot, new_event))

# =========================
# Счётчик желающих 1+1
# =========================

async def list_1plus1_wishers(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 У вас нет прав для этой команды.")
        return
    await _send_wishers_to(message.bot, message.chat.id)


# =========================
# Локальный хелпер таймера
# =========================

async def _expire_payment_after_admin(bot, chat_id: int, message_id: int, row_id: int, timeout_sec: int = 300):
    await asyncio.sleep(timeout_sec)

    # узнаем тип билета и событие (важно для 1+1)
    row = await get_row(row_id)
    ticket_type = (row["ticket_type"] or "").strip().lower() if row else ""
    event_code = row["event_code"] if row else None

    status = await get_paid_status_by_id(row_id)

    if status in ("не оплатил", "отклонено"):
                # если было «отклонено», переводим в «не оплатил»
        if status == "отклонено":
            try:
                await set_paid_status_by_id(row_id, "не оплатил")
            except Exception:
                pass
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except Exception:
            pass

        kb = await _purchase_menu_kb()
        
        await bot.send_message(
            chat_id,
            "⏰ Время оплаты истекло.\nВыберите тип билета заново:",
            reply_markup=kb
        )
        
        # если освободился слот 1+1 — предупредим желающих
        if ticket_type == "1+1" and event_code:
            await _notify_wishers_1p1_available(bot, event_code)


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

async def _broadcast_last_post_then_notice(bot, event_title: str):
    post_id = await get_meta(LAST_POST_KEY)  # может быть None, тогда просто шлём уведомление
    subs = await get_all_subscribers()
    if not subs:
        return

    # Клавиатуры
    kb_notice_subscribed = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎟 Оплатить билет", callback_data="buy_ticket_menu")]
    ])
    kb_notice_unsubscribed = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подписаться на Telegram", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
        [InlineKeyboardButton(text="📷 Подписаться на Instagram", url=INSTAGRAM_LINK)],
        [InlineKeyboardButton(text="🎟 Оплатить билет", callback_data="buy_ticket_menu")]
    ])

    for uid, _uname in subs:
        # 1) копируем последний пост (если известен)
        if post_id:
            try:
                await bot.copy_message(chat_id=uid, from_chat_id=CHANNEL_ID, message_id=int(post_id))
            except Exception:
                pass  # игнорируем тех, к кому не доставили

        # 2) проверяем подписку на канал
        subscribed = False
        try:
            member = await bot.get_chat_member(CHANNEL_ID, uid)
            status = getattr(member, "status", None)
            subscribed = status in ("member", "administrator", "creator")
        except Exception:
            # не смогли проверить — считаем, что не подписан
            subscribed = False

        kb = kb_notice_subscribed if subscribed else kb_notice_unsubscribed

        # 3) отправляем уведомление
        try:
            await bot.send_message(
                uid,
                f"🔥 Новое мероприятие: {event_title}\n\nБилеты уже доступны — жми ниже 👇",
                reply_markup=kb
            )
        except Exception:
            pass

        # ограничим скорость (≈20 сообщений/сек)
        await asyncio.sleep(0.05)


# =========================
# Рассылки поста:
# =========================
class BroadcastLastStates(StatesGroup):
    waiting_for_password = State()

@router.message(BroadcastLastStates.waiting_for_password)
async def broadcast_last_check_password(message: Message, state: FSMContext):
    if not await is_full_admin(message.from_user.id):
        await state.clear()
        return

    pwd_ok = (message.text or "").strip() == (ADMIN_BROADCAST_PASSWORD or ADMIN_EVENT_PASSWORD or "")
    if not pwd_ok:
        await message.answer("❌ Неверный пароль. Рассылка отменена.")
        await state.clear()
        return

    await state.clear()
    await message.answer("✅ Пароль принят. Начинаю рассылку…")
    await _broadcast_last_post(message.bot, message)
    
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
async def broadcast_last_cmd(message: Message, state: FSMContext):
    if not await is_full_admin(message.from_user.id):
        return
    await state.set_state(BroadcastLastStates.waiting_for_password)
    await message.answer("🔒 Введите пароль для рассылки последнего поста канала:")

@router.callback_query(F.data == "broadcast_last")
async def broadcast_last_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.set_state(BroadcastLastStates.waiting_for_password)
    await callback.message.answer("🔒 Введите пароль для рассылки последнего поста канала:")

# =========================
# Добавление админов:
# =========================

_SCANNER_META_KEY = "SCANNER_ADMIN_IDS"

def _scan_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="scan_access_cancel")]
    ])



async def _load_scanner_ids() -> set[int]:
    raw = await get_meta(_SCANNER_META_KEY)
    if raw:
        try:
            return set(int(x) for x in json.loads(raw))
        except Exception:
            return set()
    # фолбэк на .env (если мета ещё не создана)
    try:
        return set(int(x) for x in getattr(config, "SCANNER_ADMIN_IDS", []))
    except Exception:
        return set()

async def _save_scanner_ids(ids: set[int]) -> None:
    await set_meta(_SCANNER_META_KEY, json.dumps(sorted(list(ids))))


class ScanAccessStates(StatesGroup):
    waiting_for_add_id = State()
    waiting_for_remove_id = State()

def _scan_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Посмотреть админов", callback_data="scan_access_view")],
        [InlineKeyboardButton(text="➕ Добавить", callback_data="scan_access_add"),
         InlineKeyboardButton(text="➖ Убрать",   callback_data="scan_access_remove")],
        [InlineKeyboardButton(text="✖️ Закрыть",  callback_data="scan_access_close")],
    ])



@router.callback_query(F.data == "scan_access_menu")
async def scan_access_menu(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await callback.message.answer("🔐 Управление доступом к сканеру:", reply_markup=_scan_menu_kb())

@router.callback_query(F.data == "scan_access_view")
async def scan_access_view(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True); return
    ids = await get_role_user_ids("scanner")
    if not ids:
        text = "Сканер-админов нет."
    else:
        lines = ["👥 Сканер-админы:"]
        for uid in sorted(ids):
            lines.append(f"• {uid}")
        text = "\n".join(lines)
    await callback.message.answer(text, reply_markup=_scan_menu_kb())


@router.callback_query(F.data == "scan_access_cancel")
async def scan_access_cancel(callback: CallbackQuery, state: FSMContext):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.answer("🔐 Управление доступом к сканеру:", reply_markup=_scan_menu_kb())


@router.callback_query(F.data == "scan_access_add")
async def scan_access_add(callback: CallbackQuery, state: FSMContext):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.set_state(ScanAccessStates.waiting_for_add_id)
    await callback.message.answer(
        "Отправьте числовой user_id, которому выдать доступ к сканеру.",
        reply_markup=_scan_cancel_kb()
    )


@router.message(ScanAccessStates.waiting_for_add_id)
async def scan_access_add_id(message: Message, state: FSMContext):
    if not await is_full_admin(message.from_user.id):
        await state.clear(); return
    try:
        uid = int((message.text or "").strip())
    except ValueError:
        await message.answer("user_id должен быть числом. Попробуйте снова или нажмите «Отмена».",
                             reply_markup=_scan_cancel_kb()); return

    if await has_role(uid, "admin") or await has_role(uid, "scanner"):
        await message.answer("✅ У пользователя уже есть доступ к сканеру.")
    else:
        await add_role(uid, "scanner")
        await message.answer(f"✅ Выдан доступ к сканеру: {uid}")

    await state.clear()
    await message.answer("Готово. Вернуться в меню управления:", reply_markup=_scan_menu_kb())

@router.callback_query(F.data == "scan_access_remove")
async def scan_access_remove(callback: CallbackQuery, state: FSMContext):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.set_state(ScanAccessStates.waiting_for_remove_id)
    await callback.message.answer(
        "Отправьте числовой user_id, у которого нужно забрать доступ.",
        reply_markup=_scan_cancel_kb()
    )


@router.message(ScanAccessStates.waiting_for_remove_id)
async def scan_access_remove_id(message: Message, state: FSMContext):
    if not await is_full_admin(message.from_user.id):
        await state.clear(); return
    try:
        uid = int((message.text or "").strip())
    except ValueError:
        await message.answer("user_id должен быть числом. Попробуйте снова или нажмите «Отмена».",
                             reply_markup=_scan_cancel_kb()); return

    if await has_role(uid, "admin"):
        await message.answer("🚫 Нельзя отозвать доступ у полного админа (роль 'admin').")
    elif not await has_role(uid, "scanner"):
        await message.answer("ℹ️ У пользователя и так нет прав сканера.")
    else:
        await remove_role(uid, "scanner")
        await message.answer(f"✅ Доступ к сканеру отозван: {uid}")

    await state.clear()
    await message.answer("Готово. Вернуться в меню управления:", reply_markup=_scan_menu_kb())

@router.callback_query(F.data == "scan_access_close")
async def scan_access_close(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await callback.message.answer("Закрыто.")

@router.message(lambda m: m.text == "/scan_access_menu")
async def scan_access_menu_cmd(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer("Нет прав.")
        return
    await message.answer("🔐 Управление доступом к сканеру:", reply_markup=_scan_menu_kb())



async def _purchase_menu_kb() -> InlineKeyboardMarkup:
    rows = []
    try:
        limit = await get_one_plus_one_limit(config.EVENT_CODE)
    except Exception:
        limit = None

    if limit and limit > 0:
        rows.append([InlineKeyboardButton(text="🎫 Билет 1+1", callback_data="ticket_1plus1")])

    rows.append([InlineKeyboardButton(text="🎫 1 билет", callback_data="ticket_single")])
    rows.append([InlineKeyboardButton(text="🎟 У меня есть промокод", callback_data="ticket_promocode")])
    rows.append([InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data="back:start")])

    return InlineKeyboardMarkup(inline_keyboard=rows)

async def _notify_wishers_1p1_available(bot, event_code: str):
    """
    Шлём уведомления тем, кто пытался взять 1+1,
    когда слоты были заняты. Не больше текущего остатка.
    """
    remaining = await remaining_one_plus_one_for_event(event_code)
    if remaining is None or remaining <= 0:
        return

    rows = await get_unique_one_plus_one_attempters_for_event(event_code)
    if not rows:
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎟 Оплатить билет", callback_data="ticket_1plus1")],
        [InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data="back:ticket")],
    ])

    sent = 0
    for r in rows:
        uid = int(r["user_id"])
        try:
            await bot.send_message(
                uid,
                f"✨ Освободились билеты 1+1 на «{event_code}». Успей забрать 👇",
                reply_markup=kb
            )
            sent += 1
        except Exception:
            pass

        if sent >= remaining:
            break

        await asyncio.sleep(0.05)  # мягкий rate-limit



# ===============================================
# ==== helpers: цены и промокоды для события ====
# ===============================================

def _norm_ticket_key(raw: str) -> str:
    s = (raw or "").strip().lower()
    # допускаем разные варианты написания
    s = s.replace(" ", "")
    if s in ("1+1", "1plus1", "oneplusone"):
        return "1+1"
    if s in ("single", "1", "один", "solo"):
        return "single"
    if s in ("promocode", "promo", "promocod", "промокод"):
        return "promocode"
    return s  # на случай будущих типов

def _parse_prices(text: str) -> dict[str, int]:
    """
    Ожидаемый формат (по строкам; порядок свободный):
      1+1: 1500
      single: 1000
      promocode: 800
    Допускается через запятую: "1+1:1500, single:1000, promocode:800"
    """
    if not text:
        return {}
    prices = {}
    parts = []
    # поддержим и переносы строк, и записи через запятую
    for line in text.replace(",", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        parts.append(line)
    for p in parts:
        if ":" not in p:
            raise ValueError(f"Нет двоеточия: «{p}»")
        k, v = p.split(":", 1)
        k = _norm_ticket_key(k)
        v = v.strip().replace(" ", "")
        if not v.isdigit():
            raise ValueError(f"Цена должна быть числом: «{p}»")
        prices[k] = int(v)
    # sanity-check — важные ключи можно подсветить, но не требуем жёстко
    return prices

def _parse_promocodes(text: str) -> list[str]:
    """
    "VIP, SUMMER2025, test_1" -> ["VIP", "SUMMER2025", "test_1"]
    Пустая строка = нет промокодов.
    """
    if not (text or "").strip():
        return []
    arr = [c.strip() for c in text.split(",")]
    # фильтруем пустые, убираем дубликаты, сохраняем порядок
    seen = set()
    out = []
    for c in arr:
        if not c:
            continue
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out

async def _save_event_prices(event_code: str, prices: dict[str, int]):
    await set_meta(f"prices:{event_code}", json.dumps(prices, ensure_ascii=False))

async def _load_event_prices(event_code: str) -> dict[str, int] | None:
    raw = await get_meta(f"prices:{event_code}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def _fmt_amount(n: int) -> str:
    return f"{n:,}".replace(",", " ")

def _canon_type(t: str) -> str:
    s = (t or "").strip().lower()
    if s == "1+1":
        return "1+1"
    if s == "single":
        return "single"
    # всё остальное считаем промокодами
    return "promocode"

async def _calc_revenue_for_event(event_code: str) -> tuple[int, int]:
    if not event_code or event_code.strip().lower() == "none":
        return 0, 0

    rows = await get_all_users_full(event_code)
    prices = await _load_event_prices(event_code) or {}
    total = 0
    missing = 0

    for r in rows or []:
        d = dict(r)  # ← ключевая правка
        paid = (d.get("paid") or "").strip().lower()
        if paid != "оплатил":
            continue
        tt = _canon_type(d.get("ticket_type"))
        price = prices.get(tt)
        if isinstance(price, int):
            total += price
        else:
            missing += 1
    return total, missing


async def _calc_revenue_all_events() -> tuple[int, int]:
    rows = await get_all_users_full(None)
    total = 0
    missing = 0
    cache: dict[str, dict] = {}

    for r in rows or []:
        d = dict(r)  # ← ключевая правка
        paid = (d.get("paid") or "").strip().lower()
        if paid != "оплатил":
            continue

        ev = (d.get("event_code") or "").strip()
        if not ev or ev.lower() == "none":
            continue

        if ev not in cache:
            cache[ev] = await _load_event_prices(ev) or {}

        tt = _canon_type(d.get("ticket_type"))
        price = cache[ev].get(tt)
        if isinstance(price, int):
            total += price
        else:
            missing += 1

    return total, missing


@router.callback_query(F.data == "an:revenue")
async def cb_an_revenue(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await callback.answer("Считаю…", show_alert=False)

    cur_ev = config.EVENT_CODE
    cur_total, cur_missing = await _calc_revenue_for_event(cur_ev)
    all_total, all_missing = await _calc_revenue_all_events()

    lines = ["💰 Выручка"]
    if cur_ev and cur_ev.strip().lower() != "none":
        line = f"Текущее мероприятие «{cur_ev}»: {_fmt_amount(cur_total)} ₽"
        if cur_missing:
            line += f" (без цены: {cur_missing})"
        lines.append(line)
    else:
        lines.append("Текущее мероприятие: none (0 ₽)")

    line = f"Все мероприятия: {_fmt_amount(all_total)} ₽"
    if all_missing:
        line += f" (без цены: {all_missing})"
    lines.append(line)

    await callback.message.answer("\n".join(lines))


async def _save_event_promocodes(event_code: str, codes: list[str]):
    await set_meta(f"promocodes:{event_code}", json.dumps(codes, ensure_ascii=False))

async def _load_event_promocodes(event_code: str) -> list[str] | None:
    raw = await get_meta(f"promocodes:{event_code}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _send_report_to(bot, chat_id: int):
    total = await count_registered()
    active = await count_activated()
    inactive = total - active
    chat_count = await bot.get_chat_member_count(CHANNEL_ID)
    paid_count = await count_paid()
    await bot.send_message(
        chat_id,
        f"📊 Статистика:\n"
        f"👥 Подписчиков в канале: {chat_count}\n"
        f"👤 Создано покупок: {total}\n"
        f"💰 Оплачено: {paid_count}\n"
        f"✅ Пришли: {active}"
    )

async def _send_stats_this_to(bot, chat_id: int):
    ev = config.EVENT_CODE
    rows = await get_ticket_stats_for_event(ev, paid_statuses=("оплатил",))
    if not rows:
        await bot.send_message(chat_id, f"Для «{ev}» оплаченных билетов нет.")
        return
    total = sum(int(r["count"]) for r in rows)
    parts = [f"📊 «{ev}»: только оплаченные", ""]
    for r in rows:
        parts.append(f"• {r['ticket_type']}: {int(r['count'])}")
    parts.append("")
    parts.append(f"ИТОГО: {total}")
    await bot.send_message(chat_id, "\n".join(parts))

async def _send_wishers_to(bot, chat_id: int):
    rows = await get_unique_one_plus_one_attempters_for_event(config.EVENT_CODE)
    if not rows:
        await bot.send_message(chat_id, "Пока никто не пытался купить 1+1 после исчерпания лимита.")
        return
    lines = ["📝 Кто хотел 1+1, но не успел (уникальные пользователи):\n"]
    for r in rows:
        uid = r["user_id"]
        uname = r["username"] or f"id:{uid}"
        when = r["last_try"].strftime("%Y-%m-%d %H:%M")
        lines.append(f"• @{uname} (id:{uid}) — {when}")
    text = "\n".join(lines)
    if len(text) > 4000:
        with open("wishers_1plus1.txt", "w", encoding="utf-8") as f:
            f.write(text)
        await bot.send_document(chat_id, FSInputFile("wishers_1plus1.txt"), caption="📝 Список желающих 1+1")
    else:
        await bot.send_message(chat_id, text)

async def _send_export_to(bot, chat_id: int, only_this: bool):
    rows = await get_all_users_full(config.EVENT_CODE if only_this else None)
    if not rows:
        await bot.send_message(chat_id, "Данных нет.")
        return
    wb = Workbook(); ws = wb.active; ws.title = "users"
    ws.append(["id","user_id","username","event_code","ticket_type","paid","status","purchase_date"])
    for r in rows:
        ws.append([r["id"], r["user_id"], r["username"], r["event_code"], r["ticket_type"], r["paid"], r["status"], r["purchase_date"]])
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    fname = f"users_{config.EVENT_CODE}.xlsx" if only_this else "users.xlsx"
    await bot.send_document(chat_id, BufferedInputFile(buf.getvalue(), filename=fname),
                            caption="📄 Выгрузка базы users" + (f" — {config.EVENT_CODE}" if only_this else " (все мероприятия)"))
