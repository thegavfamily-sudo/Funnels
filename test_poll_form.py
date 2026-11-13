# ============================================================================
# test_poll_form.py — опрос/квиз из полей формы: сборка эталонной строки
# колонки, валидация 2–10 (лимит Telegram), автозакрытие open_period/
# close_date.
#
# Запуск:  python3 test_poll_form.py   (из папки bot/)
# ============================================================================

from __future__ import annotations

import os
import sys
import types
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from steps import (compose_poll_string,  # noqa: E402
                   compose_quiz_string, handle_poll_message,
                   handle_quiz_message, parse_close_date, parse_poll_data,
                   parse_quiz_data, timing_options)

FORM_POLL = {
    'poll_question': 'Любимый цвет?',
    'poll_answer': 'Красный, Синий',
    'poll_multiple': 'TRUE',
    'poll_anonymous': 'FALSE',
    'poll_logic_value': '0, 1',
    'poll_step': '5, 6',
}

FORM_QUIZ = {
    'quiz_question': 'Столица Франции?',
    'quiz_answer': 'Париж, Берлин, Мадрид',
    'quiz_option': '0',
    'quiz_explanation': 'Эйфелева башня в Париже',
    'quiz_anonymous': 'TRUE',
    'quiz_logic_value': '1',
    'quiz_step': '7',
}


class FakeTG:
    """Записывает вызовы, в сеть не ходит; send_poll → None (нет storage)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def send_message(self, chat_id, text, options=None):
        self.calls.append(('sendMessage', {'text': text}))
        return 1

    def send_poll(self, chat_id, question, options, poll_options=None):
        self.calls.append((
            'sendPoll',
            {'question': question, 'options': options,
             'poll': dict(poll_options or {})},
        ))
        return None

    def pin_message(self, chat_id, message_id):
        return True


def make_ctx():
    return types.SimpleNamespace(tg=FakeTG())


def calls(ctx, method):
    return [payload for m, payload in ctx.tg.calls if m == method]


class TestComposeFromFormFields(unittest.TestCase):
    """Форма пишет poll_*/quiz_* — бот собирает эталонную строку колонки."""

    def test_poll_roundtrip(self):
        parsed = parse_poll_data(compose_poll_string(dict(FORM_POLL)))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['question'], 'Любимый цвет?')
        self.assertEqual(parsed['answers'], ['Красный', 'Синий'])
        self.assertTrue(parsed['multiple'])
        self.assertFalse(parsed['anonymous'])
        self.assertEqual(parsed['logicValues'], [0, 1])
        self.assertEqual(parsed['steps'], [5, 6])

    def test_quiz_roundtrip(self):
        parsed = parse_quiz_data(compose_quiz_string(dict(FORM_QUIZ)))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['question'], 'Столица Франции?')
        self.assertEqual(parsed['answers'], ['Париж', 'Берлин', 'Мадрид'])
        self.assertEqual(parsed['correctOption'], 0)
        self.assertEqual(parsed['explanation'], 'Эйфелева башня в Париже')
        self.assertTrue(parsed['anonymous'])

    def test_empty_form_fields_compose_nothing(self):
        self.assertEqual(compose_poll_string({}), '')
        self.assertEqual(compose_quiz_string({}), '')


class TestPollHandling(unittest.TestCase):
    """handle_poll_message: форма → sendPoll, лимит 2–10, автозакрытие."""

    def test_form_fields_reach_send_poll(self):
        ctx = make_ctx()
        handle_poll_message(ctx, 1, dict(FORM_POLL), {}, 1, None)

        sent = calls(ctx, 'sendPoll')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['question'], 'Любимый цвет?')
        self.assertEqual(sent[0]['options'], ['Красный', 'Синий'])
        self.assertTrue(sent[0]['poll']['allows_multiple_answers'])
        self.assertFalse(sent[0]['poll']['is_anonymous'])

    def test_one_option_is_error(self):
        ctx = make_ctx()
        step = {'poll_question': 'Вопрос', 'poll_answer': 'Только один'}
        handle_poll_message(ctx, 1, step, {}, 1, None)
        self.assertEqual(calls(ctx, 'sendPoll'), [])
        self.assertEqual(len(calls(ctx, 'sendMessage')), 1)

    def test_eleven_options_is_error(self):
        ctx = make_ctx()
        step = {
            'poll_question': 'Вопрос',
            'poll_answer': ', '.join(f'a{i}' for i in range(11)),
        }
        handle_poll_message(ctx, 1, step, {}, 1, None)
        self.assertEqual(calls(ctx, 'sendPoll'), [])
        self.assertEqual(len(calls(ctx, 'sendMessage')), 1)

    def test_open_period_passed(self):
        ctx = make_ctx()
        step = dict(FORM_POLL, poll_open_period='60')
        handle_poll_message(ctx, 1, step, {}, 1, None)
        self.assertEqual(calls(ctx, 'sendPoll')[0]['poll'].get('open_period'),
                         60)

    def test_open_period_out_of_range_ignored(self):
        ctx = make_ctx()
        step = dict(FORM_POLL, poll_open_period='3')
        handle_poll_message(ctx, 1, step, {}, 1, None)
        self.assertNotIn('open_period', calls(ctx, 'sendPoll')[0]['poll'])

    def test_close_date_wins_over_open_period(self):
        ctx = make_ctx()
        step = dict(FORM_POLL, poll_open_period='60',
                    poll_close_date='2030-01-01 12:00')
        handle_poll_message(ctx, 1, step, {}, 1, None)
        poll = calls(ctx, 'sendPoll')[0]['poll']
        expected = int(datetime(2030, 1, 1, 12, 0,
                                 tzinfo=timezone.utc).timestamp())
        self.assertEqual(poll.get('close_date'), expected)
        self.assertNotIn('open_period', poll)


class TestQuizHandling(unittest.TestCase):
    """handle_quiz_message: форма → sendPoll(type=quiz), лимит 2–10."""

    def test_form_fields_reach_send_poll(self):
        ctx = make_ctx()
        handle_quiz_message(ctx, 1, dict(FORM_QUIZ), {}, 1, None)

        sent = calls(ctx, 'sendPoll')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['poll'].get('type'), 'quiz')
        self.assertEqual(sent[0]['poll'].get('correct_option_id'), 0)

    def test_eleven_options_is_error(self):
        ctx = make_ctx()
        step = {
            'quiz_question': 'Вопрос',
            'quiz_answer': ', '.join(f'a{i}' for i in range(11)),
            'quiz_option': '0',
        }
        handle_quiz_message(ctx, 1, step, {}, 1, None)
        self.assertEqual(calls(ctx, 'sendPoll'), [])
        self.assertEqual(len(calls(ctx, 'sendMessage')), 1)

    def test_quiz_timing(self):
        ctx = make_ctx()
        step = dict(FORM_QUIZ, quiz_open_period='3600')
        handle_quiz_message(ctx, 1, step, {}, 1, None)
        self.assertEqual(calls(ctx, 'sendPoll')[0]['poll'].get('open_period'),
                         3600)


class TestCloseDateParsing(unittest.TestCase):
    def test_unix_timestamp_string(self):
        self.assertEqual(parse_close_date('1735689600'), 1735689600)

    def test_datetime_utc(self):
        expected = int(datetime(2030, 1, 1, 12, 0,
                                 tzinfo=timezone.utc).timestamp())
        self.assertEqual(parse_close_date('2030-01-01 12:00'), expected)

    def test_invalid_returns_none(self):
        self.assertIsNone(parse_close_date(''))
        self.assertIsNone(parse_close_date('вчера'))

    def test_timing_options_empty(self):
        self.assertEqual(timing_options({}, 'poll'), {})


if __name__ == '__main__':
    unittest.main()
