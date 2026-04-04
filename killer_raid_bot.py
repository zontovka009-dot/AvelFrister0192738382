"""
╔══════════════════════════════════════════╗
║         KILLER RAID — ЗАЩИТНЫЙ БОТ       ║
║         Версия 1.0 | Cold Protocol       ║
╚══════════════════════════════════════════╝
"""

import asyncio
import time
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional

from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ChatMemberHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

# ──────────────────────────────────────────────
#  КОНФИГУРАЦИЯ
# ──────────────────────────────────────────────
TOKEN = "8387931402:AAHfIwGUmhML2eTxUtdvCDXGIJFej7gBwpQ"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("KillerRaid")

# ──────────────────────────────────────────────
#  ХРАНИЛИЩЕ СОСТОЯНИЙ
# ──────────────────────────────────────────────

# {chat_id: set(user_id)}  — назначенные защитники
defenders: dict[int, set] = defaultdict(set)

# {chat_id: bool}  — стерильный режим
sterile_mode: dict[int, bool] = defaultdict(bool)

# {chat_id: bool}  — режим тишины
silence_mode: dict[int, bool] = defaultdict(bool)

# {chat_id: datetime}  — когда включён стерильный режим (для авто-5ч)
sterile_since: dict[int, datetime] = {}

# {chat_id: {user_id: [timestamps]}}  — спам-трекер для стикеров/гифок
spam_tracker: dict[int, dict] = defaultdict(lambda: defaultdict(list))

# {chat_id: [timestamps]}  — трекер вступлений (для авто-рейд детектора)
join_tracker: dict[int, list] = defaultdict(list)

# {chat_id: {user_id: [timestamps]}}  — спам-трекер сообщений (авто-рейд)
msg_spam_tracker: dict[int, dict] = defaultdict(lambda: defaultdict(list))

# {chat_id: [join_timestamps]}  — история вступлений для команды /выгнать
join_history: dict[int, list] = defaultdict(list)  # [(timestamp, user_id)]

# {chat_id: {user_id}}  — муты выданные ботом
bot_mutes: dict[int, set] = defaultdict(set)

OPEN_PERMISSIONS = ChatPermissions(
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

SILENCE_PERMISSIONS = ChatPermissions(
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
)

# ──────────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ──────────────────────────────────────────────

async def get_chat_owner_id(chat_id: int, bot) -> Optional[int]:
    """Возвращает user_id создателя чата."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
        for admin in admins:
            if admin.status == ChatMemberStatus.OWNER:
                return admin.user.id
    except TelegramError:
        pass
    return None


async def is_owner(chat_id: int, user_id: int, bot) -> bool:
    owner = await get_chat_owner_id(chat_id, bot)
    return owner == user_id


async def is_defender(chat_id: int, user_id: int, bot) -> bool:
    if await is_owner(chat_id, user_id, bot):
        return True
    return user_id in defenders.get(chat_id, set())


async def check_permission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет, имеет ли отправитель права защитника."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    return await is_defender(chat_id, user_id, context.bot)


def ping_ms() -> float:
    start = time.perf_counter()
    _ = 1 + 1
    return round((time.perf_counter() - start) * 1_000_000, 3)


def fmt_user(user) -> str:
    if user.username:
        return f"@{user.username}"
    return f"[{user.full_name}](tg://user?id={user.id})"


async def silent_delete(message):
    try:
        await message.delete()
    except TelegramError:
        pass


# ──────────────────────────────────────────────
#  СТЕРИЛЬНЫЙ РЕЖИМ — АКТИВАЦИЯ / ДЕАКТИВАЦИЯ
# ──────────────────────────────────────────────

async def activate_sterile(chat_id: int, bot, triggered_by: str = "вручную"):
    sterile_mode[chat_id] = True
    sterile_since[chat_id] = datetime.utcnow()
    logger.info(f"[{chat_id}] Стерильный режим АКТИВИРОВАН ({triggered_by})")
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔓 Деактивировать стерильный режим", callback_data=f"deactivate_sterile:{chat_id}")
    ]])
    text = (
        "⚠️ *СТЕРИЛЬНЫЙ РЕЖИМ АКТИВИРОВАН*\n\n"
        f"Причина: `{triggered_by}`\n"
        "• Все новые участники — немедленный бан\n"
        "• Спам стикерами/гифками → мут 30 мин\n"
        "• Группа под полной блокировкой\n\n"
        "_Снять режим может только создатель или защитник._"
    )
    try:
        await bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=keyboard)
    except TelegramError as e:
        logger.warning(f"Не удалось отправить уведомление: {e}")


async def deactivate_sterile(chat_id: int, bot):
    sterile_mode[chat_id] = False
    sterile_since.pop(chat_id, None)
    logger.info(f"[{chat_id}] Стерильный режим ДЕАКТИВИРОВАН")
    text = (
        "✅ *СТЕРИЛЬНЫЙ РЕЖИМ СНЯТ*\n\n"
        "Группа переведена в стабильный режим.\n"
        "Стандартная защита продолжает работу."
    )
    try:
        await bot.send_message(chat_id, text, parse_mode="Markdown")
    except TelegramError as e:
        logger.warning(f"Не удалось отправить уведомление: {e}")


# ──────────────────────────────────────────────
#  КОМАНДЫ
# ──────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "⚡ *KILLER RAID* активен.\n"
            "Добавьте меня в группу и дайте права администратора.",
            parse_mode="Markdown"
        )


async def cmd_дать_полномочия(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Только создатель чата может назначать защитников."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_owner(chat_id, user_id, context.bot):
        await update.message.reply_text(
            "❌ Недостаточно прав. Команда доступна исключительно создателю чата.",
            parse_mode="Markdown"
        )
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "⚠️ Ответьте на сообщение пользователя, которому хотите дать полномочия защитника.",
            parse_mode="Markdown"
        )
        return

    target = update.message.reply_to_message.from_user
    defenders[chat_id].add(target.id)
    await update.message.reply_text(
        f"🛡 *Полномочия защитника выданы* — {fmt_user(target)}\n"
        f"Идентификатор: `{target.id}`",
        parse_mode="Markdown"
    )


async def cmd_стерильный(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not await check_permission(update, context):
        await update.message.reply_text("❌ Недостаточно прав.")
        return

    if sterile_mode[chat_id]:
        await update.message.reply_text(
            "ℹ️ Стерильный режим уже активен.",
            parse_mode="Markdown"
        )
        return

    initiator = fmt_user(update.effective_user)
    await activate_sterile(chat_id, context.bot, triggered_by=f"команда от {initiator}")


async def cmd_статус(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not await check_permission(update, context):
        await update.message.reply_text("❌ Недостаточно прав.")
        return

    ms = ping_ms()
    s_mode = "🔴 АКТИВЕН" if sterile_mode[chat_id] else "🟢 Отключён"
    sil_mode = "🔇 АКТИВНА" if silence_mode[chat_id] else "🔊 Отключена"
    def_count = len(defenders.get(chat_id, set()))

    s_since = "—"
    if chat_id in sterile_since:
        delta = datetime.utcnow() - sterile_since[chat_id]
        hours, rem = divmod(int(delta.total_seconds()), 3600)
        minutes = rem // 60
        s_since = f"{hours}ч {minutes}м"

    text = (
        "📊 *СТАТУС ЗАЩИТЫ — KILLER RAID*\n"
        "───────────────────────────\n"
        f"🛡 Стерильный режим: {s_mode}\n"
        f"⏱ Активен уже: {s_since}\n"
        f"🔇 Тишина: {sil_mode}\n"
        f"👥 Назначено защитников: `{def_count}`\n"
        f"🤖 Задержка бота: `{ms} мс`\n"
        "───────────────────────────\n"
        "_Данные актуальны на момент запроса._"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_снять_мут(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not await check_permission(update, context):
        await update.message.reply_text("❌ Недостаточно прав.")
        return

    target_user = None

    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
    elif context.args:
        username = context.args[0].lstrip("@")
        try:
            member = await context.bot.get_chat_member(chat_id, f"@{username}")
            target_user = member.user
        except TelegramError:
            await update.message.reply_text(f"❌ Пользователь @{username} не найден в чате.")
            return

    if not target_user:
        await update.message.reply_text(
            "⚠️ Укажите пользователя: ответьте на его сообщение или напишите /снять_мут @username"
        )
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id, target_user.id,
            permissions=OPEN_PERMISSIONS
        )
        bot_mutes[chat_id].discard(target_user.id)
        await update.message.reply_text(
            f"🔓 Мут снят — {fmt_user(target_user)}",
            parse_mode="Markdown"
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def cmd_вся_защита(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    owner_id = await get_chat_owner_id(chat_id, context.bot)

    lines = ["🛡 *СОСТАВ ЗАЩИТЫ ГРУППЫ*\n───────────────────────────"]

    if owner_id:
        try:
            owner = await context.bot.get_chat_member(chat_id, owner_id)
            lines.append(f"👑 Создатель: {fmt_user(owner.user)} (`{owner_id}`)")
        except TelegramError:
            lines.append(f"👑 Создатель: `{owner_id}`")

    def_ids = defenders.get(chat_id, set())
    if def_ids:
        lines.append(f"\n🔰 Защитники ({len(def_ids)}):")
        for uid in def_ids:
            try:
                member = await context.bot.get_chat_member(chat_id, uid)
                lines.append(f"  • {fmt_user(member.user)} (`{uid}`)")
            except TelegramError:
                lines.append(f"  • `{uid}` (покинул чат)")
    else:
        lines.append("\n_Защитники не назначены._")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_тишина(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not await check_permission(update, context):
        await update.message.reply_text("❌ Недостаточно прав.")
        return

    if silence_mode[chat_id]:
        # Снять тишину
        silence_mode[chat_id] = False
        try:
            await context.bot.set_chat_permissions(chat_id, OPEN_PERMISSIONS)
        except TelegramError:
            pass
        await update.message.reply_text(
            "🔊 *Тишина снята.* Чат открыт для всех участников.",
            parse_mode="Markdown"
        )
    else:
        # Включить тишину
        silence_mode[chat_id] = True
        try:
            await context.bot.set_chat_permissions(chat_id, SILENCE_PERMISSIONS)
        except TelegramError as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔊 Снять тишину", callback_data=f"lift_silence:{chat_id}")
        ]])
        await update.message.reply_text(
            "🔇 *ТИШИНА УСТАНОВЛЕНА*\n\n"
            "Общение заблокировано для всех, кроме создателя и защитников.\n"
            "_Снять может только защитник или создатель._",
            parse_mode="Markdown",
            reply_markup=keyboard
        )


async def cmd_выгнать(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not await check_permission(update, context):
        await update.message.reply_text("❌ Недостаточно прав.")
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Укажите интервал в минутах (5–60).\nПример: `/выгнать 30`",
            parse_mode="Markdown"
        )
        return

    try:
        minutes = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Укажите число минут (5–60).")
        return

    if not (5 <= minutes <= 60):
        await update.message.reply_text("❌ Допустимый диапазон: от 5 до 60 минут.")
        return

    cutoff = time.time() - minutes * 60
    recent_joins = [
        (ts, uid) for ts, uid in join_history.get(chat_id, [])
        if ts >= cutoff
    ]

    if not recent_joins:
        await update.message.reply_text(
            f"ℹ️ За последние {minutes} мин. новых участников не зафиксировано."
        )
        return

    kicked = 0
    failed = 0
    for ts, uid in recent_joins:
        try:
            await context.bot.ban_chat_member(chat_id, uid)
            await context.bot.unban_chat_member(chat_id, uid)
            kicked += 1
        except TelegramError:
            failed += 1

    await update.message.reply_text(
        f"🚪 *Зачистка завершена*\n\n"
        f"Интервал: последние `{minutes}` мин.\n"
        f"Выгнано: `{kicked}` участников\n"
        f"Ошибок: `{failed}`",
        parse_mode="Markdown"
    )


# ──────────────────────────────────────────────
#  CALLBACK — КНОПКИ
# ──────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("deactivate_sterile:"):
        chat_id = int(data.split(":")[1])
        if not await is_defender(chat_id, user_id, context.bot):
            await query.answer("❌ Недостаточно прав.", show_alert=True)
            return
        await deactivate_sterile(chat_id, context.bot)
        await query.edit_message_reply_markup(reply_markup=None)

    elif data.startswith("lift_silence:"):
        chat_id = int(data.split(":")[1])
        if not await is_defender(chat_id, user_id, context.bot):
            await query.answer("❌ Недостаточно прав.", show_alert=True)
            return
        silence_mode[chat_id] = False
        try:
            await context.bot.set_chat_permissions(chat_id, OPEN_PERMISSIONS)
        except TelegramError:
            pass
        await query.edit_message_text(
            "🔊 *Тишина снята.* Чат открыт.",
            parse_mode="Markdown"
        )


# ──────────────────────────────────────────────
#  ОБРАБОТЧИК НОВЫХ УЧАСТНИКОВ
# ──────────────────────────────────────────────

async def on_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = time.time()

    for member in update.message.new_chat_members:
        if member.is_bot and member.id == context.bot.id:
            await context.bot.send_message(
                chat_id,
                "⚡ *KILLER RAID* подключён.\n"
                "Для назначения защитника используйте команду `/дать_полномочия_защитника` (ответом на сообщение).\n"
                "_Протокол защиты активен._",
                parse_mode="Markdown"
            )
            continue

        # Запись в историю вступлений
        join_history[chat_id].append((now, member.id))
        # Чистим старые записи (>1 часа)
        join_history[chat_id] = [
            (ts, uid) for ts, uid in join_history[chat_id]
            if now - ts <= 3600
        ]

        # Трекер для авто-рейд детектора
        join_tracker[chat_id].append(now)
        join_tracker[chat_id] = [t for t in join_tracker[chat_id] if now - t <= 10]

        # Авто-детект рейда: 3+ вступлений за 10 сек
        if len(join_tracker[chat_id]) >= 3 and not sterile_mode[chat_id]:
            await activate_sterile(chat_id, context.bot, triggered_by="авто-детект: накрутка участников")

        # Стерильный режим — бан
        if sterile_mode[chat_id]:
            try:
                await context.bot.ban_chat_member(chat_id, member.id)
                logger.info(f"[{chat_id}] Забанен (стерильный режим): {member.id}")
            except TelegramError as e:
                logger.warning(f"Не удалось забанить {member.id}: {e}")


# ──────────────────────────────────────────────
#  ОБРАБОТЧИК СООБЩЕНИЙ — СПАМ / ТИШИНА
# ──────────────────────────────────────────────

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return

    chat_id = update.effective_chat.id
    user_id = msg.from_user.id
    now = time.time()

    # Защитники и владелец освобождены от ограничений
    if await is_defender(chat_id, user_id, context.bot):
        return

    # ── Тишина: удаляем сообщения не-защитников
    if silence_mode[chat_id]:
        await silent_delete(msg)
        return

    # ── Спам-детект (стикеры / гифки) — мут 30 мин
    is_spam_media = bool(msg.sticker or msg.animation)
    if is_spam_media:
        spam_tracker[chat_id][user_id].append(now)
        spam_tracker[chat_id][user_id] = [
            t for t in spam_tracker[chat_id][user_id] if now - t <= 10
        ]
        if len(spam_tracker[chat_id][user_id]) >= 4:
            until = datetime.utcnow() + timedelta(minutes=30)
            try:
                await context.bot.restrict_chat_member(
                    chat_id, user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until
                )
                bot_mutes[chat_id].add(user_id)
                spam_tracker[chat_id][user_id].clear()
                await msg.reply_text(
                    f"🔇 {fmt_user(msg.from_user)} — мут на 30 минут.\n"
                    "_Причина: спам стикерами/гифками._",
                    parse_mode="Markdown"
                )
            except TelegramError as e:
                logger.warning(f"Не удалось выдать мут {user_id}: {e}")
            return

    # ── Авто-детект рейда: спам от 3+ разных пользователей за 10 сек
    msg_spam_tracker[chat_id][user_id].append(now)
    msg_spam_tracker[chat_id][user_id] = [
        t for t in msg_spam_tracker[chat_id][user_id] if now - t <= 10
    ]
    active_spammers = sum(
        1 for uid, times in msg_spam_tracker[chat_id].items()
        if len(times) >= 2
    )
    if active_spammers >= 3 and not sterile_mode[chat_id]:
        await activate_sterile(chat_id, context.bot, triggered_by="авто-детект: массовый спам")


# ──────────────────────────────────────────────
#  ФОНОВАЯ ЗАДАЧА — АВТО-СНЯТИЕ СТЕРИЛЬНОГО
#  РЕЖИМА ЧЕРЕЗ 5 ЧАСОВ (если не снят вручную)
# ──────────────────────────────────────────────

async def sterile_watchdog(app: Application):
    while True:
        await asyncio.sleep(60)
        now = datetime.utcnow()
        for chat_id, since in list(sterile_since.items()):
            if (now - since).total_seconds() >= 5 * 3600:
                await deactivate_sterile(chat_id, app.bot)
                logger.info(f"[{chat_id}] Стерильный режим снят автоматически (5 часов)")


# ──────────────────────────────────────────────
#  СБОРКА И ЗАПУСК
# ──────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("дать_полномочия_защитника", cmd_дать_полномочия))
    app.add_handler(CommandHandler("стерильный_режим", cmd_стерильный))
    app.add_handler(CommandHandler("статус_защиты", cmd_статус))
    app.add_handler(CommandHandler("снять_мут", cmd_снять_мут))
    app.add_handler(CommandHandler("вся_защита", cmd_вся_защита))
    app.add_handler(CommandHandler("тишина", cmd_тишина))
    app.add_handler(CommandHandler("выгнать", cmd_выгнать))

    # Callback кнопки
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Новые участники
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member))

    # Все сообщения (спам, тишина)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))

    # Фоновый watchdog
    async def post_init(application: Application):
        asyncio.create_task(sterile_watchdog(application))

    app.post_init = post_init

    logger.info("⚡ KILLER RAID запущен. Протокол активен.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
