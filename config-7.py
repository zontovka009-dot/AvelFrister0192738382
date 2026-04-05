# ═══════════════════════════════════════════
#   KILLER RAID — config.py
# ═══════════════════════════════════════════

# Токен бота от @BotFather
BOT_TOKEN: str = "8387931402:AAHfIwGUmhML2eTxUtdvCDXGIJFej7gBwpQ"

# Telegram user_id создателя бота (запасной root-доступ)
# Узнать можно у @userinfobot
CREATOR_ID: int = 0  # ← вставь свой ID

# Путь к файлу базы данных
DB_PATH: str = "killer_raid.db"

# ── Параметры авто-защиты ──────────────────
RAID_JOIN_WINDOW_SEC: int   = 10    # окно детекции входов
RAID_JOIN_THRESHOLD: int    = 3     # кол-во входов за окно → рейд
SPAM_WINDOW_SEC: int        = 10    # окно детекции спама
SPAM_USER_THRESHOLD: int    = 4     # стикеров/гифов от 1 юзера → мут
SPAM_MASS_THRESHOLD: int    = 3     # кол-во спамеров → рейд
MUTE_MINUTES: int           = 30    # длительность мута за спам
STERILE_AUTO_HOURS: int     = 5     # длительность авто-стерильного режима
