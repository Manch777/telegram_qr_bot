from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart
from config import CHANNEL_ID, INSTAGRAM_LINK, ADMIN_IDS, PAYMENT_LINK, PROMOCODES
from database import (
    add_user, update_status, get_status,
    get_paid_status, set_paid_status,
    count_registered, count_activated,
    get_registered_users, get_paid_users,
    clear_database, count_ticket_type, set_ticket_type
)
from qr_generator import generate_qr

router = Router()

# Список промокодов
PROMOCODES = ["PROMO2025", "DISCOUNT50", "FREEENTRY"]

@router.message(CommandStart())
async def start_command(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подписаться на Telegram", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
        [InlineKeyboardButton(text="🎟 Оплатить билет", callback_data="buy_ticket_menu")]
    ])
    text = (
        "Хей! Приветствуем тебя в ЖАЖДА community 🖤\n"
        "Теперь ты точно знаешь, где лучшие тусовки\n\n"
        "Выбери, что хочешь сделать 👇"
    )
    await message.answer(text, reply_markup=keyboard)

# Меню выбора билета
@router.callback_query(F.data == "buy_ticket_menu")
async def ticket_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎫 Билет 1+1", callback_data="ticket_1plus1")],
        [InlineKeyboardButton(text="🎫 1 билет", callback_data="ticket_single")],
        [InlineKeyboardButton(text="🎟 У меня есть промокод", callback_data="ticket_promocode")]
    ])
    await callback.message.answer("Выбери тип билета:", reply_markup=kb)

# 1+1 билет
@router.callback_query(F.data == "ticket_1plus1")
async def buy_1plus1(callback: CallbackQuery):
    count = await count_ticket_type("1+1")
    if count >= 5:
        await callback.message.answer("❌ Акция '1+1' больше недоступна, лимит в 5 продаж исчерпан.")
        return
    await process_payment(callback, "1+1")

# 1 билет
@router.callback_query(F.data == "ticket_single")
async def buy_single(callback: CallbackQuery):
    await process_payment(callback, "single")

# Промокод
@router.callback_query(F.data == "ticket_promocode")
async def ask_promocode(callback: CallbackQuery):
    await callback.message.answer("Введите ваш промокод:")
    # Сохраним состояние пользователя, чтобы поймать ввод
    await update_status(callback.from_user.id, "waiting_promocode")

@router.message(F.text & ~F.text.startswith("/"))
async def handle_promocode(message: Message):
    status = await get_status(message.from_user.id)
    if status != "waiting_promocode":
        return

    code = (message.text or "").strip().upper()
    if code not in PROMOCODES:
        await message.answer("❌ Неверный промокод. Попробуйте снова или нажмите /start.")
        return

    # фиксируем тип билета и возвращаем статус в норму
    await set_ticket_type(message.from_user.id, "promocode")
    await update_status(message.from_user.id, "не активирован")

    # продолжаем оплату как раньше
    await process_payment(message, "promocode", from_message=True)

# Универсальная функция оплаты
async def process_payment(callback_or_message, ticket_type, from_message=False):
    user_id = callback_or_message.from_user.id
    username = callback_or_message.from_user.username or "Без ника"

    # Проверка статуса оплаты
    paid_status = await get_paid_status(user_id)
    if paid_status == "оплатил":
        if from_message:
            await callback_or_message.answer("✅ Вы уже оплатили. QR-код был отправлен ранее.")
        else:
            await callback_or_message.answer("✅ Вы уже оплатили. QR-код был отправлен ранее.", show_alert=True)
        return

    # Добавляем пользователя и тип билета в БД
    await add_user(user_id, username)
    await set_ticket_type(user_id, ticket_type)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=PAYMENT_LINK)],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid:{user_id}")]
    ])
    text = (
        f"Вы выбрали билет: {ticket_type}\n"
        "Стоимость — 250 руб (или скидка по акции/промокоду)\n\n"
        "❗️ Не забудьте в комментариях платежа указать свой ник в Telegram."
    )
    if from_message:
        await callback_or_message.answer(text, reply_markup=kb)
    else:
        await callback_or_message.message.answer(text, reply_markup=kb)

# Подтверждение оплаты пользователем
@router.callback_query(F.data.startswith("paid:"))
async def payment_confirmation(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    username = callback.from_user.username or "Без ника"

    paid_status = await get_paid_status(user_id)
    if paid_status == "оплатил":
        await callback.answer("✅ Вы уже оплатили. QR-код был отправлен ранее.", show_alert=True)
        return
    elif paid_status == "на проверке":
        await callback.answer("⏳ Ваша оплата уже на проверке. Пожалуйста, подождите.", show_alert=True)
        return

    await set_paid_status(user_id, "на проверке")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("⏳ Ваше подтверждение отправлено администратору. Ожидайте одобрения.")

    ticket_type = await get_status(user_id)  # предполагается, что get_status теперь возвращает ticket_type
    for admin_id in ADMIN_IDS:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"approve:{user_id}")],
            [InlineKeyboardButton(text="❌ Не подтверждена", callback_data=f"reject:{user_id}")]
        ])
        await callback.bot.send_message(
            chat_id=admin_id,
            text=f"💰 Пользователь @{username} подтвердил оплату.\nТип билета: {ticket_type}",
            reply_markup=kb
        )

@router.message(lambda msg: msg.text == "/help")
async def help_command(message: Message):
    await message.answer(
        "ℹ️ Если у вас возникли вопросы или проблемы, пожалуйста, обратитесь к администратору:\n"
        "@Manch7"
    )
