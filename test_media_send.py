# ============================================================================
# test_media_send.py — отправка медиа: альбом (sendMediaGroup) и одиночный
# файл. Telegram: sendMediaGroup принимает 2–10 элементов photo/video/
# audio/document; sendDocument/sendAudio/… — один файл.
#
# Запуск:  python3 test_media_send.py   (из папки bot/)
# ============================================================================

from __future__ import annotations

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from steps import handle_dice_message, send_media_message  # noqa: E402


class FakeTG:
    """Минимальный TelegramAPI: записывает вызовы, не ходит в сеть."""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def send_media_group(self, chat_id, media):
        self.calls.append(('sendMediaGroup', media))
        return [{'message_id': 1}]

    def send_request(self, method, payload=None):
        self.calls.append((method, payload))
        return {'ok': True, 'result': {'message_id': 2}}

    def send_photo(self, chat_id, photo, caption='', options=None):
        self.calls.append(('sendPhoto', photo))
        return 2

    def send_message(self, chat_id, text, options=None):
        self.calls.append(('sendMessage', text))
        return 2

    def send_poll(self, *args, **kwargs):
        self.calls.append(('sendPoll', args))
        return None

    def send_dice(self, *args, **kwargs):
        self.calls.append(('sendDice', args))
        return None


def make_ctx():
    return types.SimpleNamespace(tg=FakeTG())


def group_items(ctx):
    for method, payload in ctx.tg.calls:
        if method == 'sendMediaGroup':
            return payload
    return None


class TestDocumentAlbum(unittest.TestCase):
    """Несколько документов → один альбом; один → sendDocument."""

    def test_two_document_urls_form_album(self):
        ctx = make_ctx()
        step = {'file_url': 'https://x.com/a.pdf, https://x.com/b.pdf'}
        send_media_message(ctx, 1, step, 'document', {})

        items = group_items(ctx)
        self.assertIsNotNone(items, 'ожидался sendMediaGroup')
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i['type'] == 'document' for i in items))

    def test_single_document_is_not_album(self):
        ctx = make_ctx()
        step = {'file_url': 'https://x.com/a.pdf'}
        send_media_message(ctx, 1, step, 'document', {})

        methods = [m for m, _ in ctx.tg.calls]
        self.assertNotIn('sendMediaGroup', methods)
        self.assertIn('sendDocument', methods)

    def test_document_file_ids_form_album(self):
        ctx = make_ctx()
        step = {'file_id': 'AgACAgIAAxkBAA1, AgACAgIAAxkBAA2'}
        send_media_message(ctx, 1, step, 'document', {})

        items = group_items(ctx)
        self.assertIsNotNone(items, 'ожидался sendMediaGroup')
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i['type'] == 'document' for i in items))


class TestAudioAlbum(unittest.TestCase):
    """Аудио — тоже альбом (InputMediaAudio), 2–10 элементов."""

    def test_two_audio_urls_form_album(self):
        ctx = make_ctx()
        step = {'file_url': 'https://x.com/a.mp3, https://x.com/b.mp3'}
        send_media_message(ctx, 1, step, 'audio', {})

        items = group_items(ctx)
        self.assertIsNotNone(items, 'ожидался sendMediaGroup')
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i['type'] == 'audio' for i in items))


class TestAlbumCaption(unittest.TestCase):
    """Подпись в альбоме — только у первого элемента (как в Telegram)."""

    def test_caption_on_first_item_only(self):
        ctx = make_ctx()
        step = {
            'type': 'document',
            'content': '<b>Подпись</b>',
            'file_url': 'https://x.com/a.pdf, https://x.com/b.pdf',
        }
        send_media_message(ctx, 1, step, 'document', {})

        items = group_items(ctx)
        self.assertIsNotNone(items)
        self.assertIn('caption', items[0])
        self.assertNotIn('caption', items[1])


class TestSingleFileTypes(unittest.TestCase):
    """Одиночные типы (анимация и т.п.) альбом не собирают."""

    def test_animation_multiple_urls_uses_first_only(self):
        ctx = make_ctx()
        step = {'file_url': 'https://x.com/a.gif, https://x.com/b.gif'}
        send_media_message(ctx, 1, step, 'animation', {})

        methods = [m for m, _ in ctx.tg.calls]
        self.assertNotIn('sendMediaGroup', methods)
        self.assertIn('sendAnimation', methods)


class TestCaptionSupport(unittest.TestCase):
    """caption есть не у всех методов Bot API.

    Сверено по сигнатурам python-telegram-bot и @types/node-telegram-bot-api
    (core.telegram.org из этой среды недоступен):
      sendVoice    — caption, caption_entities, parse_mode ЕСТЬ;
      sendVideoNote, sendSticker, sendDice — caption НЕТ.
    """

    @staticmethod
    def _payload(ctx, method):
        for m, payload in ctx.tg.calls:
            if m == method:
                return payload
        return None

    def _sent(self, media_type, url):
        ctx = make_ctx()
        step = {'type': media_type, 'content': 'Подпись', 'file_url': url}
        send_media_message(ctx, 1, step, media_type, {})
        return ctx

    def test_video_note_sends_no_caption(self):
        ctx = self._sent('video_note', 'https://x.com/a.mp4')
        payload = self._payload(ctx, 'sendVideoNote')
        self.assertIsNotNone(payload, 'ожидался sendVideoNote')
        self.assertNotIn('caption', payload)
        self.assertNotIn('parse_mode', payload)

    def test_sticker_sends_no_caption(self):
        ctx = self._sent('sticker', 'https://x.com/s.webp')
        payload = self._payload(ctx, 'sendSticker')
        self.assertIsNotNone(payload, 'ожидался sendSticker')
        self.assertNotIn('caption', payload)
        self.assertNotIn('parse_mode', payload)

    def test_voice_sends_caption(self):
        ctx = self._sent('voice', 'https://x.com/a.ogg')
        payload = self._payload(ctx, 'sendVoice')
        self.assertIsNotNone(payload, 'ожидался sendVoice')
        self.assertEqual(payload.get('caption'), 'Подпись')

    def test_document_sends_caption(self):
        ctx = self._sent('document', 'https://x.com/a.pdf')
        payload = self._payload(ctx, 'sendDocument')
        self.assertIsNotNone(payload, 'ожидался sendDocument')
        self.assertEqual(payload.get('caption'), 'Подпись')


class TestDicePayload(unittest.TestCase):
    """sendDice принимает только reply_markup из наших options —
    parse_mode туда слать нельзя."""

    def test_only_reply_markup_passed(self):
        ctx = make_ctx()
        options = {
            'parse_mode': 'HTML',
            'reply_markup': {'inline_keyboard': [[{'text': 'ok'}]]},
        }
        handle_dice_message(ctx, 1, {'dice': '🎲'}, options, 1, None)

        dice_calls = [args for m, args in ctx.tg.calls if m == 'sendDice']
        self.assertEqual(len(dice_calls), 1)
        sent_options = dice_calls[0][2]  # (chat_id, emoji, options)
        self.assertEqual(list(sent_options), ['reply_markup'])


if __name__ == '__main__':
    unittest.main()
