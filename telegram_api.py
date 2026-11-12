# ============================================================================
# telegram_api.py — обёртка над Telegram Bot API поверх requests
#
# Каждый экземпляр привязан к одному токену (один бот = один поток).
# Все методы возвращают данные как в оригинальном TelegramAPI (см. .gs).
# ============================================================================

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

import config

logger = logging.getLogger(__name__)

API_BASE = 'https://api.telegram.org'


class TelegramAPI:
    def __init__(self, token: str, timeout: int = 65):
        self.token = token
        self.base_url = f'{API_BASE}/bot{token}'
        self.timeout = timeout
        self._session = requests.Session()

    # -- low level ----------------------------------------------------------

    def send_request(self, method: str, payload: Optional[dict] = None) -> dict:
        """POST query-метода Bot API; всегда возвращает dict (никогда не бросает)."""
        url = f'{self.base_url}/{method}'
        try:
            resp = self._session.post(url, json=payload or {}, timeout=self.timeout)
            return resp.json()
        except Exception as error:  # noqa: BLE001 — как muteHttpExceptions в оригинале
            return {'ok': False, 'description': str(error)}

    def call(self, method: str, payload: Optional[dict] = None) -> Any:
        """Возвращает result при ok, иначе None."""
        response = self.send_request(method, payload)
        return response.get('result') if response.get('ok') else None

    # -- messages -----------------------------------------------------------

    def send_message(self, chat_id: Any, text: str, options: Optional[dict] = None) -> Optional[int]:
        payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
        if options:
            payload.update(options)
        result = self.send_request('sendMessage', payload)
        return result['result']['message_id'] if result.get('ok') else None

    def send_poll(self, chat_id: Any, question: str, options: list,
                  poll_options: Optional[dict] = None) -> Optional[dict]:
        payload = {'chat_id': chat_id, 'question': question, 'options': options}
        if poll_options:
            payload.update(poll_options)
        result = self.send_request('sendPoll', payload)
        if not result.get('ok'):
            return None
        data = result['result']
        poll = data.get('poll') or {}
        return {'message_id': data['message_id'], 'poll_id': poll.get('id')}

    def send_dice(self, chat_id: Any, emoji: str = '🎲',
                  options_obj: Optional[dict] = None) -> Optional[dict]:
        payload = {'chat_id': chat_id, 'emoji': emoji}
        if options_obj:
            payload.update(options_obj)
        result = self.send_request('sendDice', payload)
        if not result.get('ok'):
            return None
        data = result['result']
        dice = data.get('dice') or {}
        return {'message_id': data['message_id'], 'dice_value': dice.get('value')}

    def send_photo(self, chat_id: Any, photo: Any, caption: str = '',
                   options: Optional[dict] = None) -> Optional[int]:
        payload = {'chat_id': chat_id, 'photo': photo, 'caption': caption,
                   'parse_mode': 'HTML'}
        if options:
            payload.update(options)
        result = self.send_request('sendPhoto', payload)
        return result['result']['message_id'] if result.get('ok') else None

    def send_media_group(self, chat_id: Any, media: list) -> Optional[list]:
        result = self.send_request('sendMediaGroup', {'chat_id': chat_id, 'media': media})
        return result.get('result') if result.get('ok') else None

    def send_chat_action(self, chat_id: Any, action: str = 'typing') -> bool:
        action_to_send = action if action in config.VALID_CHAT_ACTIONS else 'typing'
        result = self.send_request('sendChatAction',
                                   {'chat_id': chat_id, 'action': action_to_send})
        return bool(result.get('ok'))

    # -- edits / service ----------------------------------------------------

    def edit_message_text(self, chat_id: Any, message_id: int, new_text: str,
                          options: Optional[dict] = None) -> bool:
        payload = {'chat_id': chat_id, 'message_id': message_id,
                   'text': new_text, 'parse_mode': 'HTML'}
        if options:
            payload.update(options)
        return bool(self.send_request('editMessageText', payload).get('ok'))

    def edit_message_reply_markup(self, chat_id: Any, message_id: int,
                                  reply_markup: Any = None) -> bool:
        payload: dict[str, Any] = {'chat_id': chat_id, 'message_id': message_id}
        if reply_markup is not None:
            payload['reply_markup'] = reply_markup
        return bool(self.send_request('editMessageReplyMarkup', payload).get('ok'))

    def delete_message(self, chat_id: Any, message_id: int) -> bool:
        return bool(self.send_request(
            'deleteMessage', {'chat_id': chat_id, 'message_id': message_id}).get('ok'))

    def answer_callback_query(self, callback_query_id: str, text: Optional[str] = None,
                              show_alert: bool = False) -> None:
        payload: dict[str, Any] = {'callback_query_id': callback_query_id}
        if text:
            payload['text'] = text
            payload['show_alert'] = show_alert
        self.send_request('answerCallbackQuery', payload)

    def pin_message(self, chat_id: Any, message_id: int) -> bool:
        return bool(self.send_request(
            'pinChatMessage',
            {'chat_id': chat_id, 'message_id': message_id,
             'disable_notification': True}).get('ok'))

    # -- info ---------------------------------------------------------------

    def get_me(self) -> Optional[dict]:
        result = self.send_request('getMe')
        return result.get('result') if result.get('ok') else None

    def get_chat(self, chat_id: Any) -> Optional[dict]:
        result = self.send_request('getChat', {'chat_id': chat_id})
        return result.get('result') if result.get('ok') else None

    def get_chat_member(self, chat_id: Any, user_id: Any) -> Optional[dict]:
        result = self.send_request('getChatMember',
                                   {'chat_id': chat_id, 'user_id': user_id})
        return result.get('result') if result.get('ok') else None

    def get_updates(self, offset: Optional[int] = None, timeout: int = 30,
                    limit: int = 100) -> Optional[list]:
        payload: dict[str, Any] = {'timeout': timeout, 'limit': limit}
        if offset is not None:
            payload['offset'] = offset
        result = self.send_request('getUpdates', payload)
        return result.get('result') if result.get('ok') else None
