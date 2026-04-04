# ═══════════════════════════════════════════════════════════
#   KILLER RAID — database.py
#   Асинхронная SQLite3 БД (aiosqlite)
#   FTS-5 таблицы + индексы + обновление в реальном времени
# ═══════════════════════════════════════════════════════════

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import aiosqlite

from config import DB_PATH

logger = logging.getLogger("KillerRaid.DB")

# ─────────────────────────────────────────────
#  DDL — создание схемы
# ─────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

-- ── Состояние чатов ──────────────────────────
CREATE TABLE IF NOT EXISTS chat_state (
    chat_id         INTEGER PRIMARY KEY,
    sterile_mode    INTEGER NOT NULL DEFAULT 0,   -- 0/1
    silence_mode    INTEGER NOT NULL DEFAULT 0,
    sterile_until   TEXT,                          -- ISO datetime, NULL = бессрочно
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Защитники ────────────────────────────────
CREATE TABLE IF NOT EXISTS defenders (
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (chat_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_defenders_chat ON defenders(chat_id);

-- ── Муты ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS mutes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    until       TEXT NOT NULL,                     -- ISO datetime
    reason      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(chat_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_mutes_chat    ON mutes(chat_id);
CREATE INDEX IF NOT EXISTS idx_mutes_user    ON mutes(user_id);
CREATE INDEX IF NOT EXISTS idx_mutes_until   ON mutes(until);

-- ── Лог входов (для /выгнать + детекции) ─────
CREATE TABLE IF NOT EXISTS join_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    joined_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_joinlog_chat_time ON join_log(chat_id, joined_at);

-- ── Лог спама (стикеры/гифы) ─────────────────
CREATE TABLE IF NOT EXISTS spam_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    sent_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_spamlog_chat_user_time ON spam_log(chat_id, user_id, sent_at);

-- ── Лог событий (FTS-5) ──────────────────────
CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    event_type  TEXT NOT NULL,   -- raid_detected | sterile_on | sterile_off | mute | kick | ban
    actor_id    INTEGER,
    target_id   INTEGER,
    details     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_eventlog_chat_time ON event_log(chat_id, created_at);

-- FTS-5 по деталям событий
CREATE VIRTUAL TABLE IF NOT EXISTS event_log_fts
USING fts5(
    event_type,
    details,
    content='event_log',
    content_rowid='id'
);

-- Триггеры для синхронизации FTS-5 в реальном времени
CREATE TRIGGER IF NOT EXISTS event_log_ai AFTER INSERT ON event_log BEGIN
    INSERT INTO event_log_fts(rowid, event_type, details)
    VALUES (new.id, new.event_type, new.details);
END;

CREATE TRIGGER IF NOT EXISTS event_log_ad AFTER DELETE ON event_log BEGIN
    INSERT INTO event_log_fts(event_log_fts, rowid, event_type, details)
    VALUES ('delete', old.id, old.event_type, old.details);
END;

CREATE TRIGGER IF NOT EXISTS event_log_au AFTER UPDATE ON event_log BEGIN
    INSERT INTO event_log_fts(event_log_fts, rowid, event_type, details)
    VALUES ('delete', old.id, old.event_type, old.details);
    INSERT INTO event_log_fts(rowid, event_type, details)
    VALUES (new.id, new.event_type, new.details);
END;

-- Триггер: автообновление updated_at в chat_state
CREATE TRIGGER IF NOT EXISTS chat_state_au AFTER UPDATE ON chat_state BEGIN
    UPDATE chat_state SET updated_at = datetime('now') WHERE chat_id = new.chat_id;
END;
"""

# ─────────────────────────────────────────────
#  ИНИЦИАЛИЗАЦИЯ
# ─────────────────────────────────────────────

async def init_db() -> None:
    """Создать/обновить схему БД."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()
    logger.info("БД инициализирована: %s", DB_PATH)

# ─────────────────────────────────────────────
#  chat_state
# ─────────────────────────────────────────────

async def get_chat_state(chat_id: int) -> dict:
    """Вернуть состояние чата (создать запись если нет)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM chat_state WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT OR IGNORE INTO chat_state (chat_id) VALUES (?)", (chat_id,)
            )
            await db.commit()
            async with db.execute(
                "SELECT * FROM chat_state WHERE chat_id = ?", (chat_id,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row)


async def set_sterile(chat_id: int, enabled: bool, until: Optional[datetime] = None) -> None:
    until_str = until.isoformat() if until else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO chat_state (chat_id, sterile_mode, sterile_until)
               VALUES (?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   sterile_mode  = excluded.sterile_mode,
                   sterile_until = excluded.sterile_until""",
            (chat_id, int(enabled), until_str),
        )
        await db.commit()


async def set_silence(chat_id: int, enabled: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO chat_state (chat_id, silence_mode)
               VALUES (?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET silence_mode = excluded.silence_mode""",
            (chat_id, int(enabled)),
        )
        await db.commit()

# ─────────────────────────────────────────────
#  defenders
# ─────────────────────────────────────────────

async def add_defender(chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO defenders (chat_id, user_id) VALUES (?, ?)",
            (chat_id, user_id),
        )
        await db.commit()


async def remove_defender(chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM defenders WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        await db.commit()


async def get_defenders(chat_id: int) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM defenders WHERE chat_id = ?", (chat_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def is_defender(chat_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM defenders WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ) as cur:
            return await cur.fetchone() is not None

# ─────────────────────────────────────────────
#  mutes
# ─────────────────────────────────────────────

async def add_mute(chat_id: int, user_id: int, until: datetime, reason: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO mutes (chat_id, user_id, until, reason)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(chat_id, user_id) DO UPDATE SET
                   until  = excluded.until,
                   reason = excluded.reason,
                   created_at = datetime('now')""",
            (chat_id, user_id, until.isoformat(), reason),
        )
        await db.commit()


async def remove_mute(chat_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM mutes WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_active_mutes(chat_id: int) -> list[dict]:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM mutes WHERE chat_id = ? AND until > ?",
            (chat_id, now),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def is_muted(chat_id: int, user_id: int) -> bool:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM mutes WHERE chat_id = ? AND user_id = ? AND until > ?",
            (chat_id, user_id, now),
        ) as cur:
            return await cur.fetchone() is not None

# ─────────────────────────────────────────────
#  join_log
# ─────────────────────────────────────────────

async def log_join(chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO join_log (chat_id, user_id) VALUES (?, ?)",
            (chat_id, user_id),
        )
        await db.commit()


async def get_joins_since(chat_id: int, since: datetime) -> list[int]:
    """Вернуть список user_id, вступивших с момента since."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM join_log WHERE chat_id = ? AND joined_at >= ?",
            (chat_id, since.isoformat()),
        ) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def count_joins_in_window(chat_id: int, window_sec: int) -> int:
    """Кол-во входов за последние window_sec секунд."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT COUNT(*) FROM join_log
               WHERE chat_id = ?
                 AND joined_at >= datetime('now', ? || ' seconds')""",
            (chat_id, f"-{window_sec}"),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def purge_old_joins(chat_id: int, older_than_minutes: int = 60) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """DELETE FROM join_log
               WHERE chat_id = ?
                 AND joined_at < datetime('now', ? || ' minutes')""",
            (chat_id, f"-{older_than_minutes}"),
        )
        await db.commit()

# ─────────────────────────────────────────────
#  spam_log
# ─────────────────────────────────────────────

async def log_spam(chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO spam_log (chat_id, user_id) VALUES (?, ?)",
            (chat_id, user_id),
        )
        await db.commit()


async def count_spam_in_window(chat_id: int, user_id: int, window_sec: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT COUNT(*) FROM spam_log
               WHERE chat_id = ? AND user_id = ?
                 AND sent_at >= datetime('now', ? || ' seconds')""",
            (chat_id, user_id, f"-{window_sec}"),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def count_spammers_in_window(chat_id: int, threshold: int, window_sec: int) -> int:
    """Кол-во уникальных юзеров, достигших threshold спам-сообщений за window_sec."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT COUNT(*) FROM (
                   SELECT user_id FROM spam_log
                   WHERE chat_id = ?
                     AND sent_at >= datetime('now', ? || ' seconds')
                   GROUP BY user_id
                   HAVING COUNT(*) >= ?
               )""",
            (chat_id, f"-{window_sec}", threshold),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def purge_old_spam(chat_id: int, older_than_minutes: int = 5) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """DELETE FROM spam_log
               WHERE chat_id = ?
                 AND sent_at < datetime('now', ? || ' minutes')""",
            (chat_id, f"-{older_than_minutes}"),
        )
        await db.commit()

# ─────────────────────────────────────────────
#  event_log
# ─────────────────────────────────────────────

async def log_event(
    chat_id: int,
    event_type: str,
    actor_id: Optional[int] = None,
    target_id: Optional[int] = None,
    details: str = "",
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO event_log (chat_id, event_type, actor_id, target_id, details)
               VALUES (?, ?, ?, ?, ?)""",
            (chat_id, event_type, actor_id, target_id, details),
        )
        await db.commit()


async def search_events(chat_id: int, query: str, limit: int = 20) -> list[dict]:
    """FTS-5 поиск по событиям чата."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT e.* FROM event_log e
               JOIN event_log_fts f ON f.rowid = e.id
               WHERE e.chat_id = ? AND event_log_fts MATCH ?
               ORDER BY e.created_at DESC
               LIMIT ?""",
            (chat_id, query, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
