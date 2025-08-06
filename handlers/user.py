from aiogram import Router, F
from aiogram.types import FSInputFile, Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, WebAppInfo
from aiogram.filters import CommandStart
from config import CHANNEL_ID, INSTAGRAM_LINK, ADMIN_IDS, PAYMENT_LINK
from database import (
    add_user, update_status, get_status,
    get_paid_status, set_paid_status,
    count_registered, count_activated,
    get_registered_users, get_paid_users,
    clear_database
)
from qr_generator import generate_qr

router = Router()

@router.message(CommandStart())
async def start_command(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подписаться на Telegram", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
        [InlineKeyboardButton(text="📷 Подписаться на Instagram", url=INSTAGRAM_LINK)],
        [InlineKeyboardButton(text="🔍 Проверить подписку", callback_data="check_subscription")]
    ])
    await message.answer("Хей! Приветствуем тебя в ЖАЖДА community 🖤
                           Теперь ты точно знаешь, где лучшие тусовки

                           Подпишись на наши каналы, чтобы получить проходку со скидкой 👇", reply_markup=keyboard)

@router.callback_query(F.data == "check_subscription")
async def check_subscription(callback: CallbackQuery):
    user = callback.from_user
    user_id = user.id
    username = user.username

    # проверка подписки
    member = await callback.bot.get_chat_member(CHANNEL_ID, user_id)

    if member.status not in ["member", "administrator", "creator"]:
        await callback.message.answer("❌ Вы ещё не подписаны на Telegram-канал!")
        return
    

    # проверка, получал ли уже QR
    status = await get_status(user_id)
    paid_status = await get_paid_status(user_id)  # добавим эту функцию

    if status is not None:
        # Если уже был добавлен
        if paid_status == "оплатил":
            await callback.message.answer("⚠️ Вы уже получали QR-код ранее. Повторная выдача невозможна.")
        elif paid_status == "на проверке":
            await callback.message.answer("💳 Оплата на проверке. Ожидайте подтверждения и получения QR-кода.")
        else:
            await callback.message.answer("❗️ Для получения QR-кода необходимо оплатить участие.")
        return

    await add_user(user_id, username)

 # Отправляем кнопку для оплаты
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=PAYMENT_LINK)],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid:{user_id}")]
    ])
    await callback.message.answer("Вау! Кажется, ты все сделал правильно ✨
                                    Твоя скидка 50% активирована, стоимость билета - 250 руб

                                   ❗️Не забудь в комментариях платежа указать свой ник в telegram 

                                    Ну что, готов оплатить?", reply_markup=kb)

@router.callback_query(F.data.startswith("paid:"))
async def payment_confirmation(callback: CallbackQuery):
    user = callback.from_user
    user_id = int(callback.data.split(":")[1])
    username = user.username or "Без ника"

    # Проверка текущего статуса оплаты
    paid_status = await get_paid_status(user_id)

    if paid_status == "оплатил":
        await callback.answer("✅ Вы уже оплатили. QR-код был отправлен ранее.", show_alert=True)
        return
    elif paid_status == "на проверке":
        await callback.answer("⏳ Ваша оплата уже на проверке. Пожалуйста, подождите.", show_alert=True)
        return

    # Обновляем статус оплаты

    await set_paid_status(user_id, "на проверке")

    await callback.message.edit_reply_markup(reply_markup=None)  # Удаляем старые кнопки
    
    await callback.message.answer("⏳ Ваше подтверждение отправлено администратору. Ожидайте одобрения.")

    # Отправка админу уведомления
    for admin_id in ADMIN_IDS:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"approve:{user_id}")],
            [InlineKeyboardButton(text="❌ Не подтверждена", callback_data=f"reject:{user_id}")]
        ])
        await callback.bot.send_message(
            chat_id=admin_id,
            text=f"💰 Пользователь @{username} подтвердил оплату.",
            reply_markup=kb
        )

@router.message(lambda msg: msg.text == "/help")
async def help_command(message: Message):
    await message.answer(
        "ℹ️ Если у вас возникли вопросы или проблемы, пожалуйста, обратитесь к администратору:\n"
        "@Manch7\n\n"
    )
