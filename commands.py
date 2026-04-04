# ═══════════════════════════════════════════════════════════
#   KILLER RAID — handlers/commands.py
#   Все ручные команды бота
# ═══════════════════════════════════════════════════════════

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import database as db
from config import SPAM_MUTE_MINUTES
from utils.chat_control import (
    ban_user,
    disable_silence,
    disable_sterile,
    enable_silence,
    enable_sterile,
    kick_user,
    mute_user,
    unmute_user,
)
from utils.permissions import get_owner_id, is_owner, is_privileged

logger = logging.getLogger("KillerRaid.Commands")
router = Router()

# ─────────────────────────────────────────────
#  Клавиатуры
# ─────────────────────────────────────────────

def kb_sterile_off() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🟢  Отключить стерильный режим", callback_data="sterile_off")
    ]])


def kb_silence_off() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔊  Снять тишину", callback_data="silence_off")
    ]])


# ─────────────────────────────────────────────
#  Вспомогательная функция ответа
# ─────────────────────────────────────────────

async def reply(msg: Message, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    await msg.answer(text, parse_mode="HTML", reply_markup=keyboard)


def _only_group(msg: Message) -> bool:
    return msg.chat.type in ("group", "supergroup")


# ─────────────────────────────────────────────
#  /дать_полномочия_защитника
# ─────────────────────────────────────────────

@router.message(Command("дать_полномочия_защитника"))
async def cmd_give_defender(msg: Message, bot: Bot) -> None:
    if not _only_group(msg):
        await reply(msg, "❌  Команда работает только в группах."); return

    if not await is_owner(msg.from_user.id, msg.chat.id, bot):
        await reply(msg, "⛔  Только создатель чата может назначать защитников."); return

    if not msg.reply_to_message:
        await reply(msg, "❌  Ответьте этой командой на сообщение нужного участника."); return

    target = msg.reply_to_message.from_user
    if target.is_bot:
        await reply(msg, "❌  Нельзя назначить бота защитником."); return

    await db.add_defender(msg.chat.id, target.id)
    await db.log_event(msg.chat.id, "defender_add",
                       actor_id=msg.from_user.id, target_id=target.id,
                       details=f"@{target.username or target.id}")
    await reply(msg,
        f"🛡  <b>{target.full_name}</b> "
        f"(<code>@{target.username or target.id}</code>) "
        f"назначен защитником группы."
    )


# ─────────────────────────────────────────────
#  /стерильный_режим
# ─────────────────────────────────────────────

@router.message(Command("стерильный_режим"))
async def cmd_sterile_on(msg: Message, bot: Bot) -> None:
    if not _only_group(msg):
        await reply(msg, "❌  Команда работает только в группах."); return

    if not await is_privileged(msg.from_user.id, msg.chat.id, bot):
        await reply(msg, "⛔  Доступ запрещён. Недостаточно полномочий."); return

    state = await db.get_chat_state(msg.chat.id)
    if state["sterile_mode"]:
        await reply(msg, "ℹ️  Стерильный режим уже активен."); return

    await enable_sterile(msg.chat.id, bot)
    await db.log_event(msg.chat.id, "sterile_on", actor_id=msg.from_user.id)
    await reply(msg,
        "🔴  <b>СТЕРИЛЬНЫЙ РЕЖИМ АКТИВИРОВАН.</b>\n"
        "Все входящие — бан. Спам — мут.",
        keyboard=kb_sterile_off()
    )


# ─────────────────────────────────────────────
#  /отключить_стерильный
# ─────────────────────────────────────────────

@router.message(Command("отключить_стерильный"))
async def cmd_sterile_off(msg: Message, bot: Bot) -> None:
    if not await is_privileged(msg.from_user.id, msg.chat.id, bot):
        await reply(msg, "⛔  Доступ запрещён. Недостаточно полномочий."); return

    state = await db.get_chat_state(msg.chat.id)
    if not state["sterile_mode"]:
        await reply(msg, "ℹ️  Стерильный режим уже неактивен."); return

    await disable_sterile(msg.chat.id, bot)
    await db.log_event(msg.chat.id, "sterile_off", actor_id=msg.from_user.id)
    await reply(msg, "🟢  Стерильный режим снят. Группа в стабильном состоянии.")


# ─────────────────────────────────────────────
#  /статус_защиты
# ─────────────────────────────────────────────

@router.message(Command("статус_защиты"))
async def cmd_status(msg: Message, bot: Bot) -> None:
    if not _only_group(msg):
        await reply(msg, "❌  Команда работает только в группах."); return

    if not await is_privileged(msg.from_user.id, msg.chat.id, bot):
        await reply(msg, "⛔  Доступ запрещён. Недостаточно полномочий."); return

    # Пинг
    t0 = time.monotonic()
    await bot.get_me()
    ping = round((time.monotonic() - t0) * 1000)

    state      = await db.get_chat_state(msg.chat.id)
    def_count  = len(await db.get_defenders(msg.chat.id))
    mute_count = len(await db.get_active_mutes(msg.chat.id))

    s_icon  = "🔴 АКТИВЕН" if state["sterile_mode"] else "🟢 Неактивен"
    sl_icon = "🔇 АКТИВНА" if state["silence_mode"] else "🔊 Неактивна"

    auto_str = ""
    if state.get("sterile_until"):
        auto_str = f"\n        ⏱  Авто-снятие: <code>{state['sterile_until'][:16]}</code>"

    await reply(msg,
        f"<b>╔══ KILLER RAID — СТАТУС ══╗</b>\n\n"
        f"📡  Пинг:               <code>{ping} мс</code>\n"
        f"🔴  Стерильный режим:   {s_icon}{auto_str}\n"
        f"🔇  Режим тишины:       {sl_icon}\n"
        f"🛡  Защитников:        <code>{def_count}</code>\n"
        f"🔇  В муте:             <code>{mute_count}</code>\n"
        f"\n<b>╚══════════════════════════╝</b>"
    )


# ─────────────────────────────────────────────
#  /снять_мут
# ─────────────────────────────────────────────

@router.message(Command("снять_мут"))
async def cmd_unmute(msg: Message, bot: Bot) -> None:
    if not await is_privileged(msg.from_user.id, msg.chat.id, bot):
        await reply(msg, "⛔  Доступ запрещён. Недостаточно полномочий."); return

    target_id: int | None   = None
    target_name: str        = ""

    # Способ 1: ответ на сообщение
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u           = msg.reply_to_message.from_user
        target_id   = u.id
        target_name = u.full_name

    # Способ 2: аргумент @username
    elif msg.text:
        parts = msg.text.split()
        if len(parts) >= 2:
            username = parts[1].lstrip("@").lower()
            mutes = await db.get_active_mutes(msg.chat.id)
            for m in mutes:
                try:
                    member = await bot.get_chat_member(msg.chat.id, m["user_id"])
                    u = member.user
                    if u.username and u.username.lower() == username:
                        target_id   = u.id
                        target_name = u.full_name
                        break
                except Exception:
                    pass

    if not target_id:
        await reply(msg, "❌  Использование: /снять_мут @username  (или ответом на сообщение).")
        return

    ok = await unmute_user(msg.chat.id, target_id, bot)
    if ok:
        await db.log_event(msg.chat.id, "unmute",
                           actor_id=msg.from_user.id, target_id=target_id)
        await reply(msg, f"✅  Мут снят с <b>{target_name or target_id}</b>.")
    else:
        await reply(msg, "❌  Не удалось снять мут. Пользователь не найден или мут не выдан ботом.")


# ─────────────────────────────────────────────
#  /вся_защита
# ─────────────────────────────────────────────

@router.message(Command("вся_защита"))
async def cmd_defenders_list(msg: Message, bot: Bot) -> None:
    if not _only_group(msg):
        await reply(msg, "❌  Команда работает только в группах."); return

    owner_id  = await get_owner_id(msg.chat.id, bot)
    def_ids   = await db.get_defenders(msg.chat.id)
    lines: list[str] = []

    if owner_id:
        try:
            m = await bot.get_chat_member(msg.chat.id, owner_id)
            lines.append(f"👑  <b>{m.user.full_name}</b> — Владелец")
        except Exception:
            lines.append(f"👑  ID <code>{owner_id}</code> — Владелец")

    for uid in def_ids:
        try:
            m   = await bot.get_chat_member(msg.chat.id, uid)
            tag = f"@{m.user.username}" if m.user.username else f"ID {uid}"
            lines.append(f"🛡  <b>{m.user.full_name}</b> ({tag})")
        except Exception:
            lines.append(f"🛡  ID <code>{uid}</code>")

    if not lines:
        await reply(msg, "ℹ️  Защитников пока не назначено."); return

    await reply(msg,
        "<b>╔══ ЗАЩИТА ГРУППЫ ══╗</b>\n\n"
        + "\n".join(lines)
        + "\n\n<b>╚══════════════════╝</b>"
    )


# ─────────────────────────────────────────────
#  /тишина
# ─────────────────────────────────────────────

@router.message(Command("тишина"))
async def cmd_silence_on(msg: Message, bot: Bot) -> None:
    if not await is_privileged(msg.from_user.id, msg.chat.id, bot):
        await reply(msg, "⛔  Доступ запрещён. Недостаточно полномочий."); return

    state = await db.get_chat_state(msg.chat.id)
    if state["silence_mode"]:
        await reply(msg, "ℹ️  Режим тишины уже активен."); return

    await enable_silence(msg.chat.id, bot)
    await db.log_event(msg.chat.id, "silence_on", actor_id=msg.from_user.id)
    await reply(msg,
        "🔇  Чат заморожен. Право слова — только у защиты.",
        keyboard=kb_silence_off()
    )


# ─────────────────────────────────────────────
#  /снять_тишину
# ─────────────────────────────────────────────

@router.message(Command("снять_тишину"))
async def cmd_silence_off(msg: Message, bot: Bot) -> None:
    if not await is_privileged(msg.from_user.id, msg.chat.id, bot):
        await reply(msg, "⛔  Доступ запрещён. Недостаточно полномочий."); return

    state = await db.get_chat_state(msg.chat.id)
    if not state["silence_mode"]:
        await reply(msg, "ℹ️  Режим тишины уже неактивен."); return

    await disable_silence(msg.chat.id, bot)
    await db.log_event(msg.chat.id, "silence_off", actor_id=msg.from_user.id)
    await reply(msg, "🔊  Ограничение снято. Чат открыт.")


# ─────────────────────────────────────────────
#  /выгнать <5-60>
# ─────────────────────────────────────────────

@router.message(Command("выгнать"))
async def cmd_kick_recent(msg: Message, bot: Bot) -> None:
    if not await is_privileged(msg.from_user.id, msg.chat.id, bot):
        await reply(msg, "⛔  Доступ запрещён. Недостаточно полномочий."); return

    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await reply(msg, "❌  Использование: /выгнать <5-60>"); return

    minutes = int(parts[1])
    if not (5 <= minutes <= 60):
        await reply(msg, "❌  Допустимый диапазон: от 5 до 60 минут."); return

    since    = datetime.utcnow() - timedelta(minutes=minutes)
    user_ids = await db.get_joins_since(msg.chat.id, since)

    if not user_ids:
        await reply(msg, "ℹ️  За указанный период новых участников не найдено."); return

    kicked = 0
    for uid in user_ids:
        if await kick_user(msg.chat.id, uid, bot):
            kicked += 1
            await db.log_event(msg.chat.id, "kick",
                               actor_id=msg.from_user.id, target_id=uid,
                               details=f"выгнать {minutes}мин")
        await asyncio.sleep(0.3)   # антифлуд

    await reply(msg, f"🧹  Очистка завершена. Удалено участников: <b>{kicked}</b>.")


# ─────────────────────────────────────────────
#  CALLBACK — инлайн кнопки
# ─────────────────────────────────────────────

@router.callback_query(lambda c: c.data in ("sterile_off", "silence_off"))
async def on_inline_button(call: CallbackQuery, bot: Bot) -> None:
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if not await is_privileged(user_id, chat_id, bot):
        await call.answer("⛔  Недостаточно полномочий.", show_alert=True)
        return

    await call.answer()

    if call.data == "sterile_off":
        state = await db.get_chat_state(chat_id)
        if not state["sterile_mode"]:
            await call.answer("Режим уже неактивен.", show_alert=True); return
        await disable_sterile(chat_id, bot)
        await db.log_event(chat_id, "sterile_off", actor_id=user_id, details="via button")
        await call.message.edit_text("🟢  Стерильный режим снят. Группа в стабильном состоянии.")

    elif call.data == "silence_off":
        state = await db.get_chat_state(chat_id)
        if not state["silence_mode"]:
            await call.answer("Тишина уже снята.", show_alert=True); return
        await disable_silence(chat_id, bot)
        await db.log_event(chat_id, "silence_off", actor_id=user_id, details="via button")
        await call.message.edit_text("🔊  Ограничение снято. Чат открыт.")
