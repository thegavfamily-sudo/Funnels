# ============================================================================
# subscription.py — проверка подписки на канал + кэш с TTL 5 минут
#
# Порт SubscriptionManager из apps_script/БОТ_готовый.gs (:1619):
#   getChat (для @username) → getChatMember → статусы creator/administrator/member
#   кэш `sub_{userId}_{channel}`, TTL = SHORT_CACHE_TTL_SECONDS (5 мин)
# ============================================================================

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)

_subscription_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()

_SUBSCRIBED_STATUSES = {'creator', 'administrator', 'member'}


def _cache_key(user_id: Any, channel: str) -> str:
    return f'sub_{user_id}_{channel}'


def _cleanup(now: float) -> None:
    ttl = config.SHORT_CACHE_TTL_SECONDS * 1000
    expired = [k for k, v in _subscription_cache.items()
               if now - v.get('timestamp', 0) > ttl]
    for key in expired:
        del _subscription_cache[key]


def check_subscription(tg, user_id: Any, channel_identifier: Any) -> bool:
    """Проверить подписку пользователя на канал (getChatMember)."""
    try:
        if not channel_identifier or str(channel_identifier).strip() == '':
            return False

        channel = str(channel_identifier).strip()
        cache_key = _cache_key(user_id, channel)
        now = time.time() * 1000

        with _cache_lock:
            _cleanup(now)
            cached = _subscription_cache.get(cache_key)
            if cached and now - cached['timestamp'] < config.SHORT_CACHE_TTL_SECONDS * 1000:
                return cached['isSubscribed']

        chat_id: Any = channel
        if channel.startswith('@'):
            try:
                chat = tg.get_chat(channel)
                if chat:
                    chat_id = chat.get('id', channel)
            except Exception:  # noqa: BLE001
                pass

        member = tg.get_chat_member(chat_id, user_id)
        if not member:
            return False

        status = member.get('status', '')
        is_subscribed = status in _SUBSCRIBED_STATUSES

        with _cache_lock:
            _subscription_cache[cache_key] = {
                'isSubscribed': is_subscribed,
                'timestamp': now,
                'channel': channel,
                'status': status,
            }
        return is_subscribed
    except Exception as error:  # noqa: BLE001
        logger.error('[checkSubscription] %s', error)
        return False


def clear_user_cache(user_id: Any, channel_identifier: Optional[str] = None) -> None:
    with _cache_lock:
        if channel_identifier:
            _subscription_cache.pop(_cache_key(user_id, channel_identifier), None)
        else:
            prefix = f'sub_{user_id}_'
            for key in [k for k in _subscription_cache if k.startswith(prefix)]:
                del _subscription_cache[key]
