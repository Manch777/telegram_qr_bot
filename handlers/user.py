# handlers/user.py
from aiogram import Router, F
import config
import asyncio
import json
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from config import (
    ADMIN_CONTACT,
    CHANNEL_ID,
    INSTAGRAM_LINK,
    PAYMENT_LINK,
    SBP_PAYMENT_DETAILS,
)
from database import (
    add_user,  get_row,
    get_paid_status_by_id, set_paid_status_by_id,
    count_ticket_type_paid_for_event, count_ticket_type_for_event,
    log_one_plus_one_attempt, add_subscriber,
    get_one_plus_one_limit, remaining_one_plus_one_for_event,
    set_meta, get_meta, set_ticket_type_by_id,
    get_unique_one_plus_one_attempters_for_event, get_role_user_ids,
)

router = Router()

# ————— Навигация/экраны —————
_AWAIT_PROMO = set()
_LAST_MSG: dict[int, int] = {}   # user_id -> last bot screen message_id

def _event_off() -> bool:
    return (config.EVENT_CODE or "").strip().lower() == "none"

def _root_text() -> str:
    return (
        "Хей! Приветствуем тебя в ЖАЖДА community 🖤\n"
        "Теперь ты точно знаешь, где лучшие тусовки\n\n"
        "Выбери, что хочешь сделать 👇"
    )

def _root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подписаться на Telegram", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
        [InlineKeyboardButton(text="📷 Подписаться на Instagram", url=INSTAGRAM_LINK)],
        [InlineKeyboardButton(text="🎟 Оплатить билет", callback_data="buy_ticket_menu")]
    ])

async def _ticket_menu_kb() -> InlineKeyboardMarkup:
    rows = []
    # показываем 1+1 только если лимит > 0
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

def _back_to_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data="back:start")]
    ])

def _back_to_ticket_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data="back:ticket")]
    ])

def _payment_kb(row_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=PAYMENT_LINK)],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid_row:{row_id}")],
        [InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data="back:ticket")],
    ])

# ⬅️ Вернуться назад с экрана «оплата отклонена»
@router.callback_query(F.data.startswith("back_to_menu:"))
async def back_from_reject(callback: CallbackQuery):
    await callback.answer()

    # row_id пришёл в коллбэке
    try:
        row_id = int(callback.data.split(":")[1])
    except Exception:
        row_id = None

    # если это сообщение «оплата отклонена» — удалим его
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Если билету стоял статус «отклонено» — вернём в «не оплатил»
    if row_id is not None:
        try:
            cur = await get_paid_status_by_id(row_id)
            if cur == "отклонено":
                await set_paid_status_by_id(row_id, "не оплатил")
        except Exception:
            pass

    # Показать меню выбора билета (или сообщение, что мероприятий нет)
    if _event_off():
        await _push_screen(
            callback.bot, callback.from_user.id,
            "Сейчас мероприятий нет. Мы сообщим, как только объявим новое событие. 🖤",
            _back_to_start_kb()
        )
    else:
        await _show_ticket_menu(callback.bot, callback.from_user.id)

async def _push_screen(bot, chat_id: int, text: str, kb: InlineKeyboardMarkup):
    """Удаляет предыдущий экран пользователя и отправляет новый.
       НО не удаляет «защищённый» экран ожидания подтверждения."""
    protected_id_raw = await get_meta(f"review_msg:{chat_id}")  # храним id «ожидания»
    try:
        protected_id = int(protected_id_raw) if protected_id_raw else None
    except Exception:
        protected_id = None

    last_id = _LAST_MSG.get(chat_id)
    # удаляем предыдущий экран, только если он не «защищённый»
    if last_id and (protected_id is None or last_id != protected_id):
        try:
            await bot.delete_message(chat_id, last_id)
        except Exception:
            pass

    sent = await bot.send_message(chat_id, text, reply_markup=kb)
    _LAST_MSG[chat_id] = sent.message_id
    return sent

async def _show_root(bot, chat_id: int):
    return await _push_screen(bot, chat_id, _root_text(), _root_kb())

async def _show_ticket_menu(bot, chat_id: int):
    kb = await _ticket_menu_kb()
    return await _push_screen(bot, chat_id, "Выбери тип билета:", kb)

async def _notify_wishers_1p1_available(bot, event_code: str):
    """
    Шлём уведомления тем, кто пытался купить 1+1, когда слоты были заняты.
    Отправляем не больше, чем текущий остаток по 1+1.
    """
    remaining = await remaining_one_plus_one_for_event(event_code)
    if not remaining or remaining <= 0:
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
        await asyncio.sleep(0.05)  # мягкий rate limit


# ————— Логика User —————

# /start
@router.message(CommandStart())
async def start_command(message: Message):
    await add_subscriber(message.from_user.id, message.from_user.username)
    await _show_root(message.bot, message.from_user.id)

# Вернуться назад: в старт
@router.callback_query(F.data == "back:start")
async def back_start(callback: CallbackQuery):
    await callback.answer()
    await _show_root(callback.bot, callback.from_user.id)

# Вернуться назад: в меню билетов
@router.callback_query(F.data == "back:ticket")
async def back_ticket(callback: CallbackQuery):
    await callback.answer()
    if _event_off():
        await _push_screen(
            callback.bot,
            callback.from_user.id,
            "Сейчас мероприятий нет. Мы сообщим, как только объявим новое событие. 🖤",
            _back_to_start_kb()
        )
        return
    await _show_ticket_menu(callback.bot, callback.from_user.id)

# Меню выбора билета
@router.callback_query(F.data == "buy_ticket_menu")
async def ticket_menu(callback: CallbackQuery):
    await callback.answer()

    # если это нажатие из уведомления о новом событии — удалим САМО уведомление
    try:
        await callback.message.delete()
    except Exception:
        pass

    await add_subscriber(callback.from_user.id, callback.from_user.username)

    if _event_off():
        await _push_screen(
            callback.bot,
            callback.from_user.id,
            "Сейчас мероприятий нет.\nМы сообщим, как только объявим следующее событие. 🖤",
            _back_to_start_kb()
        )
        return

    # создаём черновик покупки: paid="не оплатил", тип ещё не выбран
    username = callback.from_user.username or "Без ника"
    draft_row_id = await add_user(
        user_id=callback.from_user.id,
        username=username,
        event_code=config.EVENT_CODE,
        ticket_type="—"  # или "pending"
    )
    #  запомним id черновика для этого пользователя
    await set_meta(f"draft_row:{callback.from_user.id}", str(draft_row_id))


    await _show_ticket_menu(callback.bot, callback.from_user.id)

# Билет 1+1
@router.callback_query(F.data == "ticket_1plus1")
async def buy_1plus1(callback: CallbackQuery):
    await callback.answer()

    if _event_off():
        await _push_screen(
            callback.bot, callback.from_user.id,
            "Сейчас мероприятий нет. Как только появится новое — пришлём уведомление. 🖤",
            _back_to_start_kb()
        )
        return

    limit = await get_one_plus_one_limit(config.EVENT_CODE)
    if limit is None or limit <= 0:
        await log_one_plus_one_attempt(
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            event_code=config.EVENT_CODE,
        )
        await _push_screen(
            callback.bot, callback.from_user.id,
            "❌ Акция '1+1' сейчас недоступна для этого мероприятия.",
            _back_to_ticket_kb()
        )
        return

    left = await remaining_one_plus_one_for_event(config.EVENT_CODE)
    if left is not None and left <= 0:
        await log_one_plus_one_attempt(
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            event_code=config.EVENT_CODE,
        )
        await _push_screen(
            callback.bot, callback.from_user.id,
            "❌ Акция '1+1' больше недоступна для этого мероприятия.",
            _back_to_ticket_kb()
        )
        return

    await _present_payment(callback, ticket_type="1+1")

# 1 билет
@router.callback_query(F.data == "ticket_single")
async def buy_single(callback: CallbackQuery):
    await callback.answer()

    if _event_off():
        await _push_screen(
            callback.bot, callback.from_user.id,
            "Сейчас мероприятий нет. Как только появится новое — пришлём уведомление. 🖤",
            _back_to_start_kb()
        )
        return
    await _present_payment(callback, ticket_type="single")

# Промокод — запрос ввода
@router.callback_query(F.data == "ticket_promocode")
async def ask_promocode(callback: CallbackQuery):
    await callback.answer()

    if _event_off():
        await _push_screen(
            callback.bot, callback.from_user.id,
            "Сейчас мероприятий нет. Как только объявим новое событие — можно будет применить промокод. 🖤",
            _back_to_start_kb()
        )
        return

    _AWAIT_PROMO.add(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data="promo_cancel")],
    ])
    await _push_screen(callback.bot, callback.from_user.id, "Введи промокод одним сообщением:", kb)

# Отмена ожидания промокода
@router.callback_query(F.data == "promo_cancel")
async def cancel_promocode(callback: CallbackQuery):
    await callback.answer()
    _AWAIT_PROMO.discard(callback.from_user.id)
    if _event_off():
        await _push_screen(
            callback.bot, callback.from_user.id,
            "Отменено. Сейчас мероприятий нет. /start",
            _back_to_start_kb()
        )
    else:
        await _show_ticket_menu(callback.bot, callback.from_user.id)


async def _get_event_promocodes() -> set[str]:
    """
    Читает промокоды из bot_meta по ключу 'promocodes:<EVENT_CODE>',
    который заполняет админ. Ожидается JSON-список строк.
    Возвращает множество кодов в UPPERCASE.
    Есть безопасный фолбэк на config.PROMOCODES, если меты нет.
    """
    codes: set[str] = set()

    # основной источник — мета (заполненная админом)
    raw = await get_meta(f"promocodes:{config.EVENT_CODE}")
    lst = None
    if raw:
        try:
            lst = json.loads(raw)
        except Exception:
            lst = None

    if isinstance(lst, list):
        for c in lst:
            if isinstance(c, str) and c.strip():
                codes.add(c.strip().upper())
    elif isinstance(lst, str):
        # на случай, если вдруг сохранили строкой "AAA, BBB"
        parts = [p.strip() for p in lst.split(",")]
        for c in parts:
            if c:
                codes.add(c.upper())

    # фолбэк: если админ ещё не задал мету — берём старые коды из конфига (если есть)
    try:
        from config import PROMOCODES as CFG_CODES  # опционально
        codes |= {str(c).strip().upper() for c in CFG_CODES if str(c).strip()}
    except Exception:
        pass

    return codes

# Ловим ввод промокода
@router.message(F.text & ~F.text.startswith("/"))
async def handle_promocode(message: Message):
    if message.from_user.id not in _AWAIT_PROMO:
        return

    if _event_off():
        _AWAIT_PROMO.discard(message.from_user.id)
        await _push_screen(
            message.bot, message.from_user.id,
            "Сейчас мероприятий нет. Промокод можно будет применить позже.",
            _back_to_start_kb()
        )
        return

    user_code = (message.text or "").strip().upper()
    valid_codes = await _get_event_promocodes()

    if user_code not in valid_codes:
        await message.answer("❌ Неверный промокод. Попробуй снова.")
        return

    _AWAIT_PROMO.discard(message.from_user.id)
    # Передаём сам промокод как ticket_type (цена берётся по ключу 'promocode')
    await _present_payment(message, ticket_type=user_code, from_message=True)


async def _price_for_ticket(ticket_type: str) -> int | None:
    """
    Берём цены из bot_meta по ключу prices:<EVENT_CODE>, который писал админ в /change_event.
    Формат: {"1+1": 1000, "single": 700, "promocode": 500}
    Для конкретного промокода берём цену "promocode".
    """
    raw = await get_meta(f"prices:{config.EVENT_CODE}")
    try:
        prices = json.loads(raw) if raw else {}
    except Exception:
        prices = {}

    key = "1+1" if ticket_type == "1+1" else ("single" if ticket_type == "single" else "promocode")
    val = prices.get(key)
    try:
        return int(val)
    except Exception:
        return None

# Экран оплаты (создаёт покупку)
async def _present_payment(obj, ticket_type: str, from_message: bool = False):
    # защита от гонок
    if _event_off():
        target = obj.message if hasattr(obj, "message") else obj
        await _push_screen(
            target.bot, target.chat.id,
            "Сейчас мероприятий нет. Скоро расскажем про новое событие. 🖤",
            _back_to_start_kb()
        )
        return

# ... выше проверки _event_off() без изменений ...

    user = obj.from_user
    user_id = user.id
    username = user.username or "Без ника"

# пытаемся использовать черновик, созданный при "Оплатить билет"
    draft_raw = await get_meta(f"draft_row:{user_id}")
    row_id = None
    if draft_raw:
        try:
            row_id = int(draft_raw)
        except Exception:
            row_id = None

    if row_id:
    # проставляем выбранный тип в уже созданную запись
        await set_ticket_type_by_id(row_id, ticket_type)
    else:
    # фолбэк: на всякий случай создадим новую (если меты нет)
        row_id = await add_user(
            user_id=user_id,
            username=username,
            event_code=config.EVENT_CODE,
            ticket_type=ticket_type
        )
        await set_meta(f"draft_row:{user_id}", str(row_id))

# пользователь начал оформление — статус "в процессе оплаты"
    await set_paid_status_by_id(row_id, "в процессе оплаты")


    # Человекочитаемое название для текста
    title_map = {"single": "1 билет", "1+1": "Билет 1+1"}
    pretty_type = title_map.get(ticket_type, f"Промокод «{ticket_type}»")

    # Цена
    price = await _price_for_ticket(ticket_type)
    price_line = f"\nЦена: {price}" if price is not None else ""

    payment_details = (
        f"\n\nТакже можете оплатить переводом по СБП {SBP_PAYMENT_DETAILS}"
        if SBP_PAYMENT_DETAILS
        else ""
    )

    text = (
        f"Тип билета: {pretty_type}\n"
        f"Мероприятие: {config.EVENT_CODE}"
        f"{price_line}\n\n"
        "После оплаты нажми «Я оплатил».\n"
        "⏳Ссылка на оплату действует 5 минут!\n"
        "❗️Не забудь в комментариях платежа указать свой ник в Telegram"
        f"{payment_details}"
    )

    bot = obj.bot
    sent = await _push_screen(bot, user_id, text, _payment_kb(row_id))

    # таймер 5 минут
    asyncio.create_task(
        _expire_payment_after(
            bot=bot,
            chat_id=user_id,
            message_id=sent.message_id,
            row_id=row_id,
            timeout_sec=300
        )
    )

# Пользователь: «Я оплатил»
@router.callback_query(F.data.startswith("paid_row:"))
async def payment_confirmation(callback: CallbackQuery):
    await callback.answer()
    user = callback.from_user
    row_id = int(callback.data.split(":")[1])
    username = user.username or "Без ника"

    row = await get_row(row_id)
    if not row:
        await _push_screen(
            callback.bot, user.id,
            "❌ Билет не найден.",
            _back_to_ticket_kb()
        )
        return

    ticket_type = row["ticket_type"] or "-"
    paid_status = await get_paid_status_by_id(row_id)

    if paid_status == "оплатил":
        await _push_screen(
            callback.bot, user.id,
            "✅ Вы уже оплатили. QR-код был отправлен ранее.",
            _back_to_start_kb()
        )
        return
    if paid_status == "на проверке":
        await _push_screen(
            callback.bot, user.id,
            "⏳ Ваша оплата уже на проверке. Пожалуйста, подождите.",
            _back_to_start_kb()
        )
        return

    await set_paid_status_by_id(row_id, "на проверке")

    # Покажем «защищённый» экран ожидания и запомним его message_id
    sent = await _push_screen(
        callback.bot, user.id,
        f"⏳Проверяем оплату\nОтправим QR с проходкой сразу после проверки 🖤",
        _back_to_start_kb()
    )
    # защитим этот экран от авто-удаления
    await set_meta(f"review_msg:{user.id}", str(sent.message_id))


    # Уведомляем назначенного админа
    kb_admin = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"approve_row:{row_id}")],
        [InlineKeyboardButton(text="❌ Не подтверждена",   callback_data=f"reject_row:{row_id}")]
    ])
    recipients = await get_role_user_ids("payments_admin")
    if not recipients:
        recipients = await get_role_user_ids("admin")  # фолбэк — первые попавшиеся админы
    recipient_id = recipients[0] if recipients else None

    if recipient_id:
        await callback.bot.send_message(
            chat_id=recipient_id,
            text=f"💰 Подтверждение оплаты пользователя @{username}\nТип билета: {ticket_type}",
            reply_markup=kb_admin
        )


# /help
@router.message(lambda message: message.text == "/help")
async def help_command(message: Message):
    contact_text = (
        f"\n{ADMIN_CONTACT}"
        if ADMIN_CONTACT
        else ""
    )

    await _push_screen(
        message.bot,
        message.from_user.id,
        (
            "ℹ️ Если возникли вопросы или проблемы, "
            f"обратись к администратору:{contact_text}"
        ),
        _back_to_start_kb(),
    )

# Тайм-аут оплаты
async def _expire_payment_after(bot, chat_id: int, message_id: int, row_id: int, timeout_sec: int = 300):
    await asyncio.sleep(timeout_sec)

    # узнаем тип билета и событие (важно для 1+1)
    row = await get_row(row_id)
    ticket_type = (row["ticket_type"] or "").strip().lower() if row else ""
    event_code = row["event_code"] if row else None
    
    from database import get_paid_status_by_id
    status = await get_paid_status_by_id(row_id)

    if status in ("в процессе оплаты"):
        # откатываем всё, что не "не оплатил", в "не оплатил"
        if status != "не оплатил":
            try:
                await set_paid_status_by_id(row_id, "не оплатил")
            except Exception:
                pass        
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except Exception:
            pass

        # показываем уведомление + то же меню выбора билета
        kb = await _ticket_menu_kb()
        await _push_screen(
            bot, chat_id,
            "⏰ Время оплаты истекло.\nВыберите тип билета заново:",
            kb
        )
        
        # если освободился слот 1+1 — предупредим желающих
        if ticket_type == "1+1" and event_code:
            await _notify_wishers_1p1_available(bot, event_code)
