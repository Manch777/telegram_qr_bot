# database.py
from sqlalchemy import (
    Column, String, MetaData, Table, create_engine,
    BigInteger, Integer, Date, select, desc, text, DateTime 
)
from databases import Database
from sqlalchemy.sql import func
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

# --- Таблица попыток купить 1+1 при закрытом лимите ---
one_plus_one_attempts = Table(
    "one_plus_one_attempts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", BigInteger, nullable=False),
    Column("username", String),
    Column("event_code", String, nullable=False),
    Column("attempted_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

# Получить лимит 1+1 для мероприятия (или None)
async def get_one_plus_one_limit(event_code: str) -> int | None:
    row = await database.fetch_one(
        "SELECT limit_qty FROM one_plus_one_limits WHERE event_code = :e",
        {"e": event_code},
    )
    return row["limit_qty"] if row else None

# Сколько уже занято 1+1 (оплачено + на проверке)
async def count_one_plus_one_taken(event_code: str) -> int:
    return await count_ticket_type_for_event(event_code, "1+1")

# Остаток по 1+1 (None — лимит не задан)
async def remaining_one_plus_one_for_event(event_code: str) -> int | None:
    limit = await get_one_plus_one_limit(event_code)
    if limit is None:
        return None
    used = await count_one_plus_one_taken(event_code)
    return max(limit - used, 0)


# --- Подписчики (те, кто запускал бота) ---
subscribers = Table(
    "subscribers",
    metadata,
    Column("user_id", BigInteger, primary_key=True),
    Column("username", String),
    Column("last_seen_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

# --- Таблица мета-данных бота (key-value) ---
from sqlalchemy.dialects.postgresql import insert as pg_insert

bot_meta = Table(
    "bot_meta",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String),
)


# --- Лимиты 1+1 по мероприятиям ---
one_plus_one_limits = Table(
    "one_plus_one_limits",
    metadata,
    Column("event_code", String, primary_key=True),
    Column("limit_qty", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True),
           server_default=func.now(),
           onupdate=func.now(),
           nullable=False),
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

# --- Подписчики: upsert и выборка ---
async def add_subscriber(user_id: int, username: str | None):
    q = """
    INSERT INTO subscribers(user_id, username, last_seen_at)
    VALUES (:uid, :uname, NOW())
    ON CONFLICT (user_id) DO UPDATE
      SET username = EXCLUDED.username,
          last_seen_at = NOW()
    """
    await database.execute(q, {"uid": user_id, "uname": (username or "Без ника")})

async def get_all_subscribers():
    rows = await database.fetch_all("SELECT user_id, username FROM subscribers")
    return [(r["user_id"], r["username"]) for r in rows]

async def set_meta(key: str, value: str):
    q = pg_insert(bot_meta).values(key=key, value=str(value)).on_conflict_do_update(
        index_elements=[bot_meta.c.key],
        set_={"value": str(value)}
    )
    await database.execute(q)

async def get_meta(key: str):
    row = await database.fetch_one(select(bot_meta.c.value).where(bot_meta.c.key == key))
    return row[0] if row else None

# Уникальные пользователи, которым шлём рассылку
async def get_all_recipient_ids() -> list[int]:
    rows = await database.fetch_all("SELECT DISTINCT user_id FROM users")
    return [r[0] for r in rows]


# Upsert лимита 1+1 для мероприятия
async def set_one_plus_one_limit(event_code: str, qty: int):
    q = """
        INSERT INTO one_plus_one_limits(event_code, limit_qty, updated_at)
        VALUES (:e, :q, NOW())
        ON CONFLICT (event_code) DO UPDATE
          SET limit_qty = EXCLUDED.limit_qty,
              updated_at = NOW()
    """
    await database.execute(q, {"e": event_code, "q": qty})



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

# =============================================================================
# Попытки купить 1+1
# =============================================================================

# Логируем попытку купить 1+1 при закрытом лимите
async def log_one_plus_one_attempt(user_id: int, username: str | None, event_code: str):
    q = one_plus_one_attempts.insert().values(
        user_id=user_id,
        username=username or "Без ника",
        event_code=event_code,
    )
    await database.execute(q)

# Все попытки по мероприятию (полный список)
async def get_one_plus_one_attempts_for_event(event_code: str):
    q = one_plus_one_attempts.select().where(one_plus_one_attempts.c.event_code == event_code)\
        .order_by(one_plus_one_attempts.c.attempted_at.desc())
    rows = await database.fetch_all(q)
    return rows  # можно и сразу привести к list[dict], если хочешь

# Уникальные пользователи, которые пытались (с датой последней попытки)
async def get_unique_one_plus_one_attempters_for_event(event_code: str):
    q = """
        SELECT user_id,
               MAX(username) AS username,     -- последнее известное имя
               MAX(attempted_at) AS last_try
        FROM one_plus_one_attempts
        WHERE event_code = :e
        GROUP BY user_id
        ORDER BY last_try DESC
    """
    return await database.fetch_all(q, {"e": event_code})


# =============================================================================
# Статистика продаж по мероприятиям/типам
# =============================================================================

async def get_ticket_stats_grouped(paid_statuses=("оплатил",)):
    """
    Вернёт сгруппированную статистику: (event_code, ticket_type, count)
    по указанным статусам оплаты (по умолчанию только 'оплатил').
    """
    if paid_statuses:
        placeholders = ", ".join([f":p{i}" for i in range(len(paid_statuses))])
        values = {f"p{i}": s for i, s in enumerate(paid_statuses)}
        where = f"paid IN ({placeholders})"
    else:
        values = {}
        where = "TRUE"

    q = f"""
        SELECT
            COALESCE(event_code, '—') AS event_code,
            COALESCE(ticket_type, '—') AS ticket_type,
            COUNT(*)::int AS count
        FROM users
        WHERE {where}
        GROUP BY event_code, ticket_type
        ORDER BY event_code, ticket_type
    """
    return await database.fetch_all(q, values)

async def get_ticket_stats_for_event(event_code: str, paid_statuses=("оплатил",)):
    """
    То же, но только для одного мероприятия.
    """
    if paid_statuses:
        placeholders = ", ".join([f":p{i}" for i in range(len(paid_statuses))])
        values = {f"p{i}": s for i, s in enumerate(paid_statuses)}
        paid_clause = f"AND paid IN ({placeholders})"
    else:
        values = {}
        paid_clause = ""

    values["e"] = event_code

    q = f"""
        SELECT
            COALESCE(ticket_type, '—') AS ticket_type,
            COUNT(*)::int AS count
        FROM users
        WHERE event_code = :e
          {paid_clause}
        GROUP BY ticket_type
        ORDER BY ticket_type
    """
    return await database.fetch_all(q, values)



from typing import Optional
from sqlalchemy import select, desc

async def get_all_users_full(event_code: Optional[str] = None):
    """
    Возвращает все строки из таблицы users.
    Если event_code задан — только по этому мероприятию.
    """
    if event_code:
        q = select(users).where(users.c.event_code == event_code).order_by(desc(users.c.id))
    else:
        q = select(users).order_by(desc(users.c.id))
    return await database.fetch_all(q)
