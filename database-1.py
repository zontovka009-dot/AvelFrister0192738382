# ═══════════════════════════════════════════
#   KILLER RAID — database.py
#   Async SQLite3 + FTS5 + индексы
# ═══════════════════════════════════════════

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite

from config import DB_PATH

logger = logging.getLogger("KillerRaid.DB")

# ─────────────────────────────────────────────
#  ИНИЦИАЛИЗАЦИЯ / МИГРАЦИЯ
# ─────────────────────────────────────────────

CREATE_STATEMENTS = [
    # ── Режимы чатов ──────────────────────────
    """
    CREATE TABLE IF NOT EXISTS chat_modes (
        chat_id        INTEGER PRIMARY KEY,
        sterile        INTEGER NOT NULL DEFAULT 0,
        silence        INTEGER NOT NULL DEFAULT 0,
        sterile_until  TEXT,              -- ISO datetime авто-снятия
        updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # ── Защитники ─────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS defenders (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id    INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        full_name  TEXT    NOT NULL DEFAULT '',
        username   TEXT    NOT NULL DEFAULT '',
        added_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE(chat_id, user_id)
    )
    """,

    # ── История входов (для /выгнать + рейд) ──
    """
    CREATE TABLE IF NOT EXISTS join_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id    INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        joined_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # ── Выданные муты ─────────────────────────
    """
    CREATE TABLE IF NOT EXISTS mutes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id    INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        muted_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        until      TEXT    NOT NULL,
        reason     TEXT    NOT NULL DEFAULT '',
        lifted     INTEGER NOT NULL DEFAULT 0,
        UNIQUE(chat_id, user_id)
    )
    """,

    # ── Лог событий (аудит) ───────────────────
    """
    CREATE TABLE IF NOT EXISTS event_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id    INTEGER NOT NULL,
        event_type TEXT    NOT NULL,   -- RAID / STERILE_ON / MUTE / BAN / …
        user_id    INTEGER,
        details    TEXT,
        created_at TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # ── Спам-счётчики (в памяти; таблица нужна для персистентности) ──
    """
    CREATE TABLE IF NOT EXISTS spam_counts (
        chat_id    INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        ts         TEXT    NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (chat_id, user_id, ts)
    )
    """,

    # ══ ИНДЕКСЫ ══════════════════════════════
    "CREATE INDEX IF NOT EXISTS idx_defenders_chat    ON defenders  (chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_join_log_chat_ts  ON join_log   (chat_id, joined_at)",
    "CREATE INDEX IF NOT EXISTS idx_mutes_chat_user   ON mutes      (chat_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_chat    ON event_log  (chat_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_spam_chat_user_ts ON spam_counts(chat_id, user_id, ts)",

    # ══ FTS5 ══════════════════════════════════
    # Полнотекстовый поиск по логу событий (details + event_type)
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS event_log_fts
    USING fts5(
        event_type,
        details,
        content='event_log',
        content_rowid='id'
    )
    """,

    # Полнотекстовый поиск по защитникам (full_name + username)
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS defenders_fts
    USING fts5(
        full_name,
        username,
        content='defenders',
        content_rowid='id'
    )
    """,

    # ══ ТРИГГЕРЫ для синхронизации FTS5 ═══════
    """
    CREATE TRIGGER IF NOT EXISTS event_log_ai
    AFTER INSERT ON event_log BEGIN
        INSERT INTO event_log_fts(rowid, event_type, details)
        VALUES (new.id, new.event_type, new.details);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS event_log_ad
    AFTER DELETE ON event_log BEGIN
        INSERT INTO event_log_fts(event_log_fts, rowid, event_type, details)
        VALUES ('delete', old.id, old.event_type, old.details);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS defenders_ai
    AFTER INSERT ON defenders BEGIN
        INSERT INTO defenders_fts(rowid, full_name, username)
        VALUES (new.id, new.full_name, new.username);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS defenders_ad
    AFTER DELETE ON defenders BEGIN
        INSERT INTO defenders_fts(defenders_fts, rowid, full_name, username)
        VALUES ('delete', old.id, old.full_name, old.username);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS defenders_au
    AFTER UPDATE ON defenders BEGIN
        INSERT INTO defenders_fts(defenders_fts, rowid, full_name, username)
        VALUES ('delete', old.id, old.full_name, old.username);
        INSERT INTO defenders_fts(rowid, full_name, username)
        VALUES (new.id, new.full_name, new.username);
    END
    """,
]


async def init_db() -> None:
    """Создать все таблицы, индексы и FTS5 виртуальные таблицы."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA synchronous=NORMAL")
        for stmt in CREATE_STATEMENTS:
            try:
                await db.execute(stmt)
            except Exception as e:
                logger.warning("DB init stmt error: %s | %s", e, stmt[:60])
        await db.commit()
    logger.info("Database initialised: %s", DB_PATH)


# ─────────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНЫЙ КОНТЕКСТ-МЕНЕДЖЕР
# ─────────────────────────────────────────────

async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


# ─────────────────────────────────────────────
#  chat_modes
# ─────────────────────────────────────────────

async def get_chat_mode(chat_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT sterile, silence, sterile_until FROM chat_modes WHERE chat_id=?",
            (chat_id,)
        )
        row = await cur.fetchone()
        if row:
            return dict(row)
        return {"sterile": 0, "silence": 0, "sterile_until": None}


async def set_sterile(chat_id: int, active: bool, until: Optional[datetime] = None) -> None:
    until_str = until.isoformat() if until else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO chat_modes (chat_id, sterile, sterile_until, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(chat_id) DO UPDATE SET
                sterile       = excluded.sterile,
                sterile_until = excluded.sterile_until,
                updated_at    = excluded.updated_at
            """,
            (chat_id, int(active), until_str)
        )
        await db.commit()


async def set_silence(chat_id: int, active: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO chat_modes (chat_id, silence, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(chat_id) DO UPDATE SET
                silence    = excluded.silence,
                updated_at = excluded.updated_at
            """,
            (chat_id, int(active))
        )
        await db.commit()


# ─────────────────────────────────────────────
#  defenders
# ─────────────────────────────────────────────

async def add_defender(chat_id: int, user_id: int,
                        full_name: str, username: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO defenders (chat_id, user_id, full_name, username)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                full_name = excluded.full_name,
                username  = excluded.username
            """,
            (chat_id, user_id, full_name, username)
        )
        await db.commit()


async def remove_defender(chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM defenders WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)
        )
        await db.commit()


async def get_defenders(chat_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT user_id, full_name, username FROM defenders WHERE chat_id=?",
            (chat_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def is_defender(chat_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM defenders WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)
        )
        return await cur.fetchone() is not None


# ─────────────────────────────────────────────
#  join_log
# ─────────────────────────────────────────────

async def log_join(chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO join_log (chat_id, user_id) VALUES (?, ?)",
            (chat_id, user_id)
        )
        await db.commit()


async def count_recent_joins(chat_id: int, window_sec: int) -> int:
    since = (datetime.utcnow() - timedelta(seconds=window_sec)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM join_log WHERE chat_id=? AND joined_at > ?",
            (chat_id, since)
        )
        row = await cur.fetchone()
        return row[0] if row else 0


async def get_joins_since(chat_id: int, minutes: int) -> list[int]:
    since = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id FROM join_log WHERE chat_id=? AND joined_at > ?",
            (chat_id, since)
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def cleanup_join_log(older_than_minutes: int = 60) -> None:
    since = (datetime.utcnow() - timedelta(minutes=older_than_minutes)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM join_log WHERE joined_at < ?", (since,)
        )
        await db.commit()


# ─────────────────────────────────────────────
#  mutes
# ─────────────────────────────────────────────

async def add_mute(chat_id: int, user_id: int,
                   minutes: int, reason: str = "") -> datetime:
    until = datetime.utcnow() + timedelta(minutes=minutes)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO mutes (chat_id, user_id, until, reason, lifted)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                until  = excluded.until,
                reason = excluded.reason,
                lifted = 0,
                muted_at = datetime('now')
            """,
            (chat_id, user_id, until.isoformat(), reason)
        )
        await db.commit()
    return until


async def lift_mute(chat_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id FROM mutes WHERE chat_id=? AND user_id=? AND lifted=0",
            (chat_id, user_id)
        )
        row = await cur.fetchone()
        if not row:
            return False
        await db.execute(
            "UPDATE mutes SET lifted=1 WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)
        )
        await db.commit()
        return True


async def get_active_mutes(chat_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT user_id, until, reason
            FROM mutes
            WHERE chat_id=? AND lifted=0 AND until > datetime('now')
            """,
            (chat_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────
#  event_log  (аудит + FTS5)
# ─────────────────────────────────────────────

async def log_event(chat_id: int, event_type: str,
                    user_id: Optional[int] = None,
                    details: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO event_log (chat_id, event_type, user_id, details)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, event_type, user_id, details)
        )
        await db.commit()


async def search_events(chat_id: int, query: str) -> list[dict]:
    """FTS5 полнотекстовый поиск по логу событий."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT e.id, e.event_type, e.user_id, e.details, e.created_at
            FROM event_log e
            JOIN event_log_fts f ON e.id = f.rowid
            WHERE f.event_log_fts MATCH ?
              AND e.chat_id = ?
            ORDER BY e.created_at DESC
            LIMIT 50
            """,
            (query, chat_id)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────
#  spam_counts (окно детекции)
# ─────────────────────────────────────────────

async def record_spam(chat_id: int, user_id: int) -> int:
    """Записать факт спама и вернуть кол-во за последние SPAM_WINDOW_SEC секунд."""
    from config import SPAM_WINDOW_SEC
    since = (datetime.utcnow() - timedelta(seconds=SPAM_WINDOW_SEC)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO spam_counts (chat_id, user_id) VALUES (?, ?)",
            (chat_id, user_id)
        )
        # Чистим старые записи этого пользователя
        await db.execute(
            "DELETE FROM spam_counts WHERE chat_id=? AND user_id=? AND ts < ?",
            (chat_id, user_id, since)
        )
        cur = await db.execute(
            "SELECT COUNT(*) FROM spam_counts WHERE chat_id=? AND user_id=? AND ts >= ?",
            (chat_id, user_id, since)
        )
        row = await cur.fetchone()
        await db.commit()
        return row[0] if row else 0


async def count_spammers(chat_id: int, min_count: int = 2) -> int:
    """Сколько уникальных пользователей спамят прямо сейчас."""
    from config import SPAM_WINDOW_SEC
    since = (datetime.utcnow() - timedelta(seconds=SPAM_WINDOW_SEC)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT user_id FROM spam_counts
                WHERE chat_id=? AND ts >= ?
                GROUP BY user_id
                HAVING COUNT(*) >= ?
            )
            """,
            (chat_id, since, min_count)
        )
        row = await cur.fetchone()
        return row[0] if row else 0


async def cleanup_spam_counts(older_than_sec: int = 30) -> None:
    since = (datetime.utcnow() - timedelta(seconds=older_than_sec)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM spam_counts WHERE ts < ?", (since,))
        await db.commit()
