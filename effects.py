# ============================================================================
# effects.py — эффекты <effect:*>...</effect> в текстовых сообщениях
#
# Порты из apps_script/БОТ_готовый.gs:
#   Utils.parseEffects   :602   Utils.hasEffects  :649
#   Utils.getFinalText   :654   Utils.getInitialText :687
#   Utils.extractPureText:729   EffectsHandler     :6330
#
# Типы: countdown / progress / spinner / blink / reveal.
# Кадры внутри тега разделены '|'; задержки — time.sleep (как Utilities.sleep).
# ============================================================================

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r'<effect:(\w+)(?::([^>]+))?>|</effect>', re.IGNORECASE)


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


def _outer_effect(effects: Optional[list[dict]]) -> Optional[dict]:
    if not effects:
        return None
    outer = [e for e in effects if e['depth'] == 0]
    return outer[0] if outer else None


def _is_cum(effect: dict) -> bool:
    """reveal в режиме cum: кадр = полный текст (префиксы), не накопление."""
    params = effect.get('params') or []
    return len(params) > 1 and params[1].lower() in ('cum', 'raw')


def get_final_text(content: Any) -> str:
    """Итоговый текст после проигрывания эффекта (кадры → последний)."""
    if not content or not isinstance(content, str):
        return content or ''

    effect = _outer_effect(parse_effects(content))
    if not effect:
        return content

    final_text = content[:effect['start_index']]
    inner = effect['inner_content']

    if effect['type'] in ('countdown', 'progress', 'spinner'):
        parts = inner.split('|')
        final_text += parts[-1] if parts and parts[-1] else ''
    elif effect['type'] == 'reveal':
        parts = inner.split('|')
        if not parts:
            final_text += ''
        elif _is_cum(effect):
            final_text += parts[-1]
        else:
            # Анимация заканчивается накопленным текстом — итог = все кадры.
            final_text += '\n'.join(parts)
    elif effect['type'] == 'blink':
        final_text += inner or ''
    else:
        final_text += inner

    final_text += content[effect['start_index'] + len(effect['full_content']):]
    return final_text


def get_initial_text(content: Any) -> str:
    """Первый кадр эффекта для отправки перед проигрыванием."""
    if not content or not isinstance(content, str):
        return content or ''

    effect = _outer_effect(parse_effects(content))
    if not effect:
        return content

    initial_text = content[:effect['start_index']]
    inner = effect['inner_content']
    params = effect['params']

    if effect['type'] == 'countdown':
        parts = inner.split('|')
        initial_text += (parts[0] if parts and parts[0] else '3') if parts else '3'
    elif effect['type'] == 'progress':
        parts = inner.split('|')
        initial_text += (parts[0] if parts and parts[0] else '[░░░] 0%') if parts else '[░░░] 0%'
    elif effect['type'] == 'spinner':
        parts = inner.split('|')
        initial_text += (parts[0] if parts and parts[0] else 'Загрузка') if parts else 'Загрузка'
    elif effect['type'] == 'blink':
        initial_text += inner or '⚠️'
    elif effect['type'] == 'reveal':
        parts = inner.split('|')
        initial_text += (parts[0] if parts and parts[0] else '') if parts else ''
    else:
        initial_text += inner

    initial_text += content[effect['start_index'] + len(effect['full_content']):]
    return initial_text


def extract_pure_text(effect_content: Any) -> Any:
    """Снять все теги, оставив содержимое (Utils.extractPureText :729)."""
    if not effect_content or not isinstance(effect_content, str):
        return effect_content

    text = effect_content
    effects = parse_effects(text)
    if effects:
        for effect in sorted(effects, key=lambda e: -e['start_index']):
            text = (text[:effect['start_index']]
                    + effect['inner_content']
                    + text[effect['start_index'] + len(effect['full_content']):])
    return text


# ---------------------------------------------------------------------------
# Проигрывание (EffectsHandler)
# ---------------------------------------------------------------------------

# Защита от «умирающего» бота: длинный кадр/много циклов блокируют
# поток обработки на минуты (регресс: бот переставал отвечать).
_MAX_DELAY_MS = 5000
_MAX_CYCLES = 20


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


def process_effects(tg, chat_id, message_id: int, content: str,
                    user_id: Any = None) -> bool:
    """Проиграть внешний эффект через editMessageText (EffectsHandler :6330)."""
    try:
        effects = parse_effects(content)
        effect = _outer_effect(effects)
        if not effect:
            return False

        prefix_text = content[:effect['start_index']]
        suffix_text = content[effect['start_index'] + len(effect['full_content']):]
        effect_type = effect['type']

        if effect_type == 'countdown':
            return _process_frames(tg, chat_id, message_id, effect,
                                   prefix_text, suffix_text,
                                   _delay_ms(effect['params'], 800))
        if effect_type == 'progress':
            return _process_frames(tg, chat_id, message_id, effect,
                                   prefix_text, suffix_text,
                                   _delay_ms(effect['params'], 300))
        if effect_type == 'spinner':
            return _process_spinner(tg, chat_id, message_id, effect,
                                    prefix_text, suffix_text)
        if effect_type == 'blink':
            return _process_blink(tg, chat_id, message_id, effect,
                                  prefix_text, suffix_text)
        if effect_type == 'reveal':
            return _process_reveal(tg, chat_id, message_id, effect,
                                   prefix_text, suffix_text)
        return False
    except Exception as error:  # noqa: BLE001
        logger.error('[processEffects] %s', error)
        return False


def _process_frames(tg, chat_id, message_id: int, effect: dict,
                    prefix: str, suffix: str, delay: float) -> bool:
    """countdown/progress: каждый кадр через запятую-разделитель |."""
    parts = effect['inner_content'].split('|')
    if len(parts) < 2:
        return False
    for part in parts:
        tg.edit_message_text(chat_id, message_id, prefix + part + suffix)
        time.sleep(delay)
    return True


def _process_spinner(tg, chat_id, message_id: int, effect: dict,
                     prefix: str, suffix: str) -> bool:
    delay = _delay_ms(effect['params'], 200)
    cycles = _cycles(effect['params'], 1, 3)
    parts = effect['inner_content'].split('|')
    if len(parts) < 2:
        return False

    spinner_chars = ['/', '-', '\\', '|']
    for _ in range(cycles):
        for part in parts:
            for char in spinner_chars:
                tg.edit_message_text(chat_id, message_id,
                                     prefix + part + ' ' + char + suffix)
                time.sleep(delay)

    final_text = prefix + parts[-1] + ' ✓' + suffix
    tg.edit_message_text(chat_id, message_id, final_text)
    return True


def _process_blink(tg, chat_id, message_id: int, effect: dict,
                   prefix: str, suffix: str) -> bool:
    delay = _delay_ms(effect['params'], 300)
    cycles = _cycles(effect['params'], 1, 3)
    blink_char = effect['params'][2] if len(effect['params']) > 2 else '⚠️'
    text = effect['inner_content']

    for _ in range(cycles):
        tg.edit_message_text(chat_id, message_id,
                             prefix + blink_char + ' ' + text + suffix)
        time.sleep(delay)
        tg.edit_message_text(chat_id, message_id, prefix + text + suffix)
        time.sleep(delay)

    tg.edit_message_text(chat_id, message_id, prefix + text + suffix)
    return True


def _process_reveal(tg, chat_id, message_id: int, effect: dict,
                    prefix: str, suffix: str) -> bool:
    delay = _delay_ms(effect['params'], 500)
    parts = effect['inner_content'].split('|')
    if len(parts) < 2:
        return False

    if _is_cum(effect):
        # Кадры — готовый полный текст (префиксы выделения): показываем как
        # есть, финал совпадает с исходом побуквенно.
        for part in parts:
            tg.edit_message_text(chat_id, message_id, prefix + part + suffix)
            time.sleep(delay)
        return True

    combined = prefix
    for i, part in enumerate(parts):
        combined += ('\n' if i > 0 else '') + part
        tg.edit_message_text(chat_id, message_id, combined + suffix)
        time.sleep(delay)
    return True
