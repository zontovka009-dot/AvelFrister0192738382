# ═══════════════════════════════════════════
#   KILLER RAID — main.py
#   Точка входа. Только запуск, логика в модулях.
# ═══════════════════════════════════════════

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from database import init_db
from tasks import start_tasks, stop_tasks

from handlers.commands  import router as commands_router
from handlers.callbacks import router as callbacks_router
from handlers.members   import router as members_router
from handlers.spam      import router as spam_router

# ─────────────────────────────────────────────
#  ЛОГИРОВАНИЕ
# ─────────────────────────────────────────────

logging.basicConfig(
    format  = "%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
    level   = logging.INFO,
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler("killer_raid.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("KillerRaid")


# ─────────────────────────────────────────────
#  STARTUP / SHUTDOWN
# ─────────────────────────────────────────────

async def on_startup(bot: Bot) -> None:
    await init_db()
    logger.info("Database ready.")
    asyncio.create_task(start_tasks(bot))
    logger.info("Background tasks scheduled.")
    me = await bot.get_me()
    logger.info("Bot started: @%s (%d)", me.username, me.id)


async def on_shutdown(bot: Bot) -> None:
    stop_tasks()
    logger.info("Shutdown complete.")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

async def main() -> None:
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()

    # Регистрируем роутеры (порядок важен)
    dp.include_router(callbacks_router)   # колбэки — первыми
    dp.include_router(commands_router)
    dp.include_router(members_router)
    dp.include_router(spam_router)

    # Хуки запуска/остановки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("═══════════════════════════════════════════")
    logger.info("  KILLER RAID — запуск                     ")
    logger.info("═══════════════════════════════════════════")

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
