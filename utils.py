# ═══════════════════════════════════════════
#   KILLER RAID — utils.py
#   Общие утилиты: тексты, клавиатуры, хелперы
# ═══════════════════════════════════════════

from aiogram import Bot
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ChatPermissions, Message,
)
from aiogram.exceptions import TelegramAPIError
import logging

from config import MUTE_MINUTES
import database as db

logger = logging.getLogger("KillerRaid.Utils")

# ─────────────────────────────────────────────
#  ХОЛОДНЫЕ ТЕКСТЫ
# ─────────────────────────────────────────────

MSG = {
    "no_rights":        "⛔  Доступ запрещён. Недостаточно полномочий.",
    "owner_only":       "⛔  Только создатель чата может выполнить это действие.",
    "not_in_group":     "❌  Команда работает только в группах.",
    "sterile_on":       "🔴  СТЕРИЛЬНЫЙ РЕЖИМ АКТИВИРОВАН.\nВсе входящие — бан. Спам — мут.",
    "sterile_off":      "🟢  Стерильный режим снят. Группа в стабильном состоянии.",
    "already_sterile":  "ℹ️  Стерильный режим уже активен.",
    "already_normal":   "ℹ️  Режим уже стабильный.",
    "silence_on":       "🔇  Чат заморожен. Право слова — только у защиты.",
    "silence_off":      "🔊  Ограничение снято. Чат открыт.",
    "unmute_done":      "✅  Мут снят с <b>{name}</b>.",
    "unmute_fail":      "❌  Мут не найден или уже снят.",
    "kick_done":        "🧹  Очистка завершена. Удалено участников: <b>{n}</b>",
    "kick_none":        "ℹ️  За указанный период новых участников не найдено.",
    "usage_kick":       "❌  Использование: /выгнать &lt;5–60&gt;",
    "usage_unmute":     "❌  Использование: /снять_мут @username  или ответом на сообщение.",
    "raid_detected":    (
        "⚠️  <b>ОБНАРУЖЕН РЕЙД.</b>\n"
        "Автоматический переход в стерильный режим.\n"
        "Очистка запущена."
    ),
    "no_defenders":     "ℹ️  Защитников пока не назначено.",
    "reply_required":   "❌  Ответьте этой командой на сообщение нужного участника.",
}

# ─────────────────────────────────────────────
#  КЛАВИАТУРЫ
# ─────────────────────────────────────────────

def kb_sterile_off() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🟢  Отключить стерильный режим",
            callback_data="sterile_off"
        )
    ]])


def kb_silence_off() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🔊  Снять тишину",
            callback_data="silence_off"
        )
    ]])


# ─────────────────────────────────────────────
#  ПРОВЕРКА ПРАВ
# ─────────────────────────────────────────────

async def get_owner_id(chat_id: int, bot: Bot) -> int | None:
    """Возвращает user_id владельца (Creator) чата через API."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
        for a in admins:
            if a.status == "creator":
                return a.user.id
    except TelegramAPIError as e:
        logger.warning("get_owner_id error: %s", e)
    return None


async def is_privileged(user_id: int, chat_id: int, bot: Bot) -> bool:
    """True если пользователь — владелец или защитник."""
    from config import CREATOR_ID
    if CREATOR_ID and user_id == CREATOR_ID:
        return True
    owner = await get_owner_id(chat_id, bot)
    if owner and user_id == owner:
        return True
    return await db.is_defender(chat_id, user_id)


# ─────────────────────────────────────────────
#  ОПЕРАЦИИ С ПРАВАМИ ЧАТА
# ─────────────────────────────────────────────

PERMS_LOCKED = ChatPermissions(
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

PERMS_DEFAULT = ChatPermissions(
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

PERMS_SILENT = ChatPermissions(
    can_send_messages=False,
)


async def apply_sterile(chat_id: int, bot: Bot) -> None:
    """Заблокировать все права чата."""
    try:
        await bot.set_chat_permissions(chat_id, PERMS_LOCKED)
    except TelegramAPIError as e:
        logger.warning("apply_sterile [%d]: %s", chat_id, e)


async def restore_permissions(chat_id: int, bot: Bot) -> None:
    """Восстановить стандартные права чата."""
    try:
        await bot.set_chat_permissions(chat_id, PERMS_DEFAULT)
    except TelegramAPIError as e:
        logger.warning("restore_permissions [%d]: %s", chat_id, e)


async def apply_silence(chat_id: int, bot: Bot) -> None:
    try:
        await bot.set_chat_permissions(chat_id, PERMS_SILENT)
    except TelegramAPIError as e:
        logger.warning("apply_silence [%d]: %s", chat_id, e)


async def do_mute(chat_id: int, user_id: int, bot: Bot,
                   minutes: int = MUTE_MINUTES,
                   reason: str = "спам") -> None:
    """Выдать мут и записать в БД."""
    until = await db.add_mute(chat_id, user_id, minutes, reason)
    try:
        await bot.restrict_chat_member(
            chat_id, user_id,
            ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except TelegramAPIError as e:
        logger.warning("do_mute [%d/%d]: %s", chat_id, user_id, e)


async def do_unmute(chat_id: int, user_id: int, bot: Bot) -> bool:
    """Снять мут и восстановить права в БД."""
    lifted = await db.lift_mute(chat_id, user_id)
    if not lifted:
        return False
    try:
        await bot.restrict_chat_member(chat_id, user_id, PERMS_DEFAULT)
    except TelegramAPIError as e:
        logger.warning("do_unmute [%d/%d]: %s", chat_id, user_id, e)
    return True


async def do_ban(chat_id: int, user_id: int, bot: Bot) -> None:
    try:
        await bot.ban_chat_member(chat_id, user_id)
    except TelegramAPIError as e:
        logger.warning("do_ban [%d/%d]: %s", chat_id, user_id, e)


async def do_kick(chat_id: int, user_id: int, bot: Bot) -> bool:
    """Кик = бан + немедленный анбан."""
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id)
        return True
    except TelegramAPIError as e:
        logger.warning("do_kick [%d/%d]: %s", chat_id, user_id, e)
        return False
