# ═══════════════════════════════════════════
#   KILLER RAID — handlers/commands.py
#   Ручные команды управления
# ═══════════════════════════════════════════

import time
import logging
import asyncio
from datetime import datetime, timedelta

from aiogram import Router, Bot, F
from aiogram.types import Message, ChatMemberUpdated
from aiogram.filters import Command

import database as db
from utils import (
    MSG, kb_sterile_off, kb_silence_off,
    get_owner_id, is_privileged,
    apply_sterile, restore_permissions, apply_silence,
    do_unmute, do_kick,
)
from config import STERILE_AUTO_HOURS

logger = logging.getLogger("KillerRaid.Commands")
router = Router()

# ─────────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНАЯ ОТПРАВКА
# ─────────────────────────────────────────────

async def reply(message: Message, text: str, **kwargs) -> None:
    await message.reply(text, parse_mode="HTML", **kwargs)


# ─────────────────────────────────────────────
#  /дать_полномочия_защитника
# ─────────────────────────────────────────────

@router.message(Command("дать_полномочия_защитника"))
async def cmd_give_defender(message: Message, bot: Bot) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await reply(message, MSG["not_in_group"]); return

    chat_id = message.chat.id
    user_id = message.from_user.id

    owner = await get_owner_id(chat_id, bot)
    if user_id != owner:
        await reply(message, MSG["owner_only"]); return

    if not message.reply_to_message:
        await reply(message, MSG["reply_required"]); return

    target = message.reply_to_message.from_user
    if target.is_bot:
        await reply(message, "❌  Нельзя назначить бота защитником."); return

    await db.add_defender(
        chat_id, target.id,
        target.full_name,
        target.username or ""
    )
    await db.log_event(chat_id, "DEFENDER_ADDED", user_id,
                       f"назначен: {target.full_name} ({target.id})")
    logger.info("Defender added: %s (%d) in chat %d", target.full_name, target.id, chat_id)

    await reply(message,
        f"🛡  <b>{target.full_name}</b> "
        f"(<code>@{target.username or target.id}</code>) "
        f"назначен защитником группы."
    )


# ─────────────────────────────────────────────
#  /стерильный_режим
# ─────────────────────────────────────────────

@router.message(Command("стерильный_режим"))
async def cmd_sterile_on(message: Message, bot: Bot) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await reply(message, MSG["not_in_group"]); return

    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_privileged(user_id, chat_id, bot):
        await reply(message, MSG["no_rights"]); return

    mode = await db.get_chat_mode(chat_id)
    if mode["sterile"]:
        await reply(message, MSG["already_sterile"]); return

    await db.set_sterile(chat_id, True)
    await apply_sterile(chat_id, bot)
    await db.log_event(chat_id, "STERILE_ON", user_id, "ручной режим")

    await reply(message, MSG["sterile_on"], reply_markup=kb_sterile_off())


# ─────────────────────────────────────────────
#  /отключить_стерильный
# ─────────────────────────────────────────────

@router.message(Command("отключить_стерильный"))
async def cmd_sterile_off(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_privileged(user_id, chat_id, bot):
        await reply(message, MSG["no_rights"]); return

    mode = await db.get_chat_mode(chat_id)
    if not mode["sterile"]:
        await reply(message, MSG["already_normal"]); return

    await db.set_sterile(chat_id, False)
    await restore_permissions(chat_id, bot)
    await db.log_event(chat_id, "STERILE_OFF", user_id, "ручное снятие")

    await reply(message, MSG["sterile_off"])


# ─────────────────────────────────────────────
#  /статус_защиты
# ─────────────────────────────────────────────

@router.message(Command("статус_защиты"))
async def cmd_status(message: Message, bot: Bot) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await reply(message, MSG["not_in_group"]); return

    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_privileged(user_id, chat_id, bot):
        await reply(message, MSG["no_rights"]); return

    # Замер пинга
    t0 = time.monotonic()
    await bot.get_me()
    latency_ms = round((time.monotonic() - t0) * 1000)

    mode      = await db.get_chat_mode(chat_id)
    defenders = await db.get_defenders(chat_id)
    mutes     = await db.get_active_mutes(chat_id)

    s_icon   = "🔴 АКТИВЕН"  if mode["sterile"] else "🟢 Неактивен"
    sil_icon = "🔇 АКТИВНА"  if mode["silence"] else "🔊 Неактивна"

    auto_str = ""
    if mode["sterile_until"]:
        try:
            until_dt = datetime.fromisoformat(mode["sterile_until"])
            auto_str = f"\n        ⏱  Авто-снятие: <code>{until_dt.strftime('%H:%M  %d.%m.%Y')}</code>"
        except ValueError:
            pass

    text = (
        f"<b>╔══ KILLER RAID — СТАТУС ══╗</b>\n\n"
        f"📡  Пинг:               <code>{latency_ms} мс</code>\n"
        f"🔴  Стерильный режим:   {s_icon}{auto_str}\n"
        f"🔇  Режим тишины:       {sil_icon}\n"
        f"🛡  Защитников:        <code>{len(defenders)}</code>\n"
        f"🔇  В муте:             <code>{len(mutes)}</code>\n"
        f"\n<b>╚══════════════════════════╝</b>"
    )
    await reply(message, text)


# ─────────────────────────────────────────────
#  /снять_мут
# ─────────────────────────────────────────────

@router.message(Command("снять_мут"))
async def cmd_unmute(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_privileged(user_id, chat_id, bot):
        await reply(message, MSG["no_rights"]); return

    target_user = None

    # Способ 1 — ответ на сообщение
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user

    # Способ 2 — @username аргументом
    elif message.text:
        parts = message.text.strip().split()
        if len(parts) >= 2:
            username = parts[1].lstrip("@")
            # Ищем среди активных мутов
            mutes = await db.get_active_mutes(chat_id)
            for m in mutes:
                try:
                    member = await bot.get_chat_member(chat_id, m["user_id"])
                    if (member.user.username or "").lower() == username.lower():
                        target_user = member.user
                        break
                except Exception:
                    pass

    if not target_user:
        await reply(message, MSG["usage_unmute"]); return

    success = await do_unmute(chat_id, target_user.id, bot)
    if success:
        await db.log_event(chat_id, "UNMUTE", user_id,
                           f"снят мут с {target_user.full_name} ({target_user.id})")
        await reply(message, MSG["unmute_done"].format(name=target_user.full_name))
    else:
        await reply(message, MSG["unmute_fail"])


# ─────────────────────────────────────────────
#  /вся_защита
# ─────────────────────────────────────────────

@router.message(Command("вся_защита"))
async def cmd_defenders_list(message: Message, bot: Bot) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await reply(message, MSG["not_in_group"]); return

    chat_id  = message.chat.id
    owner_id = await get_owner_id(chat_id, bot)
    lines    = []

    if owner_id:
        try:
            owner_member = await bot.get_chat_member(chat_id, owner_id)
            lines.append(
                f"👑  <b>{owner_member.user.full_name}</b> — Владелец"
            )
        except Exception:
            lines.append(f"👑  ID <code>{owner_id}</code> — Владелец")

    defenders = await db.get_defenders(chat_id)
    for d in defenders:
        tag = f"@{d['username']}" if d["username"] else f"ID {d['user_id']}"
        lines.append(f"🛡  <b>{d['full_name']}</b> ({tag})")

    if not lines:
        await reply(message, MSG["no_defenders"]); return

    await reply(message,
        "<b>╔══ ЗАЩИТА ГРУППЫ ══╗</b>\n\n"
        + "\n".join(lines)
        + "\n\n<b>╚══════════════════╝</b>"
    )


# ─────────────────────────────────────────────
#  /тишина
# ─────────────────────────────────────────────

@router.message(Command("тишина"))
async def cmd_silence_on(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_privileged(user_id, chat_id, bot):
        await reply(message, MSG["no_rights"]); return

    await db.set_silence(chat_id, True)
    await apply_silence(chat_id, bot)
    await db.log_event(chat_id, "SILENCE_ON", user_id)

    await reply(message, MSG["silence_on"], reply_markup=kb_silence_off())


# ─────────────────────────────────────────────
#  /снять_тишину
# ─────────────────────────────────────────────

@router.message(Command("снять_тишину"))
async def cmd_silence_off(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_privileged(user_id, chat_id, bot):
        await reply(message, MSG["no_rights"]); return

    mode = await db.get_chat_mode(chat_id)
    await db.set_silence(chat_id, False)

    # Восстанавливаем права только если стерильный не активен
    if not mode["sterile"]:
        await restore_permissions(chat_id, bot)

    await db.log_event(chat_id, "SILENCE_OFF", user_id)
    await reply(message, MSG["silence_off"])


# ─────────────────────────────────────────────
#  /выгнать <5-60>
# ─────────────────────────────────────────────

@router.message(Command("выгнать"))
async def cmd_kick_recent(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_privileged(user_id, chat_id, bot):
        await reply(message, MSG["no_rights"]); return

    parts = (message.text or "").strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await reply(message, MSG["usage_kick"]); return

    minutes = int(parts[1])
    if not (5 <= minutes <= 60):
        await reply(message, MSG["usage_kick"]); return

    to_kick = await db.get_joins_since(chat_id, minutes)
    kicked  = 0

    for uid in to_kick:
        success = await do_kick(chat_id, uid, bot)
        if success:
            kicked += 1
        await asyncio.sleep(0.3)

    await db.log_event(chat_id, "KICK_RECENT", user_id,
                       f"период: {minutes} мин, кикнуто: {kicked}")

    if kicked:
        await reply(message, MSG["kick_done"].format(n=kicked))
    else:
        await reply(message, MSG["kick_none"])
