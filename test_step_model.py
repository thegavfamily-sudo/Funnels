# ============================================================================
# test_step_model.py — адаптер build_step: старый (строки колонок) и новый
# (типизированный JSON) форматы шага дают одинаковый Step.
#
# Запуск:  python3 test_step_model.py   (из папки bot/)
# ============================================================================

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from step_model import build_step  # noqa: E402


class TestLegacyFormat(unittest.TestCase):
    """Старый формат: строки колонок, '0'/TRUE, опрос через '|'."""

    def test_scalars(self):
        step = build_step({
            'type': 'Text', 'content': 'Hi',
            'next_step': '0', 'scroll': 'auto', 'delay': '2', 'pin': 'TRUE',
        }, 5, 'f1')
        self.assertEqual(step.type, 'text')
        self.assertEqual(step.next, 0)          # явный '0'
        self.assertTrue(step.auto)
        self.assertEqual(step.delay, 2.0)
        self.assertTrue(step.pin)

    def test_empty_next_is_none(self):
        step = build_step({'next_step': ''}, 1, 'f1')
        self.assertIsNone(step.next)

    def test_poll_string(self):
        step = build_step(
            {'type': 'poll', 'poll': 'Вопрос|Да,Нет|false|true|1,2|3,4'},
            1, 'f1')
        self.assertIsNotNone(step.poll)
        self.assertEqual(step.poll.question, 'Вопрос')
        self.assertEqual(step.poll.answers, ['Да', 'Нет'])
        self.assertEqual(step.poll.logic_values, [1, 2])
        self.assertEqual(step.poll.steps, [3, 4])

    def test_poll_form_fields(self):
        step = build_step({
            'poll_question': 'Цвет?', 'poll_answer': 'Красный,Синий',
            'poll_logic_value': '0,1', 'poll_step': '2,3',
        }, 1, 'f1')
        self.assertEqual(step.poll.answers, ['Красный', 'Синий'])
        self.assertEqual(step.poll.steps, [2, 3])


class TestNewFormat(unittest.TestCase):
    """Новый формат: типизированные скалярные поля и вложенные объекты."""

    def test_typed_scalars(self):
        step = build_step({
            'type': 'text', 'content': 'Hi',
            'next_step': 7, 'delay': 3, 'pin': True,
        }, 5, 'f1')
        self.assertEqual(step.next, 7)
        self.assertEqual(step.delay, 3.0)
        self.assertTrue(step.pin)
        self.assertFalse(step.auto)

    def test_poll_object(self):
        step = build_step({
            'type': 'poll',
            'poll': {
                'question': 'Цвет?',
                'answers': ['Красный', 'Синий'],
                'multiple': True,
                'anonymous': False,
                'logic_values': [10, 20],
                'steps': [3, 4],
                'open_period': 60,
            },
        }, 1, 'f1')
        poll = step.poll
        self.assertEqual(poll.question, 'Цвет?')
        self.assertEqual(poll.answers, ['Красный', 'Синий'])
        self.assertTrue(poll.multiple)
        self.assertFalse(poll.anonymous)
        self.assertEqual(poll.logic_values, [10, 20])
        self.assertEqual(poll.steps, [3, 4])
        self.assertEqual(poll.open_period, 60)
        self.assertIsNone(poll.close_date)

    def test_poll_object_open_period_out_of_range(self):
        step = build_step(
            {'poll': {'answers': ['a', 'b'], 'open_period': 3}}, 1, 'f1')
        self.assertIsNone(step.poll.open_period)

    def test_quiz_object(self):
        step = build_step({
            'quiz': {
                'question': 'Столица?',
                'answers': ['Париж', 'Берлин'],
                'correct': 0,
                'explanation': 'Эйфелева',
                'logic_values': [1],
                'steps': [9],
            },
        }, 1, 'f1')
        quiz = step.quiz
        self.assertEqual(quiz.correct_option, 0)
        self.assertEqual(quiz.explanation, 'Эйфелева')
        self.assertEqual(quiz.steps, [9])

    def test_quiz_object_correct_out_of_range(self):
        step = build_step(
            {'quiz': {'answers': ['a', 'b'], 'correct': 5}}, 1, 'f1')
        self.assertEqual(step.quiz.correct_option, 0)

    def test_dice_object(self):
        step = build_step({
            'dice': {'emoji': '🎰', 'logic_values': [1, 2], 'steps': [5, 6]},
        }, 1, 'f1')
        self.assertEqual(step.dice.emoji, '🎰')
        self.assertEqual(step.dice.logic_values, [1, 2])
        self.assertEqual(step.dice.steps, [5, 6])

    def test_dice_object_invalid_emoji_falls_back(self):
        step = build_step({'dice': {'emoji': '💥'}}, 1, 'f1')
        self.assertEqual(step.dice.emoji, '🎲')

    def test_barcode_object(self):
        step = build_step({
            'type': 'QR', 'content': 'https://example.com',
            'barcode': {
                'options': {'size': 300, 'color': 'ff0000'},
                'qr': {'ecl': 'H', 'margin': 2},
            },
        }, 1, 'f1')
        self.assertEqual(step.type, 'barcode')
        self.assertEqual(step.barcode.data, 'https://example.com')
        self.assertEqual(step.barcode.options['size'], 300)
        self.assertEqual(step.barcode.options['format'], 'png')
        self.assertEqual(step.barcode.qr_config['ecl'], 'H')

    def test_absent_poll_key_is_none(self):
        step = build_step({'type': 'text'}, 1, 'f1')
        self.assertIsNone(step.poll)
        self.assertIsNone(step.quiz)


class TestNewFormatSend(unittest.TestCase):
    """Шаг из нового формата проходит через send-диспетчер без парсинга."""

    def test_handle_poll_from_object(self):
        import types
        from step_model import build_step as bs
        from steps import handle_poll_message

        class FakeTG:
            def __init__(self):
                self.calls = []

            def send_message(self, chat_id, text, options=None):
                self.calls.append(('msg', text))
                return 1

            def send_poll(self, chat_id, question, options, poll_options=None):
                self.calls.append(('poll', question, options, poll_options))
                return {'message_id': 1, 'poll_id': 'p1'}

            def pin_message(self, chat_id, message_id):
                self.calls.append(('pin', message_id))

        class FakeStorage:
            def __init__(self):
                self.poll_results = {}
                self.quiz_results = {}

            def cleanup_game_caches(self):
                pass

        ctx = types.SimpleNamespace(tg=FakeTG(), storage=FakeStorage())
        step = bs({'type': 'poll', 'pin': True, 'poll': {
            'question': 'Вопрос', 'answers': ['Да', 'Нет'],
            'open_period': 60,
        }}, 1, 'f1')
        handle_poll_message(ctx, 1, step, {}, None)

        polls = [c for c in ctx.tg.calls if c[0] == 'poll']
        self.assertEqual(len(polls), 1)
        self.assertEqual(polls[0][1], 'Вопрос')
        self.assertEqual(polls[0][3].get('open_period'), 60)
        self.assertIn(('pin', 1), ctx.tg.calls)


if __name__ == '__main__':
    unittest.main()
