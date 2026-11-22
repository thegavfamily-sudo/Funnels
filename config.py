# ============================================================================
# config.py — подключение Python-бота к Supabase и Telegram
#
# КАК ЗАПОЛНИТЬ:
#   1) Supabase → Project Settings → API
#   2) Project URL        → SUPABASE_URL
#   3) service_role key   → SUPABASE_SERVICE_KEY
#      (anon key тоже сработает — RLS в схеме выключен, но service_role
#       правильнее для серверного скрипта)
#
# Значения ниже — ЗАГЛУШКИ. Замени на свои перед запуском.
# ============================================================================

# URL проекта Supabase: https://xxxxxxxx.supabase.co
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"

# service_role ключ (Secret) из Project Settings → API
SUPABASE_SERVICE_KEY = "YOUR-SERVICE-ROLE-KEY"

# ---------------------------------------------------------------------------
# Настройки бота
# ---------------------------------------------------------------------------

# Как часто перечитывать воронки/шаги из Supabase (секунды).
# Изменения, сделанные в приложении, подхватит бот не позже чем через это время.
SYNC_INTERVAL_SECONDS = 20

# TTL кэшей в памяти (секунды) — аналог 24-часового кэша ScriptProperties.
CACHE_TTL_SECONDS = 24 * 60 * 60

# TTL кэшей подписок и результатов игр (секунды) — как в оригинале (5 минут).
SHORT_CACHE_TTL_SECONDS = 5 * 60

# Timeout long polling для getUpdates (секунды).
POLL_TIMEOUT_SECONDS = 30

# ===========================================================================
# Константы бота — порт CONFIG из apps_script/БОТ_готовый.gs
# ===========================================================================

MEDIA_TYPES = {
    'photo', 'video', 'audio', 'document', 'voice', 'video_note',
    'animation', 'media_group', 'sticker', 'poll', 'quiz', 'dice', 'barcode',
}

VALID_CHAT_ACTIONS = {
    'typing', 'upload_photo', 'upload_video', 'upload_audio',
    'upload_document', 'upload_video_note', 'choose_sticker',
    'find_location', 'record_video', 'record_video_note', 'record_voice',
}

KEYBOARD_TYPES = {'text', 'contact', 'location', 'command', 'url'}
KEYBOARD_ACTIONS = {'keep', 'hide', 'remove', 'delete'}

DICE_TYPES = {'🎲', '🎯', '🏀', '⚽', '🎳', '🎰'}

SLOT_MAPPING = {1: '🍒🍒🍒', 2: '🍋🍋🍋', 3: '🔔🔔🔔', 4: '7️⃣7️⃣7️⃣'}

SYMBOL_TO_NUMBER = {'🍒🍒🍒': 1, '🍋🍋🍋': 2, '🔔🔔🔔': 3, '7️⃣7️⃣7️⃣': 4}

SAME_SYMBOLS_VALUES = {
    '🍒🍒🍒': [22],
    '🍋🍋🍋': [43],
    '🔔🔔🔔': [1],
    '7️⃣7️⃣7️⃣': [64],
}

EFFECT_TAGS = {'countdown', 'progress', 'spinner', 'blink', 'reveal'}

SYSTEM_MESSAGE_INFO = 'Funnel info message'
SYSTEM_MESSAGE_FINAL = 'Funnel final message'

INLINE_BUTTON_TYPES = {
    'callback', 'url', 'web app', 'switch inline', 'check channel',
}

INLINE_KEYBOARD_METHODS = {'step by step', 'update step'}

CALLBACK_PREFIXES = {
    'check_sub': 'check_sub_',
    'step': 'step_',
    'cmd': 'cmd_',
}

USERS_FIXED_COLUMNS = [
    'Date', 'Funnel name', 'User id', 'Username', 'First name',
    'Completed at', 'Last Updated',
]

REFERRAL_COLUMNS = [
    'Referral link', 'Referral follower', 'Referral source', 'Referral creative',
]

BARCODE_CONFIG = {
    'BASE_URL': 'https://quickchart.io/qr',
    'DEFAULT_SIZE': 300,
    'DEFAULT_MARGIN': 4,
    'DEFAULT_FG_COLOR': '000000',
    'DEFAULT_BG_COLOR': 'ffffff',
    'DEFAULT_LOGO_URL': '',
    'DEFAULT_LOGO_SIZE_RATIO': 0.3,
    'DEFAULT_EC_LEVEL': 'M',
    'DEFAULT_FORMAT': 'png',
}

# Фолбэк-имя бота для реферал-ссылок (как в оригинале).
DEFAULT_BOT_USERNAME = 'Constructortel_bot'
