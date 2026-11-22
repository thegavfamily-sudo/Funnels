# ============================================================================
# config.py — подключение Python-бота к Supabase и Telegram
#
# Секреты НЕ хранятся в git. Читаются из окружения или из .env рядом с файлом.
#
# .env (не коммитить):
#   SUPABASE_URL=https://xxxxxxxx.supabase.co
#   SUPABASE_SERVICE_KEY=sb_secret_...
# ============================================================================

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Минимальный загрузчик .env без внешних зависимостей."""
    env_path = Path(__file__).resolve().parent / '.env'
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# URL проекта Supabase (не секрет): https://xxxxxxxx.supabase.co
SUPABASE_URL = os.environ.get(
    'SUPABASE_URL',
    'https://wbjzxljiwinddowwzxkf.supabase.co',
)

# service_role ключ — только из окружения / .env, не из git
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')

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
