# ─────────────────────────────────────────────
#  KILLER RAID — ЕДИНЫЙ БОТ
#  Запуск: python bot.py
# ─────────────────────────────────────────────

import logging
import time
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

# ─────────────────────────────────────────────
#  КОНФИГУРАЦИЯ (встроенная)
# ─────────────────────────────────────────────
# ⚠️ ЗАМЕНИ ТОКЕН НА СВОЙ ПЕРЕД ЗАПУСКОМ!
BOT_TOKEN = "8387931402:AAHfIwGUmhML2eTxUtdvCDXGIJFej7gBwpQ"

# ─────────────────────────────────────────────
#  LOGGING
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
#  RUNTIME STATE
# ─────────────────────────────────────────────
# { chat_id: bool }
sterile_mode: dict[int, bool] = {}
silence_mode: dict[int, bool] = {}

# { chat_id: datetime }  — когда автоматически выключится стерильный режим
sterile_auto_until: dict[int, datetime] = {}

# { chat_id: [timestamp, ...] }  — очередь входов для детекции рейда
join_timestamps: dict[int, list] = defaultdict(list)

# { chat_id: { user_id: [timestamp, ...] } }  — спам стикерами/гифами
spam_timestamps: dict[int, dict] = defaultdict(lambda: defaultdict(list))

# { chat_id: { user_id: timestamp } }  — когда истекает мут
muted_users: dict[int, dict] = defaultdict(dict)

# { chat_id: set(user_id) }  — назначенные защитники
defenders: dict[int, set] = defaultdict(set)

# ─────────────────────────────────────────────
#  MESSAGES (холодный стиль)
# ─────────────────────────────────────────────
MSG = {
    "no_rights":        "⛔  Доступ запрещён. Недостаточно полномочий.",
    "sterile_on":       "🔴  СТЕРИЛЬНЫЙ РЕЖИМ АКТИВИРОВАН.\nВсе входящие — бан. Спам — мут.",
    "sterile_off":      "🟢  Стерильный режим снят. Группа в стабильном состоянии.",
    "silence_on":       "🔇  Чат заморожен. Право слова — только у защиты.",
    "silence_off":      "🔊  Ограничение снято. Чат открыт.",
    "unmute_done":      "✅  Мут снят.",
    "unmute_fail":      "❌  Пользователь не найден или мут не выдан ботом.",
    "defender_added":   "🛡  Защитник назначен.",
    "raid_detected":    "⚠️  ОБНАРУЖЕН РЕЙД. Автоматический переход в стерильный режим.\nОчистка запущена.",
    "already_sterile":  "ℹ️  Стерильный режим уже активен.",
    "already_normal":   "ℹ️  Режим уже стабильный.",
    "kick_done":        "🧹  Очистка завершена. Удалено участников: {n}",
    "kick_none":        "ℹ️  За указанный период новых участников не найдено.",
    "usage_kick":       "❌  Использование: /выгнать <5-60>",
    "usage_unmute":     "❌  Использование: /снять_мут @username",
    "not_in_group":     "❌  Команда работает только в группах.",
    "owner_only":       "⛔  Только создатель чата может выполнить это действие.",
}

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
async def get_chat_owner_id(chat_id: int, bot) -> int | None:
    """Возвращает user_id владельца чата."""
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
    owner = await get_chat_owner_id(chat_id, bot)
    return user_id == owner or user_id in defenders.get(chat_id, set())


async def reply(update: Update, text: str, keyboard=None):
    kwargs = {"text": text, "parse_mode": "HTML"}
    if keyboard:
        kwargs["reply_markup"] = keyboard
    await update.effective_message.reply_text(**kwargs)


def sterile_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Отключить стерильный режим", callback_data="sterile_off")
    ]])


def silence_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔊 Снять тишину", callback_data="silence_off")
    ]])


async def enable_sterile(chat_id: int, bot, context: ContextTypes.DEFAULT_TYPE):
    """Включить стерильный режим — выставить права на ноль."""
    sterile_mode[chat_id] = True
    no_perms = ChatPermissions(
        can_send_messages=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
    )
    try:
        await bot.set_chat_permissions(chat_id, no_perms)
    except TelegramError as e:
        logger.warning("enable_sterile permissions error: %s", e)


async def disable_sterile(chat_id: int, bot):
    """Выключить стерильный режим — восстановить базовые права."""
    sterile_mode[chat_id] = False
    sterile_auto_until.pop(chat_id, None)
    default_perms = ChatPermissions(
        can_send_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False,
    )
    try:
        await bot.set_chat_permissions(chat_id, default_perms)
    except TelegramError as e:
        logger.warning("disable_sterile permissions error: %s", e)


async def mute_user(chat_id: int, user_id: int, minutes: int, bot):
    until = datetime.now() + timedelta(minutes=minutes)
    muted_users[chat_id][user_id] = until
    perms = ChatPermissions(can_send_messages=False)
    try:
        await bot.restrict_chat_member(
            chat_id, user_id, perms,
            until_date=until
        )
    except TelegramError as e:
        logger.warning("mute_user error: %s", e)

# ─────────────────────────────────────────────
#  КОМАНДЫ
# ─────────────────────────────────────────────

async def cmd_give_defender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Только владелец может назначить защитника."""
    msg = update.effective_message
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if update.effective_chat.type not in ("group", "supergroup"):
        await reply(update, MSG["not_in_group"])
        return

    owner = await get_chat_owner_id(chat_id, context.bot)
    if user_id != owner:
        await reply(update, MSG["owner_only"])
        return

    if not msg.reply_to_message:
        await reply(update, "❌  Ответьте командой на сообщение нужного пользователя.")
        return

    target = msg.reply_to_message.from_user
    defenders[chat_id].add(target.id)
    await reply(update,
        f"🛡  <b>{target.full_name}</b> (<code>@{target.username or target.id}</code>) "
        f"назначен защитником группы."
    )


async def cmd_sterile_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if update.effective_chat.type not in ("group", "supergroup"):
        await reply(update, MSG["not_in_group"]); return

    if not await is_privileged(user_id, chat_id, context.bot):
        await reply(update, MSG["no_rights"]); return

    if sterile_mode.get(chat_id):
        await reply(update, MSG["already_sterile"]); return

    await enable_sterile(chat_id, context.bot, context)
    await reply(update, MSG["sterile_on"], keyboard=sterile_keyboard())


async def cmd_sterile_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await reply(update, MSG["no_rights"]); return

    if not sterile_mode.get(chat_id):
        await reply(update, MSG["already_normal"]); return

    await disable_sterile(chat_id, context.bot)
    await reply(update, MSG["sterile_off"])


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if update.effective_chat.type not in ("group", "supergroup"):
        await reply(update, MSG["not_in_group"]); return
    if not await is_privileged(user_id, chat_id, context.bot):
        await reply(update, MSG["no_rights"]); return

    t0 = time.monotonic()
    await context.bot.get_me()
    latency_ms = round((time.monotonic() - t0) * 1000)

    s_mode = "🔴 АКТИВЕН" if sterile_mode.get(chat_id) else "🟢 Неактивен"
    sil_mode = "🔇 АКТИВНА" if silence_mode.get(chat_id) else "🔊 Неактивна"
    auto_until = sterile_auto_until.get(chat_id)
    auto_str = (
        f"\n⏱  Авто-снятие: <code>{auto_until.strftime('%H:%M:%S %d.%m.%Y')}</code>"
        if auto_until else ""
    )
    def_count = len(defenders.get(chat_id, set()))
    mute_count = len(muted_users.get(chat_id, {}))

    text = (
        f"<b>╔══ KILLER RAID — СТАТУС ══╗</b>\n\n"
        f"📡  Пинг бота:          <code>{latency_ms} мс</code>\n"
        f"🔴  Стерильный режим:   {s_mode}{auto_str}\n"
        f"🔇  Режим тишины:       {sil_mode}\n"
        f"🛡  Защитников:        <code>{def_count}</code>\n"
        f"🔇  Пользователей в муте: <code>{mute_count}</code>\n"
        f"\n<b>╚══════════════════════════╝</b>"
    )
    await reply(update, text)


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await reply(update, MSG["no_rights"]); return

    # Ищем цель: либо реплай, либо аргумент
    target_user = None
    if update.effective_message.reply_to_message:
        target_user = update.effective_message.reply_to_message.from_user
    elif context.args:
        username = context.args[0].lstrip("@")
        # Проверяем мьютнутых
        for uid in muted_users.get(chat_id, {}):
            try:
                member = await context.bot.get_chat_member(chat_id, uid)
                if member.user.username and member.user.username.lower() == username.lower():
                    target_user = member.user
                    break
            except TelegramError:
                pass
    
    if not target_user:
        await reply(update, MSG["usage_unmute"]); return

    muted_users[chat_id].pop(target_user.id, None)
    full_perms = ChatPermissions(
        can_send_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )
    try:
        await context.bot.restrict_chat_member(chat_id, target_user.id, full_perms)
        await reply(update,
            f"✅  Мут снят с <b>{target_user.full_name}</b>."
        )
    except TelegramError:
        await reply(update, MSG["unmute_fail"])


async def cmd_defenders_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if update.effective_chat.type not in ("group", "supergroup"):
        await reply(update, MSG["not_in_group"]); return

    owner_id = await get_chat_owner_id(chat_id, context.bot)
    lines = []

    if owner_id:
        try:
            owner = await context.bot.get_chat_member(chat_id, owner_id)
            lines.append(f"👑  <b>{owner.user.full_name}</b> — Владелец")
        except TelegramError:
            lines.append(f"👑  ID <code>{owner_id}</code> — Владелец")

    for def_id in defenders.get(chat_id, set()):
        try:
            m = await context.bot.get_chat_member(chat_id, def_id)
            lines.append(f"🛡  <b>{m.user.full_name}</b> (@{m.user.username or def_id})")
        except TelegramError:
            lines.append(f"🛡  ID <code>{def_id}</code>")

    if not lines:
        await reply(update, "ℹ️  Защитников не назначено."); return

    await reply(update,
        "<b>╔══ ЗАЩИТА ГРУППЫ ══╗</b>\n\n"
        + "\n".join(lines)
        + "\n\n<b>╚══════════════════╝</b>"
    )


async def cmd_silence_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await reply(update, MSG["no_rights"]); return

    silence_mode[chat_id] = True
    no_msg = ChatPermissions(can_send_messages=False)
    try:
        await context.bot.set_chat_permissions(chat_id, no_msg)
    except TelegramError as e:
        logger.warning("silence_on: %s", e)
    await reply(update, MSG["silence_on"], keyboard=silence_keyboard())


async def cmd_silence_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await reply(update, MSG["no_rights"]); return

    silence_mode[chat_id] = False
    if not sterile_mode.get(chat_id):
        await disable_sterile(chat_id, context.bot)
    await reply(update, MSG["silence_off"])


async def cmd_kick_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await reply(update, MSG["no_rights"]); return

    if not context.args or not context.args[0].isdigit():
        await reply(update, MSG["usage_kick"]); return

    minutes = int(context.args[0])
    if not (5 <= minutes <= 60):
        await reply(update, MSG["usage_kick"]); return

    since = datetime.now() - timedelta(minutes=minutes)
    kicked = 0

    # Проходим по join_timestamps
    joins = join_timestamps.get(chat_id, [])
    recent_joins = [uid for uid, ts in joins if datetime.fromtimestamp(ts) >= since]

    for uid in recent_joins:
        try:
            await context.bot.ban_chat_member(chat_id, uid)
            await asyncio.sleep(0.3)
            await context.bot.unban_chat_member(chat_id, uid)  # кик, не бан
            kicked += 1
        except TelegramError:
            pass

    if kicked:
        await reply(update, MSG["kick_done"].format(n=kicked))
    else:
        await reply(update, MSG["kick_none"])

# ─────────────────────────────────────────────
#  CALLBACK КНОПКИ
# ─────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if not await is_privileged(user_id, chat_id, context.bot):
        await query.answer(MSG["no_rights"], show_alert=True)
        return

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

    chat_id = result.chat.id
    new_status = result.new_chat_member.status
    user = result.new_chat_member.user

    if new_status not in (ChatMemberStatus.MEMBER, "restricted"):
        return

    now = time.time()

    # Запись в историю входов
    join_timestamps[chat_id].append((user.id, now))
    # Чистим старые записи (> 10 минут)
    join_timestamps[chat_id] = [
        (uid, ts) for uid, ts in join_timestamps[chat_id]
        if now - ts < 600
    ]

    # ── Стерильный режим: бан на вход ──
    if sterile_mode.get(chat_id):
        try:
            await context.bot.ban_chat_member(chat_id, user.id)
        except TelegramError as e:
            logger.warning("sterile ban error: %s", e)
        return

    # ── Автодетекция рейда: 3+ входа за 10 секунд ──
    recent = [ts for _, ts in join_timestamps[chat_id] if now - ts <= 10]
    if len(recent) >= 3 and not sterile_mode.get(chat_id):
        await _auto_raid_response(chat_id, context)

# ─────────────────────────────────────────────
#  ДЕТЕКЦИЯ СПАМА СТИКЕРАМИ/ГИФАМИ
# ─────────────────────────────────────────────

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return

    # Не трогаем защитников и владельца
    if await is_privileged(user_id, chat_id, context.bot):
        return

    now = time.time()

    is_spam_content = bool(msg.sticker or msg.animation)

    if is_spam_content:
        spam_timestamps[chat_id][user_id].append(now)
        # Чистим старые
        spam_timestamps[chat_id][user_id] = [
            ts for ts in spam_timestamps[chat_id][user_id] if now - ts <= 10
        ]
        count = len(spam_timestamps[chat_id][user_id])

        if count >= 4:
            spam_timestamps[chat_id][user_id].clear()
            await mute_user(chat_id, user_id, 30, context.bot)
            try:
                await msg.delete()
                await context.bot.send_message(
                    chat_id,
                    f"🔇  Пользователь получил мут на <b>30 минут</b> за спам."
                    f"\nПричина: спам стикерами/гифами.",
                    parse_mode="HTML"
                )
            except TelegramError:
                pass

    # ── Автодетекция массового спама (3+ разных юзера спамят) ──
    if is_spam_content:
        spammers = sum(
            1 for uid, tss in spam_timestamps[chat_id].items()
            if len([t for t in tss if now - t <= 10]) >= 2
        )
        if spammers >= 3 and not sterile_mode.get(chat_id):
            await _auto_raid_response(chat_id, context)

# ─────────────────────────────────────────────
#  АВТО-РЕЙД ОТВЕТ
# ─────────────────────────────────────────────

async def _auto_raid_response(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    sterile_mode[chat_id] = True
    until = datetime.now() + timedelta(hours=5)
    sterile_auto_until[chat_id] = until

    await enable_sterile(chat_id, context.bot, context)

    try:
        await context.bot.send_message(
            chat_id,
            MSG["raid_detected"] +
            f"\n\n⏱  Авто-снятие через <b>5 часов</b> или по команде защитника.",
            parse_mode="HTML",
            reply_markup=sterile_keyboard()
        )
    except TelegramError:
        pass

    logger.warning("[RAID] Chat %d — auto sterile activated until %s", chat_id, until)

    # Планируем авто-снятие через 5 часов
    context.application.job_queue.run_once(
        _auto_sterile_off,
        when=timedelta(hours=5),
        chat_id=chat_id,
        name=f"sterile_off_{chat_id}",
    )


async def _auto_sterile_off(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    if sterile_mode.get(chat_id):
        await disable_sterile(chat_id, context.bot)
        try:
            await context.bot.send_message(
                chat_id,
                "🟢  Стерильный режим снят автоматически по истечении 5 часов.",
            )
        except TelegramError:
            pass

# ─────────────────────────────────────────────
#  РЕГИСТРАЦИЯ И ЗАПУСК
# ─────────────────────────────────────────────

def main():
    # Проверка токена
    if BOT_TOKEN == "8387931402:AAHfIwGUmhML2eTxUtdvCDXGIJFej7gBwpQ":
        logger.warning("⚠️  ВНИМАНИЕ! Используется токен по умолчанию. Замените BOT_TOKEN на свой!")
    
    app = Application.builder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("дать_полномочия_защитника", cmd_give_defender))
    app.add_handler(CommandHandler("стерильный_режим", cmd_sterile_on))
    app.add_handler(CommandHandler("отключить_стерильный", cmd_sterile_off))
    app.add_handler(CommandHandler("статус_защиты", cmd_status))
    app.add_handler(CommandHandler("снять_мут", cmd_unmute))
    app.add_handler(CommandHandler("вся_защита", cmd_defenders_list))
    app.add_handler(CommandHandler("тишина", cmd_silence_on))
    app.add_handler(CommandHandler("снять_тишину", cmd_silence_off))
    app.add_handler(CommandHandler("выгнать", cmd_kick_recent))

    # Callback кнопки
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Новые участники
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))

    # Сообщения (спам-детектор)
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & (filters.Sticker.ALL | filters.ANIMATION),
        on_message
    ))

    logger.info("═══════════════════════════════════════")
    logger.info("  KILLER RAID — запуск                 ")
    logger.info("═══════════════════════════════════════")

    app.run_polling(
        allowed_updates=["message", "chat_member", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()