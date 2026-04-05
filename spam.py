# ═══════════════════════════════════════════
#   KILLER RAID — handlers/spam.py
#   Детекция спама стикерами / гифами
# ═══════════════════════════════════════════

import logging

from aiogram import Router, Bot, F
from aiogram.types import Message

import database as db
from utils import is_privileged, do_mute
from config import (
    SPAM_USER_THRESHOLD, SPAM_MASS_THRESHOLD,
    MUTE_MINUTES,
)
from handlers.members import _trigger_raid

logger = logging.getLogger("KillerRaid.Spam")
router = Router()


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.content_type.in_({"sticker", "animation"}),
)
async def on_spam_content(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    user    = message.from_user

    if not user or user.is_bot:
        return

    # Защита не ограничивается
    if await is_privileged(user.id, chat_id, bot):
        return

    # Стерильный режим — просто удаляем сообщение, ничего не считаем
    mode = await db.get_chat_mode(chat_id)
    if mode["sterile"]:
        try:
            await message.delete()
        except Exception:
            pass
        return

    # Записываем факт спама, получаем счётчик за окно
    count = await db.record_spam(chat_id, user.id)

    # ── Индивидуальный порог: мут ──
    if count >= SPAM_USER_THRESHOLD:
        await do_mute(chat_id, user.id, bot, MUTE_MINUTES, reason="спам стикерами/гифами")
        try:
            await message.delete()
        except Exception:
            pass

        await db.log_event(
            chat_id, "MUTE_SPAM", user.id,
            f"{user.full_name} — мут {MUTE_MINUTES} мин ({count} стикеров/гиф за окно)"
        )
        logger.info("Muted spammer: user %d in chat %d (%d items)", user.id, chat_id, count)

        try:
            await bot.send_message(
                chat_id,
                f"🔇  <b>{user.full_name}</b> получил мут на <b>{MUTE_MINUTES} минут</b>.\n"
                f"Причина: спам стикерами / гифами.",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # ── Массовый спам: проверяем порог для рейда ──
    spammers = await db.count_spammers(chat_id, min_count=2)
    if spammers >= SPAM_MASS_THRESHOLD:
        await _trigger_raid(
            chat_id, bot,
            reason=f"массовый спам: {spammers} пользователей"
        )
