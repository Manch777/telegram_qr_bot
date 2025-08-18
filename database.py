# database.py
from sqlalchemy import (
    Column, String, MetaData, Table, create_engine,
    BigInteger, Integer, Date, select, desc, text
)
from databases import Database
from config import POSTGRES_URL

# --- Подключение ---
engine = create_engine(POSTGRES_URL.replace("+asyncpg", ""))
metadata = MetaData()

# --- Таблица покупок (исторически называется "users") ---
# Одна строка = один купленный билет.
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),   # ID записи (билета)
    Column("user_id", BigInteger, index=True, nullable=False),     # Telegram user_id (может повторяться)
    Column("username", String),
    Column("event_code", String),                                   # код/название мероприятия
    Column("ticket_type", String),                                  # тип билета
    Column("paid", String, default="не оплатил"),                   # не оплатил | на проверке | оплатил | отклонено
    Column("status", String, default="не активирован"),             # не активирован | активирован
    Column("purchase_date", Date, server_default=text("CURRENT_DATE"))  # дата покупки (без времени)
)

database = Database(POSTGRES_URL)

# --- Базовые подключения ---
async def connect_db():
    await database.connect()

async def disconnect_db():
    await database.disconnect()

# =============================================================================
# Новые функции: работаем с КОНКРЕТНОЙ записью (id строки = "row_id")
# =============================================================================

async def add_user(user_id: int, username: str, event_code: str, ticket_type: str) -> int:
    """
    Создать новую покупку (новую строку). Возвращает id созданной записи (row_id).
    purchase_date ставится в БД как CURRENT_DATE (server_default).
    """
    query = users.insert().values(
        user_id=user_id,
        username=username or "Без ника",
        event_code=event_code,
        ticket_type=ticket_type,
        paid="не оплатил",
        status="не активирован",
        # purchase_date не передаём — БД выставит CURRENT_DATE сама
    ).returning(users.c.id)
    row_id = await database.fetch_val(query)
    return int(row_id)

async def get_row(row_id: int):
    """Вернуть полную запись по id строки (или None)."""
    query = select(users).where(users.c.id == row_id)
    return await database.fetch_one(query)

# ---- Статусы по id строки ----
async def get_status_by_id(row_id: int):
    r = await database.fetch_one(select(users.c.status).where(users.c.id == row_id))
    return r["status"] if r else None

async def update_status_by_id(row_id: int, status: str):
    await database.execute(users.update().where(users.c.id == row_id).values(status=status))

async def get_paid_status_by_id(row_id: int):
    r = await database.fetch_one(select(users.c.paid).where(users.c.id == row_id))
    return r["paid"] if r else None

async def set_paid_status_by_id(row_id: int, paid: str):
    await database.execute(users.update().where(users.c.id == row_id).values(paid=paid))

# ---- Подсчёты (по мероприятию и типу) ----
async def count_ticket_type_paid_for_event(event_code: str, ticket_type: str) -> int:
    q = """
        SELECT COUNT(*)
        FROM users
        WHERE event_code = :e AND ticket_type = :t AND paid = 'оплатил'
    """
    return await database.fetch_val(q, {"e": event_code, "t": ticket_type})

async def count_ticket_type_for_event(event_code: str, ticket_type: str) -> int:
    q = """
        SELECT COUNT(*)
        FROM users
        WHERE event_code = :e
          AND ticket_type = :t
          AND paid IN ('оплатил', 'на проверке')
    """
    return await database.fetch_val(q, {"e": event_code, "t": ticket_type})
# =============================================================================
# Агрегаты / списки (по всем покупкам)
# =============================================================================

async def count_registered():
    """Сколько всего строк (покупок) создано."""
    return await database.fetch_val("SELECT COUNT(*) FROM users")

async def count_activated():
    """Сколько билетов со статусом 'активирован'."""
    return await database.fetch_val("SELECT COUNT(*) FROM users WHERE status = 'активирован'")

async def count_paid():
    """Сколько билетов со статусом оплаты 'оплатил'."""
    return await database.fetch_val("SELECT COUNT(*) FROM users WHERE paid = 'оплатил'")

async def get_registered_users():
    """
    Список всех созданных покупок (для /users).
    Возвращает [(user_id, username, paid, status), ...]
    """
    query = select(
        users.c.user_id, users.c.username, users.c.paid, users.c.status
    ).order_by(desc(users.c.id))
    rows = await database.fetch_all(query)
    return [(r.user_id, r.username, r.paid, r.status) for r in rows]

async def get_paid_users():
    """
    Список всех ОПЛАТИВШИХ покупок (для /paid_users).
    Возвращает [(user_id, username, status, paid), ...]
    """
    query = select(
        users.c.user_id, users.c.username, users.c.status, users.c.paid
    ).where(users.c.paid == "оплатил").order_by(desc(users.c.id))
    rows = await database.fetch_all(query)
    return [(r.user_id, r.username, r.status, r.paid) for r in rows]

async def clear_database():
    await database.execute(users.delete())

# =============================================================================
# Legacy-обёртки по user_id (совместимость со старым кодом)
# Берут САМУЮ ПОСЛЕДНЮЮ запись этого пользователя (ORDER BY id DESC LIMIT 1)
# =============================================================================

async def _latest_row_for_user(user_id: int):
    q = select(users).where(users.c.user_id == user_id).order_by(desc(users.c.id)).limit(1)
    return await database.fetch_one(q)

async def get_status(user_id: int):
    r = await _latest_row_for_user(user_id)
    return r["status"] if r else None

async def update_status(user_id: int, status: str):
    r = await _latest_row_for_user(user_id)
    if not r:
        return
    await update_status_by_id(r["id"], status)

async def get_paid_status(user_id: int):
    r = await _latest_row_for_user(user_id)
    return r["paid"] if r else None

async def set_paid_status(user_id: int, paid: str):
    r = await _latest_row_for_user(user_id)
    if not r:
        return
    await set_paid_status_by_id(r["id"], paid)

async def set_ticket_type(user_id: int, ticket_type: str):
    r = await _latest_row_for_user(user_id)
    if not r:
        return
    await database.execute(users.update().where(users.c.id == r["id"]).values(ticket_type=ticket_type))

async def get_ticket_type(user_id: int):
    r = await _latest_row_for_user(user_id)
    return r["ticket_type"] if r else None

async def mark_as_paid(user_id: int):
    r = await _latest_row_for_user(user_id)
    if not r:
        return
    await set_paid_status_by_id(r["id"], "оплатил")

async def count_ticket_type(ticket_type: str):
    """Кол-во покупок указанного типа по всем мероприятиям (без учёта статуса оплаты)."""
    return await database.fetch_val("SELECT COUNT(*) FROM users WHERE ticket_type = :t", {"t": ticket_type})
