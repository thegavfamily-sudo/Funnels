# ============================================================================
# steps.py — StepManager + sendStepMessage/updateStepMessage + медиа/баркод/
#            опросы/кости
#
# Вся работа с шагами идёт через step_model.Step (типизированные поля):
# строки старой таблицы парсятся ОДИН РАЗ при загрузке воронки
# (storage.build_step_entry → step_model.build_step), send-time не парсит.
#
# Порты из apps_script/БОТ_готовый.gs:
#   Utils слоты/кости            :451-565
#   Utils barcode/QR             :1120-1381
#   StepManager                  :3609
#   sendMediaMessage             :4244
#   sendBarcodeMessage           :4439
#   Poll/Quiz/Dice managers      :1730-2121
#   MessageHandler.sendStepMessage :5503
#   updateStepMessage            :5664
#   completeFunnel               :6218
# ============================================================================

from __future__ import annotations

import logging
import random
import re
import threading
import time
from typing import Any, Optional

import config
import effects
import functions
import keyboards
import users
from step_model import (Step, advent_current_day, split_advent_prize,
                        valid_http_url)
from storage import BotContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Кости / слоты: рантайм значения броска (конфиг ветвления — в Step.dice)
# ---------------------------------------------------------------------------

# Длительность анимации sendDice в клиенте Telegram (сек) — с запасом, чтобы
# переход (следующий шаг) не приходил раньше её окончания; далее
# process_dice_result добавляет ~1 с паузы, как в предпросмотре.
# Слоты крутятся дольше.
DICE_ANIMATION_SECONDS = {'🎰': 3.5}
DICE_ANIMATION_DEFAULT_SECONDS = 2.0


def dice_animation_seconds(emoji: Any) -> float:
    return DICE_ANIMATION_SECONDS.get(emoji, DICE_ANIMATION_DEFAULT_SECONDS)


def resolve_dice_next_step(step: Step, dice_value: Any) -> Optional[int]:
    """Явный маппинг «исход → шаг» из конфига dice.

    None — маппинга нет или у исхода нет пары (вызывающий идёт на следующий
    шаг); int — ответ конфига, 0 = без перехода (воронка завершается), как
    в форме админки («Без перехода»).
    """
    logic_values = step.dice.logic_values
    step_mapping = step.dice.steps
    if not logic_values or not step_mapping \
            or len(logic_values) != len(step_mapping):
        return None
    lookup = dice_value
    if step.dice.emoji == '🎰':
        # 🎰: логическое значение — номер комбинации (0 = разные символы).
        symbols = get_slot_symbols(dice_value)
        lookup = (get_same_symbol_number(symbols)
                  if are_all_symbols_same(symbols) else 0)
    if lookup not in logic_values:
        return None
    return step_mapping[logic_values.index(lookup)]


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


# ---------------------------------------------------------------------------
# Barcode / QR: локальная генерация картинки (qrcode + Pillow)
# ---------------------------------------------------------------------------

def _qr_color(value: Any, default: str) -> str:
    """'#abc'/'abc' → '#abc'; мусор → default."""
    color = str(value or '').strip().replace('#', '')
    if not color:
        return '#' + default
    if len(color) in (3, 6) and all(c in '0123456789abcdefABCDEF' for c in color):
        return '#' + color
    return '#' + default


def _qr_int(value: Any, default: int, low: int, high: int) -> int:
    """Число из параметров; мусор/выход за диапазон → default."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


def _qr_hex(value: Any) -> Optional[str]:
    """'#abc'/'abc' → '#abc'; пусто/мусор → None (параметр не задан)."""
    color = str(value or '').strip().replace('#', '')
    if color and len(color) in (3, 6) \
            and all(c in '0123456789abcdefABCDEF' for c in color):
        return '#' + color
    return None


def _qr_rgb(hex_color: Any) -> tuple:
    """'#abc'/'#aabbcc' → (r, g, b); мусор → чёрный."""
    value = str(hex_color or '000').lstrip('#')
    if len(value) == 3:
        value = ''.join(c * 2 for c in value)
    if len(value) != 6:
        value = '000000'
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _qr_drop_corner(modules: Any, row: int, col: int, count: int) -> str:
    """Угол «засова» для moduleStyle='drop'.

    Первая тёмная сторона по приоритету (вниз, вправо, вверх, влево);
    угол — пересечение этой стороны со следующей по приоритету
    (вниз∩вправо = юго-восток и т.д.). Тёмных соседей нет —
    юго-восточный угол (правый-нижний).
    """
    if row + 1 < count and modules[row + 1][col]:
        return 'se'
    if col + 1 < count and modules[row][col + 1]:
        return 'ne'
    if row > 0 and modules[row - 1][col]:
        return 'nw'
    if col > 0 and modules[row][col - 1]:
        return 'sw'
    return 'se'


def _qr_diamond(draw: Any, bounds: tuple, fill: int) -> None:
    """Ромб по серединам сторон прямоугольника bounds — как style=diamond."""
    x0, y0, x1, y1 = bounds
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    draw.polygon([(cx, y0), (x1, cy), (cx, y1), (x0, cy)], fill=fill)


def _qr_octagon(draw: Any, bounds: tuple, cut: float, fill: int) -> None:
    """Восьмиугольник: прямоугольник bounds со срезанными углами cut."""
    x0, y0, x1, y1 = bounds
    draw.polygon([
        (x0 + cut, y0), (x1 - cut, y0),
        (x1, y0 + cut), (x1, y1 - cut),
        (x1 - cut, y1), (x0 + cut, y1),
        (x0, y1 - cut), (x0, y0 + cut),
    ], fill=fill)


def _qr_draw_eyes(draw: Any, qr: Any, eye_radius: float,
                  eye_style: str = '', eye_shape: str = '') -> None:
    """Три угловых глаза: внешний контур 7×7 + просвет 5×5 + точка 3×3.

    Внешний контур скругляется на долю eye_radius от стороны в 7 модулей.
    eye_style='circle' — просвет и точка тоже скруглены той же долей
    от СВОЕЙ стороны (r_hole ≤ 2.5·box, r_dot ≤ 1.5·box); иначе —
    квадратные, как в превью админки.
    eye_shape задаёт форму целиком: 'square' — прямые углы (eye_radius
    не берётся), 'circle' — круги, 'diamond' — ромбы, 'octagon' —
    восьмиугольники (там eye_radius и eye_style не участвуют);
    'drop' — капля: три угла по радиусу (при нулевом — 0.5), внутренний
    угол (в сторону центра кода) прямой. Геометрия та же, что в превью.
    """
    box = max(1, int(qr.box_size))
    border = max(0, int(qr.border))
    count = qr.modules_count
    offset = border * box
    far = (border + count - 7) * box
    origins = ((offset, offset), (far, offset), (offset, far))
    if eye_shape in ('circle', 'diamond', 'octagon'):
        for x0, y0 in origins:
            outer = (x0, y0, x0 + 7 * box - 1, y0 + 7 * box - 1)
            hole = (x0 + box, y0 + box,
                    x0 + 6 * box - 1, y0 + 6 * box - 1)
            dot = (x0 + 2 * box, y0 + 2 * box,
                   x0 + 5 * box - 1, y0 + 5 * box - 1)
            if eye_shape == 'circle':
                draw.ellipse(outer, fill=255)
                draw.ellipse(hole, fill=0)
                draw.ellipse(dot, fill=255)
            elif eye_shape == 'diamond':
                _qr_diamond(draw, outer, 255)
                _qr_diamond(draw, hole, 0)
                _qr_diamond(draw, dot, 255)
            else:  # 'octagon'
                _qr_octagon(draw, outer, 1.5 * box, 255)
                _qr_octagon(draw, hole, 1.0 * box, 0)
                _qr_octagon(draw, dot, 0.5 * box, 255)
        return
    eye_px = (0 if eye_shape == 'square'
              else int(round(7 * box * eye_radius)))
    if eye_shape == 'drop':
        # Три угла по радиусу (при нулевом — 0.5, чтобы капля читалась).
        eye_px = (int(round(7 * box * eye_radius)) if eye_radius > 0
                  else int(round(3.5 * box)))
    eye_px = min(eye_px, (7 * box) // 2)
    hole_r = int(min(round(5 * box * eye_radius), 2.5 * box))
    dot_r = int(min(round(3 * box * eye_radius), 1.5 * box))
    rounded_holes = (eye_style == 'circle')
    for idx, (x0, y0) in enumerate(origins):
        outer = (x0, y0, x0 + 7 * box - 1, y0 + 7 * box - 1)
        if eye_px > 0:
            draw.rounded_rectangle(outer, radius=eye_px, fill=255)
        else:
            draw.rectangle(outer, fill=255)
        if eye_shape == 'drop' and eye_px > 0:
            # Внутренний угол (ближе к центру кода) возвращаем квадратом.
            x1, y1 = outer[2], outer[3]
            if idx == 0:  # глаз слева-сверху — прямой юго-восток
                corner = (x1 - eye_px + 1, y1 - eye_px + 1, x1, y1)
            elif idx == 1:  # справа-сверху — юго-запад
                corner = (x0, y1 - eye_px + 1, x0 + eye_px - 1, y1)
            else:  # слева-снизу — северо-восток
                corner = (x1 - eye_px + 1, y0, x1, y0 + eye_px - 1)
            draw.rectangle(corner, fill=255)
        hole = (x0 + box, y0 + box, x0 + 6 * box - 1, y0 + 6 * box - 1)
        if rounded_holes and hole_r > 0:
            draw.rounded_rectangle(hole, radius=hole_r, fill=0)
        else:
            draw.rectangle(hole, fill=0)
        dot = (x0 + 2 * box, y0 + 2 * box, x0 + 5 * box - 1, y0 + 5 * box - 1)
        if rounded_holes and dot_r > 0:
            draw.rounded_rectangle(dot, radius=dot_r, fill=255)
        else:
            draw.rectangle(dot, fill=255)


def _qr_modules_mask(qr: Any, dot_radius: float, eye_radius: float,
                     module_style: str = '', eye_style: str = '',
                     eye_shape: str = '') -> Any:
    """Маска кода (белые модули): скругление/стили модулей + три глаза.

    dot_radius — доля стороны модуля (0..0.5): 0.5 даёт круги.
    module_style: 'diamond' — ромб по серединам сторон ячейки
    (dotRadius игнорируется); 'drop' — база по dotRadius, один угол
    «засовывается» квадратом (см. _qr_drop_corner). Глаза всегда
    рисуются блоком глаз — не ромбом и не каплей.
    eye_radius — независимая доля для внешнего контура глаз; при
    eye_style='circle' просвет и точка тоже скруглены; eye_shape
    (square/circle/diamond) задаёт форму угловых элементов целиком.
    """
    from PIL import Image, ImageDraw

    box = max(1, int(qr.box_size))
    border = max(0, int(qr.border))
    count = qr.modules_count
    side = (count + 2 * border) * box
    mask = Image.new('L', (side, side), 0)
    draw = ImageDraw.Draw(mask)
    radius = int(round(box * dot_radius))
    modules = qr.modules
    for row in range(count):
        for col in range(count):
            if not modules[row][col]:
                continue
            # Глаза рисуем целиком (ниже) — скругляется весь контур.
            if ((row < 7 and col < 7)
                    or (row < 7 and col >= count - 7)
                    or (row >= count - 7 and col < 7)):
                continue
            x0 = (col + border) * box
            y0 = (row + border) * box
            x1, y1 = x0 + box - 1, y0 + box - 1
            if module_style == 'diamond':
                # Ромб: 4 угла — середины сторон ячейки.
                cx = (x0 + x1) / 2.0
                cy = (y0 + y1) / 2.0
                draw.polygon([(cx, y0), (x1, cy), (cx, y1), (x0, cy)],
                             fill=255)
                continue
            if radius > 0:
                draw.rounded_rectangle((x0, y0, x1, y1),
                                       radius=radius, fill=255)
            else:
                draw.rectangle((x0, y0, x1, y1), fill=255)
            if module_style == 'drop' and radius > 0:
                # Одним углом возвращаем квадрат радиуса r (см. направление).
                corner = _qr_drop_corner(modules, row, col, count)
                if corner == 'ne':
                    sq = (x1 - radius + 1, y0, x1, y0 + radius - 1)
                elif corner == 'nw':
                    sq = (x0, y0, x0 + radius - 1, y0 + radius - 1)
                elif corner == 'sw':
                    sq = (x0, y1 - radius + 1, x0 + radius - 1, y1)
                else:  # 'se' — юго-восток (правый-нижний)
                    sq = (x1 - radius + 1, y1 - radius + 1, x1, y1)
                draw.rectangle(sq, fill=255)

    _qr_draw_eyes(draw, qr, eye_radius, eye_style, eye_shape)
    return mask


def _qr_eyes_mask(qr: Any, eye_radius: float, eye_style: str = '',
                  eye_shape: str = '') -> Any:
    """Маска ТОЛЬКО трёх глаз — для отдельного слоя eyeColor."""
    from PIL import Image, ImageDraw

    box = max(1, int(qr.box_size))
    border = max(0, int(qr.border))
    count = qr.modules_count
    side = (count + 2 * border) * box
    mask = Image.new('L', (side, side), 0)
    _qr_draw_eyes(ImageDraw.Draw(mask), qr, eye_radius, eye_style, eye_shape)
    return mask


def _qr_fit_mask(mask: Any, size: int, total_h: int) -> Any:
    """Маску кода по центру квадрата size и продлить полосой до total_h."""
    from PIL import Image

    if mask.width < size:
        field = Image.new('L', (size, size), 0)
        offset = (size - mask.width) // 2
        field.paste(mask, (offset, offset))
        mask = field
    full = Image.new('L', (size, total_h), 0)
    full.paste(mask, (0, 0))
    return full


def _qr_font(size: int, family: Any = None) -> Any:
    from PIL import ImageFont
    if family:
        try:
            return ImageFont.truetype(str(family), size)
        except Exception:  # noqa: BLE001 — шрифт не найден, берём дефолтный
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # noqa: BLE001 — старый Pillow без параметра size
        return ImageFont.load_default()


# Спецификации градиентов из пикера: от 2 до 6 стопов (многоцветный,
# как в форме админки). Линейный — со своим углом, радиальный — без.
_QR_GRADIENT_RE = re.compile(
    r'^linear-gradient\(\s*(-?\d+(?:\.\d+)?deg)\s*,'
    r'\s*#[0-9A-Fa-f]{3,6}\s*(?:,\s*#[0-9A-Fa-f]{3,6}\s*)+\)$'
)
_QR_RADIAL_GRADIENT_RE = re.compile(
    r'^radial-gradient\(\s*#[0-9A-Fa-f]{3,6}\s*'
    r'(?:,\s*#[0-9A-Fa-f]{3,6}\s*)+\)$'
)


def _parse_gradient(spec: Any) -> Optional[tuple]:
    """Спецификация градиента → ('linear', угол, [стопы]) или
    ('radial', None, [стопы]); сплошной цвет/мусор → None."""
    text = str(spec or '').strip()
    m = _QR_GRADIENT_RE.match(text)
    if m:
        colors = re.findall(r'#[0-9A-Fa-f]{3,6}', text)
        if len(colors) < 2:
            return None
        return 'linear', float(m.group(1).replace('deg', '')), colors
    if _QR_RADIAL_GRADIENT_RE.match(text):
        colors = re.findall(r'#[0-9A-Fa-f]{3,6}', text)
        if len(colors) < 2:
            return None
        return 'radial', None, colors
    return None


def _gradient_image(width: int, height: int, angle: float,
                    colors: Any) -> Any:
    """Линейный градиент width×height под CSS-углом (0deg = снизу вверх).

    colors — список HEX-стопов (2+), интерполяция по порядку, как в CSS.
    Метод: горизонтальная полоса → растяжение → поворот на (90 − angle)
    (PIL rotate против часовой) → центральный crop.
    """
    import math

    from PIL import Image, ImageDraw

    def rgb(hex_color: Any) -> tuple:
        v = str(hex_color).lstrip('#')
        if len(v) == 3:
            v = ''.join(ch * 2 for ch in v)
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))

    stops = [rgb(c) for c in colors]
    if len(stops) < 2:  # pragma: no cover — _parse_gradient гарантирует 2+
        stops = stops * 2
    diag = int(math.ceil(math.hypot(width, height))) + 2
    strip = Image.new('RGB', (diag, 1))
    draw = ImageDraw.Draw(strip)
    last = len(stops) - 1
    for x in range(diag):
        pos = (x / max(diag - 1, 1)) * last
        idx = min(int(pos), last - 1)
        t = pos - idx
        col = tuple(int(a + (b - a) * t)
                    for a, b in zip(stops[idx], stops[idx + 1]))
        draw.point((x, 0), fill=col)
    # Растягиваем в квадрат: каждый столбец одного цвета.
    base = strip.resize((diag, diag))
    # CSS угол A → rotate(90 − A): направление (1,0) → (sin A, −cos A).
    rotated = base.rotate(90 - angle, resample=Image.BILINEAR)
    left = (diag - width) // 2
    top = (diag - height) // 2
    return rotated.crop((left, top, left + width, top + height))


def _radial_image(width: int, height: int, colors: Any) -> Any:
    """Радиальный градиент width×height: центр — первый стоп, края и
    углы — последний (2+ HEX-стопов, интерполяция по порядку).

    Метод: карта Image.radial_gradient('L') (чёрный в центре, белый
    по краям, радиус = половина стороны) → RGB-LUT из стопов →
    point() по каждому каналу → Image.merge → растяжение под область
    (в полосе подписи фон продолжается).
    """
    from PIL import Image

    stops = [_qr_rgb(c) for c in colors]
    if len(stops) < 2:  # pragma: no cover — _parse_gradient гарантирует 2+
        stops = stops * 2
    last = len(stops) - 1
    base = Image.radial_gradient('L')
    channels = []
    for channel in range(3):
        lut = []
        for value in range(256):
            pos = (value / 255.0) * last
            idx = min(int(pos), last - 1)
            t = pos - idx
            a, b = stops[idx][channel], stops[idx + 1][channel]
            lut.append(int(a + (b - a) * t))
        channels.append(base.point(lut))
    return Image.merge('RGB', tuple(channels)).resize((width, height))


def _gradient_layer(width: int, height: int, grad: tuple) -> Any:
    """Слой градиента из _parse_gradient: линейный или радиальный."""
    kind, angle, colors = grad
    if kind == 'radial':
        return _radial_image(width, height, colors)
    return _gradient_image(width, height, angle, colors)


# ---------------------------------------------------------------------------
# SVG-вариант QR (format='svg'): та же геометрия, что в PNG, но векторно
# ---------------------------------------------------------------------------

def _svg_num(value: Any) -> str:
    """Число для SVG-атрибута: 110.0 → '110'."""
    try:
        return '%g' % float(value)
    except (TypeError, ValueError):
        return '0'


def _svg_font_family(family: Any) -> str:
    """captionFontFamily → font-family с fallback sans-serif.

    Известные файлы шрифтов маппятся в имена ('DejaVuSans.ttf' →
    'DejaVu Sans'), прочие .ttf/.otf — имя без расширения, обычные
    имена — как есть. Всегда с запасным sans-serif.
    """
    name = str(family or '').strip()
    if not name:
        return 'sans-serif'
    known = {
        'dejavusans.ttf': 'DejaVu Sans',
        'dejavusans-bold.ttf': 'DejaVu Sans',
        'dejavuseri.ttf': 'DejaVu Serif',
        'dejavusansmono.ttf': 'DejaVu Sans Mono',
        'arial.ttf': 'Arial',
        'times.ttf': 'Times New Roman',
        'verdana.ttf': 'Verdana',
        'roboto.ttf': 'Roboto',
        'segoeui.ttf': 'Segoe UI',
    }
    base = known.get(name.lower())
    if base is None:
        base = name
        if name.lower().endswith(('.ttf', '.otf')):
            base = name.rsplit('.', 1)[0]
    return base + ', sans-serif'


def _svg_gradient(gid: str, grad: tuple) -> str:
    """<linearGradient>/<radialGradient> из _parse_gradient.

    Линейный: база x1=0 y1=1 → x2=0 y2=0 (это CSS 0deg, снизу вверх) +
    gradientTransform rotate(A 0.5 0.5); объектные координаты
    (objectBoundingBox, значение по умолчанию). Радиальный: от центра до
    половины стороны (r=0.5 bbox ≈ half side, как PIL radial_gradient),
    углы — последний стоп.
    """
    kind, angle, colors = grad
    last = max(len(colors) - 1, 1)
    stops = ''.join(
        '<stop offset="%s" stop-color="%s"/>'
        % (_svg_num(i / last), _qr_color(color, '000000'))
        for i, color in enumerate(colors))
    if kind == 'radial':
        return ('<radialGradient id="%s" cx="0.5" cy="0.5" r="0.5" '
                'color-interpolation="sRGB">%s</radialGradient>'
                % (gid, stops))
    return ('<linearGradient id="%s" x1="0" y1="1" x2="0" y2="0" '
            'gradientTransform="rotate(%s 0.5 0.5)" '
            'color-interpolation="sRGB">%s</linearGradient>'
            % (gid, _svg_num(angle), stops))


def _svg_fetch_image(url: Any) -> tuple:
    """Картинка по URL → (data URI base64, bytes); ошибка → (None, None).

    SVG должен быть самодостаточным для печати, поэтому фон и логотип
    встраиваются как data URI. MIME — из Content-Type, иначе по
    расширению URL; ошибка скачивания — как в PNG-пути (warning,
    картинка пропускается).
    """
    import base64

    import requests

    try:
        resp = requests.get(str(url), timeout=15)
        if resp.status_code != 200 or not resp.content:
            return None, None
        mime = ''
        headers = getattr(resp, 'headers', None)
        if headers is not None:
            try:
                content_type = headers.get('Content-Type')
            except Exception:  # noqa: BLE001 — нестандартные headers
                content_type = None
            if isinstance(content_type, str) and content_type:
                mime = content_type.split(';')[0].strip().lower()
        if not mime.startswith('image/'):
            path = str(url).split('?')[0].split('#')[0]
            tail = path.rsplit('/', 1)[-1]
            ext = '.' + tail.rsplit('.', 1)[-1].lower() if '.' in tail else ''
            mime = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.webp': 'image/webp',
                '.bmp': 'image/bmp',
                '.svg': 'image/svg+xml',
            }.get(ext, 'image/png')
        b64 = base64.b64encode(resp.content).decode('ascii')
        return 'data:%s;base64,%s' % (mime, b64), resp.content
    except Exception as error:  # noqa: BLE001 — картинка не обязательна
        logger.warning('QR svg image fetch skipped (%s): %s', url, error)
        return None, None


def _generate_qr_svg(qr: Any, size: int, total_h: int,
                     p: Optional[dict] = None) -> str:
    """Настоящий SVG QR (format='svg').

    Та же геометрия и пиксели, что в PNG-пути: box_size, border и
    координаты модулей — теми же формулами; фон, рамка, логотип и
    подпись — тем же layout. Всё в пикселях финального полотна;
    результат обязан парситься xml.etree.ElementTree.

    p — параметры, посчитанные общим разбором generate_barcode_image:
    dark/light + градиенты, dot_radius/eye_radius, module_style/eye_style,
    eye_color, bg (has_bg/bg_url/bg_blur_raw), frame (color/width),
    логотип (center_image_url/logo_shape/logo_ratio/border*), подпись
    (caption/caption_pos/caption_style/caption_box/font_*/font_color).
    """
    from io import BytesIO

    from PIL import Image
    from xml.sax.saxutils import escape, quoteattr

    p = p or {}

    def esc(value: Any) -> str:
        return escape(str(value), {'"': '&quot;'})

    def attr(value: Any) -> str:
        return quoteattr(str(value))

    def rect(x: Any, y: Any, w: Any, h: Any, fill: Optional[str] = None,
             rx: Any = None) -> str:
        out = '<rect x="%s" y="%s" width="%s" height="%s"' % (
            _svg_num(x), _svg_num(y), _svg_num(w), _svg_num(h))
        if fill is not None:
            out += ' fill=%s' % attr(fill)
        if rx:
            out += ' rx="%s"' % _svg_num(rx)
        return out + '/>'

    box = max(1, int(qr.box_size))
    border = max(0, int(qr.border))
    count = qr.modules_count
    side = (count + 2 * border) * box
    # Код центрируется на полотне — как _qr_fit_mask.
    pad = max(0, (size - side) // 2)
    modules = qr.modules

    dark = p.get('dark') or '#000000'
    light = p.get('light') or '#ffffff'
    dark_hex = p.get('dark_hex') or dark
    light_hex = p.get('light_hex') or light
    grad_dark = p.get('grad_dark')
    grad_light = p.get('grad_light')
    dark_fill = 'url(#g_dark)' if grad_dark else dark
    light_fill = 'url(#g_light)' if grad_light else light

    defs: list[str] = []
    body: list[str] = []

    # -- фон: rect во всё полотно (сплошной light или градиент) -----------
    if grad_dark:
        defs.append(_svg_gradient('g_dark', grad_dark))
    if grad_light:
        defs.append(_svg_gradient('g_light', grad_light))
    body.append(rect(0, 0, size, total_h, light_fill))

    # -- фоновая картинка: data URI + блюр + подложка light 55% ----------
    if p.get('has_bg'):
        bg_uri, _bg_raw = _svg_fetch_image(p.get('bg_url'))
        if bg_uri:
            blur = (size // 16 if p.get('bg_blur_raw') is None
                    else _qr_int(p.get('bg_blur_raw'), size // 16, 0, 64))
            defs.append('<clipPath id="bgclip"><rect x="0" y="0" '
                        'width="%s" height="%s"/></clipPath>'
                        % (_svg_num(size), _svg_num(total_h)))
            if blur > 0:
                defs.append('<filter id="bgblur"><feGaussianBlur '
                            'stdDeviation="%s"/></filter>' % _svg_num(blur))
            image = ('<image x="0" y="0" width="%s" height="%s" '
                     'preserveAspectRatio="xMidYMid slice" href=%s'
                     % (_svg_num(size), _svg_num(total_h), attr(bg_uri)))
            if blur > 0:
                image += ' filter="url(#bgblur)"'
            body.append(
                '<g clip-path="url(#bgclip)">%s/>'
                '<rect x="0" y="0" width="%s" height="%s" fill=%s '
                'opacity="0.55"/></g>'
                % (image, _svg_num(size), _svg_num(total_h),
                   attr(light_hex)))

    # -- модули (неглазные ячейки): группировка по fill -------------------
    dot_px = int(round(box * float(p.get('dot_radius') or 0)))
    module_style = p.get('module_style') or ''
    groups: dict[str, list] = {}
    for row in range(count):
        for col in range(count):
            if not modules[row][col]:
                continue
            # Три 7×7 глаза рисуем ниже отдельной фигурой.
            if ((row < 7 and col < 7)
                    or (row < 7 and col >= count - 7)
                    or (row >= count - 7 and col < 7)):
                continue
            x0 = pad + (col + border) * box
            y0 = pad + (row + border) * box
            if module_style == 'diamond':
                # Ромб: 4 угла — середины сторон ячейки.
                cx = x0 + box / 2.0
                cy = y0 + box / 2.0
                shape = ('<polygon points="%s,%s %s,%s %s,%s %s,%s"/>'
                         % (_svg_num(cx), _svg_num(y0),
                            _svg_num(x0 + box), _svg_num(cy),
                            _svg_num(cx), _svg_num(y0 + box),
                            _svg_num(x0), _svg_num(cy)))
            else:
                shape = rect(x0, y0, box, box, None, dot_px or None)
                if module_style == 'drop' and dot_px > 0:
                    # Одним углом возвращаем квадрат радиуса r.
                    corner = _qr_drop_corner(modules, row, col, count)
                    if corner == 'ne':
                        sx, sy = x0 + box - dot_px, y0
                    elif corner == 'nw':
                        sx, sy = x0, y0
                    elif corner == 'sw':
                        sx, sy = x0, y0 + box - dot_px
                    else:  # 'se' — юго-восток (правый-нижний)
                        sx, sy = x0 + box - dot_px, y0 + box - dot_px
                    shape += rect(sx, sy, dot_px, dot_px)
            groups.setdefault(dark_fill, []).append(shape)
    for fill_key, shapes in groups.items():
        body.append('<g fill=%s>%s</g>' % (attr(fill_key), ''.join(shapes)))

    # -- три глаза: рамка контуром (stroke) + точка -----------------------
    # SVG не умеет вычитать просвет, поэтому внешний контур 7×7 — rect
    # с обводкой шириной box по центральной траектории 6×6 (со смещением
    # box/2): красится ровно кольцо [x0, x0+7box] × [y0, y0+7box].
    eye_fill = p.get('eye_color') or dark_fill
    eye_shape = p.get('eye_shape') or ''
    eye_radius = float(p.get('eye_radius') or 0)
    eye_px = (0 if eye_shape == 'square'
              else int(round(7 * box * eye_radius)))
    # Внешний rx видимого кольца = eye_px − box/2 (у обводки наружу box/2).
    ring_rx = max(0.0, eye_px - box / 2.0)
    dot_r = (int(min(round(3 * box * eye_radius), 1.5 * box))
             if p.get('eye_style') == 'circle' else 0)
    near = pad + border * box
    far = pad + (border + count - 7) * box
    ring_w = 6 * box

    def diamond_d(cx: float, cy: float, half: float) -> str:
        # Ромб по серединам сторон квадрата half×half от центра (cx, cy).
        return ('M %s,%s L %s,%s L %s,%s L %s,%s Z' % (
            _svg_num(cx), _svg_num(cy - half),
            _svg_num(cx + half), _svg_num(cy),
            _svg_num(cx), _svg_num(cy + half),
            _svg_num(cx - half), _svg_num(cy)))

    def rect_d(x: float, y: float, w: float, h: float) -> str:
        return ('M %s,%s H %s V %s H %s Z' % (
            _svg_num(x), _svg_num(y), _svg_num(x + w),
            _svg_num(y + h), _svg_num(x)))

    def rr_d(x: float, y: float, size: float, tl: float, tr: float,
             br: float, bl: float) -> str:
        # Прямоугольник size×size со скруглениями по углам (0 — прямой).
        out = ['M %s,%s' % (_svg_num(x + tl), _svg_num(y)),
               'L %s,%s' % (_svg_num(x + size - tr), _svg_num(y))]
        if tr > 0:
            out.append('A %s,%s 0 0 1 %s,%s' % (
                _svg_num(tr), _svg_num(tr),
                _svg_num(x + size), _svg_num(y + tr)))
        out.append('L %s,%s' % (_svg_num(x + size), _svg_num(y + size - br)))
        if br > 0:
            out.append('A %s,%s 0 0 1 %s,%s' % (
                _svg_num(br), _svg_num(br),
                _svg_num(x + size - br), _svg_num(y + size)))
        out.append('L %s,%s' % (_svg_num(x + bl), _svg_num(y + size)))
        if bl > 0:
            out.append('A %s,%s 0 0 1 %s,%s' % (
                _svg_num(bl), _svg_num(bl),
                _svg_num(x), _svg_num(y + size - bl)))
        out.append('L %s,%s' % (_svg_num(x), _svg_num(y + tl)))
        if tl > 0:
            out.append('A %s,%s 0 0 1 %s,%s' % (
                _svg_num(tl), _svg_num(tl),
                _svg_num(x + tl), _svg_num(y)))
        out.append('Z')
        return ' '.join(out)

    def oct_d(x: float, y: float, size: float, cut: float) -> str:
        # Восьмиугольник: квадрат size×size со срезанными углами cut.
        right, bottom = x + size, y + size
        return ('M %s,%s L %s,%s L %s,%s L %s,%s L %s,%s L %s,%s '
                'L %s,%s L %s,%s Z') % (
            _svg_num(x + cut), _svg_num(y),
            _svg_num(right - cut), _svg_num(y),
            _svg_num(right), _svg_num(y + cut),
            _svg_num(right), _svg_num(bottom - cut),
            _svg_num(right - cut), _svg_num(bottom),
            _svg_num(x + cut), _svg_num(bottom),
            _svg_num(x), _svg_num(bottom - cut),
            _svg_num(x), _svg_num(y + cut))

    for idx, (x0, y0) in enumerate(((near, near), (far, near), (near, far))):
        cx = x0 + 3.5 * box
        cy = y0 + 3.5 * box
        if eye_shape == 'circle':
            # Кольцо: ellipse r=3box под обводку box → кольцо 2.5..3.5box.
            ring = ('<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="none" '
                    'stroke=%s stroke-width="%s"/>'
                    % (_svg_num(cx), _svg_num(cy), _svg_num(3 * box),
                       _svg_num(3 * box), attr(eye_fill), _svg_num(box)))
            dot = ('<ellipse cx="%s" cy="%s" rx="%s" ry="%s"/>'
                   % (_svg_num(cx), _svg_num(cy), _svg_num(1.5 * box),
                      _svg_num(1.5 * box)))
        elif eye_shape == 'diamond':
            # Ромб кольцом (evenodd): внешний контур минус просвет + точка.
            ring = ('<path d="%s %s" fill-rule="evenodd"/>'
                    % (diamond_d(cx, cy, 3.5 * box),
                       diamond_d(cx, cy, 2.5 * box)))
            dot = '<path d="%s"/>' % diamond_d(cx, cy, 1.5 * box)
        elif eye_shape == 'octagon':
            # Восьмиугольник кольцом (evenodd) + такая же точка.
            ring = ('<path d="%s %s" fill-rule="evenodd"/>'
                    % (oct_d(x0, y0, 7 * box, 1.5 * box),
                       oct_d(x0 + box, y0 + box, 5 * box, 1.0 * box)))
            dot = ('<path d="%s"/>'
                   % oct_d(x0 + 2 * box, y0 + 2 * box, 3 * box, 0.5 * box))
        elif eye_shape == 'drop':
            # Капля: три угла по радиусу (при нулевом — 0.5), внутренний
            # угол (ближе к центру кода) прямой; кольцо — вычитанием.
            r = (7 * box * eye_radius) if eye_radius > 0 else 3.5 * box
            r = min(r, 3.5 * box)
            if idx == 0:  # слева-сверху — прямой юго-восток
                corners = (r, r, 0.0, r)
            elif idx == 1:  # справа-сверху — юго-запад
                corners = (r, r, r, 0.0)
            else:  # слева-снизу — северо-восток
                corners = (r, 0.0, r, r)
            ring = ('<path d="%s %s" fill-rule="evenodd"/>'
                    % (rr_d(x0, y0, 7 * box, *corners),
                       rect_d(x0 + box, y0 + box, 5 * box, 5 * box)))
            dot = rect(x0 + 2 * box, y0 + 2 * box, 3 * box, 3 * box, None,
                       dot_r or None)
        else:
            ring = ('<rect x="%s" y="%s" width="%s" height="%s" fill="none" '
                    'stroke=%s stroke-width="%s"'
                    % (_svg_num(x0 + box / 2.0), _svg_num(y0 + box / 2.0),
                       _svg_num(ring_w), _svg_num(ring_w), attr(eye_fill),
                       _svg_num(box)))
            if ring_rx > 0:
                ring += ' rx="%s"' % _svg_num(ring_rx)
            ring += '/>'
            dot = rect(x0 + 2 * box, y0 + 2 * box, 3 * box, 3 * box, None,
                       dot_r or None)
        body.append('<g fill=%s>%s%s</g>' % (attr(eye_fill), ring, dot))

    # -- декоративная рамка: только вокруг квадрата size×size -------------
    frame_color = p.get('frame_color')
    frame_width = p.get('frame_width') or 0
    if frame_color and frame_width > 0:
        frame_px = max(1, round(frame_width * size / 300))
        half = frame_px / 2.0
        body.append('<rect x="%s" y="%s" width="%s" height="%s" '
                    'fill="none" stroke=%s stroke-width="%s"/>'
                    % (_svg_num(half), _svg_num(half),
                       _svg_num(size - frame_px), _svg_num(size - frame_px),
                       attr(frame_color), _svg_num(frame_px)))

    # -- логотип: контур увеличенной фигурой → подложка light → картинка --
    if p.get('has_logo'):
        logo_uri, logo_raw = _svg_fetch_image(p.get('center_image_url'))
        if logo_uri and logo_raw:
            try:
                logo_w, logo_h = Image.open(BytesIO(logo_raw)).size
                ratio = float(p.get('logo_ratio') or 0.3)
                max_side = max(1, int(size * ratio))
                if max(logo_w, logo_h) > max_side:
                    factor = max_side / float(max(logo_w, logo_h))
                    logo_w = max(1, int(logo_w * factor))
                    logo_h = max(1, int(logo_h * factor))
                pad_l = max(4, max(logo_w, logo_h) // 10)
                shape = p.get('logo_shape') or ''
                if shape in ('circle', 'rounded'):
                    # Квадратная подложка, логотип по центру — как в PNG.
                    plate_w = plate_h = max(logo_w, logo_h) + pad_l * 2
                else:
                    plate_w, plate_h = logo_w + pad_l * 2, logo_h + pad_l * 2
                if shape == 'circle':
                    plate_rx: Any = plate_w / 2.0
                elif shape == 'rounded':
                    plate_rx = max(1, plate_w * 22 // 100)
                elif shape == 'square':
                    plate_rx = 0
                else:
                    # Дефолтная форма — прежнее скругление подложки.
                    plate_rx = pad_l + 4
                logo_border = int(p.get('logo_border_width') or 0)
                bw = (max(1, round(logo_border * size / 300))
                      if logo_border > 0 else 0)
                total_w = plate_w + 2 * bw
                total_l = plate_h + 2 * bw
                ox = (size - total_w) // 2
                oy = (size - total_l) // 2
                if bw:
                    border_color = p.get('logo_border_color') or dark_hex
                    outer_rx = (0 if shape == 'square'
                                else (plate_rx or 0) + bw)
                    body.append(rect(ox, oy, total_w, total_l,
                                     border_color, outer_rx))
                px, py = ox + bw, oy + bw
                body.append(rect(px, py, plate_w, plate_h, light_hex,
                                 plate_rx or None))
                clip = ''
                if plate_rx:
                    # Обрезка картинки по форме подложки.
                    if shape == 'circle':
                        defs.append(
                            '<clipPath id="logo_clip"><circle cx="%s" '
                            'cy="%s" r="%s"/></clipPath>'
                            % (_svg_num(px + plate_w / 2.0),
                               _svg_num(py + plate_h / 2.0),
                               _svg_num(plate_w / 2.0)))
                    else:
                        defs.append(
                            '<clipPath id="logo_clip">%s</clipPath>'
                            % rect(px, py, plate_w, plate_h, None,
                                   plate_rx))
                    clip = ' clip-path="url(#logo_clip)"'
                body.append(
                    '<image x="%s" y="%s" width="%s" height="%s" '
                    'preserveAspectRatio="xMidYMid meet" href=%s%s/>'
                    % (_svg_num(px + (plate_w - logo_w) // 2),
                       _svg_num(py + (plate_h - logo_h) // 2),
                       _svg_num(logo_w), _svg_num(logo_h),
                       attr(logo_uri), clip))
            except Exception as error:  # noqa: BLE001 — логотип не обязателен
                logger.warning('QR svg logo skipped: %s', error)

    # -- подпись: текст + плашка/баннер -----------------------------------
    caption = p.get('caption') or ''
    caption_box = p.get('caption_box')
    if caption and caption_box is not None:
        font_size = int(p.get('font_size') or 12)
        text_w = caption_box[2] - caption_box[0]
        text_h = caption_box[3] - caption_box[1]
        inside = p.get('caption_pos') == 'inside'
        style = p.get('caption_style')
        padx = round(font_size * 0.6)
        pady = round(font_size * 0.35)
        text_left = (size - text_w) // 2
        if inside:
            text_top = size - round(font_size * 0.6) - text_h
        else:
            text_top = size + font_size // 2
        font_color = p.get('font_color')
        text_fill = font_color or '#000000'
        if style == 'banner':
            if inside:
                banner_top = size - (text_h + font_size)
                body.append(rect(0, banner_top, size, size - banner_top,
                                 dark_hex))
                text_top = banner_top + font_size // 2
            else:
                body.append(rect(0, size, size, total_h - size, dark_hex))
            text_fill = font_color or light_hex
        elif style == 'pill' or inside:
            # Внутри без стиля — pill автоматически (иначе текст
            # невозможно отделить от фона/модулей).
            pill_w = text_w + 2 * padx
            pill_h = text_h + 2 * pady
            body.append('<rect x="%s" y="%s" width="%s" height="%s" '
                        'rx="%s" fill=%s opacity="0.92"/>'
                        % (_svg_num(text_left - padx), _svg_num(text_top - pady),
                           _svg_num(pill_w), _svg_num(pill_h),
                           _svg_num(max(1, pill_h // 2)), attr(light_hex)))
            text_fill = font_color or (dark_hex if inside else '#000000')
        body.append(
            '<text text-anchor="middle" x="%s" y="%s" font-size="%s" '
            'font-family=%s fill=%s>%s</text>'
            % (_svg_num(size / 2.0), _svg_num(text_top + text_h),
               _svg_num(font_size),
               attr(_svg_font_family(p.get('font_family'))),
               attr(text_fill), esc(caption)))

    defs_html = '<defs>%s</defs>' % ''.join(defs) if defs else ''
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%s" '
            'height="%s" viewBox="0 0 %s %s">%s%s</svg>'
            % (_svg_num(size), _svg_num(total_h),
               _svg_num(size), _svg_num(total_h), defs_html,
               ''.join(body)))


def generate_barcode_image(data: str, options: Optional[dict] = None,
                           qr_config: Optional[dict] = None) -> Optional[bytes]:
    """QR рисуется прямо в процессе бота (PNG/JPEG в памяти, либо SVG).

    Параметры: size, margin, dark/light (hex или linear-/radial-gradient),
    moduleStyle, eyeRadius/eyeStyle/eyeColor, ecLevel, фон
    (bgImageUrl/bgBlur), рамка (frameColor/frameWidth), centerImageUrl
    (логотип с обводкой), caption (текст, позиция, стиль, шрифт),
    format (png/jpeg/svg).
    """
    try:
        import qrcode as qr_lib
        from io import BytesIO

        from PIL import Image, ImageDraw, ImageFilter, ImageOps

        cfg = config.BARCODE_CONFIG
        options = options or {}
        qr_config = qr_config or {}

        qr_data = str(data or f'USER_{int(time.time() * 1000)}')

        def pick(*keys: str, default: Any = None) -> Any:
            for source in (qr_config, options):
                for key in keys:
                    value = source.get(key)
                    if value not in (None, ''):
                        return value
            return default

        size = _qr_int(pick('size'), cfg['DEFAULT_SIZE'], 16, 4096)
        margin = _qr_int(pick('margin'), cfg['DEFAULT_MARGIN'], 0, 64)
        # Радиус скругления модуля: доля стороны (0..0.5), 0 — квадраты.
        try:
            dot_radius = float(pick('dotRadius', default=0))
        except (TypeError, ValueError):
            dot_radius = 0.0
        dot_radius = min(0.5, max(0.0, dot_radius))
        # Радиус внешнего контура угловых квадратов — независим от модулей.
        try:
            eye_radius = float(pick('eyeRadius', default=0))
        except (TypeError, ValueError):
            eye_radius = 0.0
        eye_radius = min(0.5, max(0.0, eye_radius))
        module_style = str(pick('moduleStyle', default='') or '').strip().lower()
        if module_style not in ('drop', 'diamond'):
            module_style = ''
        eye_style = str(pick('eyeStyle', default='') or '').strip().lower()
        if eye_style != 'circle':
            eye_style = ''
        # Форма угловых элементов целиком: квадрат / круг / ромб / капля
        # / восьмиугольник.
        eye_shape = str(pick('eyeShape', default='') or '').strip().lower()
        if eye_shape not in ('square', 'circle', 'diamond',
                             'drop', 'octagon'):
            eye_shape = ''
        eye_color = _qr_hex(pick('eyeColor'))
        dark_spec = pick('dark', 'color')
        light_spec = pick('light', 'bgcolor', 'bgColor')
        grad_dark = _parse_gradient(dark_spec)
        grad_light = _parse_gradient(light_spec)
        dark = _qr_color(dark_spec, cfg['DEFAULT_FG_COLOR'])
        light = _qr_color(light_spec, cfg['DEFAULT_BG_COLOR'])
        # Первый стоп градиента = «сплошной» эквивалент цвета.
        light_hex = grad_light[2][0] if grad_light else light
        dark_hex = grad_dark[2][0] if grad_dark else dark

        center_image_url = str(pick('centerImageUrl', 'logoUrl', 'logo',
                                    default=cfg['DEFAULT_LOGO_URL']) or '').strip()
        has_logo = bool(center_image_url) and \
            center_image_url.lower() not in ('none', 'false', 'null')
        logo_shape = str(pick('logoShape', default='') or '').strip().lower()
        logo_border_width = _qr_int(pick('logoBorderWidth'), 0, 0, 24)
        logo_border_color = _qr_hex(pick('logoBorderColor'))

        # Фоновая картинка: cover + блюр, подложка light 55% сверху.
        bg_url = str(pick('bgImageUrl') or '').strip()
        has_bg = bool(bg_url) and \
            bg_url.lower() not in ('none', 'false', 'null')
        bg_blur_raw = pick('bgBlur')

        # Декоративная рамка вокруг квадрата QR: нужны ОБА параметра.
        frame_color = _qr_hex(pick('frameColor'))
        frame_width = _qr_int(pick('frameWidth'), 0, 1, 48)

        ec_level = str(pick('ecl', 'ecLevel',
                            default=cfg['DEFAULT_EC_LEVEL']) or 'M').upper()
        if ec_level not in ('L', 'M', 'Q', 'H'):
            ec_level = cfg['DEFAULT_EC_LEVEL']
        # Логотип в центре перекрывает часть кода — коррекция должна быть H,
        # иначе телефон может не считать (как и советовал quickchart).
        if has_logo:
            ec_level = 'H'

        ec_map = {
            'L': qr_lib.constants.ERROR_CORRECT_L,
            'M': qr_lib.constants.ERROR_CORRECT_M,
            'Q': qr_lib.constants.ERROR_CORRECT_Q,
            'H': qr_lib.constants.ERROR_CORRECT_H,
        }
        qr = qr_lib.QRCode(error_correction=ec_map[ec_level], border=max(0, margin))
        qr.add_data(qr_data)
        qr.make(fit=True)
        # box_size подбираем под запрошенный size; NEAREST-ресайз в Pillow
        # ненадёжен, поэтому точный размер добираем паддингом светом поля.
        qr.box_size = max(1, size // (qr.modules_count + 2 * max(0, margin)))

        # Подпись под кодом: высота полосы нужна до сборки слоёв,
        # чтобы градиент фона был непрерывным через всю картинку.
        caption = str(qr_config.get('caption') or '').strip()
        caption_pos = str(pick('captionPos', default='') or '').strip().lower()
        if caption_pos != 'inside':
            caption_pos = ''
        caption_style = str(pick('captionStyle', default='') or '').strip().lower()
        if caption_style not in ('pill', 'banner'):
            caption_style = ''
        font_size = _qr_int(qr_config.get('captionFontSize'),
                            max(12, size // 18), 8, 256)
        font = _qr_font(font_size, qr_config.get('captionFontFamily'))
        font_color = _qr_hex(qr_config.get('captionFontColor'))
        caption_strip = 0
        caption_box = None
        if caption and not caption_pos:
            _probe = ImageDraw.Draw(Image.new('L', (1, 1)))
            caption_box = _probe.textbbox((0, 0), caption, font=font)
            caption_strip = caption_box[3] - caption_box[1] + font_size
        elif caption:
            _probe = ImageDraw.Draw(Image.new('L', (1, 1)))
            caption_box = _probe.textbbox((0, 0), caption, font=font)
        total_h = size + caption_strip

        # Формат решается ПОСЛЕ общего разбора параметров, QR и полосы
        # подписи: SVG уходит в отдельный векторный путь, растровые слои
        # (PNG/JPEG) ниже не строятся. Разбор pick() при этом общий —
        # второй проход по параметрам не нужен.
        fmt = str(pick('format', default=cfg['DEFAULT_FORMAT']) or 'png').lower()
        if fmt == 'svg':
            try:
                logo_ratio = float(
                    pick('centerImageSizeRatio', 'logoSizeRatio',
                         default=cfg['DEFAULT_LOGO_SIZE_RATIO'])
                    or cfg['DEFAULT_LOGO_SIZE_RATIO'])
            except (TypeError, ValueError):
                logo_ratio = cfg['DEFAULT_LOGO_SIZE_RATIO']
            return _generate_qr_svg(qr, size, total_h, {
                'dark': dark, 'light': light,
                'dark_hex': dark_hex, 'light_hex': light_hex,
                'grad_dark': grad_dark, 'grad_light': grad_light,
                'dot_radius': dot_radius, 'eye_radius': eye_radius,
                'module_style': module_style, 'eye_style': eye_style,
                'eye_shape': eye_shape,
                'eye_color': eye_color,
                'has_bg': has_bg, 'bg_url': bg_url,
                'bg_blur_raw': bg_blur_raw,
                'frame_color': frame_color, 'frame_width': frame_width,
                'has_logo': has_logo, 'center_image_url': center_image_url,
                'logo_shape': logo_shape, 'logo_ratio': logo_ratio,
                'logo_border_width': logo_border_width,
                'logo_border_color': logo_border_color,
                'caption': caption, 'caption_pos': caption_pos,
                'caption_style': caption_style, 'caption_box': caption_box,
                'font_size': font_size, 'font_color': font_color,
                'font_family': qr_config.get('captionFontFamily'),
            }).encode('utf-8')

        # Прямой путь — только сплошные цвета и простые квадраты; всё
        # стилевое (градиенты, радиусы, стили модулей, глаза, фон) —
        # послойная сборка маской-композитом.
        use_mask = (grad_dark is not None or grad_light is not None
                    or dot_radius > 0 or eye_radius > 0 or eye_shape != ''
                    or module_style != '' or eye_color is not None or has_bg)

        if not use_mask:
            # Сплошные цвета и прямые углы — прежний прямой путь.
            img = qr.make_image(fill_color=dark,
                                back_color=light).convert('RGB')
            if img.width < size:
                canvas = Image.new('RGB', (size, size), light)
                offset = (size - img.width) // 2
                canvas.paste(img, (offset, offset))
                img = canvas
            if caption_strip:
                canvas = Image.new('RGB', (size, total_h), light)
                canvas.paste(img, (0, 0))
                img = canvas
        else:
            # Послойно: фон (база/градиент/картинка) → модули → глаза.
            stylized = (dot_radius > 0 or eye_radius > 0
                        or module_style != '' or eye_shape != '')
            if stylized:
                mask = _qr_modules_mask(qr, dot_radius, eye_radius,
                                        module_style, eye_style, eye_shape)
            else:
                mask = ImageOps.invert(
                    qr.make_image(fill_color='black',
                                  back_color='white').convert('L'))
            full_mask = _qr_fit_mask(mask, size, total_h)

            light_layer = (_gradient_layer(size, total_h, grad_light)
                           if grad_light
                           else Image.new('RGB', (size, total_h), light))
            if has_bg:
                try:
                    import requests
                    bg_resp = requests.get(bg_url, timeout=15)
                    if bg_resp.status_code == 200 and bg_resp.content:
                        bg = Image.open(BytesIO(bg_resp.content)).convert('RGB')
                        # Cover: обрезаем под область (size, total_h).
                        bg = ImageOps.fit(bg, (size, total_h), Image.LANCZOS)
                        blur = (size // 16 if bg_blur_raw is None
                                else _qr_int(bg_blur_raw, size // 16, 0, 64))
                        if blur > 0:
                            bg = bg.filter(ImageFilter.GaussianBlur(radius=blur))
                        base = Image.alpha_composite(
                            light_layer.convert('RGBA'), bg.convert('RGBA'))
                        # Подложка light (первый стоп), alpha 55% — код
                        # остаётся читаемым поверх картинки.
                        overlay = Image.new('RGBA', base.size,
                                            _qr_rgb(light_hex) + (143,))
                        light_layer = Image.alpha_composite(
                            base, overlay).convert('RGB')
                except Exception as error:  # noqa: BLE001 — фон не обязателен
                    logger.warning('QR background image skipped: %s', error)

            dark_layer = (_gradient_layer(size, total_h, grad_dark)
                          if grad_dark
                          else Image.new('RGB', (size, total_h), dark))
            # composite берёт первый слой там, где маска=255 (модули).
            img = Image.composite(dark_layer, light_layer, full_mask)

            if eye_color is not None:
                # Глаза отдельным слоем поверх модулей — только сплошной
                # eyeColor (градиент на глаза не распространяется).
                eyes_full = _qr_fit_mask(
                    _qr_eyes_mask(qr, eye_radius, eye_style, eye_shape),
                    size, total_h)
                eye_layer = Image.new('RGB', (size, total_h),
                                      _qr_rgb(eye_color))
                img = Image.composite(eye_layer, img, eyes_full)

        # Рамка строго вокруг квадрата QR; полотно подписи не обводится.
        if frame_color and frame_width > 0:
            frame_px = max(1, round(frame_width * size / 300))
            ImageDraw.Draw(img).rectangle(
                (0, 0, size - 1, size - 1),
                outline=frame_color, width=frame_px)

        if has_logo:
            try:
                import requests
                from io import BytesIO as _BytesIO
                logo_resp = requests.get(center_image_url, timeout=15)
                if logo_resp.status_code == 200 and logo_resp.content:
                    logo = Image.open(_BytesIO(logo_resp.content)).convert('RGBA')
                    ratio = float(pick('centerImageSizeRatio', 'logoSizeRatio',
                                       default=cfg['DEFAULT_LOGO_SIZE_RATIO'])
                                  or cfg['DEFAULT_LOGO_SIZE_RATIO'])
                    logo.thumbnail((max(1, int(size * ratio)),
                                    max(1, int(size * ratio))),
                                   Image.LANCZOS)
                    # Светлая подложка под логотипом — чтобы код остался
                    # читаемым на границе картинки и модулей.
                    pad = max(4, max(logo.size) // 10)
                    plate_color = _qr_rgb(light_hex) + (255,)
                    plate_radius = 0
                    if logo_shape in ('circle', 'rounded'):
                        # Квадратная подложка, логотип по центру: круг или
                        # квадрат со скруглением — как в превью админки.
                        box_w = box_h = max(logo.size) + pad * 2
                        plate = Image.new('RGBA', (box_w, box_h), plate_color)
                        plate.paste(
                            logo,
                            ((box_w - logo.width) // 2,
                             (box_h - logo.height) // 2),
                            logo)
                        shape_mask = Image.new('L', (box_w, box_h), 0)
                        mask_draw = ImageDraw.Draw(shape_mask)
                        if logo_shape == 'circle':
                            mask_draw.ellipse((0, 0, box_w - 1, box_h - 1),
                                              fill=255)
                        else:
                            plate_radius = max(1, box_w * 22 // 100)
                            mask_draw.rounded_rectangle(
                                (0, 0, box_w - 1, box_h - 1),
                                radius=plate_radius, fill=255)
                        plate.putalpha(shape_mask)
                    else:
                        box_w = logo.width + pad * 2
                        box_h = logo.height + pad * 2
                        plate = Image.new('RGBA', (box_w, box_h), plate_color)
                        plate.paste(logo, (pad, pad), logo)

                    if logo_border_width > 0:
                        # Обводка подложки: тот же контур, увеличенный на
                        # bw = logoBorderWidth (px при size=300) со стороны.
                        bw = max(1, round(logo_border_width * size / 300))
                        border_rgba = _qr_rgb(
                            logo_border_color or dark_hex) + (255,)
                        outer = Image.new(
                            'RGBA', (box_w + 2 * bw, box_h + 2 * bw),
                            border_rgba)
                        if logo_shape in ('circle', 'rounded'):
                            outer_mask = Image.new('L', outer.size, 0)
                            outer_draw = ImageDraw.Draw(outer_mask)
                            if logo_shape == 'circle':
                                outer_draw.ellipse(
                                    (0, 0, outer.width - 1, outer.height - 1),
                                    fill=255)
                            else:
                                outer_draw.rounded_rectangle(
                                    (0, 0, outer.width - 1, outer.height - 1),
                                    radius=plate_radius + bw, fill=255)
                            outer.putalpha(outer_mask)
                        outer.paste(plate, (bw, bw), plate)
                        plate = outer

                    # Центр квадрата QR (не всей картинки — внизу может
                    # быть полоса подписи).
                    pos = ((size - plate.width) // 2,
                           (size - plate.height) // 2)
                    img = img.convert('RGBA')
                    img.paste(plate, pos, plate)
                    img = img.convert('RGB')
            except Exception as error:  # noqa: BLE001 — логотип не обязателен
                logger.warning('QR logo skipped: %s', error)

        if caption and caption_box is not None:
            text_w = caption_box[2] - caption_box[0]
            text_h = caption_box[3] - caption_box[1]
            inside = (caption_pos == 'inside')
            padx = round(font_size * 0.6)
            pady = round(font_size * 0.35)
            # Левый верх текстового бокса: внутри — отступ ~0.6 кегля
            # от низа квадрата, снизу — как раньше, по центру полосы.
            text_left = (size - text_w) // 2
            if inside:
                text_top = size - round(font_size * 0.6) - text_h
            else:
                text_top = size + font_size // 2
            draw = ImageDraw.Draw(img)
            if caption_style == 'banner':
                if inside:
                    banner_top = size - (text_h + font_size)
                    draw.rectangle((0, banner_top, size - 1, size - 1),
                                   fill=dark_hex)
                    text_top = banner_top + font_size // 2
                else:
                    draw.rectangle((0, size, size - 1, total_h - 1),
                                   fill=dark_hex)
                draw.text((text_left - caption_box[0],
                           text_top - caption_box[1]),
                          caption, font=font,
                          fill=font_color or light_hex)
            elif caption_style == 'pill' or inside:
                # Плашка: габарит текста + padding, радиус = высота/2.
                # Внутри без стиля — pill автоматически (иначе текст
                # невозможно отделить от фона/модулей).
                pill = (text_left - padx, text_top - pady,
                        text_left + text_w + padx, text_top + text_h + pady)
                overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
                ImageDraw.Draw(overlay).rounded_rectangle(
                    pill, radius=max(1, (pill[3] - pill[1]) // 2),
                    fill=_qr_rgb(light_hex) + (235,))
                img = Image.alpha_composite(img.convert('RGBA'),
                                            overlay).convert('RGB')
                draw = ImageDraw.Draw(img)
                if inside:
                    fill = font_color or dark_hex
                else:
                    fill = font_color or '#000000'
                draw.text((text_left - caption_box[0],
                           text_top - caption_box[1]),
                          caption, font=font, fill=fill)
            else:
                draw.text((text_left - caption_box[0],
                           text_top - caption_box[1]),
                          caption, font=font,
                          fill=font_color or '#000000')

        out = BytesIO()
        if fmt in ('jpg', 'jpeg'):
            img.save(out, format='JPEG', quality=92)
        else:
            img.save(out, format='PNG')
        return out.getvalue()
    except Exception as error:  # noqa: BLE001
        logger.error('Error generating barcode image: %s', error)
        return None


def parse_barcode_data(ctx: BotContext, step: Step,
                       user_id: Any = None) -> dict:
    """Данные QR: подстановка {тегов} и генерация id, если данных нет.

    Настройки картинки (options/qr_config) уже разобраны в Step.barcode.
    """
    try:
        barcode = step.barcode
        barcode_data = str(barcode.data) if barcode else ''
        if user_id and '{' in barcode_data:
            barcode_data = users.replace_tags_in_text(ctx, barcode_data, user_id)

        if not barcode_data or barcode_data.strip() == '':
            user_info = users.get_user_info(ctx, user_id) if user_id else None
            unique_id = (user_info or {}).get('user_id') or (user_id or '')
            timestamp = int(time.time() * 1000)
            random_str = format(random.randint(0, 36 ** 9 - 1), 'X').upper()[:9]
            barcode_data = f'USER_{unique_id}_{timestamp}_{random_str}'

        return {
            'data': barcode_data,
            'options': dict(barcode.options) if barcode else {},
            'qrConfig': dict(barcode.qr_config) if barcode else {},
        }
    except Exception as error:  # noqa: BLE001
        logger.error('Error parsing barcode data: %s', error)
        return {'data': f'USER_{user_id}_{int(time.time() * 1000)}',
                'options': {}, 'qrConfig': {}}


# ---------------------------------------------------------------------------
# StepManager (:3609)
# ---------------------------------------------------------------------------

class StepManager:
    @staticmethod
    def get_step_data(ctx: BotContext, step_number: int) -> Optional[Step]:
        if not ctx.funnel_id:
            return None
        return ctx.storage.get_step(ctx.funnel_id, step_number)

    @staticmethod
    def get_next_step(ctx: BotContext, current_step: int) -> int:
        """step.next (>0), иначе current+1 (если есть), иначе 0 = конец."""
        step = StepManager.get_step_data(ctx, current_step)
        if not step:
            return 0
        if step.next is not None and step.next > 0:
            return step.next
        next_sequential = current_step + 1
        if StepManager.get_step_data(ctx, next_sequential):
            return next_sequential
        return 0

    @staticmethod
    def get_keyboard_for_step(ctx: BotContext,
                              step_number: int) -> Optional[dict]:
        step = StepManager.get_step_data(ctx, step_number)
        return step.reply_keyboard if step else None

    @staticmethod
    def get_inline_keyboard_for_step(ctx: BotContext,
                                     step_number: int) -> Optional[dict]:
        step = StepManager.get_step_data(ctx, step_number)
        return step.inline_keyboard if step else None


def build_reply_keyboard(ctx: BotContext, step_number: int,
                         user_role: str = '') -> Optional[dict]:
    """Порт buildReplyKeyboard (:3907): role-фильтр клавиатуры при отправке."""
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
                    dice_value: Any, step: Step) -> None:
    ctx.storage.dice_results[message_id] = {
        'userId': user_id, 'chatId': chat_id, 'diceValue': dice_value,
        'step': step,
        'timestamp': _now_ms(), 'processed': False,
    }
    ctx.storage.cleanup_game_caches()


def add_poll_result(ctx: BotContext, poll_id: str, user_id: Any, chat_id: Any,
                    step: Step) -> None:
    cache = ctx.storage.poll_results if step.type == 'poll' \
        else ctx.storage.quiz_results
    cache[poll_id] = {
        'userId': user_id, 'chatId': chat_id, 'selectedOption': -1,
        'step': step,
        'timestamp': _now_ms(), 'processed': False,
    }
    ctx.storage.cleanup_game_caches()


def _run_step_calc(ctx: BotContext, chat_id: Any, user_id: Any,
                   step: Optional[Step]) -> None:
    """Формула calc из шага (calc_formula) — авто-расчёт после
    броска/ответа, до отправки следующего шага. Успех не шлёт отдельное
    сообщение (следующий шаг покажет новые теги); ошибку показываем,
    чтобы её не было незаметно."""
    formula = (step.calc_formula if step else '') or ''
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
        step: Step = dice_result['step']

        dice_emoji = step.dice.emoji

        # Результат броска → теги {dice}/{dice_emoji}/… для формул calc.
        users.save_dice_tags(ctx, user_id, dice_emoji, dice_value)
        # Формула из шага (calc_formula) — авто-расчёт по тегам.
        _run_step_calc(ctx, chat_id, user_id, step)

        # Функции trigger=dice — после тегов; ветка condition перекрывает next.
        trig_msgs, trig_branch = functions.run_functions_for_trigger(
            ctx, user_id, 'dice')
        for msg in trig_msgs:
            ctx.tg.send_message(chat_id, msg)

        # Явный маппинг «исход → шаг»: 0 = без перехода (конец воронки).
        # Маппинга нет или у исхода нет пары — как раньше, следующий шаг.
        resolved = resolve_dice_next_step(step, dice_value)
        if trig_branch > 0:
            next_step = trig_branch
        elif resolved is not None:
            next_step = resolved
        else:
            next_step = StepManager.get_next_step(ctx, step.number)

        dice_result['processed'] = True
        # Пауза после броска: анимацию handle_dice_message уже выждал,
        # здесь ~1 с до отправки следующего шага (как в предпросмотре).
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
                            dice_result['step'])
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
        step: Step = poll_result['step']
        cfg = step.poll if target == 'poll' else step.quiz
        logic_values = cfg.logic_values if cfg else []
        steps_list = cfg.steps if cfg else []

        # Ответ poll/quiz → теги {poll_answer}/{quiz_correct}/… для формул calc.
        users.save_poll_tags(ctx, poll_result['userId'], target,
                             selected_option, logic_values, step.quiz)
        # Формула из шага (calc_formula) — авто-расчёт по тегам.
        _run_step_calc(ctx, poll_result['chatId'], poll_result['userId'], step)

        # Функции trigger=poll/quiz — после тегов; ветка перекрывает next.
        trig_msgs, trig_branch = functions.run_functions_for_trigger(
            ctx, poll_result['userId'], target)
        for msg in trig_msgs:
            ctx.tg.send_message(poll_result['chatId'], msg)

        # Режимы формы как у dice: no_transition («Без перехода» из чипов)
        # — воронка завершается; иначе маппинг «ответ → шаг» как раньше
        # (>0 — переход, 0/нет пары — следующий шаг).
        no_transition = bool(cfg is not None and cfg.no_transition)
        next_step = 0
        if trig_branch > 0:
            next_step = trig_branch
        elif not no_transition:
            if logic_values and steps_list \
                    and 0 <= selected_option < len(logic_values):
                # Как в JS: steps[selected] может отсутствовать → undefined → 0.
                next_step = (steps_list[selected_option]
                             if selected_option < len(steps_list) else 0) or 0
            if next_step <= 0:
                next_step = StepManager.get_next_step(ctx, step.number)

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


def _guess_url_media_type(url: str) -> str:
    """Тип элемента media_group по расширению URL (фото/видео/аудио/документ)."""
    from urllib.parse import urlparse
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


def send_media_message(ctx: BotContext, chat_id, step: Step,
                       media_type: str, options: Optional[dict] = None,
                       caption: Optional[str] = None) -> Any:
    """Порт sendMediaMessage (:4244).

    Файлы уже разобраны в step.file_urls/step.file_ids при загрузке;
    caption — текст с подставленными тегами (готовит send_step_message),
    по умолчанию — сырой step.text.
    """
    options = options or {}
    try:
        if caption is None:
            caption = step.text
        urls = list(step.file_urls)
        file_ids = list(step.file_ids)

        total_files = len(urls) + len(file_ids)

        if total_files == 0:
            content = step.text
            if content and str(content).startswith(('http://', 'https://')) \
                    and valid_http_url(str(content)):
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
                media_item: dict[str, Any] = {'type': item_type, 'media': file}
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
        caption = step.text
        if caption and str(caption).strip() != '':
            ctx.tg.send_message(chat_id,
                                '<b>ℹ️ Информация</b>\n\n' + str(caption),
                                options)
        return None


# ---------------------------------------------------------------------------
# Barcode / Poll / Quiz / Dice отправка
# ---------------------------------------------------------------------------

def send_barcode_message(ctx: BotContext, chat_id, step: Step,
                         options: dict, user_id: Any) -> Any:
    """Порт sendBarcodeMessage (:4439)."""
    try:
        time.sleep(0.5)

        barcode_info = parse_barcode_data(ctx, step, user_id)

        if user_id:
            users.save_user_input_response(
                ctx, user_id, step.type_value or 'Barcode',
                barcode_info['data'])

        qr_image = generate_barcode_image(barcode_info['data'],
                                          barcode_info['options'],
                                          barcode_info['qrConfig'])
        if not qr_image:
            ctx.tg.send_message(
                chat_id,
                '<b>❌ Ошибка генерации QR-кода</b>\n\n'
                'Не удалось сгенерировать код для данных: '
                + str(barcode_info['data'])[:50] + '...',
                options)
            return None

        caption = step.text
        processed_caption = ''
        if caption and str(caption).strip() != '':
            processed_caption = (users.replace_tags_in_text(ctx, caption, user_id)
                                 if user_id else caption)

        # SVG-код — документом (Telegram не принимает SVG как photo).
        fmt = str(barcode_info['qrConfig'].get('format') or '').strip().lower()

        try:
            if fmt == 'svg':
                message_id = ctx.tg.send_document(
                    chat_id, qr_image, 'qr.svg', processed_caption, options)
            else:
                message_id = ctx.tg.send_photo(chat_id, qr_image,
                                               processed_caption, options)
            if message_id:
                if step.pin:
                    ctx.tg.pin_message(chat_id, message_id)
                return message_id
        except Exception as error:  # noqa: BLE001
            logger.error('Error sending barcode: %s', error)
            ctx.tg.send_message(
                chat_id,
                '<b>🎫 Ваш QR-код сгенерирован!</b>\n\n'
                f'Данные: <code>{barcode_info["data"]}</code>',
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


def handle_poll_message(ctx: BotContext, chat_id, step: Step, options: dict,
                        user_id: Any) -> None:
    """Порт MessageHandler.handlePollMessage (:5733)."""
    poll = step.poll
    if not poll or not 2 <= len(poll.answers) <= 10:
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Ошибка:</b> Для опроса нужно от 2 до 10 вариантов ответа. '
            'Проверьте поле с вариантами.')
        return

    question = poll.question
    if user_id:
        question = users.replace_tags_in_text(ctx, question, user_id)

    poll_options: dict[str, Any] = {
        'type': 'regular',
        'is_anonymous': poll.anonymous,
        'allows_multiple_answers': poll.multiple,
    }
    if poll.close_date is not None:
        poll_options['close_date'] = poll.close_date
    elif poll.open_period is not None:
        poll_options['open_period'] = poll.open_period
    if options.get('reply_markup'):
        poll_options['reply_markup'] = options['reply_markup']

    poll_result = ctx.tg.send_poll(chat_id, question, poll.answers,
                                   poll_options)
    if poll_result and poll_result.get('message_id') and poll_result.get('poll_id'):
        add_poll_result(ctx, poll_result['poll_id'], user_id, chat_id, step)
        if step.pin:
            ctx.tg.pin_message(chat_id, poll_result['message_id'])


def handle_quiz_message(ctx: BotContext, chat_id, step: Step, options: dict,
                        user_id: Any) -> None:
    """Порт MessageHandler.handleQuizMessage (:5781)."""
    quiz = step.quiz
    if not quiz or not 2 <= len(quiz.answers) <= 10:
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Ошибка:</b> Для викторины нужно от 2 до 10 вариантов '
            'ответа. Проверьте поле с вариантами.')
        return

    if not (0 <= quiz.correct_option < len(quiz.answers)):
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Ошибка:</b> Неправильный номер правильного ответа. '
            'Проверьте колонку Quiz.')
        return

    question = quiz.question
    if user_id:
        question = users.replace_tags_in_text(ctx, question, user_id)

    poll_options: dict[str, Any] = {
        'type': 'quiz',
        'correct_option_id': quiz.correct_option,
        'is_anonymous': quiz.anonymous,
    }
    if quiz.close_date is not None:
        poll_options['close_date'] = quiz.close_date
    elif quiz.open_period is not None:
        poll_options['open_period'] = quiz.open_period
    if quiz.explanation and quiz.explanation.strip() != '':
        poll_options['explanation'] = quiz.explanation
    if options.get('reply_markup'):
        poll_options['reply_markup'] = options['reply_markup']

    poll_result = ctx.tg.send_poll(chat_id, question, quiz.answers,
                                   poll_options)
    if poll_result and poll_result.get('message_id') and poll_result.get('poll_id'):
        add_poll_result(ctx, poll_result['poll_id'], user_id, chat_id, step)
        if step.pin:
            ctx.tg.pin_message(chat_id, poll_result['message_id'])


def handle_dice_message(ctx: BotContext, chat_id, step: Step, options: dict,
                        user_id: Any) -> None:
    """Порт MessageHandler.handleDiceMessage (:5842)."""
    dice_emoji = step.dice.emoji
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
                            dice_result['dice_value'], step)
            # Ждём окончания анимации броска — переход (следующий шаг
            # внутри process_dice_result) не должен приходить во время неё.
            time.sleep(dice_animation_seconds(dice_emoji))
            process_dice_result(ctx, message_id)
        else:
            add_dice_result(ctx, message_id, user_id, chat_id, 0, step)
            timer = threading.Timer(dice_animation_seconds(dice_emoji),
                                    check_dice_result,
                                    args=(ctx, message_id, chat_id))
            timer.daemon = True
            timer.start()

        if step.pin:
            ctx.tg.pin_message(chat_id, message_id)


# ---------------------------------------------------------------------------
# sendStepMessage / updateStepMessage / completeFunnel
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Адвент-календарь: клавиатура и текст шага (строятся при отправке —
# в самом шаге не хранятся) + открытие двери по callback adv_<N>.
# ---------------------------------------------------------------------------

ADVENT_BUTTONS_PER_ROW = 8


def _advent_opened_set(ctx: BotContext, days: int, user_id: Any) -> set[int]:
    """Номера уже открытых дверей: тег advent_opened_<N> == '1'."""
    if not user_id:
        return set()
    tags = users.get_all_user_tags(ctx, user_id)
    return {number for number in range(1, days + 1)
            if str(tags.get(f'advent_opened_{number}') or '') == '1'}


def build_advent_keyboard(ctx: BotContext, step: Step,
                          user_id: Any) -> dict:
    """Клавиатура адвента: ряды по 8 дверей, callback = adv_<N>.

    Эмодзи двери: ✅ — открыта; 🎁 — не открыта, но день наступил (1..day_index);
    🔒 — будущая.
    """
    adv = step.advent
    if not adv:
        return {'inline_keyboard': []}
    day_index = advent_current_day(adv.start, adv.days, adv.tz)
    opened = _advent_opened_set(ctx, adv.days, user_id)

    rows: list[list[dict]] = []
    row: list[dict] = []
    for number in range(1, adv.days + 1):
        if number in opened:
            icon = '✅'
        elif 1 <= number <= day_index:
            icon = '🎁'
        else:
            icon = '🔒'
        row.append({'text': f'{icon} {number}',
                    'callback_data': f'adv_{number}'})
        if len(row) == ADVENT_BUTTONS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return {'inline_keyboard': rows}


def advent_text(ctx: BotContext, step: Step, text: Any,
                user_id: Any) -> str:
    """Текст шага адвента: {advent_day}/{advent_opened}/{advent_total} + теги юзера."""
    raw = str(text or '')
    adv = step.advent
    if adv:
        # Подставляем до replace_tags_in_text, чтобы плейсхолдеры не
        # уходили в condition-резолвер.
        day_index = advent_current_day(adv.start, adv.days, adv.tz)
        opened = len(_advent_opened_set(ctx, adv.days, user_id))
        raw = (raw.replace('{advent_day}', str(day_index))
                  .replace('{advent_opened}', str(opened))
                  .replace('{advent_total}', str(adv.days)))
    result = users.replace_tags_in_text(ctx, raw, user_id)
    return result if isinstance(result, str) else str(result or '')


def open_advent_door(ctx: BotContext, step: Step, user_id: Any,
                     day: int) -> dict:
    """Открытие двери адвента: тег открытой двери + приз-монеты дня.

    Возвращает {'popup': str, 'opened': bool}: popup — текст всплывающего
    ответа (обрезка до 200 символов — у вызывающего); opened=True — дверь
    открыта этим вызовом (клавиатуру нужно перерисовать).
    """
    adv = step.advent
    if not adv or day < 1:
        return {'popup': '', 'opened': False}

    day_index = advent_current_day(adv.start, adv.days, adv.tz)
    row = ctx.storage.get_user(ctx.funnel_id, user_id)
    data: dict = dict(row.get('data') or {}) if row else {}
    tag = f'advent_opened_{day}'

    if str(data.get(tag) or '') == '1':
        return {'popup': adv.done, 'opened': False}
    if day > day_index:
        return {'popup': adv.early.replace('{day}', str(day)),
                'opened': False}

    # Открываем дверь: тег навсегда + приз дня (индекс day-1 в advent_prizes).
    data[tag] = '1'
    popup = f'🎁 Дверь {day} открыта!'
    if day <= len(adv.prizes):
        title, amount = split_advent_prize(adv.prizes[day - 1])
        if title:
            popup = title
        if amount is not None:
            # Начисление в тег баланса — как в execute_purchase.
            balance = functions._purchase_number(
                data.get(config.PURCHASE_TAG))
            data[config.PURCHASE_TAG] = functions._purchase_normalize(
                balance + amount)

    ctx.storage.save_user_row(ctx.funnel_id, user_id,
                              users.get_user_state(ctx, user_id), data)
    return {'popup': popup, 'opened': True}


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
                   should_pin: bool) -> None:
    if message_id and should_pin:
        ctx.tg.pin_message(chat_id, message_id)


def _url_auto_fire(ctx: BotContext, chat_id, step_number: int,
                   user_id: Any, target: int) -> None:
    """Таймер url-кнопки: переход, если пользователь всё ещё на шаге."""
    try:
        if users.get_user_state(ctx, user_id) != step_number:
            return
        users.set_user_state(ctx, user_id, target)
        send_step_message(ctx, chat_id, target, user_id)
    except Exception as error:  # noqa: BLE001
        logger.error('[url_auto_fire] %s', error)


def _schedule_url_auto(ctx: BotContext, chat_id, step_number: int,
                       user_id: Any, step) -> None:
    """URL-кнопки: эмуляция авто-перехода с задержкой.

    Telegram не сообщает боту о клике по url-кнопке, поэтому переход
    планируется от показа шага: через delay секунд, если пользователь
    не ушёл дальше (state не изменился).
    """
    if not user_id or not step.inline_keyboard:
        return
    parsed = step.inline_keyboard.get('parsedData') or {}
    for row in parsed.get('buttons') or []:
        for btn in row:
            if (btn.get('type') or '') != 'url':
                continue
            try:
                target = int(btn.get('step') or 0)
                delay = int(btn.get('delay') or 0)
            except (TypeError, ValueError):
                continue
            if target <= 0 or delay <= 0 or target == step_number:
                continue
            timer = threading.Timer(
                delay, _url_auto_fire,
                args=(ctx, chat_id, step_number, user_id, target))
            timer.daemon = True
            timer.start()


def send_step_message(ctx: BotContext, chat_id, step_number: int,
                      user_id: Any = None) -> None:
    """Порт MessageHandler.sendStepMessage (:5503) — диспетчер типов шагов."""
    step = StepManager.get_step_data(ctx, step_number)
    if not step:
        ctx.tg.send_message(
            chat_id, f'<b>⚠️ Шаг {step_number} не найден в воронке</b>')
        return

    options: dict[str, Any] = {'parse_mode': 'HTML'}

    user_role = (users.get_user_tag(ctx, user_id, 'role') or '') if user_id else ''
    reply_keyboard = build_reply_keyboard(ctx, step_number, user_role)
    if reply_keyboard:
        options['reply_markup'] = reply_keyboard['reply_markup']
    elif step.inline_keyboard and step.inline_keyboard.get('inlineKeyboard'):
        options['reply_markup'] = step.inline_keyboard['inlineKeyboard']

    # Адвент: клавиатура строится при отправке и перекрывает клавиатуру шага.
    if step.type == 'advent':
        options['reply_markup'] = build_advent_keyboard(ctx, step, user_id)

    has_effects = effects.has_effects(step.text)

    processed_text = step.text
    if step.type == 'advent':
        processed_text = advent_text(ctx, step, step.text, user_id)
        # Без текста сообщение не отправится — двери календаря потерялись.
        if not str(processed_text or '').strip():
            processed_text = '🎁 Адвент-календарь'
    elif user_id:
        processed_text = users.replace_tags_in_text(ctx, step.text, user_id)

    if ctx.config.get('typing', False):
        action = _TYPING_ACTION_MAP.get(step.type, 'typing')
        ctx.tg.send_chat_action(chat_id, action)

    if step.type == 'barcode':
        send_barcode_message(ctx, chat_id, step, options, user_id)
    elif step.type == 'poll':
        handle_poll_message(ctx, chat_id, step, options, user_id)
    elif step.type == 'quiz':
        handle_quiz_message(ctx, chat_id, step, options, user_id)
    elif step.type == 'dice':
        handle_dice_message(ctx, chat_id, step, options, user_id)
    elif step.type in config.MEDIA_TYPES:
        send_media_message(ctx, chat_id, step, step.type, options,
                           caption=processed_text)
    else:
        message_text = processed_text or ''
        if message_text.strip() != '':
            if not has_effects:
                message_id = ctx.tg.send_message(chat_id, message_text, options)
                _pin_if_needed(ctx, chat_id, message_id, step.pin)
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
                                               step.pin)
                        except Exception:  # noqa: BLE001
                            final_text = effects.get_final_text(message_text)
                            if final_text and final_text.strip() != '':
                                ctx.tg.edit_message_text(chat_id, initial_message_id,
                                                         final_text, options)
                except Exception:  # noqa: BLE001
                    ctx.tg.send_message(chat_id,
                                        effects.get_final_text(message_text),
                                        options)

    # url-кнопки: авто-переход через задержку от показа шага.
    _schedule_url_auto(ctx, chat_id, step_number, user_id, step)

    # Функции с trigger=step — после показа шага, до авто-перехода.
    # Без user_id (превью/авто) нет тегов юзера — пропускаем.
    trigger_branch = 0
    if user_id and step_number > 0:
        trig_msgs, trigger_branch = functions.run_functions_for_step(
            ctx, user_id, step_number)
        for msg in trig_msgs:
            ctx.tg.send_message(chat_id, msg)

    # Ветка condition с триггером «шаг» перекрывает авто-next.
    if user_id and trigger_branch > 0 \
            and step.type not in ('dice', 'poll', 'quiz'):
        if trigger_branch != step_number:
            users.set_user_state(ctx, user_id, trigger_branch)
            send_step_message(ctx, chat_id, trigger_branch, user_id)
        return

    # Автопереход только при scroll=auto И явно заданном next_step > 0.
    # Без auto последовательного авто-перехода нет; пустой next_step при
    # auto тоже не даёт перехода (без фолбэка current+1).
    # dice/poll/quiz переходят сами после броска/ответа (process_dice_result /
    # process_poll_result уже отправляют целевой шаг: маппинг, next_step или
    # конец воронки) — авто-блок здесь срабатывал бы до ответа и дублировал
    # публикацию шага.
    if user_id and step.auto and step.type not in ('dice', 'poll', 'quiz'):
        if step.delay > 0:
            time.sleep(step.delay)

        next_step = step.next if step.next is not None and step.next > 0 else 0

        if next_step > 0:
            users.set_user_state(ctx, user_id, next_step)
            send_step_message(ctx, chat_id, next_step, user_id)
        elif step.next == 0:
            # Явный '0' при auto — конец воронки (как в старом формате).
            complete_funnel(ctx, user_id, chat_id)


def update_step_message(ctx: BotContext, chat_id, message_id: int,
                        step_number: int, user_id: Any = None) -> None:
    """Порт MessageHandler.updateStepMessage (:5664): editMessageText."""
    step = StepManager.get_step_data(ctx, step_number)
    if not step:
        ctx.tg.send_message(
            chat_id, f'<b>⚠️ Шаг {step_number} не найден в воронке</b>')
        return

    options: dict[str, Any] = {'parse_mode': 'HTML'}
    user_role = (users.get_user_tag(ctx, user_id, 'role') or '') if user_id else ''
    reply_keyboard = build_reply_keyboard(ctx, step_number, user_role)
    if reply_keyboard:
        options['reply_markup'] = reply_keyboard['reply_markup']
    elif step.inline_keyboard and step.inline_keyboard.get('inlineKeyboard'):
        options['reply_markup'] = step.inline_keyboard['inlineKeyboard']

    # Адвент: клавиатура строится заново (обновить ✅ на открытых дверях).
    if step.type == 'advent':
        options['reply_markup'] = build_advent_keyboard(ctx, step, user_id)

    has_effects = effects.has_effects(step.text)
    processed_text = step.text
    if step.type == 'advent':
        processed_text = advent_text(ctx, step, step.text, user_id)
        # Без текста сообщение не отправится — двери календаря потерялись.
        if not str(processed_text or '').strip():
            processed_text = '🎁 Адвент-календарь'
    elif user_id:
        processed_text = users.replace_tags_in_text(ctx, step.text, user_id)

    if step.type in ('barcode', 'poll', 'quiz', 'dice') \
            or step.type in config.MEDIA_TYPES:
        ctx.tg.delete_message(chat_id, message_id)
        send_step_message(ctx, chat_id, step_number, user_id)
        return

    message_text = processed_text or ''
    if message_text.strip() == '':
        return

    if not has_effects:
        ctx.tg.edit_message_text(chat_id, message_id, message_text, options)
        _schedule_url_auto(ctx, chat_id, step_number, user_id, step)
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
    _schedule_url_auto(ctx, chat_id, step_number, user_id, step)


def complete_funnel(ctx: BotContext, user_id: Any, chat_id) -> None:
    """Порт MessageHandler.completeFunnel (:6218): финальное сообщение + шаг 0."""
    final_message = functions.get_system_final_message(ctx)
    if final_message and ctx.config.get('systemmessages', True):
        processed = users.replace_tags_in_text(ctx, final_message, user_id)
        ctx.tg.send_message(chat_id, f'<b>{processed}</b>')

    users.set_user_state(ctx, user_id, 0)



