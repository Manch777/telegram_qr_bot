import asyncio
import json
from io import BytesIO

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from openpyxl import Workbook

import config
from config import (
    ADMIN_BROADCAST_PASSWORD,
    ADMIN_CONTACT,
    ADMIN_EVENT_PASSWORD,
    CHANNEL_ID,
    INSTAGRAM_LINK,
    PAYMENT_LINK,
    SCAN_WEBAPP_URL,
)
from database import (
    add_role,
    clear_database,
    count_activated,
    count_one_plus_one_taken,
    count_paid,
    count_registered,
    get_all_subscribers,
    get_all_users_full,
    get_meta,
    get_one_plus_one_limit,
    get_paid_status_by_id,
    get_role_user_ids,
    get_row,
    get_status,
    get_status_by_id,
    get_ticket_stats_for_event,
    get_unique_one_plus_one_attempters_for_event,
    has_role,
    remaining_one_plus_one_for_event,
    remove_role,
    set_meta,
    set_one_plus_one_limit,
    set_paid_status_by_id,
    update_status,
    update_status_by_id,
)
from qr_generator import generate_qr


router = Router()


# ============================================================================
# Access control
# ============================================================================

async def is_full_admin(uid: int) -> bool:
    """Return True when the user has the full administrator role."""
    return await has_role(uid, "admin")


async def _can_use_scanner(uid: int) -> bool:
    """Return True when the user is allowed to use the ticket scanner."""
    return await has_role(uid, "admin") or await has_role(uid, "scanner")


# ============================================================================
# Admin panel
# ============================================================================

@router.message(lambda msg: msg.text == "/admin")
async def admin_panel(message: Message):
    uid = message.from_user.id

    if await is_full_admin(uid):
        # Configure commands available to full administrators.
        await message.bot.set_my_commands(
            [
                BotCommand(
                    command="analytics",
                    description="📊 Event analytics summary",
                ),
                BotCommand(
                    command="event_tool_set",
                    description="🛠 Event management",
                ),
                BotCommand(
                    command="admin_tool_set",
                    description="🧰 Administration",
                ),
                BotCommand(
                    command="exit_admin",
                    description="↩️ Return to user menu",
                ),
            ],
            scope=BotCommandScopeChat(chat_id=uid),
        )

        await message.answer(
            "🛡 Administrator mode enabled. "
            "Choose the command set you need from the menu."
        )
        return

    if await _can_use_scanner(uid):
        # Configure commands available to scanner-only administrators.
        await message.bot.set_my_commands(
            [
                BotCommand(
                    command="scanner",
                    description="📷 Open Scanner",
                ),
                BotCommand(
                    command="exit_admin",
                    description="↩️ Return to user menu",
                ),
            ],
            scope=BotCommandScopeChat(chat_id=uid),
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📷 Open Scanner",
                        url=SCAN_WEBAPP_URL,
                    )
                ]
            ]
        )

        await message.answer(
            "🛡 Scanner mode enabled.",
            reply_markup=kb,
        )
        return

    await message.answer(
        "🚫 You do not have access to the admin panel."
    )


def _kb_analytics() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Statistics",
                    callback_data="an:report",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Sold Tickets (Current Event)",
                    callback_data="an:stats_this",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📝 1+1 Waiting List",
                    callback_data="an:wishers",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Revenue",
                    callback_data="an:revenue",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📤 Export (Current Event)",
                    callback_data="an:export_this",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📤 Export (All Events)",
                    callback_data="an:export_all",
                )
            ],
        ]
    )


def _kb_event_tools() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔁 Change Event",
                    callback_data="change_event_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📣 Broadcast Latest Post",
                    callback_data="broadcast_last",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📷 Open Scanner",
                    url=SCAN_WEBAPP_URL,
                )
            ],
        ]
    )


def _kb_admin_tools() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔐 Manage Scanner Access",
                    callback_data="scan_access_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧹 Clear Database",
                    callback_data="adm:clear_db",
                )
            ],
        ]
    )


@router.message(lambda m: m.text == "/analytics")
async def admin_menu_analytics(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 Insufficient permissions.")
        return

    await message.answer(
        "Choose a report:",
        reply_markup=_kb_analytics(),
    )


@router.message(lambda m: m.text == "/event_tool_set")
async def admin_menu_event_tools(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 Insufficient permissions.")
        return

    await message.answer(
        "Event tools:",
        reply_markup=_kb_event_tools(),
    )


@router.message(lambda m: m.text == "/admin_tool_set")
async def admin_menu_admin_tools(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer("🚫 Insufficient permissions.")
        return

    await message.answer(
        "Administration:",
        reply_markup=_kb_admin_tools(),
    )


@router.callback_query(F.data == "an:report")
async def cb_an_report(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await callback.answer()
    await _send_report_to(
        callback.bot,
        callback.from_user.id,
    )


@router.callback_query(F.data == "an:stats_this")
async def cb_an_stats_this(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await callback.answer()
    await _send_stats_this_to(
        callback.bot,
        callback.from_user.id,
    )


@router.callback_query(F.data == "an:wishers")
async def cb_an_wishers(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await callback.answer()
    await _send_wishers_to(
        callback.bot,
        callback.from_user.id,
    )


@router.callback_query(F.data == "an:export_this")
async def cb_an_export_this(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Preparing export…",
        show_alert=False,
    )

    await _send_export_to(
        callback.bot,
        callback.from_user.id,
        only_this=True,
    )


@router.callback_query(F.data == "an:export_all")
async def cb_an_export_all(callback: CallbackQuery):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Preparing export…",
        show_alert=False,
    )

    await _send_export_to(
        callback.bot,
        callback.from_user.id,
        only_this=False,
    )


@router.callback_query(F.data == "adm:clear_db")
async def cb_adm_clear_db(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await is_full_admin(callback.from_user.id):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await callback.answer()
    await start_clear_db(
        callback.message,
        state,
    )


# ============================================================================
# WebApp scanner handling
# ============================================================================

@router.message(lambda msg: msg.web_app_data is not None)
async def handle_webapp_data(message: Message):
    if not await _can_use_scanner(message.from_user.id):
        await message.answer(
            "🚫 You do not have permission to scan tickets."
        )
        return

    payload = (
        message.web_app_data.data or ""
    ).strip()

    if not payload:
        await message.answer(
            "⚠️ The scanner returned empty data."
        )
        return

    # Legacy format: a plain numeric payload is treated as a user ID.
    if payload.isdigit():
        user_id = int(payload)
        status = await get_status(user_id)

        if status is None:
            await message.answer(
                "❌ QR code not found."
            )

        elif status == "не активирован":
            await update_status(
                user_id,
                "активирован",
            )

            await message.answer(
                "✅ Ticket activated. "
                "Enjoy the event!"
            )

        else:
            await message.answer(
                "⚠️ This QR code has already been used."
            )

        return

    # Current-compatible formats:
    # R:<row_id>, QR:<...>, or <row_id>:<suffix>.
    payload_value = payload.lstrip()

    if payload_value.lower().startswith("qr:"):
        payload_value = payload_value[3:].lstrip()

    if payload_value.lower().startswith("r:"):
        payload_value = payload_value[2:].lstrip()

    num_str = payload_value.split(":", 1)[0]

    try:
        candidate = int(num_str)
    except ValueError:
        await message.answer(
            "⚠️ Invalid QR code format."
        )
        return

    # Preserve compatibility by trying the value as a legacy user ID first.
    status = await get_status(candidate)

    if status is not None:
        if status == "не активирован":
            await update_status(
                candidate,
                "активирован",
            )

            await message.answer(
                "✅ Ticket activated. "
                "Enjoy the event!"
            )

        else:
            await message.answer(
                "⚠️ This QR code has already been used."
            )

        return

    # Otherwise treat the value as a row ID.
    row = await get_row(candidate)

    if row is None:
        await message.answer(
            "❌ QR code not found."
        )
        return

    status_by_id = await get_status_by_id(
        candidate
    )

    if status_by_id == "не активирован":
        await update_status_by_id(
            candidate,
            "активирован",
        )

        await message.answer(
            "✅ Ticket activated. "
            "Enjoy the event!"
        )

    else:
        await message.answer(
            "⚠️ This QR code has already been used."
        )


# ============================================================================
# Reports and exports
# ============================================================================

@router.message(lambda msg: msg.text == "/report")
async def report(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer(
            "🚫 You do not have permission to use this command."
        )
        return

    await _send_report_to(
        message.bot,
        message.chat.id,
    )


async def export_users_excel(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer(
            "🚫 You do not have permission to use this command."
        )
        return

    await _send_export_to(
        message.bot,
        message.chat.id,
        only_this=(
            message.text == "/export_users_this"
        ),
    )


@router.message(lambda m: m.text == "/stats_this")
async def ticket_stats_this(message: Message):
    if not await is_full_admin(message.from_user.id):
        await message.answer(
            "🚫 You do not have permission to use this command."
        )
        return

    await _send_stats_this_to(
        message.bot,
        message.chat.id,
    )


# ============================================================================
# Leave admin mode
# ============================================================================

@router.message(lambda msg: msg.text == "/exit_admin")
async def exit_admin_mode(message: Message):
    uid = message.from_user.id

    # Reset commands configured specifically for this chat.
    try:
        await message.bot.delete_my_commands(
            scope=BotCommandScopeChat(
                chat_id=uid
            )
        )
    except Exception:
        pass

    # Base commands available to regular users.
    commands = [
        BotCommand(
            command="start",
            description="Start",
        ),
        BotCommand(
            command="help",
            description="ℹ️ Help / Contact Admin",
        ),
    ]

    # Keep the admin entry point available to users with admin/scanner access.
    if (
        await is_full_admin(uid)
        or await _can_use_scanner(uid)
    ):
        commands.append(
            BotCommand(
                command="admin",
                description="🛡 Administrator Mode",
            )
        )

    await message.bot.set_my_commands(
        commands,
        scope=BotCommandScopeChat(
            chat_id=uid
        ),
    )

    await message.answer(
        "↩️ You have exited administrator mode. "
        "Commands have been updated."
    )


# ============================================================================
# Scanner command
# ============================================================================

@router.message(lambda msg: msg.text == "/scanner")
async def scanner_command(message: Message):
    if not await _can_use_scanner(
        message.from_user.id
    ):
        await message.answer(
            "🚫 You do not have permission to use the scanner."
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📷 Open Scanner",
                    url=SCAN_WEBAPP_URL,
                )
            ]
        ]
    )

    await message.answer(
        "Scan the attendee's QR code:",
        reply_markup=keyboard,
    )


# ============================================================================
# Payment approval
# ============================================================================

@router.callback_query(
    F.data.startswith("approve_row:")
)
async def approve_payment(
    callback: CallbackQuery,
):
    await callback.answer(
        "Processing…",
        show_alert=False,
    )

    row_id = int(
        callback.data.split(":")[1]
    )

    row = await get_row(row_id)

    if not row:
        await callback.message.edit_text(
            "❌ Record not found."
        )
        return

    # Mark the payment as approved before generating the QR ticket.
    await set_paid_status_by_id(
        row_id,
        "оплатил",
    )

    ticket_type = row["ticket_type"]
    event_code = row["event_code"] or "-"

    png_bytes = await generate_qr(row_id)

    photo = BufferedInputFile(
        png_bytes,
        filename=f"ticket_{row_id}.png",
    )

    await callback.bot.send_photo(
        chat_id=row["user_id"],
        photo=photo,
        caption=(
            "🎉 Payment confirmed! "
            "Show this QR code at the entrance\n\n"
            f"Your ticket #{row_id}\n"
            f"Type: {ticket_type}\n"
            f"Event: {event_code}\n\n"
            "See you at the event 🫶"
        ),
    )

    await callback.message.edit_text(
        f"✅ Confirmed. The QR code for ticket #{row_id} "
        "has been sent to the user."
    )

    # Remove the protected review message if it still exists.
    uid = row["user_id"]

    protected_id_raw = await get_meta(
        f"review_msg:{uid}"
    )

    if protected_id_raw:
        try:
            await callback.bot.delete_message(
                uid,
                int(protected_id_raw),
            )
        except Exception:
            pass

    # Clear the review-message metadata after removing protection.
    await set_meta(
        f"review_msg:{uid}",
        "",
    )


# ============================================================================
# Payment rejection
# ============================================================================

@router.callback_query(
    F.data.startswith("reject_row:")
)
async def reject_payment(
    callback: CallbackQuery,
):
    row_id = int(
        callback.data.split(":")[1]
    )

    row = await get_row(row_id)

    if not row:
        await callback.message.edit_text(
            "❌ Record not found."
        )
        return

    await set_paid_status_by_id(
        row_id,
        "отклонено",
    )

    kb = InlineKeyboardMarkup(
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
                    callback_data=f"back_to_menu:{row_id}",
                )
            ],
        ]
    )

    admin_contact_text = (
        f" or contact the administrator: {ADMIN_CONTACT}"
        if ADMIN_CONTACT
        else ""
    )

    sent = await callback.bot.send_message(
        chat_id=row["user_id"],
        text=(
            "💔 Oops! It looks like the payment did not go through.\n"
            "Please check the payment details"
            f"{admin_contact_text}"
        ),
        reply_markup=kb,
    )

    uid = row["user_id"]

    protected_id_raw = await get_meta(
        f"review_msg:{uid}"
    )

    if protected_id_raw:
        try:
            await callback.bot.delete_message(
                uid,
                int(protected_id_raw),
            )
        except Exception:
            pass

    await set_meta(
        f"review_msg:{uid}",
        "",
    )

    # Start a new five-minute payment timer after rejection.
    asyncio.create_task(
        _expire_payment_after_admin(
            bot=callback.bot,
            chat_id=row["user_id"],
            message_id=sent.message_id,
            row_id=row_id,
            timeout_sec=300,
        )
    )

    await callback.message.edit_text(
        f"❌ Payment for ticket #{row_id} was rejected. "
        "The user has been notified."
    )


# ============================================================================
# Database cleanup
# ============================================================================

class ClearDBStates(StatesGroup):
    waiting_for_password = State()


@router.message(lambda msg: msg.text == "/clear_db")
async def start_clear_db(
    message: Message,
    state: FSMContext,
):
    if not await is_full_admin(
        message.from_user.id
    ):
        await message.answer(
            "🚫 You do not have permission to use this command."
        )
        return

    await message.answer(
        "❗️ Enter the password to clear the database:"
    )

    await state.set_state(
        ClearDBStates.waiting_for_password
    )


@router.message(
    ClearDBStates.waiting_for_password
)
async def process_password(
    message: Message,
    state: FSMContext,
):
    if (
        (message.text or "").strip()
        == (ADMIN_EVENT_PASSWORD or "")
    ):
        await clear_database()

        await message.answer(
            "✅ Database cleared successfully."
        )

    else:
        await message.answer(
            "❌ Incorrect password. Access denied."
        )

    await state.clear()

# ============================================================================
# Event configuration FSM
# ============================================================================

class ChangeEventStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_event_name = State()
    waiting_for_1p1_limit = State()
    waiting_for_price_1p1 = State()
    waiting_for_price_single = State()
    waiting_for_price_promocode = State()
    waiting_for_promocode_list = State()


def _normalize_event_name(raw: str) -> str:
    """Normalize whitespace in an event name."""
    return " ".join(
        (raw or "").strip().split()
    )


@router.callback_query(
    F.data == "change_event_menu"
)
async def change_event_menu_cb(
    callback: CallbackQuery,
):
    if not await is_full_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await callback.message.answer(
        f"Current event: {config.EVENT_CODE}",
        reply_markup=_change_event_menu_kb(),
    )


def _change_event_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔁 Change Event",
                    callback_data="change_event",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛑 Stop Ticket Sales (No Event)",
                    callback_data="event_off",
                )
            ],
        ]
    )


@router.message(
    lambda msg: msg.text == "/change_event"
)
async def change_event_command(
    message: Message,
):
    if not await is_full_admin(
        message.from_user.id
    ):
        await message.answer(
            "🚫 You do not have access to the admin panel."
        )
        return

    await message.answer(
        f"Current event: {config.EVENT_CODE}",
        reply_markup=_change_event_menu_kb(),
    )


@router.callback_query(
    F.data == "change_event"
)
async def change_event_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await is_full_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    # Switch to a new event.
    await state.update_data(
        _mode="change"
    )

    await state.set_state(
        ChangeEventStates.waiting_for_password
    )

    await callback.message.answer(
        "🔒 Enter the password to change the event:"
    )


@router.callback_query(
    F.data == "event_off"
)
async def event_off_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await is_full_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    # Disable ticket sales.
    await state.update_data(
        _mode="off"
    )

    await state.set_state(
        ChangeEventStates.waiting_for_password
    )

    await callback.message.answer(
        "🔒 Enter the password to disable ticket sales "
        "(no active event):"
    )


@router.message(
    ChangeEventStates.waiting_for_password
)
async def change_event_check_password(
    message: Message,
    state: FSMContext,
):
    if not await is_full_admin(
        message.from_user.id
    ):
        await state.clear()
        return

    if (
        (message.text or "").strip()
        != ADMIN_EVENT_PASSWORD
    ):
        await message.answer(
            "❌ Incorrect password. Access denied."
        )
        await state.clear()
        return

    data = await state.get_data()
    mode = data.get(
        "_mode",
        "change",
    )

    # Disable ticket sales and persist the inactive state.
    if mode == "off":
        config.EVENT_CODE = "none"

        await set_meta(
            "active_event_code",
            "none",
        )

        await state.clear()

        await message.answer(
            "🛑 Ticket sales have been stopped.\n"
            "Current event: none\n\n"
            "Ticket purchases are currently unavailable to users."
        )
        return

    await state.set_state(
        ChangeEventStates.waiting_for_event_name
    )

    await message.answer(
        "✍️ Enter the *event name* "
        "(visible to users).",
        parse_mode="Markdown",
    )


@router.message(
    ChangeEventStates.waiting_for_event_name
)
async def change_event_set_name(
    message: Message,
    state: FSMContext,
):
    if not await is_full_admin(
        message.from_user.id
    ):
        await state.clear()
        return

    title = _normalize_event_name(
        message.text
    )

    if not title:
        await message.answer(
            "⚠️ The event name cannot be empty. "
            "Enter it again or use /admin to cancel."
        )
        return

    old = (
        config.EVENT_CODE or ""
    ).strip().lower()

    new = (
        title or ""
    ).strip()

    # Update the active event in memory and persist it across restarts.
    config.EVENT_CODE = new

    await set_meta(
        "active_event_code",
        new,
    )

    # Remember whether a new-event broadcast is required.
    await state.update_data(
        _broadcast_needed=(
            old == "none"
            and new.strip().lower() != "none"
        ),
        _new_event_code=new,
    )

    # Continue to the 1+1 ticket limit.
    await state.set_state(
        ChangeEventStates.waiting_for_1p1_limit
    )

    await message.answer(
        "Enter the number of *1+1 tickets* "
        "available for this event.\n"
        "_0 — disable 1+1; "
        "a positive number — enable it._",
        parse_mode="Markdown",
    )


@router.message(
    ChangeEventStates.waiting_for_1p1_limit
)
async def change_event_set_limit(
    message: Message,
    state: FSMContext,
):
    if not await is_full_admin(
        message.from_user.id
    ):
        await state.clear()
        return

    raw = (
        message.text or ""
    ).strip()

    try:
        qty = int(raw)

        if qty < 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "⚠️ Enter an integer ≥ 0 "
            "(for example: 0, 3, 10)."
        )
        return

    # Save the 1+1 limit for the active event.
    await set_one_plus_one_limit(
        config.EVENT_CODE,
        qty,
    )

    # Keep the existing calculation for compatibility with the current flow.
    await count_one_plus_one_taken(
        config.EVENT_CODE
    )

    await message.answer(
        "✅ 1+1 ticket limit saved.\n"
        f"Limit: {qty}"
    )

    await state.update_data(
        _limit_qty=qty
    )

    await state.set_state(
        ChangeEventStates.waiting_for_price_1p1
    )

    await message.answer(
        "💵 Enter the price for the *1+1* ticket "
        "(integer):",
        parse_mode="Markdown",
    )


@router.message(
    ChangeEventStates.waiting_for_price_1p1
)
async def change_event_price_1p1(
    message: Message,
    state: FSMContext,
):
    try:
        price = int(
            (message.text or "").strip()
        )

        if price < 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "⚠️ The price must be an integer ≥ 0. "
            "Please try again."
        )
        return

    await state.update_data(
        price_1p1=price
    )

    await state.set_state(
        ChangeEventStates.waiting_for_price_single
    )

    await message.answer(
        "💵 Enter the price for the *single* ticket "
        "(integer):",
        parse_mode="Markdown",
    )


@router.message(
    ChangeEventStates.waiting_for_price_single
)
async def change_event_price_single(
    message: Message,
    state: FSMContext,
):
    try:
        price = int(
            (message.text or "").strip()
        )

        if price < 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "⚠️ The price must be an integer ≥ 0. "
            "Please try again."
        )
        return

    await state.update_data(
        price_single=price
    )

    await state.set_state(
        ChangeEventStates.waiting_for_price_promocode
    )

    await message.answer(
        "💵 Enter the price for the *promo code* ticket "
        "(integer):",
        parse_mode="Markdown",
    )


@router.message(
    ChangeEventStates.waiting_for_price_promocode
)
async def change_event_price_promocode(
    message: Message,
    state: FSMContext,
):
    try:
        price = int(
            (message.text or "").strip()
        )

        if price < 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "⚠️ The price must be an integer ≥ 0. "
            "Please try again."
        )
        return

    await state.update_data(
        price_promocode=price
    )

    await state.set_state(
        ChangeEventStates.waiting_for_promocode_list
    )

    await message.answer(
        "🧾 Send the *promo code list* separated by commas "
        "(for example: VIP10, EARLY, TEST).\n"
        "If there are no promo codes, send “-”.",
        parse_mode="Markdown",
    )


@router.message(
    ChangeEventStates.waiting_for_promocode_list
)
async def change_event_promocodes(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()

    new_event = data.get(
        "_new_event_code",
        config.EVENT_CODE,
    )

    raw = (
        message.text or ""
    ).strip()

    if raw in (
        "-",
        "—",
        "нет",
        "Нет",
        "no",
        "No",
        "",
    ):
        codes = []

    else:
        codes = [
            code.strip().upper()
            for code in raw.split(",")
            if code.strip()
        ]

    prices = {
        "1+1": int(
            data.get("price_1p1", 0)
        ),
        "single": int(
            data.get("price_single", 0)
        ),
        "promocode": int(
            data.get("price_promocode", 0)
        ),
    }

    # Store event-specific prices and promo codes in bot_meta.
    try:
        await set_meta(
            f"prices:{new_event}",
            json.dumps(
                prices,
                ensure_ascii=False,
            ),
        )

        await set_meta(
            f"promocodes:{new_event}",
            json.dumps(
                codes,
                ensure_ascii=False,
            ),
        )

    except Exception:
        # Keep the admin flow alive if metadata persistence fails.
        pass

    limit_qty = int(
        data.get("_limit_qty", 0)
    )

    # Keep the existing lookup because it is part of the current event flow.
    await count_one_plus_one_taken(
        new_event
    )

    broadcast_needed = bool(
        data.get("_broadcast_needed")
    )

    await state.clear()

    pretty_codes = (
        ", ".join(codes)
        if codes
        else "—"
    )

    await message.answer(
        "✅ Event updated!\n"
        f"Current event: {new_event}\n\n"
        f"1+1 limit: {limit_qty}\n\n"
        "Prices:\n"
        f"• 1+1: {prices['1+1']}\n"
        f"• single: {prices['single']}\n"
        f"• promo code: {prices['promocode']}\n\n"
        f"Promo codes: {pretty_codes}"
    )

    # Announce the event when sales change from "none" to an active event.
    if broadcast_needed:
        await message.answer(
            "📣 Sending the latest channel post first, "
            "then the event notification with a button…"
        )

        asyncio.create_task(
            _broadcast_last_post_then_notice(
                message.bot,
                new_event,
            )
        )


# ============================================================================
# 1+1 waiting list
# ============================================================================

async def list_1plus1_wishers(
    message: Message,
):
    if not await is_full_admin(
        message.from_user.id
    ):
        await message.answer(
            "🚫 You do not have permission to use this command."
        )
        return

    await _send_wishers_to(
        message.bot,
        message.chat.id,
    )


# ============================================================================
# Payment expiration helper
# ============================================================================

async def _expire_payment_after_admin(
    bot,
    chat_id: int,
    message_id: int,
    row_id: int,
    timeout_sec: int = 300,
):
    await asyncio.sleep(
        timeout_sec
    )

    # Keep the ticket type and event for possible 1+1 notifications.
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

    if status in (
        "не оплатил",
        "отклонено",
    ):
        # Reset a rejected payment before returning the user to the menu.
        if status == "отклонено":
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

        kb = await _purchase_menu_kb()

        await bot.send_message(
            chat_id,
            "⏰ Payment time has expired.\n"
            "Please choose a ticket type again:",
            reply_markup=kb,
        )

        # Notify waiting users if a 1+1 slot became available.
        if (
            ticket_type == "1+1"
            and event_code
        ):
            await _notify_wishers_1p1_available(
                bot,
                event_code,
            )


# ============================================================================
# Broadcast helpers
# ============================================================================

async def _broadcast_new_event(
    bot,
    event_title: str,
):
    subs = await get_all_subscribers()

    if not subs:
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Follow on Telegram",
                    url=(
                        f"https://t.me/"
                        f"{CHANNEL_ID.lstrip('@')}"
                    ),
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

    text = (
        f"🔥 New event: {event_title}\n\n"
        "Tickets are now available — don't forget to get yours 👇"
    )

    # Throttle broadcasts to roughly 20 messages per second.
    for uid, _username in subs:
        try:
            await bot.send_message(
                uid,
                text,
                reply_markup=kb,
            )

            await asyncio.sleep(0.05)

        except Exception:
            # Ignore delivery failures such as users blocking the bot.
            await asyncio.sleep(0.05)


async def _broadcast_last_post_then_notice(
    bot,
    event_title: str,
):
    post_id = await get_meta(
        LAST_POST_KEY
    )

    subs = await get_all_subscribers()

    if not subs:
        return

    # Build keyboards for subscribed and unsubscribed users.
    kb_notice_subscribed = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎟 Buy a Ticket",
                    callback_data="buy_ticket_menu",
                )
            ]
        ]
    )

    kb_notice_unsubscribed = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Follow on Telegram",
                    url=(
                        f"https://t.me/"
                        f"{CHANNEL_ID.lstrip('@')}"
                    ),
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

    for uid, _username in subs:
        # Copy the latest channel post when available.
        if post_id:
            try:
                await bot.copy_message(
                    chat_id=uid,
                    from_chat_id=CHANNEL_ID,
                    message_id=int(post_id),
                )
            except Exception:
                pass

        # Check whether the recipient is subscribed to the channel.
        subscribed = False

        try:
            member = await bot.get_chat_member(
                CHANNEL_ID,
                uid,
            )

            status = getattr(
                member,
                "status",
                None,
            )

            subscribed = status in (
                "member",
                "administrator",
                "creator",
            )

        except Exception:
            # Treat an unknown membership status as not subscribed.
            subscribed = False

        kb = (
            kb_notice_subscribed
            if subscribed
            else kb_notice_unsubscribed
        )

        try:
            await bot.send_message(
                uid,
                (
                    f"🔥 New event: {event_title}\n\n"
                    "Tickets are now available — tap below 👇"
                ),
                reply_markup=kb,
            )
        except Exception:
            pass

        # Throttle delivery to roughly 20 messages per second.
        await asyncio.sleep(0.05)


# ============================================================================
# Channel post broadcast
# ============================================================================

class BroadcastLastStates(StatesGroup):
    waiting_for_password = State()


@router.message(
    BroadcastLastStates.waiting_for_password
)
async def broadcast_last_check_password(
    message: Message,
    state: FSMContext,
):
    if not await is_full_admin(
        message.from_user.id
    ):
        await state.clear()
        return

    pwd_ok = (
        (message.text or "").strip()
        == (
            ADMIN_BROADCAST_PASSWORD
            or ADMIN_EVENT_PASSWORD
            or ""
        )
    )

    if not pwd_ok:
        await message.answer(
            "❌ Incorrect password. Broadcast cancelled."
        )
        await state.clear()
        return

    await state.clear()

    await message.answer(
        "✅ Password accepted. Starting broadcast…"
    )

    await _broadcast_last_post(
        message.bot,
        message,
    )


LAST_POST_KEY = "last_channel_post_id"


@router.channel_post()
async def remember_last_channel_post(
    msg: Message,
):
    # Support both @username and numeric channel IDs.
    is_same_channel = False

    try:
        is_same_channel = (
            str(msg.chat.id)
            == str(CHANNEL_ID)
            or (
                msg.chat.username
                and (
                    "@" + msg.chat.username
                ).lower()
                == str(CHANNEL_ID).lower()
            )
        )

    except Exception:
        pass

    if not is_same_channel:
        return

    await set_meta(
        LAST_POST_KEY,
        msg.message_id,
    )


async def _broadcast_last_post(
    bot,
    reply_target,
):
    post_id = await get_meta(
        LAST_POST_KEY
    )

    if not post_id:
        await reply_target.answer(
            "⚠️ I haven't received any channel posts yet. "
            "Publish a new post "
            "(the bot must be a channel administrator), "
            "then try again."
        )
        return

    subs = await get_all_subscribers()

    if not subs:
        await reply_target.answer(
            "There are currently no subscribers to broadcast to."
        )
        return

    sent = 0
    skipped = 0

    for uid, _username in subs:
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=CHANNEL_ID,
                message_id=int(post_id),
            )

            sent += 1

        except Exception:
            skipped += 1

    await reply_target.answer(
        f"📣 Done. Sent: {sent}, "
        f"skipped: {skipped}."
    )


@router.message(
    lambda m: m.text == "/broadcast_last"
)
async def broadcast_last_cmd(
    message: Message,
    state: FSMContext,
):
    if not await is_full_admin(
        message.from_user.id
    ):
        return

    await state.set_state(
        BroadcastLastStates.waiting_for_password
    )

    await message.answer(
        "🔒 Enter the password to broadcast "
        "the latest channel post:"
    )


@router.callback_query(
    F.data == "broadcast_last"
)
async def broadcast_last_cb(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await is_full_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await state.set_state(
        BroadcastLastStates.waiting_for_password
    )

    await callback.message.answer(
        "🔒 Enter the password to broadcast "
        "the latest channel post:"
    )


# ============================================================================
# Scanner access management
# ============================================================================

def _scan_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="scan_access_cancel",
                )
            ]
        ]
    )


class ScanAccessStates(StatesGroup):
    waiting_for_add_id = State()
    waiting_for_remove_id = State()


def _scan_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👥 View Scanner Admins",
                    callback_data="scan_access_view",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Add",
                    callback_data="scan_access_add",
                ),
                InlineKeyboardButton(
                    text="➖ Remove",
                    callback_data="scan_access_remove",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✖️ Close",
                    callback_data="scan_access_close",
                )
            ],
        ]
    )


@router.callback_query(
    F.data == "scan_access_menu"
)
async def scan_access_menu(
    callback: CallbackQuery,
):
    if not await is_full_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await callback.message.answer(
        "🔐 Scanner access management:",
        reply_markup=_scan_menu_kb(),
    )


@router.callback_query(
    F.data == "scan_access_view"
)
async def scan_access_view(
    callback: CallbackQuery,
):
    if not await is_full_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    ids = await get_role_user_ids(
        "scanner"
    )

    if not ids:
        text = "There are no scanner administrators."

    else:
        lines = [
            "👥 Scanner administrators:"
        ]

        for uid in sorted(ids):
            lines.append(
                f"• {uid}"
            )

        text = "\n".join(lines)

    await callback.message.answer(
        text,
        reply_markup=_scan_menu_kb(),
    )


@router.callback_query(
    F.data == "scan_access_cancel"
)
async def scan_access_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await is_full_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await state.clear()

    await callback.answer(
        "Cancelled"
    )

    await callback.message.answer(
        "🔐 Scanner access management:",
        reply_markup=_scan_menu_kb(),
    )
@router.callback_query(
    F.data == "scan_access_add"
)
async def scan_access_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await is_full_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await state.set_state(
        ScanAccessStates.waiting_for_add_id
    )

    await callback.message.answer(
        "Send the numeric user_id that should "
        "receive scanner access.",
        reply_markup=_scan_cancel_kb(),
    )


@router.message(
    ScanAccessStates.waiting_for_add_id
)
async def scan_access_add_id(
    message: Message,
    state: FSMContext,
):
    if not await is_full_admin(
        message.from_user.id
    ):
        await state.clear()
        return

    try:
        uid = int(
            (message.text or "").strip()
        )

    except ValueError:
        await message.answer(
            "user_id must be a number. "
            "Try again or tap “Cancel”.",
            reply_markup=_scan_cancel_kb(),
        )
        return

    if (
        await has_role(uid, "admin")
        or await has_role(uid, "scanner")
    ):
        await message.answer(
            "✅ This user already has scanner access."
        )

    else:
        await add_role(
            uid,
            "scanner",
        )

        await message.answer(
            f"✅ Scanner access granted to: {uid}"
        )

    await state.clear()

    await message.answer(
        "Done. Return to the access management menu:",
        reply_markup=_scan_menu_kb(),
    )


@router.callback_query(
    F.data == "scan_access_remove"
)
async def scan_access_remove(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await is_full_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await state.set_state(
        ScanAccessStates.waiting_for_remove_id
    )

    await callback.message.answer(
        "Send the numeric user_id whose "
        "scanner access should be revoked.",
        reply_markup=_scan_cancel_kb(),
    )


@router.message(
    ScanAccessStates.waiting_for_remove_id
)
async def scan_access_remove_id(
    message: Message,
    state: FSMContext,
):
    if not await is_full_admin(
        message.from_user.id
    ):
        await state.clear()
        return

    try:
        uid = int(
            (message.text or "").strip()
        )

    except ValueError:
        await message.answer(
            "user_id must be a number. "
            "Try again or tap “Cancel”.",
            reply_markup=_scan_cancel_kb(),
        )
        return

    if await has_role(uid, "admin"):
        await message.answer(
            "🚫 Scanner access cannot be revoked from a full administrator "
            "(role 'admin')."
        )

    elif not await has_role(
        uid,
        "scanner",
    ):
        await message.answer(
            "ℹ️ This user does not currently have scanner access."
        )

    else:
        await remove_role(
            uid,
            "scanner",
        )

        await message.answer(
            f"✅ Scanner access revoked from: {uid}"
        )

    await state.clear()

    await message.answer(
        "Done. Return to the access management menu:",
        reply_markup=_scan_menu_kb(),
    )


@router.callback_query(
    F.data == "scan_access_close"
)
async def scan_access_close(
    callback: CallbackQuery,
):
    if not await is_full_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await callback.message.answer(
        "Closed."
    )


@router.message(
    lambda m: m.text == "/scan_access_menu"
)
async def scan_access_menu_cmd(
    message: Message,
):
    if not await is_full_admin(
        message.from_user.id
    ):
        await message.answer(
            "Access denied."
        )
        return

    await message.answer(
        "🔐 Scanner access management:",
        reply_markup=_scan_menu_kb(),
    )


# ============================================================================
# Purchase menu used after payment expiration
# ============================================================================

async def _purchase_menu_kb() -> InlineKeyboardMarkup:
    rows = []

    try:
        limit = await get_one_plus_one_limit(
            config.EVENT_CODE
        )
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

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


async def _notify_wishers_1p1_available(
    bot,
    event_code: str,
):
    """
    Notify users who previously tried to buy a 1+1 ticket while slots
    were unavailable. Do not send more notifications than available slots.
    """
    remaining = await remaining_one_plus_one_for_event(
        event_code
    )

    if remaining is None or remaining <= 0:
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
        uid = int(
            row["user_id"]
        )

        try:
            await bot.send_message(
                uid,
                (
                    f"✨ 1+1 tickets are available again "
                    f"for “{event_code}”. Get yours while they last 👇"
                ),
                reply_markup=kb,
            )

            sent += 1

        except Exception:
            pass

        if sent >= remaining:
            break

        # Gentle rate limiting.
        await asyncio.sleep(0.05)


# ============================================================================
# Event price and promo-code helpers
# ============================================================================

def _norm_ticket_key(
    raw: str,
) -> str:
    value = (
        raw or ""
    ).strip().lower()

    # Accept several aliases for ticket types.
    value = value.replace(
        " ",
        "",
    )

    if value in (
        "1+1",
        "1plus1",
        "oneplusone",
    ):
        return "1+1"

    if value in (
        "single",
        "1",
        "один",
        "solo",
    ):
        return "single"

    if value in (
        "promocode",
        "promo",
        "promocod",
        "промокод",
    ):
        return "promocode"

    # Preserve unknown values for possible future ticket types.
    return value


def _parse_prices(
    text: str,
) -> dict[str, int]:
    """
    Parse ticket prices from line-separated or comma-separated input.

    Example:
      1+1: 1500
      single: 1000
      promocode: 800
    """
    if not text:
        return {}

    prices = {}
    parts = []

    # Support both line-separated and comma-separated values.
    for line in text.replace(
        ",",
        "\n",
    ).splitlines():
        line = line.strip()

        if not line:
            continue

        parts.append(line)

    for part in parts:
        if ":" not in part:
            raise ValueError(
                f"Missing colon: “{part}”"
            )

        key, value = part.split(
            ":",
            1,
        )

        key = _norm_ticket_key(
            key
        )

        value = (
            value.strip()
            .replace(" ", "")
        )

        if not value.isdigit():
            raise ValueError(
                f"Price must be a number: “{part}”"
            )

        prices[key] = int(value)

    # Do not require a fixed set of ticket keys.
    return prices


def _parse_promocodes(
    text: str,
) -> list[str]:
    """
    Parse a comma-separated promo-code list while preserving order.

    An empty string means that no promo codes are configured.
    """
    if not (
        text or ""
    ).strip():
        return []

    values = [
        code.strip()
        for code in text.split(",")
    ]

    # Remove empty values and duplicates while preserving order.
    seen = set()
    result = []

    for code in values:
        if not code:
            continue

        if code not in seen:
            seen.add(code)
            result.append(code)

    return result


async def _save_event_prices(
    event_code: str,
    prices: dict[str, int],
):
    await set_meta(
        f"prices:{event_code}",
        json.dumps(
            prices,
            ensure_ascii=False,
        ),
    )


async def _load_event_prices(
    event_code: str,
) -> dict[str, int] | None:
    raw = await get_meta(
        f"prices:{event_code}"
    )

    if not raw:
        return None

    try:
        return json.loads(raw)
    except Exception:
        return None


def _fmt_amount(n: int) -> str:
    return f"{n:,}".replace(
        ",",
        " ",
    )


def _canon_type(t: str) -> str:
    value = (
        t or ""
    ).strip().lower()

    if value == "1+1":
        return "1+1"

    if value == "single":
        return "single"

    # Treat all other ticket types as promo-code tickets.
    return "promocode"


async def _calc_revenue_for_event(
    event_code: str,
) -> tuple[int, int]:
    if (
        not event_code
        or event_code.strip().lower() == "none"
    ):
        return 0, 0

    rows = await get_all_users_full(
        event_code
    )

    prices = (
        await _load_event_prices(event_code)
        or {}
    )

    total = 0
    missing = 0

    for row in rows or []:
        data = dict(row)

        paid = (
            data.get("paid") or ""
        ).strip().lower()

        if paid != "оплатил":
            continue

        ticket_type = _canon_type(
            data.get("ticket_type")
        )

        price = prices.get(
            ticket_type
        )

        if isinstance(price, int):
            total += price
        else:
            missing += 1

    return total, missing


async def _calc_revenue_all_events() -> tuple[int, int]:
    rows = await get_all_users_full(
        None
    )

    total = 0
    missing = 0

    cache: dict[str, dict] = {}

    for row in rows or []:
        data = dict(row)

        paid = (
            data.get("paid") or ""
        ).strip().lower()

        if paid != "оплатил":
            continue

        event_code = (
            data.get("event_code") or ""
        ).strip()

        if (
            not event_code
            or event_code.lower() == "none"
        ):
            continue

        if event_code not in cache:
            cache[event_code] = (
                await _load_event_prices(
                    event_code
                )
                or {}
            )

        ticket_type = _canon_type(
            data.get("ticket_type")
        )

        price = cache[event_code].get(
            ticket_type
        )

        if isinstance(price, int):
            total += price
        else:
            missing += 1

    return total, missing


@router.callback_query(
    F.data == "an:revenue"
)
async def cb_an_revenue(
    callback: CallbackQuery,
):
    if not await is_full_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Access denied.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Calculating…",
        show_alert=False,
    )

    current_event = config.EVENT_CODE

    current_total, current_missing = (
        await _calc_revenue_for_event(
            current_event
        )
    )

    all_total, all_missing = (
        await _calc_revenue_all_events()
    )

    lines = [
        "💰 Revenue"
    ]

    if (
        current_event
        and current_event.strip().lower() != "none"
    ):
        line = (
            f"Current event “{current_event}”: "
            f"{_fmt_amount(current_total)} ₽"
        )

        if current_missing:
            line += (
                f" (missing price: {current_missing})"
            )

        lines.append(line)

    else:
        lines.append(
            "Current event: none (0 ₽)"
        )

    line = (
        f"All events: "
        f"{_fmt_amount(all_total)} ₽"
    )

    if all_missing:
        line += (
            f" (missing price: {all_missing})"
        )

    lines.append(line)

    await callback.message.answer(
        "\n".join(lines)
    )


async def _save_event_promocodes(
    event_code: str,
    codes: list[str],
):
    await set_meta(
        f"promocodes:{event_code}",
        json.dumps(
            codes,
            ensure_ascii=False,
        ),
    )


async def _load_event_promocodes(
    event_code: str,
) -> list[str] | None:
    raw = await get_meta(
        f"promocodes:{event_code}"
    )

    if not raw:
        return None

    try:
        return json.loads(raw)
    except Exception:
        return None


# ============================================================================
# Analytics output helpers
# ============================================================================

async def _send_report_to(
    bot,
    chat_id: int,
):
    total = await count_registered()
    active = await count_activated()

    chat_count = await bot.get_chat_member_count(
        CHANNEL_ID
    )

    paid_count = await count_paid()

    await bot.send_message(
        chat_id,
        (
            "📊 Statistics:\n"
            f"👥 Channel subscribers: {chat_count}\n"
            f"👤 Purchases created: {total}\n"
            f"💰 Paid: {paid_count}\n"
            f"✅ Attended: {active}"
        ),
    )


async def _send_stats_this_to(
    bot,
    chat_id: int,
):
    event_code = config.EVENT_CODE

    rows = await get_ticket_stats_for_event(
        event_code,
        paid_statuses=("оплатил",),
    )

    if not rows:
        await bot.send_message(
            chat_id,
            (
                f"There are no paid tickets "
                f"for “{event_code}”."
            ),
        )
        return

    total = sum(
        int(row["count"])
        for row in rows
    )

    parts = [
        f"📊 “{event_code}”: paid tickets only",
        "",
    ]

    for row in rows:
        parts.append(
            f"• {row['ticket_type']}: "
            f"{int(row['count'])}"
        )

    parts.append("")
    parts.append(
        f"TOTAL: {total}"
    )

    await bot.send_message(
        chat_id,
        "\n".join(parts),
    )


async def _send_wishers_to(
    bot,
    chat_id: int,
):
    rows = await get_unique_one_plus_one_attempters_for_event(
        config.EVENT_CODE
    )

    if not rows:
        await bot.send_message(
            chat_id,
            (
                "No one has tried to buy a 1+1 ticket "
                "after the limit was reached yet."
            ),
        )
        return

    lines = [
        "📝 Users who wanted a 1+1 ticket but missed out "
        "(unique users):\n"
    ]

    for row in rows:
        uid = row["user_id"]

        username = (
            row["username"]
            or f"id:{uid}"
        )

        when = row["last_try"].strftime(
            "%Y-%m-%d %H:%M"
        )

        lines.append(
            f"• @{username} (id:{uid}) — {when}"
        )

    text = "\n".join(lines)

    if len(text) > 4000:
        filename = "wishers_1plus1.txt"

        with open(
            filename,
            "w",
            encoding="utf-8",
        ) as file:
            file.write(text)

        await bot.send_document(
            chat_id,
            FSInputFile(filename),
            caption="📝 1+1 Waiting List",
        )

    else:
        await bot.send_message(
            chat_id,
            text,
        )


async def _send_export_to(
    bot,
    chat_id: int,
    only_this: bool,
):
    rows = await get_all_users_full(
        config.EVENT_CODE
        if only_this
        else None
    )

    if not rows:
        await bot.send_message(
            chat_id,
            "No data available.",
        )
        return

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "users"

    worksheet.append(
        [
            "id",
            "user_id",
            "username",
            "event_code",
            "ticket_type",
            "paid",
            "status",
            "purchase_date",
        ]
    )

    for row in rows:
        worksheet.append(
            [
                row["id"],
                row["user_id"],
                row["username"],
                row["event_code"],
                row["ticket_type"],
                row["paid"],
                row["status"],
                row["purchase_date"],
            ]
        )

    buffer = BytesIO()

    workbook.save(buffer)
    buffer.seek(0)

    filename = (
        f"users_{config.EVENT_CODE}.xlsx"
        if only_this
        else "users.xlsx"
    )

    caption = (
        f"📄 Users database export — {config.EVENT_CODE}"
        if only_this
        else "📄 Users database export (all events)"
    )

    await bot.send_document(
        chat_id,
        BufferedInputFile(
            buffer.getvalue(),
            filename=filename,
        ),
        caption=caption,
    )
