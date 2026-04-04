# ═══════════════════════════════════════════════════════════
#   KILLER RAID — utils/chat_control.py
#   Управление правами чата и выдача мутов
# ═══════════════════════════════════════════════════════════

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatPermissions

import database as db
from config import SPAM_MUTE_MINUTES

logger = logging.getLogger("KillerRaid.ChatControl")

# ── Права для стерильного режима (всё запрещено) ──
_STERILE_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)

# ── Базовые права (стабильный режим) ──
_DEFAULT_PERMS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_change_info=False,
    can_invite_users=True,
    can_pin_messages=False,
)

# ── Права при тишине (только текст запрещён) ──
_SILENCE_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=True,
    can_pin_messages=False,
)


async def enable_sterile(chat_id: int, bot: Bot, until: datetime | None = None) -> bool:
    """Включить стерильный режим — обнулить права чата."""
    try:
        await bot.set_chat_permissions(chat_id, _STERILE_PERMS)
        await db.set_sterile(chat_id, True, until)
        logger.info("Стерильный режим ВКЛЮЧЁН: chat_id=%d", chat_id)
        return True
    except TelegramAPIError as e:
        logger.error("enable_sterile: %s", e)
        return False


async def disable_sterile(chat_id: int, bot: Bot) -> bool:
    """Выключить стерильный режим — восстановить базовые права."""
    try:
        await bot.set_chat_permissions(chat_id, _DEFAULT_PERMS)
        await db.set_sterile(chat_id, False, None)
        logger.info("Стерильный режим ВЫКЛЮЧЕН: chat_id=%d", chat_id)
        return True
    except TelegramAPIError as e:
        logger.error("disable_sterile: %s", e)
        return False


async def enable_silence(chat_id: int, bot: Bot) -> bool:
    """Заморозить чат — запретить отправку сообщений."""
    try:
        await bot.set_chat_permissions(chat_id, _SILENCE_PERMS)
        await db.set_silence(chat_id, True)
        logger.info("Тишина ВКЛЮЧЕНА: chat_id=%d", chat_id)
        return True
    except TelegramAPIError as e:
        logger.error("enable_silence: %s", e)
        return False


async def disable_silence(chat_id: int, bot: Bot) -> bool:
    """Снять режим тишины."""
    state = await db.get_chat_state(chat_id)
    # Если стерильный режим ещё активен — не восстанавливаем полные права
    if state.get("sterile_mode"):
        await db.set_silence(chat_id, False)
        return True
    try:
        await bot.set_chat_permissions(chat_id, _DEFAULT_PERMS)
        await db.set_silence(chat_id, False)
        logger.info("Тишина ВЫКЛЮЧЕНА: chat_id=%d", chat_id)
        return True
    except TelegramAPIError as e:
        logger.error("disable_silence: %s", e)
        return False


async def mute_user(
    chat_id: int,
    user_id: int,
    bot: Bot,
    minutes: int = SPAM_MUTE_MINUTES,
    reason: str = "",
) -> bool:
    """Выдать мут пользователю на N минут."""
    until = datetime.utcnow() + timedelta(minutes=minutes)
    try:
        await bot.restrict_chat_member(
            chat_id,
            user_id,
            ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        await db.add_mute(chat_id, user_id, until, reason)
        logger.info("Мут: user_id=%d chat_id=%d на %d мин.", user_id, chat_id, minutes)
        return True
    except TelegramAPIError as e:
        logger.warning("mute_user: %s", e)
        return False


async def unmute_user(chat_id: int, user_id: int, bot: Bot) -> bool:
    """Снять мут с пользователя."""
    try:
        await bot.restrict_chat_member(
            chat_id,
            user_id,
            ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        await db.remove_mute(chat_id, user_id)
        logger.info("Мут снят: user_id=%d chat_id=%d", user_id, chat_id)
        return True
    except TelegramAPIError as e:
        logger.warning("unmute_user: %s", e)
        return False


async def ban_user(chat_id: int, user_id: int, bot: Bot) -> bool:
    """Забанить пользователя."""
    try:
        await bot.ban_chat_member(chat_id, user_id)
        return True
    except TelegramAPIError as e:
        logger.warning("ban_user uid=%d: %s", user_id, e)
        return False


async def kick_user(chat_id: int, user_id: int, bot: Bot) -> bool:
    """Кикнуть (бан + разбан) пользователя."""
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id)
        return True
    except TelegramAPIError as e:
        logger.warning("kick_user uid=%d: %s", user_id, e)
        return False
