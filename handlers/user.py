# handlers/user.py
from aiogram import Router, F
import config
import asyncio
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from config import CHANNEL_ID, PAYMENT_LINK, INSTAGRAM_LINK, PROMOCODES, ADMIN_IDS
from database import (
    add_user,  get_row,                             # -> возвращает row_id (id строки покупки)
    get_paid_status_by_id, set_paid_status_by_id,
    count_ticket_type_paid_for_event, count_ticket_type_for_event,
    log_one_plus_one_attempt, add_subscriber,
    get_one_plus_one_limit, remaining_one_plus_one_for_event,
)

router = Router()

# Локальный флаг ожидания промокода (без FSM, чтобы не трогать main.py)
_AWAIT_PROMO = set()   # set[int] of user_id


def _event_off() -> bool:
    return (config.EVENT_CODE or "").strip().lower() == "none"

# /start: приветствие + 2 кнопки
@router.message(CommandStart())
async def start_command(message: Message):
    
    await add_subscriber(message.from_user.id, message.from_user.username)
    
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

    # на всякий случай обновим подписку
    await add_subscriber(callback.from_user.id, callback.from_user.username)

    # если мероприятий нет — сообщаем и выходим
    if _event_off():
        await callback.message.answer(
            "Сейчас мероприятий нет.\nМы сообщим, как только объявим следующее событие. 🖤"
        )
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎫 Билет 1+1", callback_data="ticket_1plus1")],
        [InlineKeyboardButton(text="🎫 1 билет", callback_data="ticket_single")],
        [InlineKeyboardButton(text="🎟 У меня есть промокод", callback_data="ticket_promocode")]
    ])
    await callback.message.answer("Выбери тип билета:", reply_markup=kb)


# Билет 1+1 (лимит 5 оплаченных на текущее мероприятие)
@router.callback_query(F.data == "ticket_1plus1")
async def buy_1plus1(callback: CallbackQuery):
    if _event_off():
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Сейчас мероприятий нет. Как только появится новое — пришлём уведомление. 🖤"
        )
        return
    
    limit = await get_one_plus_one_limit(config.EVENT_CODE)
    if limit is None or limit <= 0:
        await callback.message.answer("❌ Акция '1+1' сейчас недоступна для этого мероприятия.")
        return

    left = await remaining_one_plus_one_for_event(config.EVENT_CODE)
    if left is not None and left <= 0:
        await callback.message.answer("❌ Акция '1+1' больше недоступна для этого мероприятия.")
        return

    await _present_payment(callback, ticket_type="1+1")
    
# 1 билет
@router.callback_query(F.data == "ticket_single")
async def buy_single(callback: CallbackQuery):
    if _event_off():
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Сейчас мероприятий нет. Как только появится новое — пришлём уведомление. 🖤"
        )
        return
    await _present_payment(callback, ticket_type="single")


# Промокод — запрос ввода
@router.callback_query(F.data == "ticket_promocode")
async def ask_promocode(callback: CallbackQuery):
    if _event_off():
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Сейчас мероприятий нет. Как только появится новое — пришлём уведомление. 🖤"
        )
        return
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
    if _event_off():
        _AWAIT_PROMO.discard(message.from_user.id)
        await message.answer(
            "Сейчас мероприятий нет. Промокод можно будет применить, когда объявим новое событие."
        )
        return
    code = (message.text or "").strip().upper()
    if code not in PROMOCODES:
        await message.answer("❌ Неверный промокод. Попробуйте снова или нажмите /start.")
        return

    _AWAIT_PROMO.discard(message.from_user.id)
    # ⬇️ СЮДА: вместо "promocode" пишем сам код
    await _present_payment(message, ticket_type=code, from_message=True)


# Общая функция показа оплаты — СОЗДАЁТ новую запись (новую покупку) и даёт кнопку "Я оплатил"
async def _present_payment(obj, ticket_type: str, from_message: bool = False):
    # стоп, если событие выключено (на случай гонок/старых кнопок)
    if _event_off():
        # obj может быть CallbackQuery или Message
        target = obj.message if hasattr(obj, "message") else obj
        await target.answer("Сейчас мероприятий нет. Скоро расскажем про новое событие. 🖤")
        return
    
    user = obj.from_user
    user_id = user.id
    username = user.username or "Без ника"

    # Каждая покупка = новая строка в БД
    row_id = await add_user(
        user_id=user_id,
        username=username,
        event_code=config.EVENT_CODE,           # <-- динамическое значение
        ticket_type=ticket_type
)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=PAYMENT_LINK)],
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid_row:{row_id}")]
    ])
    text = (
        f"Вы выбрали: {ticket_type}\n"
        f"Мероприятие: {config.EVENT_CODE}\n\n"
        "После оплаты нажмите «Я оплатил».\n"
        "❗️В комментариях платежа укажите свой Telegram-ник."
    )
    # отправляем сообщение с кнопками и получаем объект сообщения
    if from_message:
        sent = await obj.answer(text, reply_markup=kb)
    else:
        sent = await obj.message.answer(text, reply_markup=kb)

    # запускаем таймер на 5 минут — если не оплачен, пришлём уведомление и новое меню
    asyncio.create_task(
        _expire_payment_after(
            bot=obj.bot,
            chat_id=user_id,
            message_id=sent.message_id,
            row_id=row_id,
            timeout_sec=300  # 5 минут
        )
    )


# Пользователь нажимает "Я оплатил" — по КОНКРЕТНОЙ покупке (row_id)
@router.callback_query(F.data.startswith("paid_row:"))
async def payment_confirmation(callback: CallbackQuery):
    user = callback.from_user
    row_id = int(callback.data.split(":")[1])
    username = user.username or "Без ника"

    # 1) Берём покупку по row_id
    row = await get_row(row_id)
    if not row:
        await callback.answer("❌ Билет не найден.", show_alert=True)
        return

    ticket_type = row["ticket_type"] or "-"
    paid_status = row["paid"]

    paid_status = await get_paid_status_by_id(row_id)
    if paid_status == "оплатил":
        await callback.answer("✅ Вы уже оплатили. QR-код был отправлен ранее.", show_alert=True)
        return
    if paid_status == "на проверке":
        await callback.answer("⏳ Ваша оплата уже на проверке. Пожалуйста, подождите.", show_alert=True)
        return

    # Ставим флаг "на проверке" только для ЭТОЙ покупки
    await set_paid_status_by_id(row_id, "на проверке")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("⏳ Подтверждение отправлено администратору. Ожидайте одобрения.")

    # Уведомляем админов с коллбэками по row_id
    kb_admin = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"approve_row:{row_id}")],
        [InlineKeyboardButton(text="❌ Не подтверждена",   callback_data=f"reject_row:{row_id}")]
    ])
    recipient_id = getattr(config, "PAYMENTS_ADMIN_ID", None)
    if not recipient_id:
        # фолбэк: если не задан, шлём первому из ADMIN_IDS (или предупредим)
        recipient_id = ADMIN_IDS[0] if ADMIN_IDS else None

    if recipient_id:
        await callback.bot.send_message(
            chat_id=recipient_id,
            text=f"💰 Подтверждение оплаты пользователя @{username}\nТип билета: {ticket_type}",
            reply_markup=kb_admin
        )
    else:
        await callback.message.answer(
            "⚠️ Администратор для подтверждения оплаты не настроен. Сообщите организатору."
        )


# /help
@router.message(lambda m: m.text == "/help")
async def help_command(message: Message):
    await message.answer("ℹ️ Если у вас возникли вопросы — @Manch7")



#Хелпер для тайм-аута
async def _expire_payment_after(bot, chat_id: int, message_id: int, row_id: int, timeout_sec: int = 300):
    # ждём 5 минут
    await asyncio.sleep(timeout_sec)

    # проверяем статус по конкретной покупке
    from database import get_paid_status_by_id  # локальный импорт, чтобы избежать циклов
    status = await get_paid_status_by_id(row_id)

    if status in ("не оплатил", "отклонено"):
        # пробуем убрать старые кнопки «Оплатить / Я оплатил»
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except Exception:
            pass  # сообщение могли удалить/изменить — не критично

        # присылаем уведомление и заново меню выбора
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
