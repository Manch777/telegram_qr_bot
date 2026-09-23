import asyncio
import os

from aiohttp import ClientSession, web
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import BotCommand, Message
from aiogram.types.error_event import ErrorEvent
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

import config
from config import BOT_TOKEN, WEBHOOK_URL
from database import (
    connect_db,
    disconnect_db,
    get_meta,
    get_row,
    get_status,
    get_ticket_type,
    set_meta,
    update_status,
    update_status_by_id,
)
from handlers import admin, user


WEBHOOK_PATH = "/webhook"
FULL_WEBHOOK_URL = (WEBHOOK_URL or "").rstrip("/") + WEBHOOK_PATH

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ============================================================================
# Telegram deep-link handling
# ============================================================================

async def deep_link_start_handler(message: Message):
    """
    Handle ticket activation through Telegram deep links.

    Both legacy user-ID QR codes and current row-ID QR codes are supported.
    """
    parts = message.text.split(maxsplit=1)

    if len(parts) != 2:
        return

    payload = (parts[1] or "").strip()

    if not payload:
        await message.answer("❌ Недопустимый QR-код.")
        return

    # Normalize supported QR payload formats.
    value = payload

    if value.lower().startswith("qr:"):
        value = value[3:].lstrip()

    if value.lower().startswith("r:"):
        value = value[2:].lstrip()

    number_part = value.split(":", 1)[0]

    try:
        candidate = int(number_part)
    except ValueError:
        await message.answer("❌ Недопустимый QR-код.")
        return

    # Legacy QR format: the numeric value represents a Telegram user ID.
    # The legacy database helpers operate on the user's latest purchase.
    status = await get_status(candidate)

    if status is not None:
        ticket_type = await get_ticket_type(candidate) or "-"

        if status == "не активирован":
            await update_status(
                candidate,
                "активирован",
            )

            await message.answer(
                "✅ Пропуск активирован.\n"
                f"Тип билета: {ticket_type}"
            )
        else:
            await message.answer(
                "⚠️ Этот QR-код уже использован.\n"
                f"Тип билета: {ticket_type}"
            )

        return

    # Current QR format: the numeric value represents a ticket row ID.
    row = await get_row(candidate)

    if row is None:
        await message.answer("❌ QR-код не найден.")
        return

    ticket_type = row["ticket_type"] or "-"
    status_by_id = row["status"]

    if status_by_id == "не активирован":
        await update_status_by_id(
            candidate,
            "активирован",
        )

        await message.answer(
            "✅ Пропуск активирован.\n"
            f"Тип билета: {ticket_type}"
        )
    else:
        await message.answer(
            "⚠️ Этот QR-код уже использован.\n"
            f"Тип билета: {ticket_type}"
        )


# Router order matters: admin handlers must be registered first.
dp.include_router(admin.router)
dp.include_router(user.router)


# ============================================================================
# Global aiogram error handling
# ============================================================================

@dp.error()
async def _on_error(event: ErrorEvent):
    """
    Log handler failures without printing the complete Telegram update.

    Full updates may contain user messages or other private data.
    """
    try:
        exception = event.exception

        print(
            "[ERROR] Handler exception: "
            f"{type(exception).__name__}: {exception}",
            flush=True,
        )
    except Exception as error:
        print(
            f"[ERROR] Failed to log error: {error}",
            flush=True,
        )


# Register the deep-link handler after the routers.
dp.message.register(
    deep_link_start_handler,
    F.text.startswith("/start "),
)


# ============================================================================
# Webhook configuration
# ============================================================================

async def _set_webhook_background():
    """Configure the Telegram webhook with retries."""
    max_attempts = 5
    delay_seconds = 2

    if not WEBHOOK_URL:
        print(
            "[ERROR] WEBHOOK_URL is empty; cannot set webhook. "
            "Set WEBHOOK_URL to the public HTTPS base URL.",
            flush=True,
        )
        return

    print(
        f"[INIT] Target webhook URL: {FULL_WEBHOOK_URL}",
        flush=True,
    )

    for attempt in range(1, max_attempts + 1):
        try:
            info = await bot.get_webhook_info(
                request_timeout=10
            )

            print(
                "[DEBUG] Telegram current webhook before: "
                f"'{info.url or ''}' "
                "(pending updates: "
                f"{getattr(info, 'pending_update_count', 'n/a')})",
                flush=True,
            )

            # Reset the existing webhook before configuring it again.
            try:
                await bot.delete_webhook(
                    drop_pending_updates=True,
                    request_timeout=10,
                )

                print(
                    "ℹ️ Webhook deleted (reset)",
                    flush=True,
                )

            except Exception as error:
                print(
                    f"[WARN] delete_webhook failed: {error}",
                    flush=True,
                )

            # Reinstall the webhook to avoid stale Telegram configuration.
            await bot.set_webhook(
                FULL_WEBHOOK_URL,
                allowed_updates=[
                    "message",
                    "callback_query",
                    "channel_post",
                ],
                drop_pending_updates=True,
                request_timeout=10,
            )

            print(
                "✅ Webhook set (forced)",
                flush=True,
            )

            # Verify that Telegram stored the webhook URL.
            info_after = await bot.get_webhook_info(
                request_timeout=10
            )

            print(
                "[DEBUG] Telegram current webhook after: "
                f"'{info_after.url or ''}' "
                "(pending updates: "
                f"{getattr(info_after, 'pending_update_count', 'n/a')})",
                flush=True,
            )

            # Keep the existing raw API fallback for compatibility.
            if not (info_after.url or "").strip():
                try:
                    raw_url = (
                        "https://api.telegram.org/"
                        f"bot{BOT_TOKEN}/setWebhook"
                    )

                    params = {
                        "url": FULL_WEBHOOK_URL
                    }

                    async with ClientSession() as session:
                        async with session.get(
                            raw_url,
                            params=params,
                            timeout=15,
                        ) as response:
                            response_text = await response.text()

                            print(
                                "[FALLBACK] setWebhook raw response: "
                                f"{response_text}",
                                flush=True,
                            )

                    # Verify the webhook again after the raw API fallback.
                    info_after_fallback = (
                        await bot.get_webhook_info(
                            request_timeout=10
                        )
                    )

                    print(
                        "[DEBUG] Telegram webhook after RAW: "
                        f"'{info_after_fallback.url or ''}' "
                        "(pending updates: "
                        f"{getattr(info_after_fallback, 'pending_update_count', 'n/a')})",
                        flush=True,
                    )

                except Exception as error:
                    print(
                        "[FALLBACK][ERR] "
                        f"raw setWebhook failed: {error}",
                        flush=True,
                    )

            return

        except (
            TelegramNetworkError,
            TelegramBadRequest,
        ) as error:
            print(
                "[WARN] set_webhook attempt "
                f"{attempt}/{max_attempts} failed: {error}",
                flush=True,
            )

            if attempt < max_attempts:
                await asyncio.sleep(delay_seconds)
                delay_seconds = min(
                    delay_seconds * 2,
                    30,
                )
            else:
                print(
                    "[ERROR] Unable to set webhook after retries",
                    flush=True,
                )


# ============================================================================
# Application lifecycle
# ============================================================================

async def on_startup(app: web.Application):
    """Initialize the database, event state, bot commands, and webhook."""
    await connect_db()

    # Restore the active event persisted in bot_meta.
    saved_event_code = await get_meta(
        "active_event_code"
    )

    if saved_event_code:
        config.EVENT_CODE = saved_event_code
    else:
        await set_meta(
            "active_event_code",
            config.EVENT_CODE,
        )

    # Configure the default bot commands.
    try:
        await bot.set_my_commands(
            [
                BotCommand(
                    command="start",
                    description="Начать",
                ),
                BotCommand(
                    command="help",
                    description="ℹ️ Помощь / Связь с админом",
                ),
            ]
        )
    except Exception as error:
        print(
            f"[WARN] set_my_commands: {error}",
            flush=True,
        )

    # Verify that the Telegram bot API is reachable.
    try:
        me = await bot.get_me(
            request_timeout=10
        )

        print(
            "[INIT] Bot: "
            f"@{getattr(me, 'username', '?')} "
            f"(id={getattr(me, 'id', '?')})",
            flush=True,
        )

    except Exception as error:
        print(
            f"[WARN] get_me failed: {error}",
            flush=True,
        )

    print(
        "[INIT] WEBHOOK_URL base: "
        f"'{WEBHOOK_URL}' | "
        f"FULL: '{FULL_WEBHOOK_URL}'",
        flush=True,
    )

    # Configure the webhook in the background so startup can finish quickly.
    asyncio.create_task(
        _set_webhook_background()
    )

    print(
        "✅ Startup finished (server will bind now)",
        flush=True,
    )


async def on_shutdown(app: web.Application):
    """
    Close application resources.

    The Telegram webhook is intentionally left configured between restarts.
    """
    try:
        await disconnect_db()
    except Exception as error:
        print(
            f"[WARN] Database disconnect failed: {error}",
            flush=True,
        )

    try:
        await bot.session.close()
    except Exception as error:
        print(
            f"[WARN] Bot session close failed: {error}",
            flush=True,
        )


# ============================================================================
# HTTP endpoints
# ============================================================================

async def healthcheck(request):
    """Return a lightweight health-check response."""
    return web.Response(text="OK")


async def root(request):
    """Return a simple service status response."""
    return web.Response(
        text="Bot is up. See /healthcheck"
    )


@web.middleware
async def request_logger(request, handler):
    """Log HTTP requests without logging request bodies."""
    try:
        response = await handler(request)

        print(
            f"[HTTP] {request.method} "
            f"{request.path} -> {response.status}",
            flush=True,
        )

        return response

    except Exception as error:
        print(
            f"[HTTP][ERR] {request.method} "
            f"{request.path}: {error}",
            flush=True,
        )

        raise


def create_app():
    """Create and configure the aiohttp application."""
    app = web.Application(
        middlewares=[request_logger]
    )

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    ).register(
        app,
        path=WEBHOOK_PATH,
    )

    app.router.add_get(
        "/healthcheck",
        healthcheck,
    )

    app.router.add_get(
        "/",
        root,
    )

    return app


# ============================================================================
# Entrypoint
# ============================================================================

if __name__ == "__main__":
    port = int(
        os.environ.get(
            "PORT",
            "10000",
        )
    )

    print(
        f"🔊 Binding HTTP server on 0.0.0.0:{port}",
        flush=True,
    )

    app = create_app()

    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
    )
