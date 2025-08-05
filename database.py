from sqlalchemy import (
    Column, String, MetaData, Table, create_engine, BigInteger
)
from databases import Database
from sqlalchemy.dialects.postgresql import insert as pg_insert
from config import POSTGRES_URL

# --- Подключение ---
engine = create_engine(POSTGRES_URL.replace("+asyncpg", ""))
metadata = MetaData()

# --- Таблица пользователей ---
users = Table(
    "users",
    metadata,
    Column("user_id", BigInteger, primary_key=True),  # Telegram user_id может быть очень большим
    Column("username", String),
    Column("paid", String, default="не оплатил"),
    Column("status", String, default="не активирован")
)

database = Database(POSTGRES_URL)


# --- CRUD Функции ---
async def connect_db():
    await database.connect()

async def disconnect_db():
    await database.disconnect()

async def add_user(user_id: int, username: str):
    query = pg_insert(users).values(
        user_id=user_id,
        username=username or "Без ника",
        paid="не оплатил",
        status="не активирован"
    ).on_conflict_do_nothing(index_elements=["user_id"])
    await database.execute(query)

async def update_status(user_id: int, status: str):
    query = users.update().where(users.c.user_id == user_id).values(status=status)
    await database.execute(query)

async def set_paid_status(user_id: int, paid: str):
    query = users.update().where(users.c.user_id == user_id).values(paid=paid)
    await database.execute(query)

async def get_status(user_id: int):
    query = users.select().where(users.c.user_id == user_id)
    row = await database.fetch_one(query)
    return row["status"] if row else None

async def get_paid_status(user_id: int):
    query = users.select().where(users.c.user_id == user_id)
    row = await database.fetch_one(query)
    return row["paid"] if row else None

async def count_registered():
    return await database.fetch_val("SELECT COUNT(*) FROM users")

async def count_activated():
    return await database.fetch_val("SELECT COUNT(*) FROM users WHERE status = 'активирован'")

async def count_paid():
    query = "SELECT COUNT(*) FROM users WHERE paid = 'оплатил'"
    return await database.fetch_val(query)

async def get_registered_users():
    query = users.select().order_by(users.c.status.desc())
    return await database.fetch_all(query)

async def get_paid_users():
    query = users.select().where(users.c.status.isnot(None))
    return await database.fetch_all(query)

async def clear_database():
    await database.execute(users.delete())

async def mark_as_paid(user_id: int):
    query = users.update().where(users.c.user_id == user_id).values(paid="оплатил")
    await database.execute(query)
