# ═══════════════════════════════════════════════════════════
#   KILLER RAID — config.py
#   Токен бота и глобальные настройки
# ═══════════════════════════════════════════════════════════

# ── Токен от @BotFather ──
BOT_TOKEN: str = "8387931402:AAHfIwGUmhML2eTxUtdvCDXGIJFej7gBwpQ"

# ── Путь к файлу базы данных ──
DB_PATH: str = "killer_raid.db"

# ── Параметры авто-детекции рейда ──
RAID_JOIN_COUNT: int      = 3    # входов за окно → рейд
RAID_JOIN_WINDOW: int     = 10   # секунд
RAID_SPAM_USERS: int      = 3    # спамеров одновременно → рейд
RAID_SPAM_WINDOW: int     = 10   # секунд
RAID_SPAM_THRESHOLD: int  = 2    # сообщений от одного за окно → он «спамер»
STERILE_AUTO_HOURS: int   = 5    # часов авто-стерильного режима после рейда

# ── Параметры мута за спам стикерами/гифами ──
SPAM_MUTE_THRESHOLD: int  = 4    # стикеров/гифов за окно → мут
SPAM_MUTE_WINDOW: int     = 10   # секунд
SPAM_MUTE_MINUTES: int    = 30   # минут мута
