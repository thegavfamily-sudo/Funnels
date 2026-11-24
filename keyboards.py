# ============================================================================
# keyboards.py — парсинг/сборка regular и inline клавиатур
#
# Формат хранения — только JSON (разделителей '|' нет):
#   inline:  [[{text,type,value,method,action,step,delay}, …], …]
#   regular: {buttons: [[{text,type,command,role,step}, …], …],
#             settings: {resize_keyboard, …}}
# Значение может прийти структурой (list/dict из jsonb) или JSON-строкой —
# парсер принимает оба варианта.
#
# Порты из apps_script/БОТ_готовый.gs:
#   buildRegularKeyboardFromParsedData:3135
#   buildInlineKeyboardFromParsedData :3310
#   Utils.extractCallbackParams       :902
#   removeButtonFromKeyboard          :4516
#   processButtonAction               :4547
# ============================================================================

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)

# Список типов при парсинге (:3251) — включает 'command', в отличие от
# CONFIG.INLINE_BUTTON_TYPES (в оригинале списки различаются).
_INLINE_PARSE_TYPES = (
    'callback', 'url', 'web app', 'switch inline', 'check channel',
    'command', 'purchase',
)


def _parse_int_js(value: str) -> Optional[int]:
    """Аналог JS parseInt: берёт ведущее целое, иначе None."""
    match = re.match(r'^\s*([+-]?\d+)', str(value))
    if not match:
        return None
    return int(match.group(1))


# ---------------------------------------------------------------------------
# Regular (reply) keyboard
# ---------------------------------------------------------------------------

def _normalize_regular_button(raw: Any) -> Optional[dict]:
    """Кнопка нового формата → внутренний вид (дефолты как в legacy-парсе)."""
    if not isinstance(raw, dict):
        return None
    text = str(raw.get('text') or '').strip() or 'Кнопка'
    type_value = str(raw.get('type') or 'text').strip().lower() or 'text'
    button = {
        'text': text,
        'type': type_value if type_value in config.KEYBOARD_TYPES else 'text',
        'command': str(raw.get('command') or '').strip(),
        'role': str(raw.get('role') or '').strip().lower(),
        'step': 0,
    }
    step = raw.get('step')
    if isinstance(step, (int, float)) and not isinstance(step, bool) \
            and step > 0:
        button['step'] = int(step)
    elif str(step or '').strip():
        parsed = _parse_int_js(step)
        if parsed is not None and parsed > 0:
            button['step'] = parsed
    return button


def _regular_from_json(data: dict) -> Optional[dict]:
    """Новый формат {buttons, settings} → parsedData (та же форма, что legacy)."""
    settings = data.get('settings')
    result: dict[str, Any] = {
        'buttons': [],
        'settings': dict(settings) if isinstance(settings, dict) else {},
    }
    rows = data.get('buttons')
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, list):
            continue
        row_buttons = [b for b in (_normalize_regular_button(x) for x in row)
                       if b]
        if row_buttons:
            result['buttons'].append(row_buttons)
    return result if result['buttons'] else None


def parse_regular_keyboard_data(regular_keyboard: Any) -> Optional[dict]:
    """Клавиатура шага: JSON-формат {buttons, settings} (dict или JSON-строка)."""
    try:
        if isinstance(regular_keyboard, dict):
            return _regular_from_json(regular_keyboard)
        raw_str = str(regular_keyboard or '').strip()
        if not raw_str:
            return None
        parsed = json.loads(raw_str)
        if not isinstance(parsed, dict):
            return None
        return _regular_from_json(parsed)
    except (ValueError, TypeError) as error:
        logger.warning('[parseRegularKeyboardData] не JSON: %s', error)
        return None
    except Exception as error:  # noqa: BLE001
        logger.error('[parseRegularKeyboardData] Error: %s', error)
        return None


def build_regular_keyboard_from_parsed_data(parsed_data: Optional[dict],
                                            user_role: str = '') -> Optional[dict]:
    try:
        if not parsed_data or not parsed_data.get('buttons'):
            return None

        keyboard: list[list[dict]] = []
        has_buttons = False

        for row in parsed_data['buttons']:
            row_buttons = []
            for button_data in row:
                role = button_data.get('role') or ''
                if role and role != '' and user_role != role:
                    continue

                button_text = button_data.get('text', '')
                button_type = button_data.get('type', 'text')
                button_command = button_data.get('command', '')

                button: dict[str, Any] = {'text': button_text}

                if button_type == 'contact':
                    button['request_contact'] = True
                elif button_type == 'location':
                    button['request_location'] = True
                elif button_type == 'command':
                    button['callback_data'] = button_command or button_text
                elif button_type == 'url':
                    url = button_command
                    if url and not url.startswith('http'):
                        url = f'https://{url}'
                    button['url'] = url or 'https://example.com'
                else:  # text / default
                    button['callback_data'] = button_text

                row_buttons.append(button)

            if row_buttons:
                keyboard.append(row_buttons)
                has_buttons = True

        if not has_buttons:
            return None

        reply_markup: dict[str, Any] = {'keyboard': keyboard}
        reply_markup.update(parsed_data.get('settings') or {})

        if not reply_markup.get('resize_keyboard') and not reply_markup.get('remove_keyboard'):
            reply_markup['resize_keyboard'] = True

        return {'reply_markup': reply_markup}
    except Exception as error:  # noqa: BLE001
        logger.error('[buildRegularKeyboardFromParsedData] Error: %s', error)
        return None


# ---------------------------------------------------------------------------
# Inline keyboard
# ---------------------------------------------------------------------------

def _normalize_inline_button(raw: Any) -> Optional[dict]:
    """Кнопка нового формата → внутренний вид (дефолты как в legacy-парсе)."""
    if not isinstance(raw, dict):
        return None
    text = str(raw.get('text') or '').strip() or 'Кнопка'
    type_value = str(raw.get('type') or 'callback').strip().lower()
    button: dict[str, Any] = {
        'text': text,
        'type': type_value if type_value in _INLINE_PARSE_TYPES else 'callback',
        'step': 0,
        'method': 'step by step',
        'action': 'keep',
    }
    value = raw.get('value')
    if value is not None and str(value).strip() != '':
        button['value'] = str(value).strip()
    step = raw.get('step')
    if isinstance(step, (int, float)) and not isinstance(step, bool) \
            and step > 0:
        button['step'] = int(step)
    elif str(step or '').strip():
        parsed = _parse_int_js(step)
        if parsed is not None and parsed > 0:
            button['step'] = parsed
    # Старые записи callback/check channel хранили шаг только в value
    # («Шаг (номер)» / «Шаг после проверки») — берём его как step.
    if button['step'] == 0 and type_value in ('callback', 'check channel') \
            and str(value or '').strip():
        legacy = _parse_int_js(str(value).strip())
        if legacy is not None and legacy > 0:
            button['step'] = legacy
    method = str(raw.get('method') or '').strip().lower()
    if method in ('update step', 'update'):
        button['method'] = 'update step'
    action = str(raw.get('action') or '').strip().lower()
    if action in ('hide', 'remove', 'delete', 'keep'):
        button['action'] = action
    delay = raw.get('delay')
    if isinstance(delay, (int, float)) and not isinstance(delay, bool) \
            and delay > 0:
        button['delay'] = int(delay)
    return button


def _inline_from_json(rows: Any) -> Optional[dict]:
    """Новый формат [[кнопка,…],…] → parsedData (та же форма, что legacy)."""
    if not isinstance(rows, list):
        return None
    result: dict[str, Any] = {'buttons': []}
    for row in rows:
        if not isinstance(row, list):
            continue
        row_buttons = [b for b in (_normalize_inline_button(x) for x in row)
                       if b]
        if row_buttons:
            result['buttons'].append(row_buttons)
    return result if result['buttons'] else None


def parse_inline_keyboard_data(inline_keyboard: Any) -> Optional[dict]:
    """Клавиатура шага: JSON-формат [[кнопка,…],…] (list или JSON-строка)."""
    try:
        if isinstance(inline_keyboard, list):
            return _inline_from_json(inline_keyboard)
        raw_str = str(inline_keyboard or '').strip()
        if not raw_str:
            return None
        parsed = json.loads(raw_str)
        if not isinstance(parsed, list):
            return None
        return _inline_from_json(parsed)
    except (ValueError, TypeError) as error:
        logger.warning('[parseInlineKeyboardData] не JSON: %s', error)
        return None
    except Exception as error:  # noqa: BLE001
        logger.error('[parseInlineKeyboardData] Error: %s', error)
        return None


def build_inline_keyboard_from_parsed_data(parsed_data: Optional[dict],
                                           channel: Any = None) -> Optional[dict]:
    try:
        if not parsed_data or not parsed_data.get('buttons'):
            return None

        inline_keyboard_array: list[list[dict]] = []

        for row_index, row in enumerate(parsed_data['buttons']):
            row_buttons = []
            for col_index, button_data in enumerate(row):
                button_text = button_data.get('text', '')
                button_type = button_data.get('type', 'callback')
                step_number = button_data.get('step', 0)
                action = button_data.get('action', 'keep')
                # payload из формата приложения: URL / команда / текст запроса;
                # в старом формате payload лежит в самом text — фолбэк на него.
                # '0' — заглушка пустого value из приложения (см. serializeInto).
                payload = str(button_data.get('value') or '').strip()
                if payload == '0':
                    payload = ''

                if button_type == 'url':
                    url = payload or button_text
                    if url and not url.startswith('http'):
                        url = f'https://{url}'
                    button = {'text': button_text, 'url': url or 'https://example.com'}
                elif button_type == 'web app':
                    web_app_url = payload or button_text
                    if web_app_url and not web_app_url.startswith('http'):
                        web_app_url = f'https://{web_app_url}'
                    button = {'text': button_text,
                              'web_app': {'url': web_app_url or 'https://example.com'}}
                elif button_type == 'switch inline':
                    button = {'text': button_text,
                              'switch_inline_query': payload or button_text or ''}
                elif button_type == 'check channel':
                    if channel and str(channel).strip() != '':
                        callback_data = (
                            f"{config.CALLBACK_PREFIXES['check_sub']}{step_number}"
                            f"_row_{row_index}_col_{col_index}_action_{action}"
                            f"_channel_{channel}"
                        )
                        button = {'text': button_text, 'callback_data': callback_data}
                    else:
                        button = {'text': button_text,
                                  'callback_data': (
                                      f'step_{step_number}_row_{row_index}'
                                      f'_col_{col_index}_action_{action}')}
                elif button_type == 'command':
                    cmd_source = payload or button_text
                    cmd_value = cmd_source[1:] if cmd_source.startswith('/') else cmd_source
                    # step > 0 — после execute_function перейти на шаг.
                    step_after = (
                        f'_step_{step_number}' if step_number > 0 else '')
                    button = {'text': button_text,
                              'callback_data': (
                                  f'cmd_{cmd_value}_row_{row_index}'
                                  f'_col_{col_index}_action_{action}'
                                  f'{step_after}')}
                elif button_type == 'purchase':
                    # Цена и целевой шаг читаются из parsedData по row/col
                    # (как method/delay) — callback несёт только позицию.
                    button = {'text': button_text,
                              'callback_data': (
                                  f'buy_row_{row_index}'
                                  f'_col_{col_index}_action_{action}')}
                else:  # callback / default
                    button = {'text': button_text,
                              'callback_data': (
                                  f'step_{step_number}_row_{row_index}'
                                  f'_col_{col_index}_action_{action}')}

                row_buttons.append(button)

            if row_buttons:
                inline_keyboard_array.append(row_buttons)

        if not inline_keyboard_array:
            return None

        return {'inline_keyboard': inline_keyboard_array}
    except Exception as error:  # noqa: BLE001
        logger.error('[buildInlineKeyboardFromParsedData] Error: %s', error)
        return None


# ---------------------------------------------------------------------------
# Callback data
# ---------------------------------------------------------------------------

def extract_callback_params(callback_data: str) -> dict:
    params: dict[str, str] = {}
    if not callback_data:
        return params

    if callback_data.startswith(config.CALLBACK_PREFIXES['check_sub']):
        data_without_prefix = callback_data[len(config.CALLBACK_PREFIXES['check_sub']):]
        parts = data_without_prefix.split('_')

        step = row = col = action = channel = None
        for i, part in enumerate(parts):
            if part == 'row' and i + 1 < len(parts):
                row = parts[i + 1]
            elif part == 'col' and i + 1 < len(parts):
                col = parts[i + 1]
            elif part == 'action' and i + 1 < len(parts):
                action = parts[i + 1]
            elif part == 'channel' and i + 1 < len(parts):
                channel = '_'.join(parts[i + 1:])
                break
            elif i == 0 and part.isdigit():
                step = part

        if step is not None:
            params['check'] = step
        if row is not None:
            params['row'] = row
        if col is not None:
            params['col'] = col
        if action is not None:
            params['action'] = action
        if channel is not None:
            params['channel'] = channel

    elif callback_data.startswith(config.CALLBACK_PREFIXES['step']):
        data_without_prefix = callback_data[len(config.CALLBACK_PREFIXES['step']):]
        parts = data_without_prefix.split('_')
        for i, part in enumerate(parts):
            if part == 'row' and i + 1 < len(parts):
                params['row'] = parts[i + 1]
            elif part == 'col' and i + 1 < len(parts):
                params['col'] = parts[i + 1]
            elif part == 'action' and i + 1 < len(parts):
                params['action'] = parts[i + 1]
            elif i == 0 and part.isdigit():
                params['step'] = part

    elif callback_data.startswith(config.CALLBACK_PREFIXES['buy']):
        data_without_prefix = callback_data[len(config.CALLBACK_PREFIXES['buy']):]
        parts = data_without_prefix.split('_')
        for i, part in enumerate(parts):
            if part == 'row' and i + 1 < len(parts):
                params['row'] = parts[i + 1]
            elif part == 'col' and i + 1 < len(parts):
                params['col'] = parts[i + 1]
            elif part == 'action' and i + 1 < len(parts):
                params['action'] = parts[i + 1]

    elif callback_data.startswith(config.CALLBACK_PREFIXES['cmd']):
        data_without_prefix = callback_data[len(config.CALLBACK_PREFIXES['cmd']):]
        parts = data_without_prefix.split('_')
        for i, part in enumerate(parts):
            if part == 'row' and i + 1 < len(parts):
                params['row'] = parts[i + 1]
            elif part == 'col' and i + 1 < len(parts):
                params['col'] = parts[i + 1]
            elif part == 'action' and i + 1 < len(parts):
                params['action'] = parts[i + 1]
            elif part == 'step' and i > 0 and i + 1 < len(parts):
                # Целевой шаг после вызова функции (хвост callback).
                # i > 0: сам cmd может называться «step».
                params['step'] = parts[i + 1]
            elif i == 0:
                params['cmd'] = part

    return params


# ---------------------------------------------------------------------------
# Действия над inline-кнопкой после нажатия (keep/hide/remove/delete)
# ---------------------------------------------------------------------------

def remove_button_from_keyboard(original_keyboard: Optional[dict],
                                row_index: int, col_index: int) -> Optional[dict]:
    try:
        new_keyboard = copy.deepcopy(original_keyboard)

        if not new_keyboard or 'inline_keyboard' not in new_keyboard:
            return original_keyboard

        if row_index >= len(new_keyboard['inline_keyboard']):
            return original_keyboard

        row = new_keyboard['inline_keyboard'][row_index]
        if col_index >= len(row):
            return original_keyboard

        del row[col_index]

        new_keyboard['inline_keyboard'] = [
            r for r in new_keyboard['inline_keyboard'] if len(r) > 0
        ]

        if not new_keyboard['inline_keyboard']:
            return None

        return new_keyboard
    except Exception:  # noqa: BLE001
        return original_keyboard


def process_button_action(tg, keyboard_data: Optional[dict], chat_id, message_id: int,
                          action: Optional[str], row: int, col: int) -> bool:
    """Применить действие keep/hide/remove/delete к сообщению с inline-клавиатурой."""
    try:
        action = action or 'keep'

        if action == 'hide':
            if not keyboard_data or not keyboard_data.get('inlineKeyboard'):
                return False
            new_keyboard = remove_button_from_keyboard(
                keyboard_data['inlineKeyboard'], row, col)
            if new_keyboard is None:
                tg.edit_message_reply_markup(chat_id, message_id, None)
            else:
                tg.edit_message_reply_markup(chat_id, message_id, new_keyboard)
        elif action == 'remove':
            tg.edit_message_reply_markup(chat_id, message_id, None)
        elif action == 'delete':
            tg.delete_message(chat_id, message_id)
        # 'keep' и default — ничего не делаем

        return True
    except Exception:  # noqa: BLE001
        return False
