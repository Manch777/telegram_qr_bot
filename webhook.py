from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher
from aiogram.types import Update
import asyncio
import os

from database import connect_db, disconnect_db
from config import BOT_TOKEN

app = FastAPI()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# подключи свои роутеры
from handlers import user, admin
dp.include_router(user.router)
dp.include_router(admin.router)

@app.on_event("startup")
async def on_startup():
    await connect_db()
    await bot.set_webhook(url="https://your-app-name.onrender.com/webhook")

@app.on_event("shutdown")
async def on_shutdown():
    await disconnect_db()
    await bot.delete_webhook()

# Webhook endpoint
@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

# Healthcheck
@app.get("/healthcheck")
async def healthcheck():
    return {"status": "ok"}
