# ═══════════════════════════════════════════════════════════
#   KILLER RAID — utils/permissions.py
#   Проверка прав пользователя (владелец / защитник)
# ═══════════════════════════════════════════════════════════

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMemberOwner

import database as db

logger = logging.getLogger("KillerRaid.Permissions")


async def get_owner_id(chat_id: int, bot: Bot) -> Optional[int]:
    """Вернуть user_id владельца чата или None."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
        for a in admins:
            if isinstance(a, ChatMemberOwner):
                return a.user.id
    except TelegramAPIError as e:
        logger.warning("get_owner_id error: %s", e)
    return None


async def is_privileged(user_id: int, chat_id: int, bot: Bot) -> bool:
    """True если пользователь — владелец ИЛИ назначенный защитник."""
    owner = await get_owner_id(chat_id, bot)
    if owner and user_id == owner:
        return True
    return await db.is_defender(chat_id, user_id)


async def is_owner(user_id: int, chat_id: int, bot: Bot) -> bool:
    """True только если пользователь — владелец чата."""
    owner = await get_owner_id(chat_id, bot)
    return owner is not None and user_id == owner
