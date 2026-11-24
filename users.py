# ============================================================================
# users.py — работа с юзерами воронок + рефералы
#
# Порты из apps_script/БОТ_готовый.gs:
#   UserDataManager.upsertUser          :2201
#   UserDataManager.saveUserInputResponse:2347
#   UserDataManager.updateUserStep      :2437
#   UserDataManager.getUserTag/getAllUserTags :2486/:2520
#   Utils.replaceTagsInText             :1022
#   ReferralManager                     :1437
#
# Лист юзеров → таблица funnel_users: колонки funnel_id/user_id/current_step,
# остальное (включая динамические ответы) → data jsonb.
# Текущий шаг юзера = колонка current_step (замена UserProperties);
# дублируется в data.completed_at числом + data.last_updated = ISO.
# ============================================================================

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import config
from step_model import QuizConfig
from storage import BotContext, format_sheet_date, iso_now

logger = logging.getLogger(__name__)

# Внутренние ключи data — не выдаются как динамические теги
_INTERNAL_DATA_KEYS = {
    'username', 'first_name', 'user_id', 'date', 'funnel_name',
    'completed_at', 'last_updated', 'first_visit_date',
    'referral_link', 'referral_follower', 'referral_source',
    'referral_creative', 'referrer_id',
}

# Отображаемые имена тегов → ключи data (фиксированные колонки старого листа)
_BASIC_TAG_KEYS = {
    'Username': 'username',
    'First name': 'first_name',
    'User id': 'user_id',
    'Date': 'date',
    'Funnel name': 'funnel_name',
    'Completed at': 'completed_at',
    'Last Updated': 'last_updated',
    'Referral link': 'referral_link',
    'Referral follower': 'referral_follower',
    'Referral source': 'referral_source',
    'Referral creative': 'referral_creative',
}


# ---------------------------------------------------------------------------
# Бот-имя (для реферал-ссылок и ответов в группах)
# ---------------------------------------------------------------------------

def get_bot_username(ctx: BotContext) -> str:
    if ctx.bot_username:
        return ctx.bot_username
    username = config.DEFAULT_BOT_USERNAME
    try:
        if ctx.tg is not None:
            me = ctx.tg.get_me()
            if me and me.get('username'):
                username = me['username']
    except Exception as error:  # noqa: BLE001
        logger.error('[getBotUsername] %s', error)
    ctx.bot_username = username
    return username


# ---------------------------------------------------------------------------
# ReferralManager
# ---------------------------------------------------------------------------

class ReferralManager:
    @staticmethod
    def generate_referral_link(ctx: BotContext, user_id: Any) -> str:
        try:
            bot_username = get_bot_username(ctx)
            return f'https://t.me/{bot_username}?start=ref_{user_id}'
        except Exception:  # noqa: BLE001
            return f'https://t.me/{config.DEFAULT_BOT_USERNAME}?start=ref_{user_id}'

    @staticmethod
    def extract_referrer_id_from_start_command(text: Any) -> Optional[int]:
        """Порт :1466 — '/start ref_123' → 123."""
        try:
            if not text or not isinstance(text, str):
                return None
            if text == '/start':
                return None
            if text.startswith('/start '):
                param = text[7:].strip()
                if param.startswith('ref_'):
                    try:
                        referrer_id = int(param[4:])
                    except ValueError:
                        referrer_id = -1
                    if referrer_id > 0:
                        return referrer_id
            if 'ref_' in text:
                match = re.search(r'ref_(\d+)', text)
                if match:
                    referrer_id = int(match.group(1))
                    if referrer_id > 0:
                        return referrer_id
            return None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def extract_source_codes_from_start_command(
            text: Any) -> tuple[Optional[str], Optional[str]]:
        """'/start src_ads1' → ('ads1', None);
        '/start src_ads1--video1' → ('ads1', 'video1').

        Ссылки источников и креативов генерирует админка
        (funnel_sources_screen.dart):
        https://t.me/{bot}?start=src_{code} или src_{code}--{creative}.
        """
        try:
            if not text or not isinstance(text, str) or not text.startswith('/start'):
                return None, None
            parts = text.split()
            if len(parts) < 2:
                return None, None
            param = parts[1].strip()
            if not param.startswith('src_'):
                return None, None
            payload = param[4:]
            if not payload:
                return None, None
            if '--' in payload:
                source, creative = payload.split('--', 1)
                return (source or None), (creative or None)
            return payload, None
        except Exception:  # noqa: BLE001
            return None, None

    @staticmethod
    def find_referrer_by_link(ctx: BotContext, referral_link: Any) -> Optional[dict]:
        """Поиск по data->>referral_link в funnel_users текущей воронки."""
        try:
            if not referral_link or not isinstance(referral_link, str):
                return None
            row = ctx.storage.client.select_one(
                'funnel_users',
                filters={
                    'funnel_id': f'eq.{ctx.funnel_id}',
                    'data->>referral_link': f'eq.{referral_link}',
                },
            )
            if not row:
                return None
            return {
                'user_id': str(row.get('user_id') or ''),
                'referral_link': referral_link,
            }
        except Exception as error:  # noqa: BLE001
            logger.error('[findReferrerByLink] %s', error)
            return None

    @staticmethod
    def find_referrer_by_id(ctx: BotContext, referrer_id: Any) -> Optional[dict]:
        try:
            if not referrer_id:
                return None
            row = ctx.storage.get_user(ctx.funnel_id, referrer_id)
            if not row:
                return None
            data = row.get('data') or {}
            referral_link = data.get('referral_link') or ''
            if not referral_link:
                referral_link = ReferralManager.generate_referral_link(
                    ctx, referrer_id)
            return {
                'user_id': str(row.get('user_id') or referrer_id),
                'referral_link': referral_link,
            }
        except Exception as error:  # noqa: BLE001
            logger.error('[findReferrerById] %s', error)
            return None


# ---------------------------------------------------------------------------
# UserDataManager
# ---------------------------------------------------------------------------

def get_user_info(ctx: BotContext, user_id: Any) -> Optional[dict]:
    """data юзера или None (аналог getUserInfo :2228)."""
    row = ctx.storage.get_user(ctx.funnel_id, user_id)
    if not row:
        return None
    return row.get('data') or {}


def upsert_user(ctx: BotContext, user: dict, current_step: int,
                additional_data: Optional[dict] = None,
                referral_link: Optional[str] = None) -> bool:
    """Порт upsertUser (:2201): фиксированные поля + рефералы в data."""
    try:
        user_id = user.get('id')
        if user_id is None:
            return False

        username = user.get('username') or ''
        first_name = user.get('first_name') or ''
        flags = ctx.config

        existing = ctx.storage.get_user(ctx.funnel_id, user_id)
        user_exists = existing is not None
        data: dict = dict(existing.get('data') or {}) if existing else {}

        formatted_date = format_sheet_date()
        last_updated = iso_now()
        funnel_name = ctx.storage.get_funnel_name(ctx.funnel_id)

        # Date: первое посещение сохраняем, дальше не перетираем
        if user_exists:
            data['date'] = data.get('date') or formatted_date
        else:
            data['date'] = formatted_date

        data['funnel_name'] = funnel_name
        data['user_id'] = str(user_id)
        data['username'] = username
        data['first_name'] = first_name
        if flags.get('savestepprogress', True):
            data['completed_at'] = current_step
        data['last_updated'] = last_updated

        if not user_exists:
            if flags.get('referral', True) and not data.get('referral_link'):
                data['referral_link'] = ReferralManager.generate_referral_link(
                    ctx, user_id)
            if flags.get('referral', True) and referral_link:
                # Оригинал хранил referrerId только в кэше (не колонка) —
                # в Supabase-маппинге его нет, здесь не сохраняем.
                data['referral_follower'] = referral_link

        if additional_data:
            data.update(additional_data)

        ctx.storage.save_user_row(ctx.funnel_id, user_id, current_step, data)
        return True
    except Exception as error:  # noqa: BLE001
        logger.error('[upsertUser] %s', error)
        return False


def save_user_input_response(ctx: BotContext, user_id: Any, type_value: str,
                             response: Any) -> bool:
    """Порт saveUserInputResponse (:2347): ответ → data[type_value].

    Флаги: saveresponses (нет — пропуск), autocolumns (нет и ключа ещё нет —
    отказ, как при отсутствии колонки в старой таблице).
    """
    try:
        flags = ctx.config
        if not flags.get('saveresponses', True):
            return True

        existing = ctx.storage.get_user(ctx.funnel_id, user_id)
        data: dict = dict(existing.get('data') or {}) if existing else {}
        current_step = existing['current_step'] if existing else 0

        key = str(type_value).strip()
        if not key:
            return False

        if key not in data and not flags.get('autocolumns', True):
            return False

        data[key] = response
        data['last_updated'] = iso_now()

        ctx.storage.save_user_row(ctx.funnel_id, user_id, current_step, data)
        return True
    except Exception as error:  # noqa: BLE001
        logger.error('[saveUserInputResponse] %s', error)
        return False


def update_user_step(ctx: BotContext, user_id: Any, completed_at: int) -> bool:
    """Порт updateUserStep (:2437): Completed at + Last Updated.

    current_step (замена UserProperties) обновляется всегда; completed_at —
    только при savestepprogress (как колонка «Completed at» в оригинале).
    """
    try:
        existing = ctx.storage.get_user(ctx.funnel_id, user_id)
        data: dict = dict(existing.get('data') or {}) if existing else {}

        if ctx.config.get('savestepprogress', True):
            data['completed_at'] = completed_at
            data['last_updated'] = iso_now()

        ctx.storage.save_user_row(ctx.funnel_id, user_id, completed_at, data)
        return True
    except Exception as error:  # noqa: BLE001
        logger.error('[updateUserStep] %s', error)
        return False


def set_user_state(ctx: BotContext, user_id: Any, step: int) -> None:
    """setUserFunnelState (:6297): состояние шага + зеркало в таблицу."""
    update_user_step(ctx, user_id, step)


def get_user_state(ctx: BotContext, user_id: Any) -> int:
    """getUserFunnelState (:6304): текущий шаг (0 — не начат)."""
    row = ctx.storage.get_user(ctx.funnel_id, user_id)
    if not row:
        return 0
    try:
        return int(row.get('current_step') or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Теги {Username} / {Referral *} / динамические ключи data
# ---------------------------------------------------------------------------

def _basic_tags(data: dict) -> dict:
    tags = {}
    for display, key in _BASIC_TAG_KEYS.items():
        value = data.get(key)
        if display == 'Completed at':
            tags[display] = value or 0
        else:
            tags[display] = value if value is not None else ''
    return tags


def get_user_tag(ctx: BotContext, user_id: Any, tag_name: str) -> Any:
    """Порт getUserTag (:2486): фиксированный тег или ключ data; иначе None."""
    row = ctx.storage.get_user(ctx.funnel_id, user_id)
    if not row:
        return None
    data = row.get('data') or {}
    tags = _basic_tags(data)
    if tag_name in tags:
        return tags[tag_name]
    value = data.get(tag_name)
    return value if value else None


def get_all_user_tags(ctx: BotContext, user_id: Any) -> dict:
    """Порт getAllUserTags (:2520): фиксированные + динамические ключи data."""
    row = ctx.storage.get_user(ctx.funnel_id, user_id)
    if not row:
        return {}
    data = row.get('data') or {}
    tags = _basic_tags(data)
    for key, value in data.items():
        if key not in _INTERNAL_DATA_KEYS:
            tags[key] = value
    return tags


def replace_tags_in_text(ctx: BotContext, text: Any, user_id: Any,
                         _seen_conditions: Optional[set] = None) -> str:
    """Порт Utils.replaceTagsInText (:1022): {Tag} → значение (case-insensitive).

    Дополнительно {имя} condition-функции: проверка верна → её текст,
    иначе пусто. Без рекурсии в себя (через _seen_conditions).
    """
    if not text or not isinstance(text, str) or not user_id:
        return text
    tags = get_all_user_tags(ctx, user_id)
    result = text
    for tag_name, tag_value in tags.items():
        if tag_value is None:
            continue
        pattern = r'\{' + re.escape(str(tag_name)) + r'\}'
        result = re.sub(pattern, lambda _m, v=str(tag_value): v,
                        result, flags=re.IGNORECASE)

    # Остатки {name} — потенциальные имена условий.
    seen = set(_seen_conditions or ())

    def _cond_sub(match: 're.Match') -> str:
        name = match.group(1).strip()
        if name in seen:
            # Уже раскрываем это условие — вложенное {то же имя} → пусто.
            return ''
        resolved = _resolve_condition_placeholder(ctx, name, user_id, seen)
        return match.group(0) if resolved is None else resolved

    return re.sub(r'\{([^{}]+)\}', _cond_sub, result, flags=re.IGNORECASE)


def _resolve_condition_placeholder(ctx: BotContext, name: str, user_id: Any,
                                  seen: set) -> Optional[str]:
    """{имя} condition → текст, если верно; иначе ''. None — не условие."""
    # Ленивый импорт: functions импортирует users на уровне модуля.
    from functions import get_function_config, parse_condition, _cond_compare
    cfg = get_function_config(ctx, name)
    if not cfg or cfg.get('type') != 'condition':
        return None
    parsed = parse_condition(cfg.get('condition') or '')
    if not parsed:
        return ''
    tag, op, rhs = parsed
    row = ctx.storage.get_user(ctx.funnel_id, user_id)
    data: dict = dict(row.get('data') or {}) if row else {}
    if not _cond_compare(data.get(tag), op, rhs):
        return ''
    msg = str(cfg.get('message') or '').strip()
    if not msg:
        return ''
    return replace_tags_in_text(ctx, msg, user_id,
                                seen | {name})


def save_dice_tags(ctx: BotContext, user_id: Any, dice_emoji: Any,
                   dice_value: Any) -> None:
    """Сохранить результат броска дайса в теги пользователя.

    Теги: {dice} — значение для логики (как dice_logic_value у шага:
    для 🎰 — номер комбинации, 0 если символы разные; иначе значение
    Telegram), {dice_raw} — сырое значение (1..64 для 🎰), {dice_emoji} —
    эмодзи последнего броска, {dice_<эмодзи>} — значение конкретного типа.
    """
    try:
        if user_id is None:
            return
        emoji = str(dice_emoji or '🎲')
        try:
            raw = int(dice_value or 0)
        except (TypeError, ValueError):
            raw = 0

        # Ленивый импорт: steps импортирует users на уровне модуля.
        from steps import (are_all_symbols_same, get_same_symbol_number,
                           get_slot_symbols)
        if emoji == '🎰':
            symbols = get_slot_symbols(raw)
            value = (get_same_symbol_number(symbols)
                     if are_all_symbols_same(symbols) else 0)
        else:
            value = raw

        row = ctx.storage.get_user(ctx.funnel_id, user_id)
        data: dict = dict(row.get('data') or {}) if row else {}
        data['dice'] = value
        data['dice_raw'] = raw
        data['dice_emoji'] = emoji
        data[f'dice_{emoji}'] = value
        ctx.storage.save_user_row(ctx.funnel_id, user_id,
                                  get_user_state(ctx, user_id), data)
    except Exception as error:  # noqa: BLE001
        logger.error('[saveDiceTags] Error: %s', error)


def save_poll_tags(ctx: BotContext, user_id: Any, target: str,
                   selected_option: Any, logic_values: Optional[list],
                   quiz: Optional[QuizConfig] = None) -> None:
    """Сохранить ответ на poll/quiz в теги пользователя.

    Теги: {poll_answer}/{quiz_answer} — логическое значение выбранного
    варианта, {poll_option}/{quiz_option} — номер варианта (0..),
    {quiz_correct} — 1 правильный ответ / 0 нет (только для quiz;
    правильный вариант берётся из QuizConfig шага).
    """
    try:
        if user_id is None:
            return
        try:
            option = int(selected_option)
        except (TypeError, ValueError):
            return
        if option < 0:
            return

        prefix = 'quiz' if target == 'quiz' else 'poll'
        row = ctx.storage.get_user(ctx.funnel_id, user_id)
        data: dict = dict(row.get('data') or {}) if row else {}
        data[f'{prefix}_option'] = option

        values = logic_values or []
        if 0 <= option < len(values):
            answer = values[option]
            try:
                data[f'{prefix}_answer'] = int(answer)
            except (TypeError, ValueError):
                data[f'{prefix}_answer'] = str(answer)
        else:
            data[f'{prefix}_answer'] = option

        if target == 'quiz':
            correct = quiz.correct_option if quiz is not None else -1
            data['quiz_correct'] = 1 if option == correct else 0

        ctx.storage.save_user_row(ctx.funnel_id, user_id,
                                  get_user_state(ctx, user_id), data)
    except Exception as error:  # noqa: BLE001
        logger.error('[savePollTags] Error: %s', error)
