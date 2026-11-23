# ============================================================================
# subscription.py — проверка подписки на канал (без кэша статуса)
#
# Порт SubscriptionManager из apps_script/БОТ_готовый.gs (:1619):
#   getChat (для @username) → getChatMember → статусы creator/administrator/member
#
# Статус подписки НЕ кэшируется: кэширование False «запирало» пользователя
# на TTL после подписки (повторная проверка отдавала устаревший отказ).
# Кэшируется только chat_id из getChat — он не меняется.
# ============================================================================

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# @username → chat_id (бессрочно: идентификатор канала не меняется)
_chat_id_cache: dict[str, Any] = {}
_chat_id_lock = threading.Lock()

_SUBSCRIBED_STATUSES = {'creator', 'administrator', 'member'}


def check_subscription(tg, user_id: Any, channel_identifier: Any) -> bool:
    """Проверить подписку пользователя на канал (getChatMember)."""
    try:
        if not channel_identifier or str(channel_identifier).strip() == '':
            return False

        channel = str(channel_identifier).strip()

        chat_id: Any = channel
        if channel.startswith('@'):
            with _chat_id_lock:
                cached_id = _chat_id_cache.get(channel)
            if cached_id is not None:
                chat_id = cached_id
            else:
                try:
                    chat = tg.get_chat(channel)
                    if chat:
                        chat_id = chat.get('id', channel)
                        with _chat_id_lock:
                            _chat_id_cache[channel] = chat_id
                except Exception:  # noqa: BLE001
                    pass

        member = tg.get_chat_member(chat_id, user_id)
        if not member:
            return False

        return member.get('status', '') in _SUBSCRIBED_STATUSES
    except Exception as error:  # noqa: BLE001
        logger.error('[checkSubscription] %s', error)
        return False


def clear_cache() -> None:
    """Сбросить кэш chat_id (например, при смене @username канала)."""
    with _chat_id_lock:
        _chat_id_cache.clear()
