# ═══════════════════════════════════════════
#   KILLER RAID — handlers/callbacks.py
#   Обработка инлайн-кнопок
# ═══════════════════════════════════════════

import logging

from aiogram import Router, Bot, F
from aiogram.types import CallbackQuery

import database as db
from utils import MSG, is_privileged, restore_permissions

logger = logging.getLogger("KillerRaid.Callbacks")
router = Router()


@router.callback_query(F.data == "sterile_off")
async def cb_sterile_off(call: CallbackQuery, bot: Bot) -> None:
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if not await is_privileged(user_id, chat_id, bot):
        await call.answer(MSG["no_rights"], show_alert=True)
        return

    mode = await db.get_chat_mode(chat_id)
    if not mode["sterile"]:
        await call.answer("Режим уже неактивен.", show_alert=True)
        return

    await db.set_sterile(chat_id, False)
    await restore_permissions(chat_id, bot)
    await db.log_event(chat_id, "STERILE_OFF", user_id, "кнопка")

    await call.answer("Стерильный режим снят.")
    await call.message.edit_text(MSG["sterile_off"], parse_mode="HTML")


@router.callback_query(F.data == "silence_off")
async def cb_silence_off(call: CallbackQuery, bot: Bot) -> None:
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if not await is_privileged(user_id, chat_id, bot):
        await call.answer(MSG["no_rights"], show_alert=True)
        return

    mode = await db.get_chat_mode(chat_id)
    if not mode["silence"]:
        await call.answer("Тишина уже снята.", show_alert=True)
        return

    await db.set_silence(chat_id, False)
    if not mode["sterile"]:
        await restore_permissions(chat_id, bot)

    await db.log_event(chat_id, "SILENCE_OFF", user_id, "кнопка")

    await call.answer("Тишина снята.")
    await call.message.edit_text(MSG["silence_off"], parse_mode="HTML")
