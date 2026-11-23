# ============================================================================
# test_poll_form.py — опрос/квиз: объектный формат (JSON) → sendPoll,
# лимит 2–10 (лимит Telegram), автозакрытие open_period/close_date.
#
# Запуск:  python3 test_poll_form.py   (из папки bot git/)
# ============================================================================

from __future__ import annotations

import os
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from step_model import build_step, parse_close_date  # noqa: E402
from steps import handle_poll_message, handle_quiz_message  # noqa: E402
from steps import process_poll_result  # noqa: E402


def mk(data: dict):
    """Сырой dict шага → Step (как при загрузке воронки)."""
    return build_step(data, 1, 'f1')


POLL = {
    'question': 'Любимый цвет?',
    'answers': ['Красный', 'Синий'],
    'multiple': True,
    'anonymous': False,
    'logic_values': [0, 1],
    'steps': [5, 6],
}

QUIZ = {
    'question': 'Столица Франции?',
    'answers': ['Париж', 'Берлин', 'Мадрид'],
    'correct': 0,
    'explanation': 'Эйфелева башня в Париже',
    'anonymous': True,
    'logic_values': [1],
    'steps': [7],
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


class TestPollHandling(unittest.TestCase):
    """handle_poll_message: объект poll → sendPoll, лимит 2–10, автозакрытие."""

    def test_object_reaches_send_poll(self):
        ctx = make_ctx()
        handle_poll_message(ctx, 1, mk({'type': 'poll', 'poll': POLL}),
                            {}, None)

        sent = calls(ctx, 'sendPoll')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['question'], 'Любимый цвет?')
        self.assertEqual(sent[0]['options'], ['Красный', 'Синий'])
        self.assertTrue(sent[0]['poll']['allows_multiple_answers'])
        self.assertFalse(sent[0]['poll']['is_anonymous'])

    def test_one_option_is_error(self):
        ctx = make_ctx()
        step = {'type': 'poll', 'poll': {
            'question': 'Вопрос', 'answers': ['Только один'],
        }}
        handle_poll_message(ctx, 1, mk(step), {}, None)
        self.assertEqual(calls(ctx, 'sendPoll'), [])
        self.assertEqual(len(calls(ctx, 'sendMessage')), 1)

    def test_eleven_options_is_error(self):
        ctx = make_ctx()
        step = {'type': 'poll', 'poll': {
            'question': 'Вопрос',
            'answers': [f'a{i}' for i in range(11)],
        }}
        handle_poll_message(ctx, 1, mk(step), {}, None)
        self.assertEqual(calls(ctx, 'sendPoll'), [])
        self.assertEqual(len(calls(ctx, 'sendMessage')), 1)

    def test_open_period_passed(self):
        ctx = make_ctx()
        step = {'type': 'poll', 'poll': dict(POLL, open_period=60)}
        handle_poll_message(ctx, 1, mk(step), {}, None)
        self.assertEqual(calls(ctx, 'sendPoll')[0]['poll'].get('open_period'),
                         60)

    def test_open_period_out_of_range_ignored(self):
        ctx = make_ctx()
        step = {'type': 'poll', 'poll': dict(POLL, open_period=3)}
        handle_poll_message(ctx, 1, mk(step), {}, None)
        self.assertNotIn('open_period', calls(ctx, 'sendPoll')[0]['poll'])

    def test_close_date_wins_over_open_period(self):
        ctx = make_ctx()
        step = {'type': 'poll', 'poll': dict(
            POLL, open_period=60, close_date='2030-01-01 12:00')}
        handle_poll_message(ctx, 1, mk(step), {}, None)
        poll = calls(ctx, 'sendPoll')[0]['poll']
        expected = int(datetime(2030, 1, 1, 12, 0,
                                tzinfo=timezone.utc).timestamp())
        self.assertEqual(poll.get('close_date'), expected)
        self.assertNotIn('open_period', poll)


class TestQuizHandling(unittest.TestCase):
    """handle_quiz_message: объект quiz → sendPoll(type=quiz), лимит 2–10."""

    def test_object_reaches_send_poll(self):
        ctx = make_ctx()
        handle_quiz_message(ctx, 1, mk({'type': 'quiz', 'quiz': QUIZ}),
                            {}, None)

        sent = calls(ctx, 'sendPoll')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['poll'].get('type'), 'quiz')
        self.assertEqual(sent[0]['poll'].get('correct_option_id'), 0)

    def test_eleven_options_is_error(self):
        ctx = make_ctx()
        step = {'type': 'quiz', 'quiz': {
            'question': 'Вопрос',
            'answers': [f'a{i}' for i in range(11)],
            'correct': 0,
        }}
        handle_quiz_message(ctx, 1, mk(step), {}, None)
        self.assertEqual(calls(ctx, 'sendPoll'), [])
        self.assertEqual(len(calls(ctx, 'sendMessage')), 1)

    def test_quiz_timing(self):
        ctx = make_ctx()
        step = {'type': 'quiz', 'quiz': dict(QUIZ, open_period=3600)}
        handle_quiz_message(ctx, 1, mk(step), {}, None)
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


class TestTransitionModes(unittest.TestCase):
    """Режимы перехода poll/quiz как у dice (форма админки):

    — no_transition (чип «Без перехода») → воронка завершается;
    — маппинг «ответ → шаг» есть → переход по нему (>0), 0 — без ветки;
    — маппинга нет (чип «Следующий шаг») → следующий шаг воронки.
    """

    def _run(self, row: dict, selected: int = 0, target: str = 'poll'):
        """process_poll_result с репликой ctx; возвращает (send, complete)."""
        step = build_step(dict(row), 1, 'f1')
        step2 = build_step({'type': 'text', 'content': 'next'}, 2, 'f1')
        steps_by_num = {1: step, 2: step2}
        storage = types.SimpleNamespace(
            poll_results={},
            quiz_results={},
            get_step=lambda funnel_id, number: steps_by_num.get(number),
        )
        ctx = types.SimpleNamespace(storage=storage, funnel_id='f1',
                                    tg=FakeTG())
        cache = storage.poll_results if target == 'poll' \
            else storage.quiz_results
        cache['p1'] = {
            'userId': 'u1', 'chatId': 1, 'selectedOption': selected,
            'step': step, 'processed': False,
        }
        with mock.patch('steps.users.save_poll_tags'), \
                mock.patch('steps.users.set_user_state') as set_state, \
                mock.patch('steps.send_step_message') as send, \
                mock.patch('steps.complete_funnel') as complete, \
                mock.patch('steps.time.sleep'):
            result = process_poll_result(ctx, 'p1', target)
        return result, set_state, send, complete

    def _row(self, **poll_overrides) -> dict:
        poll = {
            'question': 'Цвет?',
            'answers': ['Красный', 'Синий'],
            'logic_values': [0, 1],
            'steps': [2, 2],
        }
        poll.update(poll_overrides)
        return {'type': 'poll', 'poll': poll}

    def test_no_transition_completes_funnel(self):
        row = self._row(no_transition=True, steps=[2, 2])
        result, set_state, send, complete = self._run(row, selected=1)
        self.assertFalse(result)
        complete.assert_called_once()
        send.assert_not_called()
        set_state.assert_not_called()

    def test_no_transition_wins_over_steps_mapping(self):
        # Флаг авторитетен даже при наличии шагов в объекте.
        row = self._row(no_transition=True, steps=[2, 2])
        _, _, send, complete = self._run(row, selected=0)
        complete.assert_called_once()
        send.assert_not_called()

    def test_no_transition_default_is_false(self):
        step = build_step(self._row(steps=[2, 2]), 1, 'f1')
        self.assertFalse(step.poll.no_transition)
        quiz = build_step({'type': 'quiz', 'quiz': {
            'question': 'Q', 'answers': ['a', 'b'], 'correct': 0,
        }}, 1, 'f1')
        self.assertFalse(quiz.quiz.no_transition)

    def test_steps_mapping_goes_to_mapped_step(self):
        row = self._row(steps=[2, 2])
        result, set_state, send, complete = self._run(row, selected=0)
        self.assertTrue(result)
        send.assert_called_once()
        self.assertEqual(send.call_args[0][1], 1)  # chat_id
        self.assertEqual(send.call_args[0][2], 2)  # next_step
        set_state.assert_called_once_with(mock.ANY, 'u1', 2)
        complete.assert_not_called()

    def test_zero_in_mapping_falls_back_to_next_step(self):
        # Старая семантика сохранена: 0 в маппинге = ветки нет → следующий.
        row = self._row(steps=[0, 0])
        result, _, send, complete = self._run(row, selected=0)
        self.assertTrue(result)
        send.assert_called_once()
        self.assertEqual(send.call_args[0][2], 2)
        complete.assert_not_called()

    def test_no_mapping_goes_to_next_step(self):
        row = self._row(steps=[])
        del row['poll']['steps']
        result, _, send, complete = self._run(row, selected=1)
        self.assertTrue(result)
        send.assert_called_once()
        self.assertEqual(send.call_args[0][2], 2)
        complete.assert_not_called()

    def test_quiz_no_transition_completes_funnel(self):
        row = {
            'type': 'quiz',
            'quiz': {
                'question': '2+2?', 'answers': ['3', '4'], 'correct': 1,
                'logic_values': [0, 1], 'steps': [2, 2],
                'no_transition': True,
            },
        }
        result, set_state, send, complete = self._run(
            row, selected=1, target='quiz')
        self.assertFalse(result)
        complete.assert_called_once()
        send.assert_not_called()
        set_state.assert_not_called()


if __name__ == '__main__':
    unittest.main()
