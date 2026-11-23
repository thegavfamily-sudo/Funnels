# ============================================================================
# effects.py — эффекты <effect:*>... в текстовых сообщениях
#
# Порт из apps_script/БОТ_готовый.gs:
#   Utils.parseEffects   :602   Utils.hasEffects  :649
#   Utils.getFinalText   :654   Utils.getInitialText :687
#   Utils.extractPureText:729   EffectsHandler     :6330
#
# Типы: countdown / progress / spinner / blink / reveal.
# Кадры внутри тега разделены '|'; задержки — time.sleep (как Utilities.sleep).
#
# В СООБЩЕНИИ МОЖЕТ БЫТЬ НЕСКОЛЬКО тегов — они проигрываются ПОСЛЕДОВАТЕЛЬНО
# (фраза за фразой): старт = все эффекты в своих первых кадрах, затем
# по очереди каждый доводится до финала.
# ============================================================================

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r'<effect:(\w+)(?::([^>]+))?>|</effect>', re.IGNORECASE)

# Защита от «умирающего» бота: длинный кадр/много циклов блокируют
# поток обработки на минуты (регресс: бот переставал отвечать).
_MAX_DELAY_MS = 5000
_MAX_CYCLES = 20


def parse_effects(content: Any) -> Optional[list[dict]]:
    """Разбор вложенных effect-тегов; возвращает список (глубина по убыванию)."""
    if not content or not isinstance(content, str):
        return None

    effects: list[dict] = []
    stack: list[dict] = []

    for match in _TAG_RE.finditer(content):
        full_match = match.group(0)
        match_index = match.start()

        if full_match.lower().startswith('</effect>'):
            if stack:
                effect = stack.pop()
                effect['end_index'] = match_index
                effect['full_content'] = \
                    content[effect['start_index']:match_index + len(full_match)]
                effect['inner_start_index'] = \
                    effect['start_index'] + len(effect['full_match'])
                effect['inner_content'] = \
                    content[effect['inner_start_index']:match_index]
                effect['depth'] = len(stack)
                effects.append(effect)
        else:
            effect_type = match.group(1).lower()
            params_str = match.group(2) or ''
            params = [p for p in params_str.split(':') if p != ''] if params_str else []
            effect = {
                'type': effect_type,
                'params': params,
                'start_index': match_index,
                'full_match': full_match,
                'depth': len(stack),
            }
            stack.append(effect)

    effects.sort(key=lambda e: -e['depth'])
    return effects if effects else None


def has_effects(content: Any) -> bool:
    if not content or not isinstance(content, str):
        return False
    return '<effect:' in content and '</effect>' in content


# ---------------------------------------------------------------------------
# Параметры / клампы
# ---------------------------------------------------------------------------

def _delay_ms(params: list[str], default_ms: int) -> float:
    """Задержка кадра в СЕКУНДАХ для time.sleep (кламп 0…5с).

    Порт: effect.params[0] ? parseInt(...) : default — но оригинальный
    Utilities.sleep принимает миллисекунды, time.sleep — секунды,
    поэтому и параметр, и дефолт делим на 1000.
    """
    if not params:
        ms = default_ms
    else:
        try:
            ms = int(params[0])
        except (ValueError, TypeError):
            ms = default_ms
    ms = min(max(ms, 0), _MAX_DELAY_MS)
    return ms / 1000.0


def _cycles(params: list[str], index: int, default: int) -> int:
    if len(params) <= index:
        value = default
    else:
        try:
            value = int(params[index])
        except (ValueError, TypeError):
            value = default
    return min(max(value, 0), _MAX_CYCLES)


def _is_cum(effect: dict) -> bool:
    """reveal в режиме cum: кадр = полный текст (префиксы), не накопление."""
    params = effect.get('params') or []
    return len(params) > 1 and params[1].lower() in ('cum', 'raw')


# ---------------------------------------------------------------------------
# План проигрывания и рендер
# ---------------------------------------------------------------------------

def _outer_list(content: str) -> list[dict]:
    """Внешние (depth=0) эффекты в порядке их появления в тексте."""
    effects = parse_effects(content)
    if not effects:
        return []
    outer = [e for e in effects if e['depth'] == 0]
    outer.sort(key=lambda e: e['start_index'])
    return outer


def _play_plan(effect: dict) -> tuple[str, list[tuple[float, str]]]:
    """(стартовое состояние, шаги [(пауза_сек_перед_показом, текст)]).

    Стартовое состояние — то, что уже на экране (get_initial_text);
    каждый шаг: подождать паузу, затем показать текст (полный рендер).
    """
    etype = effect['type']
    inner = effect['inner_content']
    params = effect['params']
    parts = inner.split('|')

    if etype == 'countdown':
        delay = _delay_ms(params, 800)
        if len(parts) < 2:
            return inner, []
        return parts[0], [(delay, p) for p in parts[1:]]

    if etype == 'progress':
        delay = _delay_ms(params, 300)
        if len(parts) < 2:
            return inner, []
        return parts[0], [(delay, p) for p in parts[1:]]

    if etype == 'spinner':
        delay = _delay_ms(params, 200)
        cycles = _cycles(params, 1, 3)
        if len(parts) < 2:
            return inner, []
        steps: list[tuple[float, str]] = []
        for _ in range(cycles):
            for part in parts:
                for char in ('/', '-', '\\', '|'):
                    steps.append((delay, part + ' ' + char))
        steps.append((delay, parts[-1] + ' ✓'))
        if steps:
            steps[0] = (0.0, steps[0][1])  # первый кадр — сразу
        return parts[0], steps

    if etype == 'blink':
        delay = _delay_ms(params, 300)
        cycles = _cycles(params, 1, 3)
        # Мигает сам обёрнутый текст (без символа-настройки):
        # показать → скрыть (пробел) → показать…, финал = текст.
        steps = []
        for _ in range(cycles):
            steps.append((delay, ' '))
            steps.append((delay, inner))
        return inner, steps

    if etype == 'reveal':
        delay = _delay_ms(params, 500)
        if len(parts) == 1:
            # Одна фраза: старт пустой → пауза → ВСЯ фраза целиком.
            if not inner.strip():
                return '', []
            return '', [(delay, inner)]
        if _is_cum(effect):
            # Кадры — готовый полный текст: показываем как есть.
            return parts[0], [(delay, p) for p in parts[1:]]
        # Накопление строк (формат гугл-скрипта «а|б»).
        steps = []
        combined = parts[0]
        for part in parts[1:]:
            combined += '\n' + part
            steps.append((delay, combined))
        return parts[0], steps

    # Неизвестный тип — без анимации.
    return inner, []


def _render(content: str, outer: list[dict], states: list[str]) -> str:
    """Полный текст сообщения: каждый внешний эффект заменён своим состоянием."""
    out = content
    for eff, state in sorted(zip(outer, states),
                             key=lambda p: -p[0]['start_index']):
        out = (out[:eff['start_index']] + state
               + out[eff['start_index'] + len(eff['full_content']):])
    return out


def get_final_text(content: Any) -> str:
    """Итоговый текст после проигрывания всех эффектов."""
    if not content or not isinstance(content, str):
        return content or ''
    outer = _outer_list(content)
    if not outer:
        return content
    states = []
    for eff in outer:
        initial, steps = _play_plan(eff)
        states.append(steps[-1][1] if steps else initial)
    return _render(content, outer, states)


def get_initial_text(content: Any) -> str:
    """Старт: каждый эффект в своём первом состоянии (тегов не видно)."""
    if not content or not isinstance(content, str):
        return content or ''
    outer = _outer_list(content)
    if not outer:
        return content
    states = [_play_plan(eff)[0] for eff in outer]
    return _render(content, outer, states)


# ---------------------------------------------------------------------------
# Проигрывание (EffectsHandler, порт :6330 — теперь для всех тегов сразу)
# ---------------------------------------------------------------------------

def process_effects(tg, chat_id, message_id: int, content: str,
                    user_id: Any = None,
                    edit_options: Optional[dict] = None) -> bool:
    """Проиграть внешние эффекты ПОСЛЕДОВАТЕЛЬНО через editMessageText.

    Сообщение уже отправлено в стартовом состоянии (get_initial_text);
    здесь каждый эффект по очереди доводится до финала, каждый кадр —
    полный рендер сообщения со всеми состояниями.
    """
    try:
        outer = _outer_list(content)
        if not outer:
            return False
        plans = [_play_plan(eff) for eff in outer]
        if not any(steps for _, steps in plans):
            return False

        states = [initial for initial, _ in plans]
        animated = False
        for i, (_, steps) in enumerate(plans):
            for delay, text in steps:
                if delay > 0:
                    time.sleep(delay)
                states[i] = text
                # edit_options обязателен при инлайн-клавиатуре: без
                # reply_markup Telegram СНИМАЕТ клавиатуру при редактировании.
                tg.edit_message_text(
                    chat_id, message_id, _render(content, outer, states),
                    edit_options)
                animated = True
        return animated
    except Exception as error:  # noqa: BLE001
        logger.error('[processEffects] %s', error)
        return False
