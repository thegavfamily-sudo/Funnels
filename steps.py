# ============================================================================
# steps.py — StepManager + sendStepMessage/updateStepMessage + медиа/баркод/
#            опросы/кости
#
# Порты из apps_script/БОТ_готовый.gs:
#   Utils.normalizeTypeValue :280   Utils.parsePollData :2605
#   Utils.parseQuizData      :2659  Utils слоты/кости    :451-565
#   Utils barcode/QR         :1120-1381
#   StepManager              :3609  MessageHandler.sendStepMessage :5503
#   updateStepMessage        :5664  sendMediaMessage :4244
#   sendBarcodeMessage       :4439  Poll/Quiz/Dice managers :1730-2121
#   completeFunnel           :6218
# ============================================================================

from __future__ import annotations

import base64
import logging
import random
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import config
import effects
import functions
import keyboards
import subscription
import users
from storage import (BotContext, clean_file_id, get_step_value,
                     parse_delay, parse_step_number)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Нормализация типов шагов (Utils.normalizeTypeValue :280)
# ---------------------------------------------------------------------------

_TYPE_MAPPING = {
    'photo': 'photo',
    'video': 'video',
    'audio': 'audio',
    'voice': 'voice',
    'document': 'document',
    'video note': 'video_note',
    'video_note': 'video_note',
    'animation': 'animation',
    'sticker': 'sticker',
    'media group': 'media_group',
    'media_group': 'media_group',
    'poll': 'poll',
    'quiz': 'quiz',
    'dice': 'dice',
    'barcode': 'barcode',
    'qr': 'barcode',
    'qr code': 'barcode',
    'qr-code': 'barcode',
    'text': 'text',
    'input': 'input',
}


def normalize_type_value(type_value: Any) -> str:
    if not type_value:
        return ''
    # re.ASCII: как в JS \w — только [A-Za-z0-9_] (кириллица вырезается)
    clean = re.sub(r'[^\w\s]', '', str(type_value), flags=re.ASCII)
    clean = re.sub(r'\s+', ' ', clean).strip().lower()
    if clean in _TYPE_MAPPING:
        return _TYPE_MAPPING[clean]
    for key, value in _TYPE_MAPPING.items():
        if key in clean:
            return value
    return clean


def is_media_type(type_value: Any) -> bool:
    if not type_value:
        return False
    return normalize_type_value(type_value) in config.MEDIA_TYPES


def is_input_type(type_value: Any) -> bool:
    if not type_value:
        return False
    return normalize_type_value(type_value) == 'input'


# ---------------------------------------------------------------------------
# Poll / Quiz (parsePollData :2605 / parseQuizData :2659)
# ---------------------------------------------------------------------------

_TRUE_VALUES = ('true', '1', 'да', 'yes', 'on')


def _parse_int_leading(value: str) -> Optional[int]:
    """Аналог JS parseInt: ведущее целое, иначе None (NaN)."""
    match = re.match(r'^\s*([+-]?\d+)', str(value))
    return int(match.group(1)) if match else None


def _parse_int_list(text: str) -> Optional[list[int]]:
    """Список parseInt-значений; если хотя бы одно NaN → None (как every(!isNaN))."""
    values = []
    for part in text.split(','):
        parsed = _parse_int_leading(part.strip())
        if parsed is None:
            return None
        values.append(parsed)
    return values


def parse_poll_data(poll_str: Any) -> Optional[dict]:
    try:
        if not poll_str or str(poll_str).strip() == '':
            return None

        parts = [p.strip() for p in str(poll_str).split('|')]
        result = {
            'question': parts[0] if parts and parts[0] else 'Вопрос',
            'answers': [],
            'multiple': False,
            'anonymous': True,
            'logicValues': [],
            'steps': [],
        }

        if len(parts) > 1 and parts[1]:
            result['answers'] = [a.strip() for a in parts[1].split(',')
                                 if a.strip() != '']

        if len(parts) > 2 and parts[2]:
            result['multiple'] = parts[2].lower() in _TRUE_VALUES

        if len(parts) > 3 and parts[3]:
            result['anonymous'] = parts[3].lower() in _TRUE_VALUES

        if len(parts) > 4 and parts[4] and parts[4].strip() != '':
            parsed = _parse_int_list(parts[4])
            if parsed is not None:
                result['logicValues'] = parsed

        if len(parts) > 5 and parts[5] and parts[5].strip() != '':
            parsed = _parse_int_list(parts[5])
            if parsed is not None:
                result['steps'] = [v for v in parsed if v > 0]

        return result
    except Exception as error:  # noqa: BLE001
        logger.error('[parsePollData] Error: %s', error)
        return None


def parse_quiz_data(quiz_str: Any) -> Optional[dict]:
    try:
        if not quiz_str or str(quiz_str).strip() == '':
            return None

        parts = [p.strip() for p in str(quiz_str).split('|')]
        result = {
            'question': parts[0] if parts and parts[0] else 'Вопрос',
            'answers': [],
            'correctOption': 0,
            'explanation': '',
            'anonymous': True,
            'logicValues': [],
            'steps': [],
        }

        if len(parts) > 1 and parts[1]:
            result['answers'] = [a.strip() for a in parts[1].split(',')
                                 if a.strip() != '']

        if len(parts) > 2 and parts[2]:
            option = _parse_int_leading(parts[2])
            if option is not None and 0 <= option < len(result['answers']):
                result['correctOption'] = option

        if len(parts) > 3 and parts[3]:
            result['explanation'] = parts[3]

        if len(parts) > 4 and parts[4]:
            result['anonymous'] = parts[4].lower() in _TRUE_VALUES

        if len(parts) > 5 and parts[5] and parts[5].strip() != '':
            parsed = _parse_int_list(parts[5])
            if parsed is not None:
                result['logicValues'] = parsed

        if len(parts) > 6 and parts[6] and parts[6].strip() != '':
            parsed = _parse_int_list(parts[6])
            if parsed is not None:
                result['steps'] = [v for v in parsed if v > 0]

        return result
    except Exception as error:  # noqa: BLE001
        logger.error('[parseQuizData] Error: %s', error)
        return None


def _bool_col(step_data: dict, column: str) -> str:
    """Переключатель формы → 'TRUE'/'FALSE' для эталонной строки колонки."""
    raw = str(get_step_value(step_data, column) or '').strip().lower()
    return 'TRUE' if raw in _TRUE_VALUES else 'FALSE'


def compose_poll_string(step_data: dict) -> str:
    """Строка колонки Poll из полей формы (poll_question, poll_answer, …).

    Форма админки пишет раздельные ключи, а эталонный формат бота —
    `вопрос|варианты|multiple|anonymous|logic|steps`: без этой сборки
    опрос из формы в бот не попадает.
    """
    question = str(get_step_value(step_data, 'poll_question') or '').strip()
    answers = str(get_step_value(step_data, 'poll_answer') or '').strip()
    if not question and not answers:
        return ''
    return '|'.join([
        question,
        answers,
        _bool_col(step_data, 'poll_multiple'),
        _bool_col(step_data, 'poll_anonymous'),
        str(get_step_value(step_data, 'poll_logic_value') or '').strip(),
        str(get_step_value(step_data, 'poll_step') or '').strip(),
    ])


def compose_quiz_string(step_data: dict) -> str:
    """Строка колонки Quiz из полей формы:
    вопрос|варианты|правильный №|пояснение|аноним|logic|steps."""
    question = str(get_step_value(step_data, 'quiz_question') or '').strip()
    answers = str(get_step_value(step_data, 'quiz_answer') or '').strip()
    if not question and not answers:
        return ''
    correct = str(get_step_value(step_data, 'quiz_option') or '').strip() or '0'
    return '|'.join([
        question,
        answers,
        correct,
        str(get_step_value(step_data, 'quiz_explanation') or '').strip(),
        _bool_col(step_data, 'quiz_anonymous'),
        str(get_step_value(step_data, 'quiz_logic_value') or '').strip(),
        str(get_step_value(step_data, 'quiz_step') or '').strip(),
    ])


def parse_close_date(value: Any) -> Optional[int]:
    """close_date из формы: unix-timestamp или 'ГГГГ-ММ-ДД ЧЧ:ММ[:SS]' (UTC)."""
    v = str(value or '').strip()
    if not v:
        return None
    if v.isdigit():
        return int(v)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(v, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    logger.warning('[parseCloseDate] Не распознан формат даты: %s', v)
    return None


def timing_options(step_data: dict, prefix: str) -> dict:
    """Автозакрытие опроса/квиза: Telegram принимает open_period (5–86400 с)
    или close_date. Приоритет у close_date — при обоих период не шлём."""
    close_date = parse_close_date(
        get_step_value(step_data, f'{prefix}_close_date'))
    if close_date is not None:
        return {'close_date': close_date}
    raw = str(get_step_value(step_data, f'{prefix}_open_period') or '').strip()
    if raw.isdigit():
        period = int(raw)
        if 5 <= period <= 86400:
            return {'open_period': period}
    return {}


# ---------------------------------------------------------------------------
# Кости / слоты (Utils :451-565)
# ---------------------------------------------------------------------------

def parse_dice_value(dice_str: Any) -> str:
    if not dice_str or str(dice_str).strip() == '':
        return '🎲'
    dice = str(dice_str).strip()
    return dice if dice in config.DICE_TYPES else '🎲'


def parse_dice_logic_value(logic_value_str: Any) -> Optional[list[int]]:
    if not logic_value_str or str(logic_value_str).strip() == '':
        return None
    try:
        text = str(logic_value_str)
        if any(sym in text for sym in ('🍒', '🍋', '🔔', '7️⃣')):
            symbols = [s.strip() for s in text.split(',') if s.strip()]
            numeric_values = [config.SYMBOL_TO_NUMBER[s] for s in symbols
                              if s in config.SYMBOL_TO_NUMBER]
            return numeric_values or None
        return _parse_int_list(text)
    except Exception:  # noqa: BLE001
        return None


def parse_dice_step_mapping(dice_step_str: Any) -> Optional[list[int]]:
    if not dice_step_str or str(dice_step_str).strip() == '':
        return None
    try:
        return _parse_int_list(str(dice_step_str))
    except Exception:  # noqa: BLE001
        return None


def get_dice_step_from_value(dice_value: Any, logic_values: Optional[list],
                             step_mapping: Optional[list]) -> int:
    if not logic_values or not step_mapping \
            or len(logic_values) != len(step_mapping):
        return 0
    try:
        index = logic_values.index(dice_value)
    except ValueError:
        return 0
    if 0 <= index < len(step_mapping):
        return step_mapping[index]
    return 0


def get_slot_symbols(dice_value: int) -> str:
    if dice_value < 1 or dice_value > 64:
        return '❓❓❓'

    for symbol, values in config.SAME_SYMBOLS_VALUES.items():
        if dice_value in values:
            return symbol

    different_symbol_combinations = [
        '🍒🍋🔔', '🍋🍒7️⃣', '🔔7️⃣🍒', '7️⃣🔔🍋',
        '🍒🔔7️⃣', '🍋7️⃣🔔', '🔔🍋7️⃣', '7️⃣🍒🔔',
        '🍒🍒🍋', '🍋🍋🔔', '🔔🔔7️⃣', '7️⃣7️⃣🍒',
        '🍒🍋🍒', '🍋🔔🍋', '🔔7️⃣🔔', '7️⃣🍒7️⃣',
    ]
    index = (dice_value - 1) % len(different_symbol_combinations)
    return different_symbol_combinations[index]


def are_all_symbols_same(symbols: Any) -> bool:
    if not symbols or not isinstance(symbols, str):
        return False
    return symbols in ('🍒🍒🍒', '🍋🍋🍋', '🔔🔔🔔', '7️⃣7️⃣7️⃣')


def get_same_symbol_number(symbols: str) -> int:
    return config.SYMBOL_TO_NUMBER.get(symbols, 0)


def get_symbol_number_from_dice_value(dice_value: int) -> int:
    symbols = get_slot_symbols(dice_value)
    if are_all_symbols_same(symbols):
        return get_same_symbol_number(symbols)
    return 0


def format_dice_result(dice_emoji: str, dice_value: Any) -> str:
    if dice_emoji == '🎰':
        symbols = get_slot_symbols(dice_value)
        if are_all_symbols_same(symbols):
            combo_number = get_same_symbol_number(symbols)
            return (f'{dice_emoji}: {symbols} '
                    f'(комбинация #{combo_number}, значение: {dice_value})')
        return f'{dice_emoji}: {symbols} (разные символы, значение: {dice_value})'
    return f'{dice_emoji}: {dice_value}'


# ---------------------------------------------------------------------------
# Barcode / QR (Utils :1120-1381)
# ---------------------------------------------------------------------------

def parse_qr_config(qr_param_str: str) -> dict:
    try:
        if not qr_param_str or qr_param_str.strip() == '':
            return {}

        params: dict[str, Any] = {}
        for pair in qr_param_str.split('&'):
            if '=' not in pair:
                continue
            key, _, value = pair.partition('=')
            clean_key = key.strip().lower()
            clean_value = value.strip()
            try:
                from urllib.parse import unquote
                clean_value = unquote(clean_value)
            except Exception:  # noqa: BLE001
                pass

            if clean_key == 'text':
                params['text'] = clean_value
            elif clean_key == 'size':
                try:
                    size = int(clean_value)
                    if size > 0:
                        params['size'] = size
                except ValueError:
                    pass
            elif clean_key == 'margin':
                try:
                    margin = int(clean_value)
                    if margin >= 0:
                        params['margin'] = margin
                except ValueError:
                    pass
            elif clean_key == 'format':
                fmt = clean_value.lower()
                if fmt in ('png', 'jpeg', 'jpg', 'svg'):
                    params['format'] = 'jpeg' if fmt == 'jpg' else fmt
            elif clean_key in ('dark', 'color'):
                dark_color = clean_value.replace('#', '')
                if re.match(r'^[0-9A-Fa-f]{3,6}$', dark_color):
                    params['dark'] = dark_color
            elif clean_key in ('light', 'bgcolor'):
                light_color = clean_value.replace('#', '')
                if re.match(r'^[0-9A-Fa-f]{3,6}$', light_color):
                    params['light'] = light_color
            elif clean_key in ('ecl', 'eclevel'):
                ecl = clean_value.upper()
                if ecl in ('L', 'M', 'Q', 'H'):
                    params['ecl'] = ecl
            elif clean_key == 'centerimageurl':
                if clean_value and clean_value.lower() not in ('none', 'false', 'null'):
                    params['centerImageUrl'] = clean_value
            elif clean_key == 'centerimagesizeratio':
                try:
                    logo_size = float(clean_value)
                    if 0 < logo_size <= 1:
                        params['centerImageSizeRatio'] = logo_size
                except ValueError:
                    pass
            elif clean_key == 'caption':
                params['caption'] = clean_value
            elif clean_key == 'captionfontfamily':
                params['captionFontFamily'] = clean_value
            elif clean_key == 'captionfontsize':
                try:
                    caption_size = int(clean_value)
                    if caption_size > 0:
                        params['captionFontSize'] = caption_size
                except ValueError:
                    pass
            elif clean_key == 'captionfontcolor':
                caption_color = clean_value.replace('#', '')
                if re.match(r'^[0-9A-Fa-f]{3,6}$', caption_color):
                    params['captionFontColor'] = caption_color

        return params
    except Exception as error:  # noqa: BLE001
        logger.error('Error parsing QR config: %s', error)
        return {}


def generate_barcode_url(data: str, options: Optional[dict] = None,
                         qr_config: Optional[dict] = None) -> Optional[str]:
    try:
        cfg = config.BARCODE_CONFIG
        options = options or {}
        qr_config = qr_config or {}

        qr_data = data or f'USER_{int(time.time() * 1000)}'
        from urllib.parse import quote
        qr_url = f"{cfg['BASE_URL']}?text={quote(str(qr_data), safe='')}"

        size = qr_config.get('size') or options.get('size') or cfg['DEFAULT_SIZE']
        qr_url += f'&size={size}'

        margin = qr_config.get('margin') or options.get('margin') or cfg['DEFAULT_MARGIN']
        qr_url += f'&margin={margin}'

        fmt = qr_config.get('format') or options.get('format') or cfg['DEFAULT_FORMAT']
        qr_url += f'&format={fmt}'

        dark_color = (qr_config.get('dark') or qr_config.get('color')
                      or options.get('color') or options.get('dark')
                      or cfg['DEFAULT_FG_COLOR'])
        light_color = (qr_config.get('light') or qr_config.get('bgcolor')
                       or options.get('bgColor') or cfg['DEFAULT_BG_COLOR'])

        if dark_color:
            qr_url += f"&dark={str(dark_color).replace('#', '')}"
        if light_color:
            qr_url += f"&light={str(light_color).replace('#', '')}"

        ec_level = (qr_config.get('ecl') or qr_config.get('ecLevel')
                    or options.get('ecl') or options.get('ecLevel')
                    or cfg['DEFAULT_EC_LEVEL'])
        qr_url += f'&ecLevel={ec_level}'

        center_image_url = (qr_config.get('centerImageUrl')
                            or options.get('logoUrl') or options.get('logo')
                            or cfg['DEFAULT_LOGO_URL'])
        if center_image_url and str(center_image_url).strip() != '' \
                and str(center_image_url).lower() not in ('none', 'false', 'null'):
            qr_url += f'&centerImageUrl={quote(str(center_image_url), safe="")}'
            ratio = (qr_config.get('centerImageSizeRatio')
                     or options.get('logoSizeRatio')
                     or cfg['DEFAULT_LOGO_SIZE_RATIO'])
            qr_url += f'&centerImageSizeRatio={ratio}'

        caption = qr_config.get('caption')
        if caption and str(caption).strip() != '':
            qr_url += f'&caption={quote(str(caption).strip(), safe="")}'
            if qr_config.get('captionFontFamily'):
                qr_url += (f"&captionFontFamily="
                           f"{quote(str(qr_config['captionFontFamily']), safe='')}")
            if qr_config.get('captionFontSize'):
                qr_url += f"&captionFontSize={qr_config['captionFontSize']}"
            if qr_config.get('captionFontColor'):
                font_color = str(qr_config['captionFontColor']).replace('#', '')
                qr_url += f'&captionFontColor={font_color}'

        qr_url += f'&t={int(time.time() * 1000)}'
        return qr_url
    except Exception as error:  # noqa: BLE001
        logger.error('Error generating barcode URL: %s', error)
        return None


def process_barcode_data(data: str) -> str:
    """Возвращает данные как есть (портировано для паритета с оригиналом)."""
    return data


def parse_barcode_data(ctx: BotContext, step_data: dict,
                       user_id: Any = None) -> dict:
    try:
        content = get_step_value(step_data, 'content') or ''
        barcode_data = str(content)
        if user_id and '{' in barcode_data:
            barcode_data = users.replace_tags_in_text(ctx, barcode_data, user_id)

        barcode_data = process_barcode_data(barcode_data)

        if not barcode_data or barcode_data.strip() == '':
            user_info = users.get_user_info(ctx, user_id) if user_id else None
            unique_id = (user_info or {}).get('user_id') or (user_id or '')
            timestamp = int(time.time() * 1000)
            random_str = format(random.randint(0, 36 ** 9 - 1), 'X').upper()[:9]
            barcode_data = f'USER_{unique_id}_{timestamp}_{random_str}'

        qr_param = get_step_value(step_data, 'qr') or ''
        qr_config = {}
        if qr_param and str(qr_param).strip() != '':
            qr_config = parse_qr_config(str(qr_param).strip())

        options: dict[str, Any] = {}
        barcode_size = get_step_value(step_data, 'barcode_size') or ''
        barcode_color = get_step_value(step_data, 'barcode_color') or ''
        barcode_bg_color = get_step_value(step_data, 'barcode_bg_color') or ''
        barcode_logo = get_step_value(step_data, 'barcode_logo') or ''
        barcode_logo_size = get_step_value(step_data, 'barcode_logo_size') or ''
        barcode_ecl = get_step_value(step_data, 'barcode_ecl') or ''

        if barcode_size:
            try:
                options['size'] = int(str(barcode_size))
            except ValueError:
                pass
        if barcode_color:
            options['color'] = str(barcode_color).replace('#', '')
        if barcode_bg_color:
            options['bgColor'] = str(barcode_bg_color).replace('#', '')
        if barcode_logo:
            options['logoUrl'] = barcode_logo
        if barcode_logo_size:
            try:
                options['logoSizeRatio'] = float(str(barcode_logo_size))
            except ValueError:
                pass
        if barcode_ecl:
            options['ecl'] = barcode_ecl
        options['format'] = 'png'

        return {'data': barcode_data, 'options': options, 'qrConfig': qr_config}
    except Exception as error:  # noqa: BLE001
        logger.error('Error parsing barcode data: %s', error)
        return {'data': f'USER_{user_id}_{int(time.time() * 1000)}',
                'options': {}, 'qrConfig': {}}


def download_image_as_base64(image_url: str) -> Optional[str]:
    try:
        if not image_url:
            return None
        import requests
        response = requests.get(image_url, timeout=30)
        if response.status_code == 200:
            mime_type = response.headers.get('Content-Type', 'image/png')
            b64 = base64.b64encode(response.content).decode('ascii')
            return f'data:{mime_type};base64,{b64}'
        return None
    except Exception as error:  # noqa: BLE001
        logger.error('Error downloading image: %s', error)
        return None


# ---------------------------------------------------------------------------
# StepManager (:3609)
# ---------------------------------------------------------------------------

class StepManager:
    @staticmethod
    def get_step_data(ctx: BotContext, step_number: int) -> Optional[dict]:
        if not ctx.funnel_id:
            return None
        return ctx.storage.get_step(ctx.funnel_id, step_number)

    @staticmethod
    def get_next_step(ctx: BotContext, current_step: int) -> int:
        """next_step из шага, иначе current+1 (если есть), иначе 0 = конец."""
        step_data = StepManager.get_step_data(ctx, current_step)
        if not step_data:
            return 0

        next_step_value = get_step_value(step_data, 'next_step') or ''
        if str(next_step_value).strip() != '':
            next_step = parse_step_number(next_step_value)
            if next_step is not None and next_step > 0:
                return next_step

        next_sequential = current_step + 1
        if StepManager.get_step_data(ctx, next_sequential):
            return next_sequential
        return 0

    @staticmethod
    def get_keyboard_for_step(ctx: BotContext, step_number: int) -> Optional[dict]:
        if not ctx.funnel_id:
            return None
        cached = ctx.storage.get_reply_keyboard(ctx.funnel_id, step_number)
        if cached:
            return cached
        # Фолбэк (в оригинале — чтение листа): разбор из данных шага
        step_data = StepManager.get_step_data(ctx, step_number)
        if not step_data:
            return None
        keyboard_str = get_step_value(step_data, 'regular_keyboard') or ''
        if not str(keyboard_str).strip():
            return None
        parsed = keyboards.parse_regular_keyboard_data(keyboard_str)
        if parsed and parsed['buttons']:
            keyboard_data = {'parsedData': parsed, 'step': step_number,
                             'funnelId': ctx.funnel_id}
            ctx.storage.reply_keyboards_cache[f'{ctx.funnel_id}_{step_number}'] = \
                keyboard_data
            return keyboard_data
        return None

    @staticmethod
    def get_inline_keyboard_for_step(ctx: BotContext,
                                     step_number: int) -> Optional[dict]:
        if not ctx.funnel_id:
            return None
        cached = ctx.storage.get_inline_keyboard(ctx.funnel_id, step_number)
        if cached:
            return cached
        step_data = StepManager.get_step_data(ctx, step_number)
        if not step_data:
            return None
        inline_str = get_step_value(step_data, 'inline_keyboard') or ''
        if not str(inline_str).strip():
            return None
        channel = get_step_value(step_data, 'channel')
        subscription = get_step_value(step_data, 'subscription')
        no_subscription = get_step_value(step_data, 'no_subscription')
        parsed = keyboards.parse_inline_keyboard_data(inline_str)
        if parsed and parsed['buttons']:
            built = keyboards.build_inline_keyboard_from_parsed_data(parsed, channel)
            keyboard_data = {
                'inlineKeyboard': built,
                'step': step_number,
                'funnelId': ctx.funnel_id,
                'channel': channel or '',
                'subscriptionMessage': subscription or '',
                'noSubscriptionMessage': no_subscription or '',
                'parsedData': parsed,
            }
            ctx.storage.inline_keyboards_cache[f'{ctx.funnel_id}_{step_number}'] = \
                keyboard_data
            return keyboard_data
        return None


def build_reply_keyboard(ctx: BotContext, step_number: int,
                         user_role: str = '') -> Optional[dict]:
    """Порт buildReplyKeyboard (:3907)."""
    if not ctx.funnel_id:
        return None
    keyboard_data = StepManager.get_keyboard_for_step(ctx, step_number)
    if not keyboard_data or not keyboard_data.get('parsedData'):
        return None
    return keyboards.build_regular_keyboard_from_parsed_data(
        keyboard_data['parsedData'], user_role)


# ---------------------------------------------------------------------------
# Игровые кэши: dice / poll / quiz (TTL 5 мин — в Storage)
# ---------------------------------------------------------------------------

def _now_ms() -> float:
    return time.time() * 1000


def add_dice_result(ctx: BotContext, message_id: Any, user_id: Any, chat_id: Any,
                    dice_value: Any, step_number: int, step_data: dict) -> None:
    ctx.storage.dice_results[message_id] = {
        'userId': user_id, 'chatId': chat_id, 'diceValue': dice_value,
        'stepNumber': step_number, 'stepData': step_data,
        'timestamp': _now_ms(), 'processed': False,
    }
    ctx.storage.cleanup_game_caches()


def add_poll_result(ctx: BotContext, poll_id: str, user_id: Any, chat_id: Any,
                    step_number: int, step_data: dict, logic_values: list,
                    steps: list, target: str = 'poll') -> None:
    cache = ctx.storage.poll_results if target == 'poll' else ctx.storage.quiz_results
    cache[poll_id] = {
        'userId': user_id, 'chatId': chat_id, 'selectedOption': -1,
        'stepNumber': step_number, 'stepData': step_data,
        'logicValues': logic_values or [], 'steps': steps or [],
        'timestamp': _now_ms(), 'processed': False,
    }
    ctx.storage.cleanup_game_caches()


def _run_step_calc(ctx: BotContext, chat_id: Any, user_id: Any,
                   step_data: Optional[dict]) -> None:
    """Формула calc из формы шага (calc_formula) — авто-расчёт после
    броска/ответа, до отправки следующего шага. Успех не шлёт отдельное
    сообщение (следующий шаг покажет новые теги); ошибку показываем,
    чтобы её не было незаметно."""
    formula = str(get_step_value(step_data or {}, 'calc_formula') or '').strip()
    if not formula:
        return
    result = functions.execute_calc_formula(ctx, user_id, formula, '')
    if not result.get('success') and result.get('message'):
        ctx.tg.send_message(chat_id, result['message'])


def process_dice_result(ctx: BotContext, message_id: Any) -> bool:
    """Порт DiceManager.processDiceResult (:1807)."""
    try:
        dice_result = ctx.storage.dice_results.get(message_id)
        if not dice_result or dice_result.get('processed'):
            return False

        user_id = dice_result['userId']
        chat_id = dice_result['chatId']
        dice_value = dice_result['diceValue']
        step_number = dice_result['stepNumber']
        step_data = dice_result['stepData']

        dice_emoji = get_step_value(step_data, 'dice') or '🎲'
        dice_logic_value = get_step_value(step_data, 'dice_logic_value') or ''
        dice_step = get_step_value(step_data, 'dice_step') or ''

        # Результат броска → теги {dice}/{dice_emoji}/… для формул calc.
        users.save_dice_tags(ctx, user_id, dice_emoji, dice_value)
        # Формула из формы шага (calc_formula) — авто-расчёт по тегам.
        _run_step_calc(ctx, chat_id, user_id, step_data)

        next_step = 0
        if str(dice_logic_value).strip() and str(dice_step).strip():
            logic_values = parse_dice_logic_value(dice_logic_value)
            step_mapping = parse_dice_step_mapping(dice_step)
            if logic_values and step_mapping and len(logic_values) == len(step_mapping):
                if dice_emoji == '🎰':
                    symbols = get_slot_symbols(dice_value)
                    if are_all_symbols_same(symbols):
                        next_step = get_dice_step_from_value(
                            get_same_symbol_number(symbols),
                            logic_values, step_mapping)
                    else:
                        next_step = get_dice_step_from_value(
                            0, logic_values, step_mapping)
                else:
                    next_step = get_dice_step_from_value(
                        dice_value, logic_values, step_mapping)

        if next_step <= 0:
            if dice_emoji == '🎰':
                symbols = get_slot_symbols(dice_value)
                if not are_all_symbols_same(symbols):
                    next_step = StepManager.get_next_step(ctx, step_number)
            else:
                next_step = StepManager.get_next_step(ctx, step_number)

        dice_result['processed'] = True
        time.sleep(1)

        if next_step > 0:
            users.set_user_state(ctx, user_id, next_step)
            send_step_message(ctx, chat_id, next_step, user_id)
            return True
        complete_funnel(ctx, user_id, chat_id)
        return False
    except Exception as error:  # noqa: BLE001
        logger.error('[processDiceResult] %s', error)
        return False


def check_dice_result(ctx: BotContext, message_id: Any, chat_id: Any) -> None:
    """Порт checkDiceResult (:5324): фолбэк рандомного значения 1..6."""
    try:
        dice_result = ctx.storage.dice_results.get(message_id)
        if not dice_result:
            return
        if dice_result.get('diceValue') and dice_result['diceValue'] > 0:
            process_dice_result(ctx, message_id)
        else:
            random_value = random.randint(1, 6)
            add_dice_result(ctx, message_id, dice_result['userId'],
                            dice_result['chatId'], random_value,
                            dice_result['stepNumber'], dice_result['stepData'])
            time.sleep(1)
            process_dice_result(ctx, message_id)
    except Exception:  # noqa: BLE001
        pass


def process_poll_result(ctx: BotContext, poll_id: str, target: str = 'poll') -> bool:
    """Порт PollManager.processPollResult / QuizManager.processQuizResult."""
    try:
        cache = ctx.storage.poll_results if target == 'poll' else ctx.storage.quiz_results
        poll_result = cache.get(poll_id)
        if not poll_result or poll_result.get('processed'):
            return False

        selected_option = poll_result.get('selectedOption', -1)
        step_number = poll_result['stepNumber']
        logic_values = poll_result.get('logicValues') or []
        steps = poll_result.get('steps') or []

        # Ответ poll/quiz → теги {poll_answer}/{quiz_correct}/… для формул calc.
        users.save_poll_tags(ctx, poll_result['userId'], target,
                             selected_option, logic_values,
                             poll_result.get('stepData') or {})
        # Формула из формы шага (calc_formula) — авто-расчёт по тегам.
        _run_step_calc(ctx, poll_result['chatId'], poll_result['userId'],
                       poll_result.get('stepData') or {})

        next_step = 0
        if logic_values and steps:
            if 0 <= selected_option < len(logic_values):
                next_step = steps[selected_option] or 0

        if next_step <= 0:
            next_step = StepManager.get_next_step(ctx, step_number)

        poll_result['processed'] = True
        time.sleep(0.5)

        if next_step > 0:
            users.set_user_state(ctx, poll_result['userId'], next_step)
            send_step_message(ctx, poll_result['chatId'], next_step,
                              poll_result['userId'])
            return True
        complete_funnel(ctx, poll_result['userId'], poll_result['chatId'])
        return False
    except Exception as error:  # noqa: BLE001
        logger.error('[processPollResult] %s', error)
        return False


# ---------------------------------------------------------------------------
# Отправка медиа (sendMediaMessage :4244 и др.)
# ---------------------------------------------------------------------------

_MEDIA_METHOD_MAP = {
    'photo': 'sendPhoto',
    'video': 'sendVideo',
    'audio': 'sendAudio',
    'document': 'sendDocument',
    'voice': 'sendVoice',
    'video_note': 'sendVideoNote',
    'animation': 'sendAnimation',
    'sticker': 'sendSticker',
    'media_group': 'sendDocument',
}

# Подпись (caption) принимают только эти методы Bot API — у sendSticker и
# sendVideoNote параметра caption нет (сверено с сигнатурами python-telegram-bot
# и @types/node-telegram-bot-api); им caption слать нельзя.
CAPTION_MEDIA_TYPES = frozenset({
    'photo', 'video', 'audio', 'document', 'animation', 'voice',
    'media_group',  # одиночный файл уходит sendDocument
})


def _media_payload_field(media_type: str) -> str:
    if media_type == 'photo':
        return 'photo'
    if media_type == 'media_group':
        return 'document'
    return media_type


def _valid_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.netloc)
    except Exception:  # noqa: BLE001
        return False


def _guess_url_media_type(url: str) -> str:
    """Тип элемента media_group по расширению URL (фото/видео/аудио/документ)."""
    path = urlparse(url).path.lower()
    for ext in ('.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv'):
        if path.endswith(ext):
            return 'video'
    for ext in ('.mp3', '.ogg', '.m4a', '.wav', '.aac', '.flac'):
        if path.endswith(ext):
            return 'audio'
    for ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
        if path.endswith(ext):
            return 'photo'
    return 'document'


def send_media_items_individually(ctx: BotContext, chat_id, media_items: list,
                                  caption: str, options: Optional[dict] = None) -> Any:
    """Порт sendMediaItemsIndividually (:4200)."""
    options = options or {}
    last_message_id = None
    total = len(media_items)

    for index, item in enumerate(media_items):
        media_type = item.get('type', 'document')
        media_source = item.get('media')
        payload: dict[str, Any] = {'chat_id': chat_id}
        payload[_media_payload_field(media_type)] = media_source

        if index == total - 1 and caption and caption.strip() != '':
            payload['caption'] = caption.strip()
            payload['parse_mode'] = 'HTML'
        if index == total - 1 and options.get('reply_markup'):
            payload['reply_markup'] = options['reply_markup']

        method = _MEDIA_METHOD_MAP.get(media_type, 'sendMessage')
        response = ctx.tg.send_request(method, payload)
        if response.get('ok'):
            last_message_id = response['result']['message_id']

    return last_message_id


def send_media_group_universal(ctx: BotContext, chat_id, media_items: list,
                               caption: str, options: Optional[dict] = None) -> Any:
    """Порт sendMediaGroupUniversal (:4172) + фолбэк поштучной отправки."""
    options = options or {}
    try:
        if not media_items:
            return None

        result = ctx.tg.send_media_group(chat_id, media_items)
        if result:
            if options.get('reply_markup'):
                keyboard_message = (caption if caption and caption.strip() != ''
                                    else f'📁 Медиагруппа ({len(media_items)} файлов)')
                ctx.tg.send_message(chat_id, keyboard_message,
                                    {'parse_mode': 'HTML',
                                     'reply_markup': options['reply_markup']})
            return result[0].get('message_id') if result else None
        return send_media_items_individually(ctx, chat_id, media_items,
                                             caption, options)
    except Exception as error:  # noqa: BLE001
        logger.error('sendMediaGroupUniversal: %s', error)
        return None


def send_media_message(ctx: BotContext, chat_id, step_data: dict,
                       media_type: str, options: Optional[dict] = None) -> Any:
    """Порт sendMediaMessage (:4244)."""
    options = options or {}
    try:
        file_url = get_step_value(step_data, 'file_url') or ''
        file_id = get_step_value(step_data, 'file_id') or ''
        if file_id and isinstance(file_id, str):
            file_id = clean_file_id(file_id)

        caption = ''
        step_type_value = get_step_value(step_data, 'type') or ''
        if is_media_type(step_type_value):
            caption = get_step_value(step_data, 'content') or ''

        urls: list[str] = []
        if file_url and str(file_url).strip() != '':
            urls = [u.strip() for u in str(file_url).split(',') if u.strip()]
            urls = [u for u in urls if _valid_http_url(u)]

        file_ids: list[str] = []
        if file_id and str(file_id).strip() != '':
            file_ids = [f.strip() for f in str(file_id).split(',') if f.strip()]
            file_ids = [f for f in file_ids
                        if f and not f.startswith(('http://', 'https://'))
                        and re.match(r'^[A-Za-z0-9_\-]+$', f)]

        total_files = len(urls) + len(file_ids)

        if total_files == 0:
            content = get_step_value(step_data, 'content') or ''
            if content and str(content).startswith(('http://', 'https://')) \
                    and _valid_http_url(str(content)):
                message_id = ctx.tg.send_photo(chat_id, content, caption, options)
                if message_id:
                    return message_id
            ctx.tg.send_message(
                chat_id,
                '<b>ℹ️ Информация</b>\n\n'
                + (caption or 'Нет данных для отображения'),
                options)
            return None

        # Несколько файлов photo/video/audio/document (и media_group)
        # → sendMediaGroup: Telegram принимает альбом из 2–10 элементов.
        if media_type in ('photo', 'video', 'audio', 'document',
                          'media_group') and total_files > 1:
            # У однотипных медиа тип элемента = тип шага; у media_group
            # определяем по расширению URL, file_id уходит document.
            plain_media = ('photo', 'video', 'audio', 'document')
            all_media: list[dict] = []
            for url in urls:
                item_type = media_type if media_type in plain_media \
                    else _guess_url_media_type(url)
                media_item: dict[str, Any] = {'type': item_type, 'media': url}
                if not all_media and caption and caption.strip() != '':
                    media_item['caption'] = caption.strip()
                    media_item['parse_mode'] = 'HTML'
                all_media.append(media_item)
            for file in file_ids:
                item_type = media_type if media_type in plain_media \
                    else 'document'
                media_item = {'type': item_type, 'media': file}
                if not all_media and caption and caption.strip() != '':
                    media_item['caption'] = caption.strip()
                    media_item['parse_mode'] = 'HTML'
                all_media.append(media_item)

            if len(all_media) > 1:
                return send_media_group_universal(ctx, chat_id, all_media,
                                                  caption, options)

        # Один файл
        if urls:
            media_source = urls[0]
        elif file_ids:
            media_source = file_ids[0]
        else:
            ctx.tg.send_message(
                chat_id,
                '<b>ℹ️ Информация</b>\n\n'
                + (caption or 'Нет данных для отображения'),
                options)
            return None

        payload: dict[str, Any] = {'chat_id': chat_id}
        payload[_media_payload_field(media_type)] = media_source
        if media_type in CAPTION_MEDIA_TYPES \
                and caption and str(caption).strip() != '':
            payload['caption'] = str(caption).strip()
            payload['parse_mode'] = 'HTML'
        if options.get('reply_markup'):
            payload['reply_markup'] = options['reply_markup']

        method = _MEDIA_METHOD_MAP.get(media_type, 'sendMessage')
        response = ctx.tg.send_request(method, payload)
        if response.get('ok'):
            return response['result']['message_id']

        description = str(response.get('description') or '')
        if media_type != 'document' and any(
                marker in description
                for marker in ('wrong file identifier', 'file_id', 'Wrong character')):
            document_payload: dict[str, Any] = {
                'chat_id': chat_id,
                'document': media_source,
                'caption': caption or 'Файл',
                'parse_mode': 'HTML',
            }
            if options.get('reply_markup'):
                document_payload['reply_markup'] = options['reply_markup']
            doc_response = ctx.tg.send_request('sendDocument', document_payload)
            if doc_response.get('ok'):
                return doc_response['result']['message_id']

        ctx.tg.send_message(
            chat_id,
            '<b>ℹ️ Информация</b>\n\n'
            + (caption or 'Не удалось отправить медиафайл'),
            options)
        return None
    except Exception as error:  # noqa: BLE001
        logger.error('Error in sendMediaMessage: %s', error)
        caption = get_step_value(step_data, 'content') or ''
        if caption and str(caption).strip() != '':
            ctx.tg.send_message(chat_id,
                                '<b>ℹ️ Информация</b>\n\n' + str(caption),
                                options)
        return None


# ---------------------------------------------------------------------------
# Barcode / Poll / Quiz / Dice отправка
# ---------------------------------------------------------------------------

def _should_pin(should_pin: Any) -> bool:
    return str(should_pin or '').lower().strip() in ('true', '1', 'да', 'yes')


def send_barcode_message(ctx: BotContext, chat_id, step_data: dict,
                         options: dict, user_id: Any) -> Any:
    """Порт sendBarcodeMessage (:4439)."""
    try:
        time.sleep(0.5)

        barcode_info = parse_barcode_data(ctx, step_data, user_id)

        if user_id:
            type_value = get_step_value(step_data, 'type_value') or 'Barcode'
            users.save_user_input_response(ctx, user_id, type_value,
                                           barcode_info['data'])

        qr_code_url = generate_barcode_url(barcode_info['data'],
                                           barcode_info['options'],
                                           barcode_info['qrConfig'])
        if not qr_code_url:
            ctx.tg.send_message(
                chat_id,
                '<b>❌ Ошибка генерации QR-кода</b>\n\n'
                'Не удалось сгенерировать код для данных: '
                + str(barcode_info['data'])[:50] + '...',
                options)
            return None

        caption = get_step_value(step_data, 'content') or ''
        processed_caption = ''
        if caption and str(caption).strip() != '':
            processed_caption = (users.replace_tags_in_text(ctx, caption, user_id)
                                 if user_id else caption)

        try:
            message_id = ctx.tg.send_photo(chat_id, qr_code_url,
                                           processed_caption, options)
            if message_id:
                should_pin = (get_step_value(step_data, 'pin')
                              or get_step_value(step_data, 'pin_message'))
                if _should_pin(should_pin):
                    ctx.tg.pin_message(chat_id, message_id)
                return message_id
        except Exception as error:  # noqa: BLE001
            logger.error('Error sending barcode: %s', error)
            base64_image = download_image_as_base64(qr_code_url)
            if base64_image:
                return ctx.tg.send_photo(chat_id, base64_image,
                                         processed_caption, options)
            ctx.tg.send_message(
                chat_id,
                '<b>🎫 Ваш QR-код сгенерирован!</b>\n\n'
                f'Данные: <code>{barcode_info["data"]}</code>\n\n'
                f'Ссылка на код: {qr_code_url}',
                options)

        return None
    except Exception as error:  # noqa: BLE001
        logger.error('Error in sendBarcodeMessage: %s', error)
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Ошибка при создании QR-кода</b>\n\n'
            'Попробуйте позже или обратитесь к администратору.',
            options)
        return None


def handle_poll_message(ctx: BotContext, chat_id, step_data: dict, options: dict,
                        step_number: int, user_id: Any) -> None:
    """Порт MessageHandler.handlePollMessage (:5733)."""
    poll_str = get_step_value(step_data, 'poll') or ''
    if not str(poll_str).strip():
        # Форма пишет раздельные поля — собираем эталонную строку.
        poll_str = compose_poll_string(step_data)
    poll_data = parse_poll_data(poll_str)

    if not poll_data or not 2 <= len(poll_data['answers']) <= 10:
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Ошибка:</b> Для опроса нужно от 2 до 10 вариантов ответа. '
            'Проверьте поле с вариантами.')
        return

    question = poll_data['question']
    if user_id:
        question = users.replace_tags_in_text(ctx, question, user_id)

    poll_options: dict[str, Any] = {
        'type': 'regular',
        'is_anonymous': poll_data['anonymous'],
        'allows_multiple_answers': poll_data['multiple'],
    }
    poll_options.update(timing_options(step_data, 'poll'))
    if options.get('reply_markup'):
        poll_options['reply_markup'] = options['reply_markup']

    poll_result = ctx.tg.send_poll(chat_id, question, poll_data['answers'],
                                   poll_options)
    if poll_result and poll_result.get('message_id') and poll_result.get('poll_id'):
        add_poll_result(ctx, poll_result['poll_id'], user_id, chat_id,
                        step_number, step_data,
                        poll_data.get('logicValues') or [],
                        poll_data.get('steps') or [], target='poll')
        if get_step_value(step_data, 'pin') == 'true':
            ctx.tg.pin_message(chat_id, poll_result['message_id'])


def handle_quiz_message(ctx: BotContext, chat_id, step_data: dict, options: dict,
                        step_number: int, user_id: Any) -> None:
    """Порт MessageHandler.handleQuizMessage (:5781)."""
    quiz_str = get_step_value(step_data, 'quiz') or ''
    if not str(quiz_str).strip():
        # Форма пишет раздельные поля — собираем эталонную строку.
        quiz_str = compose_quiz_string(step_data)
    quiz_data = parse_quiz_data(quiz_str)

    if not quiz_data or not 2 <= len(quiz_data['answers']) <= 10:
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Ошибка:</b> Для викторины нужно от 2 до 10 вариантов '
            'ответа. Проверьте поле с вариантами.')
        return

    if not (0 <= quiz_data['correctOption'] < len(quiz_data['answers'])):
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Ошибка:</b> Неправильный номер правильного ответа. '
            'Проверьте колонку Quiz.')
        return

    question = quiz_data['question']
    if user_id:
        question = users.replace_tags_in_text(ctx, question, user_id)

    poll_options: dict[str, Any] = {
        'type': 'quiz',
        'correct_option_id': quiz_data['correctOption'],
        'is_anonymous': quiz_data['anonymous'],
    }
    poll_options.update(timing_options(step_data, 'quiz'))
    if quiz_data.get('explanation') and quiz_data['explanation'].strip() != '':
        poll_options['explanation'] = quiz_data['explanation']
    if options.get('reply_markup'):
        poll_options['reply_markup'] = options['reply_markup']

    poll_result = ctx.tg.send_poll(chat_id, question, quiz_data['answers'],
                                   poll_options)
    if poll_result and poll_result.get('message_id') and poll_result.get('poll_id'):
        add_poll_result(ctx, poll_result['poll_id'], user_id, chat_id,
                        step_number, step_data,
                        quiz_data.get('logicValues') or [],
                        quiz_data.get('steps') or [], target='quiz')
        if get_step_value(step_data, 'pin') == 'true':
            ctx.tg.pin_message(chat_id, poll_result['message_id'])


def handle_dice_message(ctx: BotContext, chat_id, step_data: dict, options: dict,
                        step_number: int, user_id: Any) -> None:
    """Порт MessageHandler.handleDiceMessage (:5842)."""
    dice_emoji = parse_dice_value(get_step_value(step_data, 'dice') or '🎲')
    # sendDice не принимает parse_mode/caption — из options шлём только
    # клавиатуру (reply_markup).
    dice_options: dict[str, Any] = {}
    if options.get('reply_markup'):
        dice_options['reply_markup'] = options['reply_markup']
    dice_result = ctx.tg.send_dice(chat_id, dice_emoji, dice_options)

    if dice_result and dice_result.get('message_id'):
        message_id = dice_result['message_id']
        if dice_result.get('dice_value'):
            add_dice_result(ctx, message_id, user_id, chat_id,
                            dice_result['dice_value'], step_number, step_data)
            time.sleep(1)
            process_dice_result(ctx, message_id)
        else:
            add_dice_result(ctx, message_id, user_id, chat_id, 0,
                            step_number, step_data)
            timer = threading.Timer(3.0, check_dice_result,
                                    args=(ctx, message_id, chat_id))
            timer.daemon = True
            timer.start()

        if get_step_value(step_data, 'pin') == 'true':
            ctx.tg.pin_message(chat_id, message_id)


# ---------------------------------------------------------------------------
# sendStepMessage / updateStepMessage / completeFunnel
# ---------------------------------------------------------------------------

_TYPING_ACTION_MAP = {
    'photo': 'upload_photo',
    'video': 'upload_video',
    'audio': 'upload_audio',
    'document': 'upload_document',
    'voice': 'upload_audio',
    'video_note': 'upload_video_note',
    'animation': 'upload_video',
    'sticker': 'choose_sticker',
    'media_group': 'upload_photo',
    'poll': 'typing',
    'quiz': 'typing',
    'dice': 'choose_sticker',
    'barcode': 'upload_photo',
    'text': 'typing',
    'input': 'typing',
}


def _pin_if_needed(ctx: BotContext, chat_id, message_id: Any,
                   should_pin: Any) -> None:
    if message_id and _should_pin(should_pin):
        ctx.tg.pin_message(chat_id, message_id)


def send_step_message(ctx: BotContext, chat_id, step_number: int,
                      user_id: Any = None) -> None:
    """Порт MessageHandler.sendStepMessage (:5503) — диспетчер типов шагов."""
    step_data = StepManager.get_step_data(ctx, step_number)
    if not step_data:
        ctx.tg.send_message(
            chat_id, f'<b>⚠️ Шаг {step_number} не найден в воронке</b>')
        return

    step_type_value = get_step_value(step_data, 'type') or ''
    step_type = normalize_type_value(step_type_value)
    scroll_value = str(get_step_value(step_data, 'scroll') or '').strip().lower()
    delay_value = get_step_value(step_data, 'delay') or ''
    should_pin = (get_step_value(step_data, 'pin')
                  or get_step_value(step_data, 'pin_message'))
    content = get_step_value(step_data, 'content') or ''

    options: dict[str, Any] = {'parse_mode': 'HTML'}

    user_role = (users.get_user_tag(ctx, user_id, 'role') or '') if user_id else ''
    reply_keyboard = build_reply_keyboard(ctx, step_number, user_role)
    if reply_keyboard:
        options['reply_markup'] = reply_keyboard['reply_markup']
    else:
        inline_data = StepManager.get_inline_keyboard_for_step(ctx, step_number)
        if inline_data and inline_data.get('inlineKeyboard'):
            options['reply_markup'] = inline_data['inlineKeyboard']

    has_effects = effects.has_effects(content)

    processed_content = content
    if user_id:
        processed_content = users.replace_tags_in_text(ctx, content, user_id)

    if ctx.config.get('typing', False):
        action = _TYPING_ACTION_MAP.get(step_type, 'typing')
        ctx.tg.send_chat_action(chat_id, action)

    if step_type == 'barcode':
        send_barcode_message(ctx, chat_id, step_data, options, user_id)
    elif step_type == 'poll':
        handle_poll_message(ctx, chat_id, step_data, options, step_number, user_id)
    elif step_type == 'quiz':
        handle_quiz_message(ctx, chat_id, step_data, options, step_number, user_id)
    elif step_type == 'dice':
        handle_dice_message(ctx, chat_id, step_data, options, step_number, user_id)
    elif is_media_type(step_type_value):
        step_data_for_media = dict(step_data)
        if user_id:
            caption = get_step_value(step_data_for_media, 'content') or ''
            if caption:
                step_data_for_media['content'] = users.replace_tags_in_text(
                    ctx, caption, user_id)
        send_media_message(ctx, chat_id, step_data_for_media, step_type, options)
    else:
        message_text = processed_content or ''
        if message_text.strip() != '':
            if not has_effects:
                message_id = ctx.tg.send_message(chat_id, message_text, options)
                _pin_if_needed(ctx, chat_id, message_id, should_pin)
            else:
                try:
                    initial_text = effects.get_initial_text(message_text)
                    initial_message_id = ctx.tg.send_message(
                        chat_id, initial_text if initial_text else ' ', options)

                    if initial_message_id:
                        time.sleep(0.5)
                        try:
                            effects_processed = effects.process_effects(
                                ctx.tg, chat_id, initial_message_id,
                                message_text, user_id,
                                edit_options=options)
                            if effects_processed:
                                _pin_if_needed(ctx, chat_id, initial_message_id,
                                               should_pin)
                        except Exception:  # noqa: BLE001
                            final_text = effects.get_final_text(message_text)
                            if final_text and final_text.strip() != '':
                                ctx.tg.edit_message_text(chat_id, initial_message_id,
                                                         final_text, options)
                except Exception:  # noqa: BLE001
                    ctx.tg.send_message(chat_id,
                                        effects.get_final_text(message_text),
                                        options)

    if user_id and scroll_value == 'auto':
        delay = parse_delay(delay_value)
        if delay > 0:
            time.sleep(delay)

        next_step = 0
        next_step_value = get_step_value(step_data, 'next_step') or ''
        if str(next_step_value).strip() != '':
            next_step = parse_step_number(next_step_value) or 0
        else:
            next_step = StepManager.get_next_step(ctx, step_number)

        if next_step > 0:
            users.set_user_state(ctx, user_id, next_step)
            send_step_message(ctx, chat_id, next_step, user_id)
        else:
            complete_funnel(ctx, user_id, chat_id)


def update_step_message(ctx: BotContext, chat_id, message_id: int,
                        step_number: int, user_id: Any = None) -> None:
    """Порт MessageHandler.updateStepMessage (:5664): editMessageText."""
    step_data = StepManager.get_step_data(ctx, step_number)
    if not step_data:
        ctx.tg.send_message(
            chat_id, f'<b>⚠️ Шаг {step_number} не найден в воронке</b>')
        return

    step_type_value = get_step_value(step_data, 'type') or ''
    step_type = normalize_type_value(step_type_value)
    content = get_step_value(step_data, 'content') or ''

    options: dict[str, Any] = {'parse_mode': 'HTML'}
    user_role = (users.get_user_tag(ctx, user_id, 'role') or '') if user_id else ''
    reply_keyboard = build_reply_keyboard(ctx, step_number, user_role)
    if reply_keyboard:
        options['reply_markup'] = reply_keyboard['reply_markup']
    else:
        inline_data = StepManager.get_inline_keyboard_for_step(ctx, step_number)
        if inline_data and inline_data.get('inlineKeyboard'):
            options['reply_markup'] = inline_data['inlineKeyboard']

    has_effects = effects.has_effects(content)
    processed_content = content
    if user_id:
        processed_content = users.replace_tags_in_text(ctx, content, user_id)

    if step_type in ('barcode', 'poll', 'quiz', 'dice') \
            or is_media_type(step_type_value):
        ctx.tg.delete_message(chat_id, message_id)
        send_step_message(ctx, chat_id, step_number, user_id)
        return

    message_text = processed_content or ''
    if message_text.strip() == '':
        return

    if not has_effects:
        ctx.tg.edit_message_text(chat_id, message_id, message_text, options)
        return

    try:
        initial_text = effects.get_initial_text(message_text)
        if initial_text:
            ctx.tg.edit_message_text(chat_id, message_id, initial_text, options)
        time.sleep(0.5)
        try:
            effects.process_effects(ctx.tg, chat_id, message_id, message_text,
                                    user_id, edit_options=options)
        except Exception:  # noqa: BLE001
            final_text = effects.get_final_text(message_text)
            if final_text and final_text.strip() != '':
                ctx.tg.edit_message_text(chat_id, message_id, final_text, options)
    except Exception:  # noqa: BLE001
        ctx.tg.edit_message_text(chat_id, message_id,
                                 effects.get_final_text(message_text), options)


def complete_funnel(ctx: BotContext, user_id: Any, chat_id) -> None:
    """Порт MessageHandler.completeFunnel (:6218): финальное сообщение + шаг 0."""
    final_message = functions.get_system_final_message(ctx)
    if final_message and ctx.config.get('systemmessages', True):
        processed = users.replace_tags_in_text(ctx, final_message, user_id)
        ctx.tg.send_message(chat_id, f'<b>{processed}</b>')

    users.set_user_state(ctx, user_id, 0)
