# ═══════════════════════════════════════════════════════════
#   KILLER RAID — handlers/auto_guard.py
#   Автоматическая защита:
#     • Детекция рейда (массовый вход / массовый спам)
#     • Бан новых участников в стерильном режиме
#     • Мут за спам стикерами / гифами
#     • Авто-снятие стерильного режима через N часов
# ═══════════════════════════════════════════════════════════

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Router
from aiogram.filters import ChatMemberUpdatedFilter, IS_NOT_MEMBER, MEMBER, RESTRICTED
from aiogram.types import (
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.filters import Filter
from aiogram import F

import database as db
from config import (
    RAID_JOIN_COUNT,
    RAID_JOIN_WINDOW,
    RAID_SPAM_THRESHOLD,
    RAID_SPAM_USERS,
    RAID_SPAM_WINDOW,
    SPAM_MUTE_MINUTES,
    SPAM_MUTE_THRESHOLD,
    SPAM_MUTE_WINDOW,
    STERILE_AUTO_HOURS,
)
from utils.chat_control import ban_user, enable_sterile, disable_sterile, mute_user
from utils.permissions import is_privileged

logger = logging.getLogger("KillerRaid.AutoGuard")
router = Router()

# ─────────────────────────────────────────────
#  Хранилище задач авто-снятия (chat_id → asyncio.Task)
# ─────────────────────────────────────────────
_sterile_tasks: dict[int, asyncio.Task] = {}


def _sterile_off_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🟢  Отключить стерильный режим", callback_data="sterile_off")
    ]])


# ─────────────────────────────────────────────
#  Авто-снятие стерильного режима через N часов
# ─────────────────────────────────────────────

async def _schedule_sterile_off(chat_id: int, bot: Bot, hours: int) -> None:
    """Запускает корутину авто-снятия стерильного режима."""
    # Отменяем предыдущую задачу для этого чата если есть
    old = _sterile_tasks.get(chat_id)
    if old and not old.done():
        old.cancel()

    async def _job():
        await asyncio.sleep(hours * 3600)
        state = await db.get_chat_state(chat_id)
        if state["sterile_mode"]:
            await disable_sterile(chat_id, bot)
            await db.log_event(chat_id, "sterile_off_auto",
                               details=f"авто-снятие после {hours}ч")
            try:
                await bot.send_message(
                    chat_id,
                    f"🟢  Стерильный режим снят автоматически (истекло {hours} ч).",
                )
            except Exception:
                pass
        _sterile_tasks.pop(chat_id, None)

    task = asyncio.create_task(_job())
    _sterile_tasks[chat_id] = task


def cancel_sterile_task(chat_id: int) -> None:
    """Отменить задачу авто-снятия (вызывается при ручном отключении)."""
    task = _sterile_tasks.pop(chat_id, None)
    if task and not task.done():
        task.cancel()


# ─────────────────────────────────────────────
#  Активация авто-рейд ответа
# ─────────────────────────────────────────────

async def trigger_auto_raid(chat_id: int, bot: Bot, reason: str) -> None:
    """Включить стерильный режим из-за обнаруженного рейда."""
    state = await db.get_chat_state(chat_id)
    if state["sterile_mode"]:
        return  # уже активен

    until = datetime.utcnow() + timedelta(hours=STERILE_AUTO_HOURS)
    await enable_sterile(chat_id, bot, until=until)
    await db.log_event(chat_id, "raid_detected", details=reason)

    logger.warning("[РЕЙД] chat_id=%d причина: %s — стерильный до %s",
                   chat_id, reason, until.isoformat())

    try:
        await bot.send_message(
            chat_id,
            f"⚠️  <b>ОБНАРУЖЕН РЕЙД.</b> Автоматический переход в стерильный режим.\n"
            f"Причина: {reason}\n\n"
            f"⏱  Авто-снятие через <b>{STERILE_AUTO_HOURS} ч</b> или по команде защитника.",
            parse_mode="HTML",
            reply_markup=_sterile_off_keyboard(),
        )
    except Exception as e:
        logger.warning("trigger_auto_raid send_message: %s", e)

    await _schedule_sterile_off(chat_id, bot, STERILE_AUTO_HOURS)


# ─────────────────────────────────────────────
#  Обработчик: новый участник вошёл в чат
# ─────────────────────────────────────────────

@router.chat_member(
    ChatMemberUpdatedFilter(member_status_changed=(IS_NOT_MEMBER >> (MEMBER | RESTRICTED)))
)
async def on_new_member(event: ChatMemberUpdated, bot: Bot) -> None:
    chat_id = event.chat.id
    user    = event.new_chat_member.user

    if user.is_bot:
        return

    # Записываем вход в БД
    await db.log_join(chat_id, user.id)
    # Чистим старые записи (> 60 мин)
    await db.purge_old_joins(chat_id, older_than_minutes=60)

    state = await db.get_chat_state(chat_id)

    # ── Стерильный режим: мгновенный бан ──
    if state["sterile_mode"]:
        await ban_user(chat_id, user.id, bot)
        await db.log_event(chat_id, "ban", target_id=user.id,
                           details="стерильный режим — авто-бан при входе")
        logger.info("Стерильный бан: user_id=%d chat_id=%d", user.id, chat_id)
        return

    # ── Детекция рейда: N+ входов за окно ──
    count = await db.count_joins_in_window(chat_id, RAID_JOIN_WINDOW)
    if count >= RAID_JOIN_COUNT:
        await trigger_auto_raid(
            chat_id, bot,
            reason=f"{count} входов за {RAID_JOIN_WINDOW} сек."
        )


# ─────────────────────────────────────────────
#  Обработчик: спам стикерами / гифами
# ─────────────────────────────────────────────

@router.message(F.chat.type.in_({"group", "supergroup"}) & (F.sticker | F.animation))
async def on_spam_content(msg: Message, bot: Bot) -> None:
    if not msg.from_user or msg.from_user.is_bot:
        return

    chat_id = msg.chat.id
    user_id = msg.from_user.id

    # Защитники/владелец неприкосновенны
    if await is_privileged(user_id, chat_id, bot):
        return

    # Записываем спам
    await db.log_spam(chat_id, user_id)
    await db.purge_old_spam(chat_id, older_than_minutes=5)

    # ── Индивидуальный мут: SPAM_MUTE_THRESHOLD+ за окно ──
    count = await db.count_spam_in_window(chat_id, user_id, SPAM_MUTE_WINDOW)
    if count >= SPAM_MUTE_THRESHOLD:
        await mute_user(chat_id, user_id, bot,
                        minutes=SPAM_MUTE_MINUTES,
                        reason="спам стикерами/гифами")
        await db.log_event(chat_id, "mute", target_id=user_id,
                           details=f"спам {count} раз за {SPAM_MUTE_WINDOW}с → {SPAM_MUTE_MINUTES}мин")
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            await bot.send_message(
                chat_id,
                f"🔇  Участник получил мут на <b>{SPAM_MUTE_MINUTES} минут</b>.\n"
                f"Причина: спам стикерами / гифами ({count} сообщений за {SPAM_MUTE_WINDOW} сек).",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    # ── Детекция рейда: N+ спамеров одновременно ──
    state = await db.get_chat_state(chat_id)
    if not state["sterile_mode"]:
        spammers = await db.count_spammers_in_window(
            chat_id, RAID_SPAM_THRESHOLD, RAID_SPAM_WINDOW
        )
        if spammers >= RAID_SPAM_USERS:
            await trigger_auto_raid(
                chat_id, bot,
                reason=f"{spammers} пользователей спамят одновременно"
            )
