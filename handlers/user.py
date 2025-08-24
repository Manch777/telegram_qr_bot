# handlers/user.py
from aiogram import Router, F
import config
import asyncio
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from config import CHANNEL_ID, PAYMENT_LINK, INSTAGRAM_LINK, PROMOCODES, ADMIN_IDS
from database import (
    add_user,  get_row,
    get_paid_status_by_id, set_paid_status_by_id,
    count_ticket_type_paid_for_event, count_ticket_type_for_event,
    log_one_plus_one_attempt, add_subscriber,
    get_one_plus_one_limit, remaining_one_plus_one_for_event,
    set_meta, get_meta,
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

def _ticket_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎫 Билет 1+1", callback_data="ticket_1plus1")],
        [InlineKeyboardButton(text="🎫 1 билет", callback_data="ticket_single")],
        [InlineKeyboardButton(text="🎟 У меня есть промокод", callback_data="ticket_promocode")],
        [InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data="back:start")],
    ])

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
    return await _push_screen(bot, chat_id, "Выбери тип билета:", _ticket_menu_kb())

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
    await add_subscriber(callback.from_user.id, callback.from_user.username)

    if _event_off():
        await _push_screen(
            callback.bot,
            callback.from_user.id,
            "Сейчас мероприятий нет.\nМы сообщим, как только объявим следующее событие. 🖤",
            _back_to_start_kb()
        )
        return

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
    await _push_screen(callback.bot, callback.from_user.id, "Введите ваш промокод одним сообщением:", kb)

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

    code = (message.text or "").strip().upper()
    if code not in PROMOCODES:
        # не трогаем экран — просто скажем, что неверно
        await message.answer("❌ Неверный промокод. Попробуйте снова.")
        return

    _AWAIT_PROMO.discard(message.from_user.id)
    # вместо "promocode" пишем сам код
    await _present_payment(message, ticket_type=code, from_message=True)

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

    user = obj.from_user
    user_id = user.id
    username = user.username or "Без ника"

    row_id = await add_user(
        user_id=user_id,
        username=username,
        event_code=config.EVENT_CODE,
        ticket_type=ticket_type
    )

    text = (
        f"Тип билета: {ticket_type}\n"
        f"Мероприятие: {config.EVENT_CODE}\n\n"
        "После оплаты нажми «Я оплатил».\n"
        "❗️В комментариях платежа укажи свой Telegram-ник."
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
        "⏳ Подтверждение отправлено администратору. Ожидайте одобрения.",
        _back_to_start_kb()
    )
    # защитим этот экран от авто-удаления
    await set_meta(f"review_msg:{user.id}", str(sent.message_id))


    # Уведомляем назначенного админа
    kb_admin = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"approve_row:{row_id}")],
        [InlineKeyboardButton(text="❌ Не подтверждена",   callback_data=f"reject_row:{row_id}")]
    ])
    recipient_id = getattr(config, "PAYMENTS_ADMIN_ID", None) or (ADMIN_IDS[0] if ADMIN_IDS else None)
    if recipient_id:
        await callback.bot.send_message(
            chat_id=recipient_id,
            text=f"💰 Подтверждение оплаты пользователя @{username}\nТип билета: {ticket_type}",
            reply_markup=kb_admin
        )

# /help
@router.message(lambda m: m.text == "/help")
async def help_command(message: Message):
    await _push_screen(
        message.bot, message.from_user.id,
        "ℹ️ Если у вас возникли вопросы или проблемы, пожалуйста, обратитесь к администратору:\n@Manch7",
        _back_to_start_kb()
    )

# Тайм-аут оплаты
async def _expire_payment_after(bot, chat_id: int, message_id: int, row_id: int, timeout_sec: int = 300):
    await asyncio.sleep(timeout_sec)

    from database import get_paid_status_by_id
    status = await get_paid_status_by_id(row_id)

    if status in ("не оплатил", "отклонено"):
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except Exception:
            pass

        await _show_ticket_menu(bot, chat_id)
