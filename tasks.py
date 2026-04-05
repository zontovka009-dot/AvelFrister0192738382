# ═══════════════════════════════════════════
#   KILLER RAID — tasks.py
#   Фоновые задачи (планировщик)
# ═══════════════════════════════════════════

import asyncio
import logging
from datetime import datetime

import aiosqlite
from aiogram import Bot

import database as db
from utils import restore_permissions
from config import DB_PATH

logger = logging.getLogger("KillerRaid.Tasks")

_running = False


async def task_auto_sterile_off(bot: Bot) -> None:
    """Каждые 60 сек снимает стерильный режим если истёк таймер."""
    while _running:
        try:
            async with aiosqlite.connect(DB_PATH) as conn:
                conn.row_factory = aiosqlite.Row
                cur = await conn.execute(
                    "SELECT chat_id, sterile_until FROM chat_modes "
                    "WHERE sterile=1 AND sterile_until IS NOT NULL"
                )
                rows = [dict(r) for r in await cur.fetchall()]

            now = datetime.utcnow()
            for row in rows:
                try:
                    until_dt = datetime.fromisoformat(row["sterile_until"])
                except (ValueError, TypeError):
                    continue
                if now >= until_dt:
                    chat_id = row["chat_id"]
                    await db.set_sterile(chat_id, False)
                    await restore_permissions(chat_id, bot)
                    await db.log_event(chat_id, "STERILE_AUTO_OFF",
                                       details="истёк таймер")
                    logger.info("Auto sterile OFF: chat_id=%d", chat_id)
                    try:
                        await bot.send_message(
                            chat_id,
                            "🟢  Стерильный режим снят автоматически (истёк таймер).",
                        )
                    except Exception as e:
                        logger.warning("send auto_off: %s", e)
        except Exception as e:
            logger.error("task_auto_sterile_off: %s", e)
        await asyncio.sleep(60)


async def task_cleanup(_bot: Bot) -> None:
    """Каждые 10 минут удаляет устаревшие строки."""
    while _running:
        try:
            await db.cleanup_join_log(older_than_minutes=60)
            await db.cleanup_spam_counts(older_than_sec=60)
        except Exception as e:
            logger.error("task_cleanup: %s", e)
        await asyncio.sleep(600)


async def start_tasks(bot: Bot) -> None:
    global _running
    _running = True
    logger.info("Background tasks started.")
    await asyncio.gather(
        task_auto_sterile_off(bot),
        task_cleanup(bot),
    )


def stop_tasks() -> None:
    global _running
    _running = False
    logger.info("Background tasks stopped.")
