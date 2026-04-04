# ═══════════════════════════════════════════════════════════
#   KILLER RAID — main.py
#   Точка входа. Только инициализация и запуск.
# ═══════════════════════════════════════════════════════════

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import database as db
from config import BOT_TOKEN
from handlers.commands import router as cmd_router
from handlers.auto_guard import router as guard_router

# ─────────────────────────────────────────────
#  Логирование
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
#  Старт / стоп хуки
# ─────────────────────────────────────────────

async def on_startup(bot: Bot) -> None:
    await db.init_db()
    me = await bot.get_me()
    logger.info("═══════════════════════════════════════════")
    logger.info("  KILLER RAID запущен  @%s", me.username)
    logger.info("═══════════════════════════════════════════")


async def on_shutdown(bot: Bot) -> None:
    logger.info("KILLER RAID остановлен.")


# ─────────────────────────────────────────────
#  Точка входа
# ─────────────────────────────────────────────

async def main() -> None:
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()

    # Хуки жизненного цикла
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Роутеры (порядок важен: сначала команды, потом авто-защита)
    dp.include_router(cmd_router)
    dp.include_router(guard_router)

    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "chat_member",
            "callback_query",
        ],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
