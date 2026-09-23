import asyncio
import json

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from config import (
    ADMIN_CONTACT,
    CHANNEL_ID,
    INSTAGRAM_LINK,
    PAYMENT_LINK,
    SBP_PAYMENT_DETAILS,
)
from database import (
    add_subscriber,
    add_user,
    get_meta,
    get_one_plus_one_limit,
    get_paid_status_by_id,
    get_role_user_ids,
    get_row,
    get_unique_one_plus_one_attempters_for_event,
    log_one_plus_one_attempt,
    remaining_one_plus_one_for_event,
    set_meta,
    set_paid_status_by_id,
    set_ticket_type_by_id,
)


router = Router()

# Users currently waiting to enter a promo code.
_AWAIT_PROMO = set()

# Stores the last bot screen message ID for each user.
_LAST_MSG: dict[int, int] = {}


# ---------------------------------------------------------------------------
# Navigation and screen helpers
# ---------------------------------------------------------------------------

def _event_off() -> bool:
    """Return True when there is no active event."""
    return (config.EVENT_CODE or "").strip().lower() == "none"


def _root_text() -> str:
    return (
        "Hey! Welcome to the ZHAZHDA community 🖤\n"
        "Now you know where to find the best parties.\n\n"
        "Choose what you'd like to do 👇"
    )


def _root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Follow on Telegram",
                    url=f"https://t.me/{CHANNEL_ID.lstrip('@')}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📷 Follow on Instagram",
                    url=INSTAGRAM_LINK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎟 Buy a Ticket",
                    callback_data="buy_ticket_menu",
                )
            ],
        ]
    )


async def _ticket_menu_kb() -> InlineKeyboardMarkup:
    rows = []

    # Show the 1+1 ticket option only when a positive limit is configured.
    try:
        limit = await get_one_plus_one_limit(config.EVENT_CODE)
    except Exception:
        limit = None

    if limit and limit > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🎫 1+1 Ticket",
                    callback_data="ticket_1plus1",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🎫 Single Ticket",
                callback_data="ticket_single",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="🎟 I Have a Promo Code",
                callback_data="ticket_promocode",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Back",
                callback_data="back:start",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back_to_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Back",
                    callback_data="back:start",
                )
            ]
        ]
    )


def _back_to_ticket_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Back",
                    callback_data="back:ticket",
                )
            ]
        ]
    )


def _payment_kb(row_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Pay",
                    url=PAYMENT_LINK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ I Have Paid",
                    callback_data=f"paid_row:{row_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Back",
                    callback_data="back:ticket",
                )
            ],
        ]
    )


@router.callback_query(F.data.startswith("back_to_menu:"))
async def back_from_reject(callback: CallbackQuery):
    """Return to the ticket menu after a payment rejection."""
    await callback.answer()

    # Extract the ticket row ID from the callback payload.
    try:
        row_id = int(callback.data.split(":")[1])
    except Exception:
        row_id = None

    # Remove the payment rejection message when possible.
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Allow the user to retry payment after a rejection.
    if row_id is not None:
        try:
            current_status = await get_paid_status_by_id(row_id)

            if current_status == "отклонено":
                await set_paid_status_by_id(
                    row_id,
                    "не оплатил",
                )
        except Exception:
            pass

    if _event_off():
        await _push_screen(
            callback.bot,
            callback.from_user.id,
            (
                "There are no active events right now. "
                "We'll let you know as soon as a new event is announced. 🖤"
            ),
            _back_to_start_kb(),
        )
    else:
        await _show_ticket_menu(
            callback.bot,
            callback.from_user.id,
        )


async def _push_screen(
    bot,
    chat_id: int,
    text: str,
    kb: InlineKeyboardMarkup,
):
    """
    Replace the user's previous bot screen with a new one.

    A payment-review message is protected from automatic deletion while
    the payment is waiting for administrator confirmation.
    """
    protected_id_raw = await get_meta(
        f"review_msg:{chat_id}"
    )

    try:
        protected_id = (
            int(protected_id_raw)
            if protected_id_raw
            else None
        )
    except Exception:
        protected_id = None

    last_id = _LAST_MSG.get(chat_id)

    # Delete the previous screen unless it is protected.
    if last_id and (
        protected_id is None
        or last_id != protected_id
    ):
        try:
            await bot.delete_message(
                chat_id,
                last_id,
            )
        except Exception:
            pass

    sent = await bot.send_message(
        chat_id,
        text,
        reply_markup=kb,
    )

    _LAST_MSG[chat_id] = sent.message_id
    return sent


async def _show_root(bot, chat_id: int):
    return await _push_screen(
        bot,
        chat_id,
        _root_text(),
        _root_kb(),
    )


async def _show_ticket_menu(bot, chat_id: int):
    kb = await _ticket_menu_kb()

    return await _push_screen(
        bot,
        chat_id,
        "Choose a ticket type:",
        kb,
    )


async def _notify_wishers_1p1_available(
    bot,
    event_code: str,
):
    """
    Notify users who previously tried to buy a 1+1 ticket.

    Notifications are limited to the number of currently available
    1+1 ticket slots.
    """
    remaining = await remaining_one_plus_one_for_event(
        event_code
    )

    if not remaining or remaining <= 0:
        return

    rows = await get_unique_one_plus_one_attempters_for_event(
        event_code
    )

    if not rows:
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎟 Buy a Ticket",
                    callback_data="ticket_1plus1",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Back",
                    callback_data="back:ticket",
                )
            ],
        ]
    )

    sent = 0

    for row in rows:
        user_id = int(row["user_id"])

        try:
            await bot.send_message(
                user_id,
                (
                    f"✨ 1+1 tickets are available again for "
                    f"“{event_code}”. Get yours while they last 👇"
                ),
                reply_markup=kb,
            )
            sent += 1
        except Exception:
            pass

        if sent >= remaining:
            break

        # Small delay to reduce the chance of hitting Telegram rate limits.
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# User flow
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def start_command(message: Message):
    """Register the user as a subscriber and show the main menu."""
    await add_subscriber(
        message.from_user.id,
        message.from_user.username,
    )

    await _show_root(
        message.bot,
        message.from_user.id,
    )


@router.callback_query(F.data == "back:start")
async def back_start(callback: CallbackQuery):
    await callback.answer()

    await _show_root(
        callback.bot,
        callback.from_user.id,
    )


@router.callback_query(F.data == "back:ticket")
async def back_ticket(callback: CallbackQuery):
    await callback.answer()

    if _event_off():
        await _push_screen(
            callback.bot,
            callback.from_user.id,
            (
                "There are no active events right now. "
                "We'll let you know as soon as a new event is announced. 🖤"
            ),
            _back_to_start_kb(),
        )
        return

    await _show_ticket_menu(
        callback.bot,
        callback.from_user.id,
    )


@router.callback_query(F.data == "buy_ticket_menu")
async def ticket_menu(callback: CallbackQuery):
    await callback.answer()

    # Remove a previous event notification when this menu was opened from it.
    try:
        await callback.message.delete()
    except Exception:
        pass

    await add_subscriber(
        callback.from_user.id,
        callback.from_user.username,
    )

    if _event_off():
        await _push_screen(
            callback.bot,
            callback.from_user.id,
            (
                "There are no active events right now.\n"
                "We'll let you know as soon as the next event is announced. 🖤"
            ),
            _back_to_start_kb(),
        )
        return

    # Create a draft purchase before the user selects a ticket type.
    username = callback.from_user.username or "No username"

    draft_row_id = await add_user(
        user_id=callback.from_user.id,
        username=username,
        event_code=config.EVENT_CODE,
        ticket_type="—",
    )

    # Store the draft row ID so the same purchase can be updated later.
    await set_meta(
        f"draft_row:{callback.from_user.id}",
        str(draft_row_id),
    )

    await _show_ticket_menu(
        callback.bot,
        callback.from_user.id,
    )


# ---------------------------------------------------------------------------
# Ticket selection
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "ticket_1plus1")
async def buy_1plus1(callback: CallbackQuery):
    await callback.answer()

    if _event_off():
        await _push_screen(
            callback.bot,
            callback.from_user.id,
            (
                "There are no active events right now. "
                "We'll notify you as soon as a new event is announced. 🖤"
            ),
            _back_to_start_kb(),
        )
        return

    limit = await get_one_plus_one_limit(
        config.EVENT_CODE
    )

    if limit is None or limit <= 0:
        await log_one_plus_one_attempt(
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            event_code=config.EVENT_CODE,
        )

        await _push_screen(
            callback.bot,
            callback.from_user.id,
            "❌ The 1+1 offer is currently unavailable for this event.",
            _back_to_ticket_kb(),
        )
        return

    left = await remaining_one_plus_one_for_event(
        config.EVENT_CODE
    )

    if left is not None and left <= 0:
        await log_one_plus_one_attempt(
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            event_code=config.EVENT_CODE,
        )

        await _push_screen(
            callback.bot,
            callback.from_user.id,
            "❌ The 1+1 offer is no longer available for this event.",
            _back_to_ticket_kb(),
        )
        return

    await _present_payment(
        callback,
        ticket_type="1+1",
    )


@router.callback_query(F.data == "ticket_single")
async def buy_single(callback: CallbackQuery):
    await callback.answer()

    if _event_off():
        await _push_screen(
            callback.bot,
            callback.from_user.id,
            (
                "There are no active events right now. "
                "We'll notify you as soon as a new event is announced. 🖤"
            ),
            _back_to_start_kb(),
        )
        return

    await _present_payment(
        callback,
        ticket_type="single",
    )


# ---------------------------------------------------------------------------
# Promo codes
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "ticket_promocode")
async def ask_promocode(callback: CallbackQuery):
    await callback.answer()

    if _event_off():
        await _push_screen(
            callback.bot,
            callback.from_user.id,
            (
                "There are no active events right now. "
                "Once a new event is announced, "
                "you'll be able to use a promo code. 🖤"
            ),
            _back_to_start_kb(),
        )
        return

    _AWAIT_PROMO.add(callback.from_user.id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Back",
                    callback_data="promo_cancel",
                )
            ]
        ]
    )

    await _push_screen(
        callback.bot,
        callback.from_user.id,
        "Enter your promo code in a single message:",
        kb,
    )


@router.callback_query(F.data == "promo_cancel")
async def cancel_promocode(callback: CallbackQuery):
    await callback.answer()

    _AWAIT_PROMO.discard(callback.from_user.id)

    if _event_off():
        await _push_screen(
            callback.bot,
            callback.from_user.id,
            "Cancelled. There are no active events right now. /start",
            _back_to_start_kb(),
        )
    else:
        await _show_ticket_menu(
            callback.bot,
            callback.from_user.id,
        )


async def _get_event_promocodes() -> set[str]:
    """
    Return promo codes configured for the active event.

    Event-specific promo codes are stored in bot_meta under
    "promocodes:<EVENT_CODE>". config.PROMOCODES is used as a fallback.
    """
    codes: set[str] = set()

    raw = await get_meta(
        f"promocodes:{config.EVENT_CODE}"
    )

    promo_data = None

    if raw:
        try:
            promo_data = json.loads(raw)
        except Exception:
            promo_data = None

    if isinstance(promo_data, list):
        for code in promo_data:
            if isinstance(code, str) and code.strip():
                codes.add(
                    code.strip().upper()
                )

    elif isinstance(promo_data, str):
        # Support legacy comma-separated promo code storage.
        parts = [
            part.strip()
            for part in promo_data.split(",")
        ]

        for code in parts:
            if code:
                codes.add(code.upper())

    # Use environment-configured promo codes as a fallback.
    try:
        codes |= {
            str(code).strip().upper()
            for code in config.PROMOCODES
            if str(code).strip()
        }
    except Exception:
        pass

    return codes


@router.message(F.text & ~F.text.startswith("/"))
async def handle_promocode(message: Message):
    if message.from_user.id not in _AWAIT_PROMO:
        return

    if _event_off():
        _AWAIT_PROMO.discard(
            message.from_user.id
        )

        await _push_screen(
            message.bot,
            message.from_user.id,
            (
                "There are no active events right now. "
                "You'll be able to use a promo code later."
            ),
            _back_to_start_kb(),
        )
        return

    user_code = (
        message.text or ""
    ).strip().upper()

    valid_codes = await _get_event_promocodes()

    if user_code not in valid_codes:
        await message.answer(
            "❌ Invalid promo code. Please try again."
        )
        return

    _AWAIT_PROMO.discard(
        message.from_user.id
    )

    # A promo code becomes the ticket type while its price uses
    # the shared "promocode" price configuration.
    await _present_payment(
        message,
        ticket_type=user_code,
        from_message=True,
    )


# ---------------------------------------------------------------------------
# Pricing and payment
# ---------------------------------------------------------------------------

async def _price_for_ticket(
    ticket_type: str,
) -> int | None:
    """
    Return the configured price for a ticket type.

    Prices are stored in bot_meta under "prices:<EVENT_CODE>".
    """
    raw = await get_meta(
        f"prices:{config.EVENT_CODE}"
    )

    try:
        prices = json.loads(raw) if raw else {}
    except Exception:
        prices = {}

    if ticket_type == "1+1":
        key = "1+1"
    elif ticket_type == "single":
        key = "single"
    else:
        key = "promocode"

    value = prices.get(key)

    try:
        return int(value)
    except Exception:
        return None


async def _present_payment(
    obj,
    ticket_type: str,
    from_message: bool = False,
):
    """Prepare a purchase and display the payment screen."""
    # Re-check the event to avoid starting a payment after it was disabled.
    if _event_off():
        target = (
            obj.message
            if hasattr(obj, "message")
            else obj
        )

        await _push_screen(
            target.bot,
            target.chat.id,
            (
                "There are no active events right now. "
                "We'll announce a new event soon. 🖤"
            ),
            _back_to_start_kb(),
        )
        return

    user = obj.from_user
    user_id = user.id
    username = user.username or "No username"

    # Reuse the draft purchase created when the user opened the ticket menu.
    draft_raw = await get_meta(
        f"draft_row:{user_id}"
    )

    row_id = None

    if draft_raw:
        try:
            row_id = int(draft_raw)
        except Exception:
            row_id = None

    if row_id:
        await set_ticket_type_by_id(
            row_id,
            ticket_type,
        )
    else:
        # Create a new purchase if the draft record is unavailable.
        row_id = await add_user(
            user_id=user_id,
            username=username,
            event_code=config.EVENT_CODE,
            ticket_type=ticket_type,
        )

        await set_meta(
            f"draft_row:{user_id}",
            str(row_id),
        )

    # Keep the existing database status value for compatibility.
    await set_paid_status_by_id(
        row_id,
        "в процессе оплаты",
    )

    title_map = {
        "single": "Single Ticket",
        "1+1": "1+1 Ticket",
    }

    pretty_type = title_map.get(
        ticket_type,
        f"Promo Code “{ticket_type}”",
    )

    price = await _price_for_ticket(
        ticket_type
    )

    price_line = (
        f"\nPrice: {price}"
        if price is not None
        else ""
    )

    payment_details = (
        f"\n\nYou can also pay via SBP transfer: {SBP_PAYMENT_DETAILS}"
        if SBP_PAYMENT_DETAILS
        else ""
    )

    text = (
        f"Ticket type: {pretty_type}\n"
        f"Event: {config.EVENT_CODE}"
        f"{price_line}\n\n"
        "After payment, tap “I Have Paid”.\n"
        "⏳ The payment link is valid for 5 minutes!\n"
        "❗️ Don't forget to include your Telegram username "
        "in the payment comment."
        f"{payment_details}"
    )

    bot = obj.bot

    sent = await _push_screen(
        bot,
        user_id,
        text,
        _payment_kb(row_id),
    )

    # Expire an unfinished payment after five minutes.
    asyncio.create_task(
        _expire_payment_after(
            bot=bot,
            chat_id=user_id,
            message_id=sent.message_id,
            row_id=row_id,
            timeout_sec=300,
        )
    )


# ---------------------------------------------------------------------------
# Payment confirmation
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("paid_row:"))
async def payment_confirmation(
    callback: CallbackQuery,
):
    await callback.answer()

    user = callback.from_user
    row_id = int(
        callback.data.split(":")[1]
    )
    username = user.username or "No username"

    row = await get_row(row_id)

    if not row:
        await _push_screen(
            callback.bot,
            user.id,
            "❌ Ticket not found.",
            _back_to_ticket_kb(),
        )
        return

    ticket_type = row["ticket_type"] or "-"
    paid_status = await get_paid_status_by_id(
        row_id
    )

    # Keep existing database status values for compatibility.
    if paid_status == "оплатил":
        await _push_screen(
            callback.bot,
            user.id,
            (
                "✅ Your payment has already been confirmed. "
                "The QR code was sent earlier."
            ),
            _back_to_start_kb(),
        )
        return

    if paid_status == "на проверке":
        await _push_screen(
            callback.bot,
            user.id,
            (
                "⏳ Your payment is already under review. "
                "Please wait."
            ),
            _back_to_start_kb(),
        )
        return

    await set_paid_status_by_id(
        row_id,
        "на проверке",
    )

    # Show and protect the payment-review screen.
    sent = await _push_screen(
        callback.bot,
        user.id,
        (
            "⏳ Reviewing your payment\n"
            "We'll send your QR ticket as soon as the payment is approved. 🖤"
        ),
        _back_to_start_kb(),
    )

    await set_meta(
        f"review_msg:{user.id}",
        str(sent.message_id),
    )

    # Notify the payments administrator.
    kb_admin = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Approve Payment",
                    callback_data=f"approve_row:{row_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Reject Payment",
                    callback_data=f"reject_row:{row_id}",
                )
            ],
        ]
    )

    recipients = await get_role_user_ids(
        "payments_admin"
    )

    # Fall back to an administrator if no dedicated payment reviewer exists.
    if not recipients:
        recipients = await get_role_user_ids(
            "admin"
        )

    recipient_id = (
        recipients[0]
        if recipients
        else None
    )

    if recipient_id:
        await callback.bot.send_message(
            chat_id=recipient_id,
            text=(
                f"💰 Payment confirmation for @{username}\n"
                f"Ticket type: {ticket_type}"
            ),
            reply_markup=kb_admin,
        )


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

@router.message(
    lambda message: message.text == "/help"
)
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
            "ℹ️ If you have any questions or issues, "
            f"contact the administrator:{contact_text}"
        ),
        _back_to_start_kb(),
    )


# ---------------------------------------------------------------------------
# Payment expiration
# ---------------------------------------------------------------------------

async def _expire_payment_after(
    bot,
    chat_id: int,
    message_id: int,
    row_id: int,
    timeout_sec: int = 300,
):
    """Expire an unfinished payment after the configured timeout."""
    await asyncio.sleep(timeout_sec)

    # Keep the ticket type and event so a released 1+1 slot can be announced.
    row = await get_row(row_id)

    ticket_type = (
        (row["ticket_type"] or "").strip().lower()
        if row
        else ""
    )

    event_code = (
        row["event_code"]
        if row
        else None
    )

    status = await get_paid_status_by_id(
        row_id
    )

    # Keep existing database status values for compatibility.
    if status == "в процессе оплаты":
        try:
            await set_paid_status_by_id(
                row_id,
                "не оплатил",
            )
        except Exception:
            pass

        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        except Exception:
            pass

        kb = await _ticket_menu_kb()

        await _push_screen(
            bot,
            chat_id,
            (
                "⏰ Payment time has expired.\n"
                "Please choose a ticket type again:"
            ),
            kb,
        )

        # Notify waiting users if a 1+1 slot became available.
        if ticket_type == "1+1" and event_code:
            await _notify_wishers_1p1_available(
                bot,
                event_code,
            )
