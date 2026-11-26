# ============================================================================
# step_model.py — модель шага воронки.
#
# Хранение: JSON-объект шага в Supabase (Step json). Клавиатуры, опросы,
# квизы, кости — только JSON-структуры, разделителей '|' нет.
#
# При загрузке воронки build_step() ОДИН РАЗ превращает сырой dict
# в объект Step с типизированными полями. Весь дальнейший код (steps/handlers)
# работает только с полями Step.
# ============================================================================

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import config
import keyboards

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Утилиты «строка колонки» (переехали из storage.py — нужны адаптеру)
# ---------------------------------------------------------------------------


def normalize_column_key(header: Any) -> str:
    """lowercase, всё кроме [a-z0-9_] → '_' (Utils.normalizeColumnKey :748)."""
    if not header:
        return ''
    return re.sub(r'[^a-z0-9_]', '_', str(header).lower().strip())


def get_step_value(step_data: Optional[dict], column_name: Any) -> Any:
    """Порт Utils.getStepValue: точное имя → normalized → скан по normalized.

    Только для адаптера build_step (чтение сырых ключей строки);
    обычный код работает с полями Step.
    """
    if not step_data:
        return ''
    if column_name in step_data and step_data[column_name] is not None:
        return step_data[column_name]
    normalized = normalize_column_key(column_name)
    if normalized in step_data and step_data[normalized] is not None:
        return step_data[normalized]
    for key, value in step_data.items():
        if normalize_column_key(key) == normalized and value is not None:
            return value
    return ''


def parse_step_number(step_value: Any) -> Optional[int]:
    """Порт Utils.parseStepNumber (:255): parseInt-семантика."""
    if step_value is None or isinstance(step_value, bool):
        return None
    if isinstance(step_value, (int, float)):
        return int(step_value)
    text = str(step_value).strip()
    if not text:
        return None
    if ',' in text:
        text = text.replace(',', '.', 1).split('.')[0]
    match = re.match(r'^[+-]?\d+', text)
    return int(match.group(0)) if match else None


def parse_delay(delay_value: Any) -> float:
    """Порт Utils.getDelay (:222): секунды, запятая→точка, иначе 0."""
    if delay_value is None:
        return 0
    text = str(delay_value).strip()
    if not text:
        return 0
    try:
        delay = float(text.replace(',', '.'))
    except ValueError:
        match = re.match(r'^-?\d+(?:\.\d+)?', text)
        if not match:
            return 0
        delay = float(match.group(0))
    return delay if delay > 0 else 0


def clean_file_id(file_id: Any) -> Any:
    """Порт Utils.cleanFileId: срезает 'File ID:' и подобные префиксы."""
    if not file_id or not isinstance(file_id, str):
        return file_id
    cleaned = file_id.strip()
    for pattern in (r'^File ID:\s*', r'^File id:\s*', r'^file_id:\s*', r'^ID:\s*'):
        if re.match(pattern, cleaned, flags=re.IGNORECASE):
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


_TRUE_VALUES = ('true', '1', 'да', 'yes', 'on')


def should_pin(pin_value: Any, pin_message_value: Any = None) -> bool:
    """pin/pin_message колонка → bool (единое правило для всех типов шагов)."""
    raw = str(pin_value or pin_message_value or '').strip().lower()
    return raw in ('true', '1', 'да', 'yes')


def valid_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.netloc)
    except Exception:  # noqa: BLE001
        return False


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
    'advent': 'advent',
}

# Кириллические алиасы: re.ASCII-очистка вырезает кириллицу,
# поэтому проверяем их по исходной строке до чистки.
_CYRILLIC_TYPE_ALIASES = {
    'адвент': 'advent',
}


def normalize_type_value(type_value: Any) -> str:
    if not type_value:
        return ''
    raw = str(type_value).strip().lower()
    for key, value in _CYRILLIC_TYPE_ALIASES.items():
        if key in raw:
            return value
    # re.ASCII: как в JS \w — только [A-Za-z0-9_] (кириллица вырезается)
    clean = re.sub(r'[^\w\s]', '', str(type_value), flags=re.ASCII)
    clean = re.sub(r'\s+', ' ', clean).strip().lower()
    if clean in _TYPE_MAPPING:
        return _TYPE_MAPPING[clean]
    for key, value in _TYPE_MAPPING.items():
        if key in clean:
            return value
    return clean


# ---------------------------------------------------------------------------
# Poll / Quiz: только объектный формат (JSON внутри шага)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Кости: разбор конфигурации шага (значение броска считается в steps.py)
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


# ---------------------------------------------------------------------------
# Адвент-календарь: разбор полей шага (advent_*)
# ---------------------------------------------------------------------------

ADVENT_DEFAULT_DAYS = 24
ADVENT_DEFAULT_TZ = 3            # МСК
ADVENT_EARLY_TEXT = 'Ещё рано! Дверь {day} откроется в свой день.'
ADVENT_DONE_TEXT = 'Эта дверь уже открыта.'


def parse_advent_days(value: Any) -> int:
    """Количество дверей: строка int, диапазон 1..31, пусто/мусор → 24."""
    days = _as_int(value, ADVENT_DEFAULT_DAYS)
    if not 1 <= days <= 31:
        return ADVENT_DEFAULT_DAYS
    return days


def parse_advent_tz(value: Any) -> int:
    """Смещение часового пояса от UTC (часы), пусто/мусор → 3 (МСК)."""
    if value is None or str(value).strip() == '':
        return ADVENT_DEFAULT_TZ
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return ADVENT_DEFAULT_TZ


def parse_advent_prizes(value: Any) -> list[str]:
    """advent_prizes: JSON-массив строк; индекс 0 = приз за день 1.

    Формат строки: 'Текст приза' либо 'Текст приза | число'
    (после '|' — монеты за открытие двери).
    """
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    text = str(value or '').strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def split_advent_prize(prize: Any) -> tuple[str, Optional[float]]:
    """Строка приза: 'Текст | число' → (текст, число); иначе (строка, None).

    Число после '|' — монеты за открытие двери; если после '|' не число,
    вся строка считается текстом приза.
    """
    text = str(prize or '').strip()
    if '|' not in text:
        return text, None
    title, _, tail = text.partition('|')
    raw = tail.strip().replace(',', '.')
    try:
        amount = float(raw)
    except ValueError:
        return text, None
    return title.strip(), amount


def advent_current_day(start: str, days: int, tz: int,
                       now: Optional[datetime] = None) -> int:
    """Номер текущего дня адвента: сколько дверей уже доступно (кламп 0..days).

    day_index = (дата(now + tz часов) − дата(start)).days + 1;
    <=0 — старт ещё не наступил (ни одна дверь не открыта);
    пустой start — все двери доступны сразу (days).
    """
    if not start or not str(start).strip():
        return days
    try:
        start_date = datetime.strptime(str(start).strip()[:10],
                                       '%Y-%m-%d').date()
    except ValueError:
        # Не распознанная дата — как пустая: все двери доступны.
        return days
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_date = (current + timedelta(hours=tz)).date()
    day_index = (local_date - start_date).days + 1
    if day_index <= 0:
        return 0
    return min(day_index, days)


# ---------------------------------------------------------------------------
# QR: разбор параметров колонки qr (генерация картинки — в steps.py, send-time)
# ---------------------------------------------------------------------------

# Спецификация градиента из пикера: linear-gradient(135deg, #a, #b[, #c…])
# — от 2 до 6 стопов (многоцветный).
_QR_GRADIENT_RE = re.compile(
    r'^linear-gradient\(\s*-?\d+(?:\.\d+)?deg\s*,'
    r'\s*#[0-9A-Fa-f]{3,6}\s*(?:,\s*#[0-9A-Fa-f]{3,6}\s*)+\)$'
)
_QR_RADIAL_GRADIENT_RE = re.compile(
    r'^radial-gradient\(\s*#[0-9A-Fa-f]{3,6}\s*'
    r'(?:,\s*#[0-9A-Fa-f]{3,6}\s*)+\)$'
)


def _qr_color_value(value: str) -> str:
    """Цвет QR-параметра: hex (без '#') или градиент-спецификация.

    Мусор возвращает '' — параметр отбрасывается (как раньше).
    """
    v = (value or '').strip()
    if _QR_GRADIENT_RE.match(v) or _QR_RADIAL_GRADIENT_RE.match(v):
        return v
    hex_value = v.replace('#', '')
    if re.match(r'^[0-9A-Fa-f]{3,6}$', hex_value):
        return hex_value
    return ''


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
            elif clean_key == 'dotradius':
                # Радиус скругления модулей кода: доля стороны (0..0.5).
                try:
                    dot_radius = float(clean_value)
                    if 0 <= dot_radius <= 0.5:
                        params['dotRadius'] = dot_radius
                except ValueError:
                    pass
            elif clean_key == 'eyeradius':
                # Радиус внешнего контура угловых квадратов — свой,
                # независимый от модулей (0..0.5 доли стороны).
                try:
                    eye_radius = float(clean_value)
                    if 0 <= eye_radius <= 0.5:
                        params['eyeRadius'] = eye_radius
                except ValueError:
                    pass
            elif clean_key == 'modulestyle':
                # Стиль модулей поверх радиуса: капля / ромб.
                module_style = clean_value.lower()
                if module_style in ('drop', 'diamond'):
                    params['moduleStyle'] = module_style
            elif clean_key == 'format':
                fmt = clean_value.lower()
                if fmt in ('png', 'jpeg', 'jpg', 'svg'):
                    params['format'] = 'jpeg' if fmt == 'jpg' else fmt
            elif clean_key in ('dark', 'color'):
                color = _qr_color_value(clean_value)
                if color:
                    params['dark'] = color
            elif clean_key in ('light', 'bgcolor'):
                color = _qr_color_value(clean_value)
                if color:
                    params['light'] = color
            elif clean_key in ('ecl', 'eclevel'):
                ecl = clean_value.upper()
                if ecl in ('L', 'M', 'Q', 'H'):
                    params['ecl'] = ecl
            elif clean_key == 'eyecolor':
                # Цвет глаз — только сплошной hex (без '#').
                eye_color = clean_value.replace('#', '')
                if re.match(r'^[0-9A-Fa-f]{3,6}$', eye_color):
                    params['eyeColor'] = eye_color
            elif clean_key == 'eyestyle':
                # 'circle' — просвет и точка глаза тоже скруглены.
                if clean_value.lower() == 'circle':
                    params['eyeStyle'] = 'circle'
            elif clean_key == 'eyeshape':
                # Форма угловых элементов: квадрат / круг / ромб / капля
                # / восьмиугольник.
                eye_shape = clean_value.lower()
                if eye_shape in ('square', 'circle', 'diamond',
                                 'drop', 'octagon'):
                    params['eyeShape'] = eye_shape
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
            elif clean_key == 'logoshape':
                # Форма подложки логотипа: круг / скруглённый квадрат / квадрат.
                logo_shape = clean_value.lower()
                if logo_shape in ('circle', 'rounded', 'square'):
                    params['logoShape'] = logo_shape
            elif clean_key == 'logoplate':
                # Подложка под логотипом: '0' — выкл, логотип с прозрачным
                # фоном ложится на код как есть (иначе вкл, как раньше).
                if clean_value.lower() in ('0', 'off', 'false', 'no'):
                    params['logoPlate'] = False
                elif clean_value.lower() in ('1', 'on', 'true', 'yes'):
                    params['logoPlate'] = True
            elif clean_key == 'logoborderwidth':
                try:
                    logo_border = int(clean_value)
                    if 0 <= logo_border <= 24:
                        params['logoBorderWidth'] = logo_border
                except ValueError:
                    pass
            elif clean_key == 'logobordercolor':
                border_color = clean_value.replace('#', '')
                if re.match(r'^[0-9A-Fa-f]{3,6}$', border_color):
                    params['logoBorderColor'] = border_color
            elif clean_key == 'bgimageurl':
                if clean_value and \
                        clean_value.lower() not in ('none', 'false', 'null'):
                    params['bgImageUrl'] = clean_value
            elif clean_key == 'bgblur':
                try:
                    bg_blur = int(clean_value)
                    if 0 <= bg_blur <= 64:
                        params['bgBlur'] = bg_blur
                except ValueError:
                    pass
            elif clean_key == 'framecolor':
                frame_color = clean_value.replace('#', '')
                if re.match(r'^[0-9A-Fa-f]{3,6}$', frame_color):
                    params['frameColor'] = frame_color
            elif clean_key == 'framewidth':
                try:
                    frame_width = int(clean_value)
                    if 1 <= frame_width <= 48:
                        params['frameWidth'] = frame_width
                except ValueError:
                    pass
            elif clean_key == 'captionpos':
                if clean_value.lower() == 'inside':
                    params['captionPos'] = 'inside'
            elif clean_key == 'captionstyle':
                caption_style = clean_value.lower()
                if caption_style in ('pill', 'banner'):
                    params['captionStyle'] = caption_style
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
                # Градиент для текста подписи не рисуем — только сплошной hex.
                caption_color = clean_value.replace('#', '')
                if re.match(r'^[0-9A-Fa-f]{3,6}$', caption_color):
                    params['captionFontColor'] = caption_color

        return params
    except Exception as error:  # noqa: BLE001
        logger.error('Error parsing QR config: %s', error)
        return {}


# ---------------------------------------------------------------------------
# Модель шага
# ---------------------------------------------------------------------------

@dataclass
class PollConfig:
    """Опрос: всё уже разобрано, send-time только отправка."""
    question: str = 'Вопрос'
    answers: list[str] = field(default_factory=list)
    multiple: bool = False
    anonymous: bool = True
    logic_values: list[int] = field(default_factory=list)
    steps: list[int] = field(default_factory=list)
    no_transition: bool = False     # «Без перехода»: воронка завершается
    open_period: Optional[int] = None
    close_date: Optional[int] = None


@dataclass
class QuizConfig:
    """Викторина: correct_option — индекс правильного ответа (0..)."""
    question: str = 'Вопрос'
    answers: list[str] = field(default_factory=list)
    correct_option: int = 0
    explanation: str = ''
    anonymous: bool = True
    logic_values: list[int] = field(default_factory=list)
    steps: list[int] = field(default_factory=list)
    no_transition: bool = False     # «Без перехода»: воронка завершается
    open_period: Optional[int] = None
    close_date: Optional[int] = None


@dataclass
class DiceConfig:
    """Кости/слот: emoji + таблица ветвления (значение броска → шаг)."""
    emoji: str = '🎲'
    logic_values: Optional[list[int]] = None
    steps: Optional[list[int]] = None


@dataclass
class BarcodeConfig:
    """QR: данные (могут содержать {теги} — подставляются при отправке)
    и заранее разобранные настройки картинки."""
    data: str = ''
    options: dict = field(default_factory=dict)
    qr_config: dict = field(default_factory=dict)


@dataclass
class AdventConfig:
    """Адвент-календарь: расписание дверей, призы, часовой пояс, тексты."""
    start: str = ''                 # 'YYYY-MM-DD'; пусто — все двери сразу
    days: int = ADVENT_DEFAULT_DAYS     # количество дверей (1..31)
    tz: int = ADVENT_DEFAULT_TZ         # смещение от UTC в часах (3 = МСК)
    prizes: list[str] = field(default_factory=list)  # индекс 0 = день 1
    early: str = ADVENT_EARLY_TEXT     # тап на будущую дверь ({day})
    done: str = ADVENT_DONE_TEXT       # повторное открытие двери


@dataclass
class Step:
    """Шаг воронки в нормальном виде: типизированные поля вместо колонок.

    Пустые/магические значения старой таблицы уже преобразованы:
      next_step '' → next=None (без авто-перехода; у интерактива — current+1);
      next_step '0' → next=0 (при scroll=auto — конец воронки),
      scroll 'auto' → auto=True (переход только если задан next_step > 0),
      delay '0'     → delay=0.0,
      pin 'TRUE'    → pin=True.
    """
    number: int
    funnel_id: str
    type: str = ''                     # нормализованный тип (text/poll/…)
    type_value: str = ''               # исходное имя типа (для input-сохранения)
    text: str = ''                     # контент; теги подставляются при отправке
    next: Optional[int] = None         # явный переход; None → у интерактива current+1
    auto: bool = False                 # scroll == 'auto' — автопереход
    delay: float = 0.0                 # пауза перед автопереходом, сек
    pin: bool = False                  # закрепить сообщение
    calc_formula: str = ''             # формула calc после реакции пользователя
    poll: Optional[PollConfig] = None
    quiz: Optional[QuizConfig] = None
    dice: DiceConfig = field(default_factory=DiceConfig)
    barcode: Optional[BarcodeConfig] = None
    advent: Optional[AdventConfig] = None
    file_urls: list[str] = field(default_factory=list)
    file_ids: list[str] = field(default_factory=list)
    reply_keyboard: Optional[dict] = None    # {'parsedData', 'step', 'funnelId'}
    inline_keyboard: Optional[dict] = None   # {'inlineKeyboard', …, 'parsedData'}


# ---------------------------------------------------------------------------
# Адаптер: сырая строка (старый табличный формат) → Step. Вызывается один
# раз при индексации воронки (storage.build_step_entry).
#
# Поддерживаются оба формата значений:
#   старый — строки колонок ('5', 'TRUE', 'вопрос|да,нет|…', поля poll_*);
#   новый — типизированные JSON-значения (int/bool/списки/объекты).
# ---------------------------------------------------------------------------

def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE_VALUES


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def _int_list(value: Any) -> list[int]:
    """JSON-список (числа или цифровые строки) → list[int]; мусор отбрасывается."""
    if not isinstance(value, (list, tuple)):
        return []
    out: list[int] = []
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            out.append(int(item))
            continue
        try:
            out.append(int(str(item).strip()))
        except (ValueError, TypeError):
            continue
    return out


def _int_list_or_none(value: Any) -> Optional[list[int]]:
    if value is None:
        return None
    out = _int_list(value)
    return out or None


def _timing_period(value: Any) -> Optional[int]:
    """open_period: Telegram принимает только 5–86400 с."""
    if value is None or value == '':
        return None
    period = _as_int(value, 0)
    return period if 5 <= period <= 86400 else None


def _timing_close(value: Any) -> Optional[int]:
    """close_date: unix-timestamp (новый формат) или строка даты (старый)."""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return parse_close_date(value)


def _build_poll(data: dict) -> Optional[PollConfig]:
    # Опрос — только объект (JSON); плоские поля/строки не поддерживаются.
    poll_raw = get_step_value(data, 'poll')
    if not isinstance(poll_raw, dict) or not poll_raw:
        return None
    return PollConfig(
        question=str(poll_raw.get('question') or 'Вопрос'),
        answers=[str(a) for a in (poll_raw.get('answers') or [])],
        multiple=_as_bool(poll_raw.get('multiple')),
        anonymous=_as_bool(poll_raw.get('anonymous'), True),
        logic_values=_int_list(poll_raw.get('logic_values')),
        # steps выровнен с answers: 0 = ветки нет.
        steps=_int_list(poll_raw.get('steps')),
        no_transition=_as_bool(poll_raw.get('no_transition')),
        open_period=_timing_period(poll_raw.get('open_period')),
        close_date=_timing_close(poll_raw.get('close_date')),
    )


def _build_quiz(data: dict) -> Optional[QuizConfig]:
    # Квиз — только объект (JSON); плоские поля/строки не поддерживаются.
    quiz_raw = get_step_value(data, 'quiz')
    if not isinstance(quiz_raw, dict) or not quiz_raw:
        return None
    answers = [str(a) for a in (quiz_raw.get('answers') or [])]
    correct = _as_int(quiz_raw.get('correct'), 0)
    if not (0 <= correct < len(answers)):
        correct = 0
    return QuizConfig(
        question=str(quiz_raw.get('question') or 'Вопрос'),
        answers=answers,
        correct_option=correct,
        explanation=str(quiz_raw.get('explanation') or ''),
        anonymous=_as_bool(quiz_raw.get('anonymous'), True),
        logic_values=_int_list(quiz_raw.get('logic_values')),
        # steps выровнен с answers: 0 = ветки нет.
        steps=_int_list(quiz_raw.get('steps')),
        no_transition=_as_bool(quiz_raw.get('no_transition')),
        open_period=_timing_period(quiz_raw.get('open_period')),
        close_date=_timing_close(quiz_raw.get('close_date')),
    )


def _build_dice(data: dict) -> DiceConfig:
    dice_raw = get_step_value(data, 'dice')
    if isinstance(dice_raw, dict):
        return DiceConfig(
            emoji=parse_dice_value(dice_raw.get('emoji')),
            logic_values=_int_list_or_none(dice_raw.get('logic_values')),
            steps=_int_list_or_none(dice_raw.get('steps')),
        )
    return DiceConfig(
        emoji=parse_dice_value(dice_raw),
        logic_values=parse_dice_logic_value(
            get_step_value(data, 'dice_logic_value')),
        steps=parse_dice_step_mapping(get_step_value(data, 'dice_step')),
    )


def _build_barcode(data: dict) -> BarcodeConfig:
    # Новый формат: barcode = {options: {...}, qr: {...}}.
    barcode_raw = get_step_value(data, 'barcode')
    if isinstance(barcode_raw, dict):
        options = dict(barcode_raw.get('options') or {})
        qr_config = dict(barcode_raw.get('qr') or {})
        options.setdefault('format', 'png')
        return BarcodeConfig(data=str(get_step_value(data, 'content') or ''),
                             options=options, qr_config=qr_config)

    options: dict[str, Any] = {}

    size = str(get_step_value(data, 'barcode_size') or '').strip()
    if size:
        try:
            options['size'] = int(size)
        except ValueError:
            pass
    color = str(get_step_value(data, 'barcode_color') or '').strip()
    if color:
        options['color'] = color.replace('#', '')
    bg = str(get_step_value(data, 'barcode_bg_color') or '').strip()
    if bg:
        options['bgColor'] = bg.replace('#', '')
    logo = str(get_step_value(data, 'barcode_logo') or '').strip()
    if logo:
        options['logoUrl'] = logo
    logo_size = str(get_step_value(data, 'barcode_logo_size') or '').strip()
    if logo_size:
        try:
            options['logoSizeRatio'] = float(logo_size)
        except ValueError:
            pass
    ecl = str(get_step_value(data, 'barcode_ecl') or '').strip()
    if ecl:
        options['ecl'] = ecl
    options['format'] = 'png'

    qr_raw = str(get_step_value(data, 'qr') or '').strip()
    qr_config = parse_qr_config(qr_raw) if qr_raw else {}
    # Данные QR — колонка content; {теги} подставляются при отправке.
    return BarcodeConfig(data=str(get_step_value(data, 'content') or ''),
                         options=options, qr_config=qr_config)


def _build_advent(data: dict) -> AdventConfig:
    # Строковые поля шага advent_* → типизированный конфиг при загрузке.
    early = str(get_step_value(data, 'advent_early') or '').strip()
    done = str(get_step_value(data, 'advent_done') or '').strip()
    return AdventConfig(
        start=str(get_step_value(data, 'advent_start') or '').strip(),
        days=parse_advent_days(get_step_value(data, 'advent_days')),
        tz=parse_advent_tz(get_step_value(data, 'advent_tz')),
        prizes=parse_advent_prizes(get_step_value(data, 'advent_prizes')),
        early=early or ADVENT_EARLY_TEXT,
        done=done or ADVENT_DONE_TEXT,
    )


def _build_reply_keyboard(data: dict, number: int,
                          funnel_id: str) -> Optional[dict]:
    # Только JSON: {buttons, settings} (dict или JSON-строка).
    keyboard_raw = get_step_value(data, 'regular_keyboard')
    if keyboard_raw is None or keyboard_raw == '':
        return None
    if isinstance(keyboard_raw, str) and not keyboard_raw.strip():
        return None
    parsed = keyboards.parse_regular_keyboard_data(keyboard_raw)
    if not parsed or not parsed['buttons']:
        return None
    return {'parsedData': parsed, 'step': number, 'funnelId': funnel_id}


def _build_purchase_texts(data: dict) -> dict:
    """Тексты кнопок purchase: JSON шага `purchase_buttons`.

    Формат: {"0_0": {"ok": "...", "fail": "..."}, "0_1": {...}},
    ключ — позиция кнопки row_col (свои тексты на каждую кнопку).
    """
    raw = str(get_step_value(data, 'purchase_buttons') or '').strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _build_inline_keyboard(data: dict, number: int,
                           funnel_id: str) -> Optional[dict]:
    # Только JSON: [[{…}]] (список или JSON-строка).
    inline_raw = get_step_value(data, 'inline_keyboard')
    if inline_raw is None or inline_raw == '':
        return None
    if isinstance(inline_raw, str) and not inline_raw.strip():
        return None
    channel = str(get_step_value(data, 'channel') or '')
    parsed = keyboards.parse_inline_keyboard_data(inline_raw)
    if not parsed or not parsed['buttons']:
        return None
    built = keyboards.build_inline_keyboard_from_parsed_data(parsed, channel)
    return {
        'inlineKeyboard': built,
        'step': number,
        'funnelId': funnel_id,
        'channel': channel,
        'subscriptionMessage': str(get_step_value(data, 'subscription') or ''),
        'noSubscriptionMessage': str(get_step_value(data, 'no_subscription') or ''),
        'purchaseButtons': _build_purchase_texts(data),
        'parsedData': parsed,
    }


def _build_file_lists(data: dict) -> tuple[list[str], list[str]]:
    urls: list[str] = []
    file_url = str(get_step_value(data, 'file_url') or '')
    if file_url.strip():
        urls = [u.strip() for u in file_url.split(',') if u.strip()]
        urls = [u for u in urls if valid_http_url(u)]

    file_ids: list[str] = []
    file_id = get_step_value(data, 'file_id')
    if file_id and isinstance(file_id, str):
        file_id = clean_file_id(file_id)
    if str(file_id or '').strip():
        file_ids = [f.strip() for f in str(file_id).split(',') if f.strip()]
        file_ids = [f for f in file_ids
                    if f and not f.startswith(('http://', 'https://'))
                    and re.match(r'^[A-Za-z0-9_\-]+$', f)]
    return urls, file_ids


def build_step(data: Optional[dict], number: int, funnel_id: str) -> Step:
    """Сырая строка шага (старый формат колонок) → Step. Один раз при загрузке."""
    raw = dict(data or {})

    step = Step(
        number=number,
        funnel_id=funnel_id,
        type=normalize_type_value(get_step_value(raw, 'type')),
        type_value=str(get_step_value(raw, 'type_value') or ''),
        text=str(get_step_value(raw, 'content') or ''),
        calc_formula=str(get_step_value(raw, 'calc_formula') or '').strip(),
        pin=should_pin(get_step_value(raw, 'pin'),
                       get_step_value(raw, 'pin_message')),
        dice=_build_dice(raw),
        reply_keyboard=_build_reply_keyboard(raw, number, funnel_id),
        inline_keyboard=_build_inline_keyboard(raw, number, funnel_id),
    )

    # Явный переход: пусто → None (у интерактива current+1; при auto — без
    # перехода); непустое значение парсим (в т.ч. '0' — конец при auto).
    raw_next = str(get_step_value(raw, 'next_step') or '').strip()
    if raw_next == '':
        step.next = None
    else:
        parsed_next = parse_step_number(raw_next)
        step.next = parsed_next if parsed_next is not None else 0

    step.auto = str(get_step_value(raw, 'scroll') or '').strip().lower() == 'auto'
    step.delay = parse_delay(get_step_value(raw, 'delay'))

    step.poll = _build_poll(raw)
    step.quiz = _build_quiz(raw)
    if step.type == 'barcode':
        step.barcode = _build_barcode(raw)
    if step.type == 'advent':
        step.advent = _build_advent(raw)
    step.file_urls, step.file_ids = _build_file_lists(raw)

    return step

