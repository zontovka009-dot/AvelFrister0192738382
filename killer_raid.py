# ═══════════════════════════════════════════════════════════
#   KILLER RAID — Telegram Anti-Raid Bot
#   Готов к деплою на BotHost / любой VPS
# ═══════════════════════════════════════════════════════════

import logging
import time
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

# ─────────────────────────────────────────────
#  КОНФИГУРАЦИЯ
# ─────────────────────────────────────────────

BOT_TOKEN = "8387931402:AAHfIwGUmhML2eTxUtdvCDXGIJFej7gBwpQ"

# ─────────────────────────────────────────────
#  ЛОГИРОВАНИЕ
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("killer_raid.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("KillerRaid")

# ─────────────────────────────────────────────
#  СОСТОЯНИЕ (хранится в памяти)
# ─────────────────────────────────────────────

sterile_mode: dict[int, bool]       = {}          # { chat_id: bool }
silence_mode: dict[int, bool]       = {}          # { chat_id: bool }
sterile_auto_until: dict[int, datetime] = {}      # { chat_id: datetime }
join_timestamps: dict               = defaultdict(list)          # { chat_id: [(uid, ts), ...] }
spam_timestamps: dict               = defaultdict(lambda: defaultdict(list))  # { chat_id: { uid: [ts] } }
muted_users: dict                   = defaultdict(dict)          # { chat_id: { uid: datetime } }
defenders: dict[int, set]           = defaultdict(set)           # { chat_id: set(uid) }

# ─────────────────────────────────────────────
#  ТЕКСТОВЫЕ ОТВЕТЫ (холодный стиль)
# ─────────────────────────────────────────────

MSG = {
    "no_rights":       "⛔  Доступ запрещён. Недостаточно полномочий.",
    "owner_only":      "⛔  Только создатель чата может выполнить это действие.",
    "not_in_group":    "❌  Команда работает только в группах.",
    "sterile_on":      "🔴  СТЕРИЛЬНЫЙ РЕЖИМ АКТИВИРОВАН.\nВсе входящие — бан. Спам — мут.",
    "sterile_off":     "🟢  Стерильный режим снят. Группа в стабильном состоянии.",
    "already_sterile": "ℹ️  Стерильный режим уже активен.",
    "already_normal":  "ℹ️  Режим уже стабильный.",
    "silence_on":      "🔇  Чат заморожен. Право слова — только у защиты.",
    "silence_off":     "🔊  Ограничение снято. Чат открыт.",
    "unmute_done":     "✅  Мут снят.",
    "unmute_fail":     "❌  Пользователь не найден или мут не выдан ботом.",
    "kick_done":       "🧹  Очистка завершена. Удалено участников: {n}",
    "kick_none":       "ℹ️  За указанный период новых участников не найдено.",
    "usage_kick":      "❌  Использование: /выгнать <5-60>",
    "usage_unmute":    "❌  Использование: /снять_мут @username  (или ответом на сообщение)",
    "raid_detected":   "⚠️  ОБНАРУЖЕН РЕЙД. Автоматический переход в стерильный режим.\nОчистка запущена.",
}

# ─────────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────

async def get_owner_id(chat_id: int, bot) -> int | None:
    """Возвращает user_id владельца (Creator) чата."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
        for a in admins:
            if a.status == ChatMemberStatus.OWNER:
                return a.user.id
    except TelegramError:
        pass
    return None


async def is_privileged(user_id: int, chat_id: int, bot) -> bool:
    """True если пользователь — владелец или назначенный защитник."""
    owner = await get_owner_id(chat_id, bot)
    return user_id == owner or user_id in defenders.get(chat_id, set())


async def send(update: Update, text: str, keyboard=None):
    kwargs = {"text": text, "parse_mode": "HTML"}
    if keyboard:
        kwargs["reply_markup"] = keyboard
    await update.effective_message.reply_text(**kwargs)


def kb_sterile_off():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢  Отключить стерильный режим", callback_data="sterile_off")
    ]])


def kb_silence_off():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔊  Снять тишину", callback_data="silence_off")
    ]])


async def enable_sterile(chat_id: int, bot):
    """Включить стерильный режим — обнулить права чата."""
    sterile_mode[chat_id] = True
    perms = ChatPermissions(
        can_send_messages=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
    )
    try:
        await bot.set_chat_permissions(chat_id, perms)
    except TelegramError as e:
        logger.warning("enable_sterile: %s", e)


async def disable_sterile(chat_id: int, bot):
    """Выключить стерильный режим — восстановить базовые права."""
    sterile_mode[chat_id] = False
    sterile_auto_until.pop(chat_id, None)
    perms = ChatPermissions(
        can_send_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False,
    )
    try:
        await bot.set_chat_permissions(chat_id, perms)
    except TelegramError as e:
        logger.warning("disable_sterile: %s", e)


async def mute_user(chat_id: int, user_id: int, minutes: int, bot):
    """Выдать мут на указанное количество минут."""
    until = datetime.now() + timedelta(minutes=minutes)
    muted_users[chat_id][user_id] = until
    try:
        await bot.restrict_chat_member(
            chat_id, user_id,
            ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except TelegramError as e:
        logger.warning("mute_user: %s", e)

# ─────────────────────────────────────────────
#  КОМАНДЫ — РУЧНОЕ УПРАВЛЕНИЕ
# ─────────────────────────────────────────────

async def cmd_give_defender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /дать_полномочия_защитника
    Назначает защитника. Только владелец чата. Применяется ответом на сообщение.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    msg     = update.effective_message

    if update.effective_chat.type not in ("group", "supergroup"):
        await send(update, MSG["not_in_group"]); return

    owner = await get_owner_id(chat_id, context.bot)
    if user_id != owner:
        await send(update, MSG["owner_only"]); return

    if not msg.reply_to_message:
        await send(update, "❌  Ответьте этой командой на сообщение нужного участника."); return

    target = msg.reply_to_message.from_user
    defenders[chat_id].add(target.id)
    logger.info("Defender added: %s (%d) in chat %d", target.full_name, target.id, chat_id)
    await send(update,
        f"🛡  <b>{target.full_name}</b> "
        f"(<code>@{target.username or target.id}</code>) "
        f"назначен защитником группы."
    )


async def cmd_sterile_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /стерильный_режим
    Включить полную защиту. Доступно владельцу и защитникам.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if update.effective_chat.type not in ("group", "supergroup"):
        await send(update, MSG["not_in_group"]); return
    if not await is_privileged(user_id, chat_id, context.bot):
        await send(update, MSG["no_rights"]); return
    if sterile_mode.get(chat_id):
        await send(update, MSG["already_sterile"]); return

    await enable_sterile(chat_id, context.bot)
    await send(update, MSG["sterile_on"], keyboard=kb_sterile_off())


async def cmd_sterile_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /отключить_стерильный
    Снять стерильный режим. Доступно владельцу и защитникам.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await send(update, MSG["no_rights"]); return
    if not sterile_mode.get(chat_id):
        await send(update, MSG["already_normal"]); return

    await disable_sterile(chat_id, context.bot)
    await send(update, MSG["sterile_off"])


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /статус_защиты
    Показать состояние защиты и пинг бота. Только привилегированные.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if update.effective_chat.type not in ("group", "supergroup"):
        await send(update, MSG["not_in_group"]); return
    if not await is_privileged(user_id, chat_id, context.bot):
        await send(update, MSG["no_rights"]); return

    t0 = time.monotonic()
    await context.bot.get_me()
    latency_ms = round((time.monotonic() - t0) * 1000)

    s_icon   = "🔴 АКТИВЕН"   if sterile_mode.get(chat_id) else "🟢 Неактивен"
    sil_icon = "🔇 АКТИВНА"   if silence_mode.get(chat_id) else "🔊 Неактивна"

    auto_until = sterile_auto_until.get(chat_id)
    auto_str   = (
        f"\n        ⏱  Авто-снятие: <code>{auto_until.strftime('%H:%M  %d.%m.%Y')}</code>"
        if auto_until else ""
    )

    text = (
        f"<b>╔══ KILLER RAID — СТАТУС ══╗</b>\n\n"
        f"📡  Пинг:               <code>{latency_ms} мс</code>\n"
        f"🔴  Стерильный режим:   {s_icon}{auto_str}\n"
        f"🔇  Режим тишины:       {sil_icon}\n"
        f"🛡  Защитников:        <code>{len(defenders.get(chat_id, set()))}</code>\n"
        f"🔇  В муте:             <code>{len(muted_users.get(chat_id, {}))}</code>\n"
        f"\n<b>╚══════════════════════════╝</b>"
    )
    await send(update, text)


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /снять_мут @username  (или ответом на сообщение)
    Снять мут с пользователя. Только привилегированные.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await send(update, MSG["no_rights"]); return

    target_user = None

    # Способ 1: ответ на сообщение
    if update.effective_message.reply_to_message:
        target_user = update.effective_message.reply_to_message.from_user

    # Способ 2: аргумент @username
    elif context.args:
        username = context.args[0].lstrip("@").lower()
        for uid in list(muted_users.get(chat_id, {}).keys()):
            try:
                member = await context.bot.get_chat_member(chat_id, uid)
                if member.user.username and member.user.username.lower() == username:
                    target_user = member.user
                    break
            except TelegramError:
                pass

    if not target_user:
        await send(update, MSG["usage_unmute"]); return

    muted_users[chat_id].pop(target_user.id, None)
    full_perms = ChatPermissions(
        can_send_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )
    try:
        await context.bot.restrict_chat_member(chat_id, target_user.id, full_perms)
        await send(update, f"✅  Мут снят с <b>{target_user.full_name}</b>.")
    except TelegramError:
        await send(update, MSG["unmute_fail"])


async def cmd_defenders_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /вся_защита
    Список защитников группы. Доступно всем.
    """
    chat_id = update.effective_chat.id

    if update.effective_chat.type not in ("group", "supergroup"):
        await send(update, MSG["not_in_group"]); return

    owner_id = await get_owner_id(chat_id, context.bot)
    lines = []

    if owner_id:
        try:
            owner_member = await context.bot.get_chat_member(chat_id, owner_id)
            lines.append(f"👑  <b>{owner_member.user.full_name}</b> — Владелец")
        except TelegramError:
            lines.append(f"👑  ID <code>{owner_id}</code> — Владелец")

    for def_id in defenders.get(chat_id, set()):
        try:
            m = await context.bot.get_chat_member(chat_id, def_id)
            tag = f"@{m.user.username}" if m.user.username else f"ID {def_id}"
            lines.append(f"🛡  <b>{m.user.full_name}</b> ({tag})")
        except TelegramError:
            lines.append(f"🛡  ID <code>{def_id}</code>")

    if not lines:
        await send(update, "ℹ️  Защитников пока не назначено."); return

    await send(update,
        "<b>╔══ ЗАЩИТА ГРУППЫ ══╗</b>\n\n"
        + "\n".join(lines)
        + "\n\n<b>╚══════════════════╝</b>"
    )


async def cmd_silence_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /тишина
    Заморозить чат для всех кроме защиты. Только привилегированные.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await send(update, MSG["no_rights"]); return

    silence_mode[chat_id] = True
    try:
        await context.bot.set_chat_permissions(
            chat_id, ChatPermissions(can_send_messages=False)
        )
    except TelegramError as e:
        logger.warning("silence_on: %s", e)

    await send(update, MSG["silence_on"], keyboard=kb_silence_off())


async def cmd_silence_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /снять_тишину
    Восстановить права на отправку. Только привилегированные.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await send(update, MSG["no_rights"]); return

    silence_mode[chat_id] = False
    if not sterile_mode.get(chat_id):
        await disable_sterile(chat_id, context.bot)
    await send(update, MSG["silence_off"])


async def cmd_kick_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /выгнать <5-60>
    Кикнуть всех, кто вступил за последние N минут. Только привилегированные.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await send(update, MSG["no_rights"]); return

    if not context.args or not context.args[0].isdigit():
        await send(update, MSG["usage_kick"]); return

    minutes = int(context.args[0])
    if not (5 <= minutes <= 60):
        await send(update, MSG["usage_kick"]); return

    since    = datetime.now() - timedelta(minutes=minutes)
    joins    = join_timestamps.get(chat_id, [])
    to_kick  = [uid for uid, ts in joins if datetime.fromtimestamp(ts) >= since]
    kicked   = 0

    for uid in to_kick:
        try:
            await context.bot.ban_chat_member(chat_id, uid)
            await asyncio.sleep(0.3)
            await context.bot.unban_chat_member(chat_id, uid)  # кик без перманентного бана
            kicked += 1
        except TelegramError:
            pass

    if kicked:
        await send(update, MSG["kick_done"].format(n=kicked))
    else:
        await send(update, MSG["kick_none"])

# ─────────────────────────────────────────────
#  CALLBACK — ИНЛАЙН КНОПКИ
# ─────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await query.answer(MSG["no_rights"], show_alert=True)
        return

    await query.answer()

    if query.data == "sterile_off":
        if sterile_mode.get(chat_id):
            await disable_sterile(chat_id, context.bot)
            await query.edit_message_text(MSG["sterile_off"])
        else:
            await query.answer("Режим уже неактивен.", show_alert=True)

    elif query.data == "silence_off":
        if silence_mode.get(chat_id):
            silence_mode[chat_id] = False
            if not sterile_mode.get(chat_id):
                await disable_sterile(chat_id, context.bot)
            await query.edit_message_text(MSG["silence_off"])
        else:
            await query.answer("Тишина уже снята.", show_alert=True)

# ─────────────────────────────────────────────
#  ДЕТЕКЦИЯ НОВЫХ УЧАСТНИКОВ
# ─────────────────────────────────────────────

async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result:
        return

    chat_id    = result.chat.id
    new_status = result.new_chat_member.status
    user       = result.new_chat_member.user

    if new_status not in (ChatMemberStatus.MEMBER, "restricted"):
        return

    now = time.time()

    # Записываем вход
    join_timestamps[chat_id].append((user.id, now))
    # Чистим записи старше 10 минут
    join_timestamps[chat_id] = [
        (uid, ts) for uid, ts in join_timestamps[chat_id] if now - ts < 600
    ]

    # Стерильный режим: мгновенный бан
    if sterile_mode.get(chat_id):
        try:
            await context.bot.ban_chat_member(chat_id, user.id)
            logger.info("Sterile ban: user %d in chat %d", user.id, chat_id)
        except TelegramError as e:
            logger.warning("Sterile ban error: %s", e)
        return

    # Автодетекция рейда: 3+ входа за 10 секунд
    recent = [ts for _, ts in join_timestamps[chat_id] if now - ts <= 10]
    if len(recent) >= 3:
        await _auto_raid(chat_id, context)

# ─────────────────────────────────────────────
#  ДЕТЕКЦИЯ СПАМА СТИКЕРАМИ / ГИФАМИ
# ─────────────────────────────────────────────

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Защита игнорируется
    if await is_privileged(user_id, chat_id, context.bot):
        return

    is_spam = bool(msg.sticker or msg.animation)
    if not is_spam:
        return

    now = time.time()
    spam_timestamps[chat_id][user_id].append(now)
    spam_timestamps[chat_id][user_id] = [
        ts for ts in spam_timestamps[chat_id][user_id] if now - ts <= 10
    ]

    # Индивидуальный порог: 4+ за 10 сек → мут 30 минут
    if len(spam_timestamps[chat_id][user_id]) >= 4:
        spam_timestamps[chat_id][user_id].clear()
        await mute_user(chat_id, user_id, 30, context.bot)
        try:
            await msg.delete()
            await context.bot.send_message(
                chat_id,
                "🔇  Участник получил мут на <b>30 минут</b>.\n"
                "Причина: спам стикерами / гифами (4+ за 10 сек).",
                parse_mode="HTML",
            )
        except TelegramError:
            pass

    # Массовый спам: 3+ разных пользователя → рейд
    spammers = sum(
        1 for uid, tss in spam_timestamps[chat_id].items()
        if len([t for t in tss if now - t <= 10]) >= 2
    )
    if spammers >= 3 and not sterile_mode.get(chat_id):
        await _auto_raid(chat_id, context)

# ─────────────────────────────────────────────
#  АВТО-РЕЙД: ВКЛЮЧИТЬ СТЕРИЛЬНЫЙ НА 5 ЧАСОВ
# ─────────────────────────────────────────────

async def _auto_raid(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    if sterile_mode.get(chat_id):
        return  # уже активен

    until = datetime.now() + timedelta(hours=5)
    sterile_auto_until[chat_id] = until
    await enable_sterile(chat_id, context.bot)

    logger.warning("[RAID DETECTED] chat_id=%d — sterile until %s", chat_id, until)

    try:
        await context.bot.send_message(
            chat_id,
            MSG["raid_detected"] +
            "\n\n⏱  Авто-снятие через <b>5 часов</b> или по команде защитника.",
            parse_mode="HTML",
            reply_markup=kb_sterile_off(),
        )
    except TelegramError:
        pass

    # Запланировать авто-снятие
    context.application.job_queue.run_once(
        _job_sterile_off,
        when=timedelta(hours=5),
        chat_id=chat_id,
        name=f"auto_sterile_off_{chat_id}",
    )


async def _job_sterile_off(context: ContextTypes.DEFAULT_TYPE):
    """Job: автоматически снять стерильный режим через 5 часов."""
    chat_id = context.job.chat_id
    if not sterile_mode.get(chat_id):
        return
    await disable_sterile(chat_id, context.bot)
    try:
        await context.bot.send_message(
            chat_id,
            "🟢  Стерильный режим снят автоматически (истекло 5 часов).",
        )
    except TelegramError:
        pass

# ─────────────────────────────────────────────
#  РЕГИСТРАЦИЯ ХЭНДЛЕРОВ И ЗАПУСК
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # ── Ручные команды ──
    app.add_handler(CommandHandler("дать_полномочия_защитника", cmd_give_defender))
    app.add_handler(CommandHandler("стерильный_режим",          cmd_sterile_on))
    app.add_handler(CommandHandler("отключить_стерильный",      cmd_sterile_off))
    app.add_handler(CommandHandler("статус_защиты",             cmd_status))
    app.add_handler(CommandHandler("снять_мут",                 cmd_unmute))
    app.add_handler(CommandHandler("вся_защита",                cmd_defenders_list))
    app.add_handler(CommandHandler("тишина",                    cmd_silence_on))
    app.add_handler(CommandHandler("снять_тишину",              cmd_silence_off))
    app.add_handler(CommandHandler("выгнать",                   cmd_kick_recent))

    # ── Инлайн кнопки ──
    app.add_handler(CallbackQueryHandler(on_callback))

    # ── Отслеживание входов (для детекции рейда и /выгнать) ──
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))

    # ── Спам-детектор (стикеры и гифы) ──
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & (filters.Sticker.ALL | filters.ANIMATION),
        on_message,
    ))

    logger.info("═══════════════════════════════════════════")
    logger.info("  KILLER RAID — бот запущен                ")
    logger.info("═══════════════════════════════════════════")

    app.run_polling(
        allowed_updates=["message", "chat_member", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
