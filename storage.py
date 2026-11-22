# ============================================================================
# storage.py — загрузка/кэши funnels/steps/functions/users из Supabase
#
# Порты из apps_script/БОТ_готовый.gs:
#   parseConfig-аналог для bool-флагов (Configuration-строка упразднена,
#   поля лежат в funnels.data): cache/typing/referral/autocolumns/
#   saveresponses/savestepprogress/systemmessages
#   cacheActiveFunnels        :2799
#   cacheStepsForActiveFunnels:2894
#   cacheSystemMessages       :3528
#
# Шаги индексируются в Step (step_model.build_step): строки старой
# таблицы парсятся один раз при загрузке, кэш steps_cache хранит объекты.
#
# Вместо ScriptProperties — только память; refresh() перечитывает всё
# целиком (периодически зовёт main.py).
# ============================================================================

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import config
from function_seed import merge_function_seeds
from step_model import Step, build_step, parse_step_number
from supabase_client import SupabaseClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Утилиты значений (порты Utils)
# ---------------------------------------------------------------------------

def parse_boolean(value: Any, default_value: bool = False) -> bool:
    """Аналог Utils.parseBoolean: true/1/yes/on, false/0/no/off."""
    if value is None or value == '':
        return default_value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).lower().strip()
    if text in ('true', '1', 'yes', 'on'):
        return True
    if text in ('false', '0', 'no', 'off'):
        return False
    return default_value


def parse_bot_flags(data: Any) -> dict:
    """Порт parseConfig для bool-полей funnels.data (Configuration больше нет).

    Дефолты как в оригинале (:186-192): cache/referral/autocolumns/
    saveresponses/savestepprogress/systemmessages=true, typing=false.
    Значение 'active' для cache (старый синтаксис) тоже считается True.
    """
    flags = {
        'cache': True,
        'referral': True,
        'autocolumns': True,
        'saveresponses': True,
        'savestepprogress': True,
        'systemmessages': True,
        'typing': False,
    }
    if isinstance(data, dict):
        for key, default in flags.items():
            if key in data and data[key] is not None:
                if key == 'cache' and isinstance(data[key], str) \
                        and data[key].strip().lower() == 'active':
                    flags[key] = True
                else:
                    flags[key] = parse_boolean(data[key], default)
    return flags


def extract_bot_id_from_token(token: Any) -> str:
    """Числовой префикс токена: '123456:AA-...' → '123456' (Utils :1105)."""
    if not token or not isinstance(token, str):
        return ''
    match = re.match(r'^(\d+):', token.strip())
    return match.group(1) if match else ''


def format_sheet_date(date: Optional[datetime] = None) -> str:
    """Формат старой Google-таблицы: YYYY-MM-DD HH:MM:SS (Utils :1061)."""
    dt = date or datetime.now()
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def iso_now() -> str:
    """ISO-таймстемп для data.last_updated (по спеке порта)."""
    return datetime.now().isoformat(timespec='seconds')


def is_funnel_served(funnel_row: dict) -> bool:
    """Условие обслуживания: token непустой AND lowercase(status) != 'archived'.

    Status управляет режимом, а не питанием бота (как Cache:Active/Inactive
    в оригинале): Active — ответы из кэша памяти, Inactive — бот работает,
    но перечитывает воронку из таблицы напрямую, Archived — не обслуживается.
    """
    token = str(funnel_row.get('token') or '').strip()
    if not token:
        return False
    data = funnel_row.get('data') or {}
    if not isinstance(data, dict):
        return False
    return str(data.get('status') or '').strip().lower() != 'archived'


def build_step_entry(row: dict) -> Optional[tuple]:
    """(funnel_id, step_number, Step) для строки steps.

    Одна индексация используется и refresh (наполнение кэшей), и режимом
    Inactive (прямое чтение таблицы шагов). None — строка не проходит.

    Здесь старая табличная строка ОДИН РАЗ конвертируется в Step
    (step_model.build_step): тип, переходы, опрос/квиз/кости/QR и
    клавиатуры разбираются при загрузке, а не при каждой отправке.
    """
    data = dict(row.get('data') or {})
    funnel_id = str(row.get('funnel_id') or '').strip() or \
        str(data.get('funnel_id') or '').strip()
    if not funnel_id:
        return None

    step_value = row.get('step')
    if step_value is None or step_value == '':
        step_value = data.get('step')
    step_number = parse_step_number(step_value)
    if step_number is None or step_number <= 0:
        return None

    return funnel_id, step_number, build_step(data, step_number, funnel_id)


def system_messages_from(entries: list) -> dict:
    """Ключи `${funnel_id}_${type}` → сообщение (cacheSystemMessages :3528)."""
    out: dict[str, str] = {}
    for entry in entries:
        data = entry.get('data') or {}
        row_type = str(data.get('type') or '').strip()
        row_name = str(data.get('function_name') or '').strip()
        row_message = str(data.get('message') or '').strip()
        row_funnel_id = str(entry.get('funnel_id') or '').strip()

        is_system = (
            row_type == 'Funnel system'
            or row_name in (config.SYSTEM_MESSAGE_INFO, config.SYSTEM_MESSAGE_FINAL)
            or (row_type == 'System'
                and row_name in (config.SYSTEM_MESSAGE_INFO,
                                 config.SYSTEM_MESSAGE_FINAL))
        )
        if row_message and (is_system or row_type == 'Funnel system'):
            if row_type == 'Funnel system':
                out[f'{row_funnel_id}_Funnel system'] = row_message
            if row_name:
                out[f'{row_funnel_id}_{row_name}'] = row_message
                out[f'{row_funnel_id}_{row_type}'] = row_message
    return out


# ---------------------------------------------------------------------------
# BotContext — контекст одного бота (замена currentBotToken/… из .gs)
# ---------------------------------------------------------------------------

@dataclass
class BotContext:
    bot_id: str
    token: str
    funnel_id: str
    funnel_name: str
    config: dict                      # bool-флаги (parse_bot_flags)
    storage: 'Storage'
    tg: Any = None                    # TelegramAPI, подставляет main.py
    bot_username: Optional[str] = None
    menu: dict = field(default_factory=dict)  # синяя кнопка меню (menu_button*)

    def spec_key(self) -> tuple:
        """Ключ для решения «перезапустить ли поток бота» при refresh."""
        return (self.token, self.funnel_id,
                tuple(sorted(self.config.items())),
                tuple(sorted(self.menu.items())))


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class Storage:
    def __init__(self, client: SupabaseClient):
        self.client = client
        self._lock = threading.RLock()

        # Табличные кэши (перестраиваются в refresh)
        self.active_funnels: dict[str, dict] = {}       # bot_id → {…}
        self.steps_cache: dict[str, Step] = {}          # "funnel_step" → Step
        self.system_messages_cache: dict[str, str] = {}
        self.funnel_names: dict[str, str] = {}          # funnel_id → name
        self.functions_rows: list[dict] = []            # {id, funnel_id, data}
        self.cache_timestamp: float = 0.0
        # Status Inactive: воронки, чьи данные читаем из таблицы, без кэша
        self._direct_funnels: set[str] = set()

        # Юзеры: read-through dict по (funnel_id, user_id)
        self._users: dict[tuple, Optional[dict]] = {}

        # Игровые кэши (TTL 5 мин — как в оригинале)
        self.dice_results: dict[Any, dict] = {}
        self.poll_results: dict[str, dict] = {}
        self.quiz_results: dict[str, dict] = {}

    # -- refresh ------------------------------------------------------------

    def refresh(self) -> None:
        """Перечитать funnels/steps/functions и перестроить все кэши."""
        try:
            funnels = self.client.select('funnels')
        except Exception as error:  # noqa: BLE001
            logger.error('[refresh] Ошибка чтения funnels: %s', error)
            return

        active: dict[str, dict] = {}
        funnel_names: dict[str, str] = {}
        direct: set[str] = set()
        for row in funnels:
            data = row.get('data') or {}
            funnel_id = str(row.get('id') or '').strip()
            if not funnel_id:
                continue
            name = str(data.get('funnel_name') or '').strip() or f'Funnel {funnel_id}'
            funnel_names[funnel_id] = name
            if not is_funnel_served(row):
                continue
            token = str(row.get('token') or '').strip()
            bot_id = extract_bot_id_from_token(token)
            if not bot_id:
                logger.error('[refresh] Invalid token for funnel %s', funnel_id)
                continue
            flags = parse_bot_flags(data)
            if str(data.get('status') or '').strip().lower() == 'inactive':
                direct.add(funnel_id)
            active[bot_id] = {
                'funnel_id': funnel_id,
                'funnel_name': name,
                'token': token,
                'config': flags,
                'menu': {
                    'type': str(data.get('menu_button_type') or '').strip(),
                    'value': str(data.get('menu_button') or '').strip(),
                    'url': str(data.get('menu_button_command') or '').strip(),
                },
            }
            logger.info('[refresh] Loaded bot %s: funnelId=%s typing=%s',
                        bot_id, funnel_id, flags['typing'])

        try:
            steps_rows = self.client.select('steps')
        except Exception as error:  # noqa: BLE001
            logger.error('[refresh] Ошибка чтения steps: %s', error)
            steps_rows = []

        active_funnel_ids = {info['funnel_id'] for info in active.values()}
        steps_cache: dict[str, 'Step'] = {}

        for row in steps_rows:
            entry = build_step_entry(row)
            if entry is None:
                continue
            row_funnel_id, step_number, step = entry
            if row_funnel_id not in active_funnel_ids:
                continue
            steps_cache[f'{row_funnel_id}_{step_number}'] = step

        # Functions + системные сообщения (cacheSystemMessages :3528)
        try:
            functions_rows = self.client.select('functions')
        except Exception as error:  # noqa: BLE001
            logger.error('[refresh] Ошибка чтения functions: %s', error)
            functions_rows = []

        normalized_functions: list[dict] = []
        for row in functions_rows:
            data = row.get('data') or {}
            normalized_functions.append({
                'id': str(row.get('id') or ''),
                'funnel_id': str(row.get('funnel_id') or '').strip(),
                'data': data,
            })

        # Сид системных функций (function_seed.py): если в Supabase их нет —
        # подставляем, чтобы бот работал и без отдельной вставки в базу.
        normalized_functions = merge_function_seeds(normalized_functions)
        system_messages = system_messages_from(normalized_functions)

        with self._lock:
            self.active_funnels = active
            self.steps_cache = steps_cache
            self.system_messages_cache = system_messages
            self.funnel_names = funnel_names
            self.functions_rows = normalized_functions
            self._direct_funnels = direct
            self.cache_timestamp = time.time()

        self.cleanup_game_caches()
        logger.info('[refresh] %d bots, %d steps, %d system messages',
                    len(active), len(steps_cache), len(system_messages))

    # -- accessors ----------------------------------------------------------

    def get_active_bot(self, bot_id: str) -> Optional[dict]:
        return self.active_funnels.get(bot_id)

    def get_funnel_name(self, funnel_id: str) -> str:
        return self.funnel_names.get(funnel_id, f'Funnel {funnel_id}')

    def get_step(self, funnel_id: str, step_number: int) -> Optional[Step]:
        return self.steps_cache.get(f'{funnel_id}_{step_number}')

    def get_system_message(self, funnel_id: str, message_type: str) -> str:
        cached = self.system_messages_cache.get(f'{funnel_id}_{message_type}')
        if cached is not None:
            return cached
        # Фолбэк getSystemMessageFromSheet (:843): Type ∈ {Funnel system, System},
        # имя совпадает, Funnel id пустой или текущий.
        for entry in self.functions_rows:
            data = entry['data']
            row_type = str(data.get('type') or '').strip()
            row_name = str(data.get('function_name') or '').strip()
            row_message = str(data.get('message') or '').strip()
            if row_type in ('Funnel system', 'System') and row_name == message_type:
                if entry['funnel_id'] in ('', funnel_id):
                    return row_message
        return ''

    # -- режим Inactive: прямое чтение из таблицы --------------------------

    def is_direct(self, funnel_id: str) -> bool:
        """True — статус Inactive: данные воронки читаем из Supabase,
        а не из кэша памяти (как Cache:Inactive в оригинале)."""
        with self._lock:
            return funnel_id in self._direct_funnels

    def refresh_if_direct(self, funnel_id: str) -> None:
        """Перед обработкой апдейта: для Inactive-воронки перечитать шаги,
        клавиатуры и системные сообщения напрямую из таблицы."""
        if not funnel_id or not self.is_direct(funnel_id):
            return
        self.reload_funnel(funnel_id)

    def reload_funnel(self, funnel_id: str) -> None:
        """Заменить кэш воронки её свежими строками из Supabase."""
        try:
            steps_rows = self.client.select(
                'steps', filters={'funnel_id': f'eq.{funnel_id}'})
            functions_rows = self.client.select(
                'functions', filters={'funnel_id': f'eq.{funnel_id}'})
        except Exception as error:
            # Сеть недоступна — отвечаем из прошлого кэша, а не падаем.
            logger.error('[direct] Ошибка чтения воронки %s: %s', funnel_id, error)
            return

        prefix = f'{funnel_id}_'
        new_steps: dict[str, Step] = {}
        for row in steps_rows:
            entry = build_step_entry(row)
            if entry is None:
                continue
            row_fid, step_number, step = entry
            if row_fid != funnel_id:
                continue
            new_steps[f'{row_fid}_{step_number}'] = step

        new_functions = [{
            'id': str(row.get('id') or ''),
            'funnel_id': funnel_id,
            'data': row.get('data') or {},
        } for row in functions_rows]
        new_messages = system_messages_from(new_functions)

        def without_funnel(cache: dict) -> dict:
            # Префикс со знаком подчёркивания: 'f1_' не захватит 'f10_…'
            return {k: v for k, v in cache.items() if not k.startswith(prefix)}

        with self._lock:
            self.steps_cache = {**without_funnel(self.steps_cache), **new_steps}
            self.system_messages_cache = {
                **without_funnel(self.system_messages_cache), **new_messages}
            self.functions_rows = [
                e for e in self.functions_rows if e['funnel_id'] != funnel_id
            ] + new_functions

    # -- юзеры (read-through) ----------------------------------------------

    def get_user(self, funnel_id: str, user_id: Any) -> Optional[dict]:
        key = (funnel_id, str(user_id))
        with self._lock:
            if key in self._users:
                return self._users[key]
        try:
            row = self.client.select_one(
                'funnel_users',
                filters={'funnel_id': f'eq.{funnel_id}', 'user_id': f'eq.{user_id}'},
            )
        except Exception as error:  # noqa: BLE001
            logger.error('[get_user] %s/%s: %s', funnel_id, user_id, error)
            return None
        row = self._normalize_user_row(row)
        with self._lock:
            self._users[key] = row
        return row

    @staticmethod
    def _normalize_user_row(row: Optional[dict]) -> Optional[dict]:
        if not row:
            return None
        try:
            row['current_step'] = int(row.get('current_step') or 0)
        except (TypeError, ValueError):
            row['current_step'] = 0
        if not isinstance(row.get('data'), dict):
            row['data'] = {}
        return row

    def put_user(self, funnel_id: str, user_id: Any, row: Optional[dict]) -> None:
        key = (funnel_id, str(user_id))
        with self._lock:
            self._users[key] = self._normalize_user_row(row)

    def save_user_row(self, funnel_id: str, user_id: Any, current_step: int,
                      data: dict) -> None:
        """Запись строки юзера в Supabase + мгновенное обновление кэша."""
        row = {
            'funnel_id': funnel_id,
            'user_id': str(user_id),
            'current_step': int(current_step),
            'data': data,
        }
        try:
            self.client.upsert('funnel_users', [row])
        except Exception as error:  # noqa: BLE001
            logger.error('[save_user_row] %s/%s: %s', funnel_id, user_id, error)
        self.put_user(funnel_id, user_id, row)

    # -- игровые кэши -------------------------------------------------------

    @staticmethod
    def _cleanup(cache: dict, now_ms: float) -> None:
        ttl = config.SHORT_CACHE_TTL_SECONDS * 1000
        expired = [k for k, v in cache.items()
                   if isinstance(v, dict) and now_ms - v.get('timestamp', 0) > ttl]
        for key in expired:
            del cache[key]

    def cleanup_game_caches(self) -> None:
        now_ms = time.time() * 1000
        with self._lock:
            self._cleanup(self.dice_results, now_ms)
            self._cleanup(self.poll_results, now_ms)
            self._cleanup(self.quiz_results, now_ms)
