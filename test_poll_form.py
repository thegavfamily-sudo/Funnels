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
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from step_model import (build_step,  # noqa: E402
                        compose_poll_string, compose_quiz_string,
                        parse_close_date, parse_poll_data, parse_quiz_data,
                        timing_options)
from steps import handle_poll_message, handle_quiz_message  # noqa: E402
from steps import process_poll_result  # noqa: E402


def mk(data: dict):
    """Сырая строка шага → Step (как при загрузке воронки)."""
    return build_step(data, 1, 'f1')

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
        handle_poll_message(ctx, 1, mk(dict(FORM_POLL)), {}, None)

        sent = calls(ctx, 'sendPoll')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['question'], 'Любимый цвет?')
        self.assertEqual(sent[0]['options'], ['Красный', 'Синий'])
        self.assertTrue(sent[0]['poll']['allows_multiple_answers'])
        self.assertFalse(sent[0]['poll']['is_anonymous'])

    def test_one_option_is_error(self):
        ctx = make_ctx()
        step = {'poll_question': 'Вопрос', 'poll_answer': 'Только один'}
        handle_poll_message(ctx, 1, mk(step), {}, None)
        self.assertEqual(calls(ctx, 'sendPoll'), [])
        self.assertEqual(len(calls(ctx, 'sendMessage')), 1)

    def test_eleven_options_is_error(self):
        ctx = make_ctx()
        step = {
            'poll_question': 'Вопрос',
            'poll_answer': ', '.join(f'a{i}' for i in range(11)),
        }
        handle_poll_message(ctx, 1, mk(step), {}, None)
        self.assertEqual(calls(ctx, 'sendPoll'), [])
        self.assertEqual(len(calls(ctx, 'sendMessage')), 1)

    def test_open_period_passed(self):
        ctx = make_ctx()
        step = dict(FORM_POLL, poll_open_period='60')
        handle_poll_message(ctx, 1, mk(step), {}, None)
        self.assertEqual(calls(ctx, 'sendPoll')[0]['poll'].get('open_period'),
                         60)

    def test_open_period_out_of_range_ignored(self):
        ctx = make_ctx()
        step = dict(FORM_POLL, poll_open_period='3')
        handle_poll_message(ctx, 1, mk(step), {}, None)
        self.assertNotIn('open_period', calls(ctx, 'sendPoll')[0]['poll'])

    def test_close_date_wins_over_open_period(self):
        ctx = make_ctx()
        step = dict(FORM_POLL, poll_open_period='60',
                    poll_close_date='2030-01-01 12:00')
        handle_poll_message(ctx, 1, mk(step), {}, None)
        poll = calls(ctx, 'sendPoll')[0]['poll']
        expected = int(datetime(2030, 1, 1, 12, 0,
                                 tzinfo=timezone.utc).timestamp())
        self.assertEqual(poll.get('close_date'), expected)
        self.assertNotIn('open_period', poll)


class TestQuizHandling(unittest.TestCase):
    """handle_quiz_message: форма → sendPoll(type=quiz), лимит 2–10."""

    def test_form_fields_reach_send_poll(self):
        ctx = make_ctx()
        handle_quiz_message(ctx, 1, mk(dict(FORM_QUIZ)), {}, None)

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
        handle_quiz_message(ctx, 1, mk(step), {}, None)
        self.assertEqual(calls(ctx, 'sendPoll'), [])
        self.assertEqual(len(calls(ctx, 'sendMessage')), 1)

    def test_quiz_timing(self):
        ctx = make_ctx()
        step = dict(FORM_QUIZ, quiz_open_period='3600')
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

    def test_timing_options_empty(self):
        self.assertEqual(timing_options({}, 'poll'), {})


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

    def test_no_transition_read_from_flat_form_fields(self):
        # Фолбэк без объекта (плоские колонки формы / старые строки).
        step = build_step(dict(FORM_POLL, poll_no_transition='TRUE'), 1, 'f1')
        self.assertTrue(step.poll.no_transition)
        step = build_step(dict(FORM_QUIZ, quiz_no_transition='TRUE'), 1, 'f1')
        self.assertTrue(step.quiz.no_transition)

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
