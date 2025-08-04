import aiosqlite
from config import ADMIN_IDS
from datetime import datetime


DB_NAME = "users.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                paid TEXT,
                status TEXT DEFAULT 'не активирован'
            )
        ''')
        await db.commit()

async def add_user(user_id, username):
    if user_id in ADMIN_IDS:
        return  # админов не добавляем

    async with aiosqlite.connect("users.db") as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, paid, status) VALUES (?, ?, ?, ?)",
            (user_id, username or "Без ника", "не оплатил", "не активирован")
        )
        await db.commit()

async def update_status(user_id, status):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET status=? WHERE user_id=?", (status, user_id))
        await db.commit()

async def get_status(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT status FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else None
    
async def count_registered():
    async with aiosqlite.connect("users.db") as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        result = await cursor.fetchone()
        return result[0]

async def count_activated():
    async with aiosqlite.connect("users.db") as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE status = 'активирован'")
        result = await cursor.fetchone()
        return result[0]
    
async def get_registered_users():
    async with aiosqlite.connect("users.db") as db:
        cursor = await db.execute("SELECT user_id, username, paid, status FROM users ORDER BY status DESC")
        return await cursor.fetchall()

async def get_paid_users():
    async with aiosqlite.connect("users.db") as db:
        cursor = await db.execute(
            "SELECT user_id, username, status, paid  FROM users WHERE status IS NOT NULL"
        )
        return await cursor.fetchall()

async def mark_as_paid(user_id: int):
    async with aiosqlite.connect("users.db") as db:
        await db.execute("UPDATE users SET paid = 'оплатил' WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_paid_status(user_id):
    async with aiosqlite.connect("users.db") as db:
        cursor = await db.execute("SELECT paid FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else None

async def set_paid_status(user_id: int, paid: str):
    async with aiosqlite.connect("users.db") as db:
        await db.execute("UPDATE users SET paid = ? WHERE user_id = ?", (paid, user_id))
        await db.commit()

async def clear_database():
    async with aiosqlite.connect("users.db") as db:
        await db.execute("DELETE FROM users")
        await db.commit()
