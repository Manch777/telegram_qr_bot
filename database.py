from databases import Database
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    desc,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from config import POSTGRES_URL


# Database setup
engine = create_engine(POSTGRES_URL.replace("+asyncpg", ""))
metadata = MetaData()
database = Database(POSTGRES_URL)


# Purchases
# The table is historically named "users".
# Each row represents one ticket purchase.
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", BigInteger, index=True, nullable=False),
    Column("username", String),
    Column("event_code", String),
    Column("ticket_type", String),
    Column("paid", String, default="не оплатил"),
    Column("status", String, default="не активирован"),
    Column(
        "purchase_date",
        Date,
        server_default=text("CURRENT_DATE"),
    ),
)


# Attempts to purchase a 1+1 ticket after the limit has been reached
one_plus_one_attempts = Table(
    "one_plus_one_attempts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", BigInteger, nullable=False),
    Column("username", String),
    Column("event_code", String, nullable=False),
    Column(
        "attempted_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
)


# User roles: admin, scanner, payments
roles = Table(
    "roles",
    metadata,
    Column("user_id", BigInteger, nullable=False),
    Column("role", String, nullable=False),
    Column(
        "granted_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
)


# Bot subscribers
subscribers = Table(
    "subscribers",
    metadata,
    Column("user_id", BigInteger, primary_key=True),
    Column("username", String),
    Column(
        "last_seen_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
)


# Persistent key-value bot configuration
bot_meta = Table(
    "bot_meta",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String),
)


# Per-event 1+1 ticket limits
one_plus_one_limits = Table(
    "one_plus_one_limits",
    metadata,
    Column("event_code", String, primary_key=True),
    Column("limit_qty", Integer, nullable=False),
    Column(
        "updated_at",
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    ),
)


# Database connection
async def connect_db():
    await database.connect()


async def disconnect_db():
    await database.disconnect()


# ---------------------------------------------------------------------------
# Ticket operations by row ID
# ---------------------------------------------------------------------------

async def add_user(
    user_id: int,
    username: str,
    event_code: str,
    ticket_type: str,
) -> int:
    """Create a ticket purchase and return its row ID."""
    query = (
        users.insert()
        .values(
            user_id=user_id,
            username=username or "Без ника",
            event_code=event_code,
            ticket_type=ticket_type,
            paid="не оплатил",
            status="не активирован",
        )
        .returning(users.c.id)
    )

    row_id = await database.fetch_val(query)
    return int(row_id)


async def get_row(row_id: int):
    """Return a complete ticket record by row ID."""
    query = select(users).where(users.c.id == row_id)
    return await database.fetch_one(query)


async def get_status_by_id(row_id: int):
    query = select(users.c.status).where(users.c.id == row_id)
    row = await database.fetch_one(query)
    return row["status"] if row else None


async def update_status_by_id(row_id: int, status: str):
    query = (
        users.update()
        .where(users.c.id == row_id)
        .values(status=status)
    )
    await database.execute(query)


async def get_paid_status_by_id(row_id: int):
    query = select(users.c.paid).where(users.c.id == row_id)
    row = await database.fetch_one(query)
    return row["paid"] if row else None


async def set_paid_status_by_id(row_id: int, paid: str):
    query = (
        users.update()
        .where(users.c.id == row_id)
        .values(paid=paid)
    )
    await database.execute(query)


async def set_ticket_type_by_id(row_id: int, ticket_type: str):
    query = (
        users.update()
        .where(users.c.id == row_id)
        .values(ticket_type=ticket_type)
    )
    await database.execute(query)


# ---------------------------------------------------------------------------
# Ticket limits and counters
# ---------------------------------------------------------------------------

async def get_one_plus_one_limit(event_code: str) -> int | None:
    row = await database.fetch_one(
        "SELECT limit_qty FROM one_plus_one_limits WHERE event_code = :e",
        {"e": event_code},
    )
    return row["limit_qty"] if row else None


async def count_one_plus_one_taken(event_code: str) -> int:
    return await count_ticket_type_for_event(event_code, "1+1")


async def remaining_one_plus_one_for_event(
    event_code: str,
) -> int | None:
    limit = await get_one_plus_one_limit(event_code)

    if limit is None:
        return None

    used = await count_one_plus_one_taken(event_code)
    return max(limit - used, 0)


async def set_one_plus_one_limit(event_code: str, qty: int):
    query = """
        INSERT INTO one_plus_one_limits(event_code, limit_qty, updated_at)
        VALUES (:e, :q, NOW())
        ON CONFLICT (event_code) DO UPDATE
        SET limit_qty = EXCLUDED.limit_qty,
            updated_at = NOW()
    """
    await database.execute(
        query,
        {
            "e": event_code,
            "q": qty,
        },
    )


async def count_ticket_type_paid_for_event(
    event_code: str,
    ticket_type: str,
) -> int:
    query = """
        SELECT COUNT(*)
        FROM users
        WHERE event_code = :e
          AND ticket_type = :t
          AND paid = 'оплатил'
    """
    return await database.fetch_val(
        query,
        {
            "e": event_code,
            "t": ticket_type,
        },
    )


async def count_ticket_type_for_event(
    event_code: str,
    ticket_type: str,
) -> int:
    query = """
        SELECT COUNT(*)
        FROM users
        WHERE event_code = :e
          AND ticket_type = :t
          AND paid <> 'не оплатил'
    """
    return await database.fetch_val(
        query,
        {
            "e": event_code,
            "t": ticket_type,
        },
    )


# ---------------------------------------------------------------------------
# Aggregate purchase statistics
# ---------------------------------------------------------------------------

async def count_registered():
    """Return the total number of ticket purchase records."""
    return await database.fetch_val(
        "SELECT COUNT(*) FROM users"
    )


async def count_activated():
    """Return the number of activated tickets."""
    return await database.fetch_val(
        "SELECT COUNT(*) FROM users WHERE status = 'активирован'"
    )


async def count_paid():
    """Return the number of paid tickets."""
    return await database.fetch_val(
        "SELECT COUNT(*) FROM users WHERE paid = 'оплатил'"
    )


async def get_registered_users():
    """Return all ticket purchases ordered from newest to oldest."""
    query = select(
        users.c.user_id,
        users.c.username,
        users.c.paid,
        users.c.status,
    ).order_by(desc(users.c.id))

    rows = await database.fetch_all(query)

    return [
        (row.user_id, row.username, row.paid, row.status)
        for row in rows
    ]


async def get_paid_users():
    """Return all paid ticket purchases ordered from newest to oldest."""
    query = (
        select(
            users.c.user_id,
            users.c.username,
            users.c.status,
            users.c.paid,
        )
        .where(users.c.paid == "оплатил")
        .order_by(desc(users.c.id))
    )

    rows = await database.fetch_all(query)

    return [
        (row.user_id, row.username, row.status, row.paid)
        for row in rows
    ]


async def clear_database():
    await database.execute(users.delete())


# ---------------------------------------------------------------------------
# Subscribers
# ---------------------------------------------------------------------------

async def add_subscriber(
    user_id: int,
    username: str | None,
):
    query = """
        INSERT INTO subscribers(user_id, username, last_seen_at)
        VALUES (:uid, :uname, NOW())
        ON CONFLICT (user_id) DO UPDATE
        SET username = EXCLUDED.username,
            last_seen_at = NOW()
    """

    await database.execute(
        query,
        {
            "uid": user_id,
            "uname": username or "Без ника",
        },
    )


async def get_all_subscribers():
    rows = await database.fetch_all(
        "SELECT user_id, username FROM subscribers"
    )

    return [
        (row["user_id"], row["username"])
        for row in rows
    ]


async def get_all_recipient_ids() -> list[int]:
    rows = await database.fetch_all(
        "SELECT DISTINCT user_id FROM users"
    )
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Persistent bot metadata
# ---------------------------------------------------------------------------

async def set_meta(key: str, value: str):
    query = (
        pg_insert(bot_meta)
        .values(
            key=key,
            value=str(value),
        )
        .on_conflict_do_update(
            index_elements=[bot_meta.c.key],
            set_={"value": str(value)},
        )
    )

    await database.execute(query)


async def get_meta(key: str):
    query = select(bot_meta.c.value).where(
        bot_meta.c.key == key
    )
    row = await database.fetch_one(query)

    return row[0] if row else None


# ---------------------------------------------------------------------------
# Legacy user-ID operations
#
# Kept for compatibility with older QR codes and older parts of the bot.
# These functions operate on the user's most recent ticket purchase.
# ---------------------------------------------------------------------------

async def _latest_row_for_user(user_id: int):
    query = (
        select(users)
        .where(users.c.user_id == user_id)
        .order_by(desc(users.c.id))
        .limit(1)
    )

    return await database.fetch_one(query)


async def get_status(user_id: int):
    row = await _latest_row_for_user(user_id)
    return row["status"] if row else None


async def update_status(user_id: int, status: str):
    row = await _latest_row_for_user(user_id)

    if not row:
        return

    await update_status_by_id(row["id"], status)


async def get_paid_status(user_id: int):
    row = await _latest_row_for_user(user_id)
    return row["paid"] if row else None


async def set_paid_status(user_id: int, paid: str):
    row = await _latest_row_for_user(user_id)

    if not row:
        return

    await set_paid_status_by_id(row["id"], paid)


async def set_ticket_type(user_id: int, ticket_type: str):
    row = await _latest_row_for_user(user_id)

    if not row:
        return

    query = (
        users.update()
        .where(users.c.id == row["id"])
        .values(ticket_type=ticket_type)
    )
    await database.execute(query)


async def get_ticket_type(user_id: int):
    row = await _latest_row_for_user(user_id)
    return row["ticket_type"] if row else None


async def mark_as_paid(user_id: int):
    row = await _latest_row_for_user(user_id)

    if not row:
        return

    await set_paid_status_by_id(row["id"], "оплатил")


async def count_ticket_type(ticket_type: str):
    return await database.fetch_val(
        "SELECT COUNT(*) FROM users WHERE ticket_type = :t",
        {"t": ticket_type},
    )


# ---------------------------------------------------------------------------
# 1+1 purchase attempts
# ---------------------------------------------------------------------------

async def log_one_plus_one_attempt(
    user_id: int,
    username: str | None,
    event_code: str,
):
    query = one_plus_one_attempts.insert().values(
        user_id=user_id,
        username=username or "Без ника",
        event_code=event_code,
    )

    await database.execute(query)


async def get_one_plus_one_attempts_for_event(
    event_code: str,
):
    query = (
        one_plus_one_attempts.select()
        .where(
            one_plus_one_attempts.c.event_code == event_code
        )
        .order_by(
            one_plus_one_attempts.c.attempted_at.desc()
        )
    )

    return await database.fetch_all(query)


async def get_unique_one_plus_one_attempters_for_event(
    event_code: str,
):
    query = """
        SELECT
            user_id,
            MAX(username) AS username,
            MAX(attempted_at) AS last_try
        FROM one_plus_one_attempts
        WHERE event_code = :e
        GROUP BY user_id
        ORDER BY last_try DESC
    """

    return await database.fetch_all(
        query,
        {"e": event_code},
    )


# ---------------------------------------------------------------------------
# Ticket statistics
# ---------------------------------------------------------------------------

async def get_ticket_stats_grouped(
    paid_statuses=("оплатил",),
):
    """Return ticket statistics grouped by event and ticket type."""
    if paid_statuses:
        placeholders = ", ".join(
            f":p{i}" for i in range(len(paid_statuses))
        )
        values = {
            f"p{i}": status
            for i, status in enumerate(paid_statuses)
        }
        where = f"paid IN ({placeholders})"
    else:
        values = {}
        where = "TRUE"

    query = f"""
        SELECT
            COALESCE(event_code, '—') AS event_code,
            COALESCE(ticket_type, '—') AS ticket_type,
            COUNT(*)::int AS count
        FROM users
        WHERE {where}
        GROUP BY event_code, ticket_type
        ORDER BY event_code, ticket_type
    """

    return await database.fetch_all(query, values)


async def get_ticket_stats_for_event(
    event_code: str,
    paid_statuses=("оплатил",),
):
    """Return ticket statistics for a single event."""
    if paid_statuses:
        placeholders = ", ".join(
            f":p{i}" for i in range(len(paid_statuses))
        )
        values = {
            f"p{i}": status
            for i, status in enumerate(paid_statuses)
        }
        paid_clause = f"AND paid IN ({placeholders})"
    else:
        values = {}
        paid_clause = ""

    values["e"] = event_code

    query = f"""
        SELECT
            COALESCE(ticket_type, '—') AS ticket_type,
            COUNT(*)::int AS count
        FROM users
        WHERE event_code = :e
          {paid_clause}
        GROUP BY ticket_type
        ORDER BY ticket_type
    """

    return await database.fetch_all(query, values)


async def get_all_users_full(
    event_code: str | None = None,
):
    """Return all purchases, optionally filtered by event."""
    if event_code:
        query = (
            select(users)
            .where(users.c.event_code == event_code)
            .order_by(desc(users.c.id))
        )
    else:
        query = select(users).order_by(
            desc(users.c.id)
        )

    return await database.fetch_all(query)


# ---------------------------------------------------------------------------
# Roles and access control
# ---------------------------------------------------------------------------

async def has_role(
    user_id: int,
    role: str,
) -> bool:
    query = (
        select(roles.c.user_id)
        .where(
            roles.c.user_id == user_id,
            roles.c.role == role,
        )
        .limit(1)
    )

    row = await database.fetch_one(query)
    return row is not None


async def add_role(
    user_id: int,
    role: str,
) -> None:
    query = (
        pg_insert(roles)
        .values(
            user_id=user_id,
            role=role,
        )
        .on_conflict_do_nothing(
            index_elements=[
                roles.c.user_id,
                roles.c.role,
            ]
        )
    )

    await database.execute(query)


async def remove_role(
    user_id: int,
    role: str,
) -> None:
    query = roles.delete().where(
        roles.c.user_id == user_id,
        roles.c.role == role,
    )

    await database.execute(query)


async def get_role_user_ids(
    role: str,
) -> list[int]:
    query = select(roles.c.user_id).where(
        roles.c.role == role
    )

    rows = await database.fetch_all(query)
    return [int(row[0]) for row in rows]
