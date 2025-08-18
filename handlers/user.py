# handlers/user.py
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from config import CHANNEL_ID, PAYMENT_LINK, PROMOCODES, EVENT_CODE, ADMIN_IDS
from database import (
    add_user,                               # -> возвращает row_id (id строки покупки)
    get_paid_status_by_id, set_paid_status_by_id,
    count_ticket_type_paid_for_event,
)

router = Router()

# Локальный флаг ожидания промокода (без FSM, чтобы не трогать main.py)
_AWAIT_PROMO = set()   # set[int] of user_id


# /start: приветствие + 2 кнопки
@router.message(CommandStart())
async def start_command(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подписаться на Telegram", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
        [InlineKeyboardButton(text="📷 Подписаться на Instagram", url=INSTAGRAM_LINK)],
        [InlineKeyboardButton(text="🎟 Оплатить билет", callback_data="buy_ticket_menu")]
    ])
    text = (
        "Хей! Приветствуем тебя в ЖАЖДА community 🖤\n"
        "Теперь ты точно знаешь, где лучшие тусовки\n\n"
        "Выбери, что хочешь сделать 👇"
    )
    await message.answer(text, reply_markup=kb)


# Меню выбора билета
@router.callback_query(F.data == "buy_ticket_menu")
async def ticket_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎫 Билет 1+1", callback_data="ticket_1plus1")],
        [InlineKeyboardButton(text="🎫 1 билет", callback_data="ticket_single")],
        [InlineKeyboardButton(text="🎟 У меня есть промокод", callback_data="ticket_promocode")]
    ])
    await callback.message.answer("Выбери тип билета:", reply_markup=kb)


# Билет 1+1 (лимит 5 оплаченных на текущее мероприятие)
@router.callback_query(F.data == "ticket_1plus1")
async def buy_1plus1(callback: CallbackQuery):
    paid_count = await count_ticket_type_paid_for_event(EVENT_CODE, "1+1")
    if paid_count >= 5:
        await callback.message.answer("❌ Акция '1+1' больше недоступна (лимит 5 продаж на это мероприятие).")
        return
    await _present_payment(callback, ticket_type="1+1")


# 1 билет
@router.callback_query(F.data == "ticket_single")
async def buy_single(callback: CallbackQuery):
    await _present_payment(callback, ticket_type="single")


# Промокод — запрос ввода
@router.callback_query(F.data == "ticket_promocode")
async def ask_promocode(callback: CallbackQuery):
    _AWAIT_PROMO.add(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="promo_cancel")]
    ])
    await callback.message.answer("Введите ваш промокод одним сообщением:", reply_markup=kb)


# Отмена ожидания промокода
@router.callback_query(F.data == "promo_cancel")
async def cancel_promocode(callback: CallbackQuery):
    _AWAIT_PROMO.discard(callback.from_user.id)
    await callback.message.answer("Отменено. Вернитесь в меню: /start")


# Ловим ввод промокода (игнорируем команды)
@router.message(F.text & ~F.text.startswith("/"))
async def handle_promocode(message: Message):
    if message.from_user.id not in _AWAIT_PROMO:
        return

    code = (message.text or "").strip().upper()
    if code not in PROMOCODES:
        await message.answer("❌ Неверный промокод. Попробуйте снова или нажмите /start.")
        return

    # Успех — больше не ждём код
    _AWAIT_PROMO.discard(message.from_user.id)
    await _present_payment(message, ticket_type="promocode", from_message=True)


# Общая функция показа оплаты — СОЗДАЁТ новую запись (новую покупку) и даёт кнопку "Я оплатил"
async def _present_payment(obj, ticket_type: str, from_message: bool = False):
    user = obj.from_user
    user_id = user.id
    username = user.username or "Без ника"

    # Каждая покупка = новая строка в БД
    row_id = await add_user(user_id=user_id, username=username, event_code=EVENT_CODE, ticket_type=ticket_type)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=PAYMENT_LINK)],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid_row:{row_id}")]
    ])
    text = (
        f"Вы выбрали: {ticket_type}\n"
        f"Мероприятие: {EVENT_CODE}\n\n"
        "После оплаты нажмите «Я оплатил».\n"
        "❗️В комментариях платежа укажите свой Telegram-ник."
    )
    if from_message:
        await obj.answer(text, reply_markup=kb)
    else:
        await obj.message.answer(text, reply_markup=kb)


# Пользователь нажимает "Я оплатил" — по КОНКРЕТНОЙ покупке (row_id)
@router.callback_query(F.data.startswith("paid:"))
async def payment_confirmation(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])

    # Имя/ник для сообщений
    tg_username = callback.from_user.username
    username = tg_username or "Без ника"  # для текста
    mention = f"@{tg_username}" if tg_username else callback.from_user.full_name

    # Тип билета (из последней покупки этого пользователя — legacy-обёртка)
    ticket_type = await get_ticket_type(user_id) or "-"

    # Проверка текущего статуса оплаты
    paid_status = await get_paid_status(user_id)
    if paid_status == "оплатил":
        await callback.answer("✅ Вы уже оплатили. QR-код был отправлен ранее.", show_alert=True)
        return
    if paid_status == "на проверке":
        await callback.answer("⏳ Ваша оплата уже на проверке. Пожалуйста, подождите.", show_alert=True)
        return

    # Ставим "на проверке"
    await set_paid_status(user_id, "на проверке")

    # Уберём старые кнопки у пользователя
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("⏳ Ваше подтверждение отправлено администратору. Ожидайте одобрения.")

    # Кнопки для администратора
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"approve:{user_id}")],
        [InlineKeyboardButton(text="❌ Не подтверждена",     callback_data=f"reject:{user_id}")]
    ])

    # Уведомление администраторам
    for admin_id in ADMIN_IDS:
        await callback.bot.send_message(
            chat_id=admin_id,
            text=(
                f"💰 Подтверждение оплаты от {mention}\n"
                f"Тип билета: {ticket_type}"
            ),
            reply_markup=kb
        )



# /help
@router.message(lambda m: m.text == "/help")
async def help_command(message: Message):
    await message.answer("ℹ️ Если у вас возникли вопросы — @Manch7")
