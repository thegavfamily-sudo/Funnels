# ============================================================================
# keyboards.py — парсинг/сборка regular и inline клавиатур
#
# Порты из apps_script/БОТ_готовый.gs:
#   parseRegularKeyboardData          :3044
#   buildRegularKeyboardFromParsedData:3135
#   parseInlineKeyboardData           :3216
#   buildInlineKeyboardFromParsedData :3310
#   Utils.extractCallbackParams       :902
#   removeButtonFromKeyboard          :4516
#   processButtonAction               :4547
#
# Форматы строк (НЕ менять):
#   regular: строки через '||', кнопки через ',', поля через '|':
#            text|type|command|role; хвост 'action:...' — настройки
#   inline:  строки через '||', кнопки через ',', поля через '|':
#            text|type|step|method|action|points|role
# ============================================================================

from __future__ import annotations

import copy
import logging
import re
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)

# Список типов при парсинге (:3251) — включает 'command', в отличие от
# CONFIG.INLINE_BUTTON_TYPES (в оригинале списки различаются).
_INLINE_PARSE_TYPES = (
    'callback', 'url', 'web app', 'switch inline', 'check channel',
    'command',
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

def parse_regular_keyboard_data(regular_keyboard_str: Any) -> Optional[dict]:
    try:
        if not regular_keyboard_str or str(regular_keyboard_str).strip() == '':
            return None

        parts = [p.strip() for p in str(regular_keyboard_str).split('||')]
        parts = [p for p in parts if p != '']

        settings_part = ''
        buttons_parts: list[str] = []

        if parts:
            last_part = parts[-1]
            if last_part.startswith('action:'):
                settings_part = last_part.replace('action:', '', 1).strip()
                buttons_parts = parts[:-1]
            else:
                buttons_parts = parts

        result: dict[str, Any] = {'buttons': [], 'settings': {}}

        if settings_part:
            for setting in (s.strip().lower() for s in settings_part.split(',')):
                if setting == 'resize':
                    result['settings']['resize_keyboard'] = True
                elif setting == 'one_time':
                    result['settings']['one_time_keyboard'] = True
                elif setting == 'selective':
                    result['settings']['selective'] = True
                elif setting.startswith('placeholder='):
                    result['settings']['input_field_placeholder'] = \
                        setting.replace('placeholder=', '', 1)

        for row in buttons_parts:
            buttons = [b.strip() for b in row.split(',')]
            buttons = [b for b in buttons if b != '']
            row_buttons = []

            for button_str in buttons:
                button_parts = [p.strip() for p in button_str.split('|')]
                if len(button_parts) < 1:
                    continue

                button_data: dict[str, Any] = {
                    'text': button_parts[0] or 'Кнопка',
                    'type': 'text',
                    'command': '',
                    'role': '',
                }

                if len(button_parts) > 1 and button_parts[1]:
                    type_value = button_parts[1].lower()
                    if type_value in config.KEYBOARD_TYPES:
                        button_data['type'] = type_value

                if len(button_parts) > 2 and button_parts[2]:
                    button_data['command'] = button_parts[2]

                if len(button_parts) > 3 and button_parts[3]:
                    button_data['role'] = button_parts[3].lower()

                row_buttons.append(button_data)

            if row_buttons:
                result['buttons'].append(row_buttons)

        return result if result['buttons'] else None
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

def parse_inline_keyboard_data(inline_keyboard_str: Any) -> Optional[dict]:
    try:
        if not inline_keyboard_str or str(inline_keyboard_str).strip() == '':
            return None

        rows = [r.strip() for r in str(inline_keyboard_str).split('||')]
        rows = [r for r in rows if r != '']
        result: dict[str, Any] = {'buttons': []}

        for row in rows:
            buttons = [b.strip() for b in row.split(',')]
            buttons = [b for b in buttons if b != '']
            row_buttons = []

            for button_str in buttons:
                parts = [p.strip() for p in button_str.split('|')]
                if len(parts) < 1:
                    continue

                button_data: dict[str, Any] = {
                    'text': parts[0] or 'Кнопка',
                    'type': 'callback',
                    'step': 0,
                    'method': 'step by step',
                    'action': 'keep',
                    'points': 0,
                    'role': '',
                }

                param_index = 1
                if len(parts) > param_index and parts[param_index]:
                    type_value = parts[param_index].lower()
                    if type_value in _INLINE_PARSE_TYPES:
                        button_data['type'] = type_value
                        param_index += 1

                # parts[2] — «step»: номер шага (callback/check channel) ИЛИ
                # payload приложения: URL (url/web app), текст (switch inline),
                # команда (command). Сохраняем и как строку, и как число.
                if len(parts) > param_index:
                    raw_value = parts[param_index]
                    param_index += 1
                    if raw_value:
                        button_data['value'] = raw_value
                        step_value = _parse_int_js(raw_value)
                        if step_value is not None and step_value > 0:
                            button_data['step'] = step_value

                if len(parts) > param_index:
                    method_value = parts[param_index].lower()
                    param_index += 1
                    if method_value in ('update step', 'update'):
                        button_data['method'] = 'update step'
                    else:
                        button_data['method'] = 'step by step'

                if len(parts) > param_index:
                    action_value = parts[param_index].lower()
                    param_index += 1
                    if action_value in ('hide', 'remove', 'delete', 'keep'):
                        button_data['action'] = action_value

                if len(parts) > param_index:
                    points_value = _parse_int_js(parts[param_index])
                    param_index += 1
                    if points_value is not None and points_value > 0:
                        button_data['points'] = points_value

                if len(parts) > param_index and parts[param_index]:
                    button_data['role'] = parts[param_index].lower()

                row_buttons.append(button_data)

            if row_buttons:
                result['buttons'].append(row_buttons)

        return result if result['buttons'] else None
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
                    button = {'text': button_text,
                              'callback_data': (
                                  f'cmd_{cmd_value}_row_{row_index}'
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
