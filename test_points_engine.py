# ============================================================================
# test_points_engine.py — calc (формулы над тегами) + save_dice_tags
#
# Запуск:  python3 test_points_engine.py   (из папки bot/)
# ============================================================================

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from functions import (CalcError, evaluate_calc,  # noqa: E402
                       execute_calc_formula)
from users import save_dice_tags, save_poll_tags  # noqa: E402


class _FakeStorage:
    def __init__(self, data=None, step=1):
        self.row = {'current_step': step, 'data': dict(data or {})}

    def get_user(self, funnel_id, user_id):
        return self.row

    def save_user_row(self, funnel_id, user_id, current_step, data):
        self.row = {'current_step': current_step, 'data': dict(data)}


class _FakeCtx:
    funnel_id = 'f1'

    def __init__(self, data=None, step=1):
        self.storage = _FakeStorage(data, step)


class TestCalcEval(unittest.TestCase):
    def test_precedence_and_parens(self):
        self.assertEqual(evaluate_calc('2 + 3 * 4', {}), 14.0)
        self.assertEqual(evaluate_calc('(2 + 3) * 4', {}), 20.0)
        self.assertEqual(evaluate_calc('-5 + 10', {}), 5.0)

    def test_tags(self):
        tags = {'coins': 200, 'dice': 4}
        self.assertEqual(evaluate_calc('coins * dice', tags), 800.0)
        self.assertEqual(evaluate_calc('coins + 1', tags), 201.0)

    def test_string_numbers_coerced(self):
        self.assertEqual(evaluate_calc('coins * 2', {'coins': '25'}), 50.0)

    def test_missing_tag_is_zero(self):
        self.assertEqual(evaluate_calc('missing * 5', {}), 0.0)

    def test_non_numeric_tag_raises(self):
        with self.assertRaises(CalcError):
            evaluate_calc('name + 1', {'name': 'abc'})

    def test_division_by_zero(self):
        with self.assertRaises(CalcError):
            evaluate_calc('10 / 0', {})

    def test_min_max(self):
        self.assertEqual(evaluate_calc('min(coins, 1000)', {'coins': 5000}),
                         1000.0)
        self.assertEqual(evaluate_calc('max(coins, 10)', {'coins': 3}), 10.0)

    def test_emoji_tag_identifier(self):
        # Тег вида dice_🎲 — идентификатор может содержать эмодзи.
        self.assertEqual(evaluate_calc('coins * dice_🎲',
                                       {'coins': 10, 'dice_🎲': 3}), 30.0)

    def test_syntax_error(self):
        with self.assertRaises(CalcError):
            evaluate_calc('coins *', {'coins': 1})
        with self.assertRaises(CalcError):
            evaluate_calc('coins = coins', {'coins': 1})


class TestExecuteCalcFormula(unittest.TestCase):
    def test_write_and_message(self):
        ctx = _FakeCtx({'coins': 200, 'dice': 4}, step=7)
        result = execute_calc_formula(
            ctx, 111, 'coins = coins * dice',
            'Выпало {dice}! Баланс: {coins} ({result})')
        self.assertTrue(result['success'])
        self.assertEqual(result['message'], 'Выпало 4! Баланс: 800 (800)')
        self.assertEqual(ctx.storage.row['data']['coins'], 800)
        self.assertEqual(ctx.storage.row['current_step'], 7)

    def test_no_message_is_silent(self):
        ctx = _FakeCtx({'coins': 200}, step=7)
        result = execute_calc_formula(ctx, 111, 'coins = coins * 2', '')
        self.assertTrue(result['success'])
        self.assertEqual(result['message'], '')
        self.assertEqual(ctx.storage.row['data']['coins'], 400)

    def test_missing_formula(self):
        result = execute_calc_formula(_FakeCtx(), 111, '', '')
        self.assertFalse(result['success'])

    def test_no_equals_sign(self):
        result = execute_calc_formula(_FakeCtx(), 111, 'coins + 1', '')
        self.assertFalse(result['success'])

    def test_bad_target(self):
        result = execute_calc_formula(_FakeCtx(), 111, 'coins + 1 = 2', '')
        self.assertFalse(result['success'])

    def test_calc_error_message(self):
        ctx = _FakeCtx({'coins': 10})
        result = execute_calc_formula(ctx, 111, 'coins = coins / 0', '')
        self.assertFalse(result['success'])
        self.assertIn('деление на ноль', result['message'])


class TestSaveDiceTags(unittest.TestCase):
    def test_plain_dice(self):
        ctx = _FakeCtx({'coins': 200}, step=5)
        save_dice_tags(ctx, 111, '🎲', 4)
        data = ctx.storage.row['data']
        self.assertEqual(data['dice'], 4)
        self.assertEqual(data['dice_raw'], 4)
        self.assertEqual(data['dice_emoji'], '🎲')
        self.assertEqual(data['dice_🎲'], 4)
        self.assertEqual(data['coins'], 200)

    def test_all_dice_types(self):
        ctx = _FakeCtx()
        for emoji, value in (('🎯', 3), ('🏀', 2), ('⚽', 5),
                             ('🎳', 6), ('🎲', 1)):
            save_dice_tags(ctx, 111, emoji, value)
            data = ctx.storage.row['data']
            self.assertEqual(data['dice'], value)
            self.assertEqual(data['dice_emoji'], emoji)
            self.assertEqual(data[f'dice_{emoji}'], value)

    def test_slots_combo(self):
        ctx = _FakeCtx()
        # 22 → 🍒🍒🍒 (комбинация №1), сырое значение 22.
        save_dice_tags(ctx, 111, '🎰', 22)
        data = ctx.storage.row['data']
        self.assertEqual(data['dice'], 1)
        self.assertEqual(data['dice_raw'], 22)

    def test_slots_different_symbols(self):
        ctx = _FakeCtx()
        save_dice_tags(ctx, 111, '🎰', 5)  # разные символы → 0
        self.assertEqual(ctx.storage.row['data']['dice'], 0)

    def test_none_user_id_is_noop(self):
        ctx = _FakeCtx()
        save_dice_tags(ctx, None, '🎲', 4)  # не должно упасть
        self.assertEqual(ctx.storage.row['data'], {})


class TestSavePollTags(unittest.TestCase):
    def test_poll_answer(self):
        ctx = _FakeCtx({'coins': 100}, step=3)
        save_poll_tags(ctx, 111, 'poll', 2, ['0', '1', '2'])
        data = ctx.storage.row['data']
        self.assertEqual(data['poll_option'], 2)
        self.assertEqual(data['poll_answer'], 2)
        self.assertEqual(data['coins'], 100)  # остальные теги не тронуты

    def test_poll_answer_out_of_range(self):
        ctx = _FakeCtx()
        save_poll_tags(ctx, 111, 'poll', 5, ['0', '1'])
        self.assertEqual(ctx.storage.row['data']['poll_answer'], 5)

    def test_negative_option_is_noop(self):
        ctx = _FakeCtx()
        save_poll_tags(ctx, 111, 'poll', -1, ['0'])
        self.assertEqual(ctx.storage.row['data'], {})

    def test_quiz_correct(self):
        # Формат quiz: вопрос|варианты|правильный|пояснение|…
        step_data = {'quiz': 'Вопрос|Да,Нет|1|'}
        ctx = _FakeCtx()
        save_poll_tags(ctx, 111, 'quiz', 1, ['0', '1'], step_data)
        data = ctx.storage.row['data']
        self.assertEqual(data['quiz_option'], 1)
        self.assertEqual(data['quiz_answer'], 1)
        self.assertEqual(data['quiz_correct'], 1)

    def test_quiz_wrong(self):
        step_data = {'quiz': 'Вопрос|Да,Нет|1|'}
        ctx = _FakeCtx()
        save_poll_tags(ctx, 111, 'quiz', 0, ['0', '1'], step_data)
        data = ctx.storage.row['data']
        self.assertEqual(data['quiz_correct'], 0)
        self.assertEqual(data['quiz_option'], 0)

    def test_quiz_without_config_correct_is_zero(self):
        ctx = _FakeCtx()
        save_poll_tags(ctx, 111, 'quiz', 0, ['0', '1'], {})
        # Нет конфига → correctOption -1 → совпадений нет → 0.
        self.assertEqual(ctx.storage.row['data']['quiz_correct'], 0)


class _FakeTg:
    def __init__(self):
        self.messages = []

    def send_message(self, chat_id, text, *args, **kwargs):
        self.messages.append(text)


class _FakeCalcCtx(_FakeCtx):
    def __init__(self, data=None, step=1):
        super().__init__(data, step)
        self.tg = _FakeTg()


class TestRunStepCalc(unittest.TestCase):
    """Формула из формы шага (calc_formula) — авто-расчёт после броска."""

    def test_runs_formula_silently_on_success(self):
        # Успех не шлёт сообщение — следующий шаг покажет новые теги.
        from steps import _run_step_calc
        ctx = _FakeCalcCtx({'coins': 10, 'dice': 4})
        _run_step_calc(ctx, 555, 'u1',
                       {'calc_formula': 'coins = coins * dice'})
        self.assertEqual(ctx.storage.row['data']['coins'], 40)
        self.assertEqual(ctx.tg.messages, [])

    def test_empty_formula_is_noop(self):
        from steps import _run_step_calc
        ctx = _FakeCalcCtx({'coins': 10})
        _run_step_calc(ctx, 555, 'u1', {'calc_formula': '   '})
        _run_step_calc(ctx, 555, 'u1', {})
        self.assertEqual(ctx.storage.row['data']['coins'], 10)
        self.assertEqual(ctx.tg.messages, [])

    def test_error_reported_and_tag_untouched(self):
        from steps import _run_step_calc
        ctx = _FakeCalcCtx({'coins': 10})
        _run_step_calc(ctx, 555, 'u1',
                       {'calc_formula': 'coins = coins / 0'})
        self.assertEqual(ctx.storage.row['data']['coins'], 10)
        self.assertEqual(len(ctx.tg.messages), 1)
        self.assertTrue(ctx.tg.messages[0].startswith('❌'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
