# ═══════════════════════════════════════════
#   KILLER RAID — handlers/members.py
#   Отслеживание входов + авто-детекция рейда
# ═══════════════════════════════════════════

import logging
from datetime import datetime, timedelta

from aiogram import Router, Bot, F
from aiogram.types import ChatMemberUpdated
from aiogram.filters.chat_member_updated import (
    ChatMemberUpdatedFilter, JOIN_TRANSITION,
)

import database as db
from utils import MSG, kb_sterile_off, apply_sterile, do_ban
from config import (
    RAID_JOIN_WINDOW_SEC, RAID_JOIN_THRESHOLD,
    STERILE_AUTO_HOURS,
)

logger = logging.getLogger("KillerRaid.Members")
router = Router()


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_new_member(event: ChatMemberUpdated, bot: Bot) -> None:
    chat_id = event.chat.id
    user    = event.new_chat_member.user

    if user.is_bot:
        return

    # Записываем вход в БД
    await db.log_join(chat_id, user.id)

    # ── Стерильный режим: мгновенный бан ──
    mode = await db.get_chat_mode(chat_id)
    if mode["sterile"]:
        await do_ban(chat_id, user.id, bot)
        await db.log_event(chat_id, "BAN_STERILE", user.id,
                           f"{user.full_name} забанен при входе (стерильный режим)")
        logger.info("Sterile ban: user %d in chat %d", user.id, chat_id)
        return

    # ── Авто-детекция рейда: N входов за T секунд ──
    recent = await db.count_recent_joins(chat_id, RAID_JOIN_WINDOW_SEC)
    if recent >= RAID_JOIN_THRESHOLD:
        await _trigger_raid(chat_id, bot, reason=f"{recent} входов за {RAID_JOIN_WINDOW_SEC} сек")


async def _trigger_raid(chat_id: int, bot: Bot, reason: str = "") -> None:
    """Активировать авто-стерильный режим при обнаружении рейда."""
    mode = await db.get_chat_mode(chat_id)
    if mode["sterile"]:
        return  # уже активен

    until = datetime.utcnow() + timedelta(hours=STERILE_AUTO_HOURS)
    await db.set_sterile(chat_id, True, until=until)
    await apply_sterile(chat_id, bot)
    await db.log_event(chat_id, "RAID_DETECTED", details=reason)

    logger.warning("[RAID] chat_id=%d | %s | sterile until %s", chat_id, reason, until)

    try:
        await bot.send_message(
            chat_id,
            MSG["raid_detected"] +
            f"\n\n⏱  Авто-снятие через <b>{STERILE_AUTO_HOURS} часов</b> "
            f"или по команде защитника.",
            parse_mode="HTML",
            reply_markup=kb_sterile_off(),
        )
    except Exception as e:
        logger.warning("_trigger_raid send_message: %s", e)


# Экспортируем _trigger_raid для использования в spam-хэндлере
__all__ = ["router", "_trigger_raid"]
