# test_condition.py — parse_condition / _cond_compare / execute_condition.

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from functions import _cond_compare, execute_condition, parse_condition  # noqa: E402


class _FakeStorage:
    def __init__(self, data=None):
        self.row = {'current_step': 1, 'data': dict(data or {})}

    def get_user(self, funnel_id, user_id):
        return self.row


class _FakeCtx:
    funnel_id = 'f1'

    def __init__(self, data=None):
        self.storage = _FakeStorage(data)


class TestParseCondition(unittest.TestCase):
    def test_number(self):
        self.assertEqual(parse_condition('coins >= 100'),
                         ('coins', '>=', 100.0))

    def test_equals_single(self):
        self.assertEqual(parse_condition('group = 3'),
                         ('group', '=', 3.0))

    def test_equals_double(self):
        self.assertEqual(parse_condition('group == 3'),
                         ('group', '==', 3.0))

    def test_not_equals(self):
        self.assertEqual(parse_condition('group != "A"'),
                         ('group', '!=', 'A'))

    def test_quoted_string(self):
        self.assertEqual(parse_condition('city = "Москва"'),
                         ('city', '=', 'Москва'))

    def test_bare_string(self):
        self.assertEqual(parse_condition('city = vip'),
                         ('city', '=', 'vip'))

    def test_less(self):
        self.assertEqual(parse_condition('score < 10'),
                         ('score', '<', 10.0))

    def test_empty(self):
        self.assertIsNone(parse_condition(''))
        self.assertIsNone(parse_condition('   '))

    def test_no_operator(self):
        self.assertIsNone(parse_condition('coins'))

    def test_no_value(self):
        self.assertIsNone(parse_condition('coins >='))


class TestCondCompare(unittest.TestCase):
    def test_ge_true(self):
        self.assertTrue(_cond_compare(150, '>=', 100.0))

    def test_ge_false(self):
        self.assertFalse(_cond_compare(50, '>=', 100.0))

    def test_missing_tag_is_zero(self):
        self.assertTrue(_cond_compare(None, '>=', 0.0))
        self.assertFalse(_cond_compare(None, '>', 0.0))
        self.assertFalse(_cond_compare('', '>', 0.0))

    def test_numeric_string_tag(self):
        self.assertTrue(_cond_compare('42', '<=', 42.0))

    def test_non_numeric_against_number(self):
        self.assertFalse(_cond_compare('abc', '>', 1.0))

    def test_string_equal(self):
        self.assertTrue(_cond_compare('VIP', '=', 'VIP'))
        self.assertFalse(_cond_compare('vip', '=', 'VIP'))

    def test_not_equal(self):
        self.assertTrue(_cond_compare('A', '!=', 'B'))

    def test_missing_tag_string(self):
        self.assertTrue(_cond_compare(None, '=', ''))
        self.assertFalse(_cond_compare(None, '=', 'A'))

    def test_comma_decimal(self):
        self.assertTrue(_cond_compare('10,5', '>', 10.0))


class TestExecuteConditionSteps(unittest.TestCase):
    """execute_condition возвращает шаг ветки (step_yes / step_no)."""

    def test_yes_branch_returns_step_yes(self):
        ctx = _FakeCtx({'coins': 150})
        result = execute_condition(
            ctx, 111, 'coins >= 100', 'VIP', 'Нет',
            step_yes=7, step_no=9)
        self.assertTrue(result['success'])
        self.assertEqual(result['message'], 'VIP')
        self.assertEqual(result['step'], 7)

    def test_no_branch_returns_step_no(self):
        ctx = _FakeCtx({'coins': 50})
        result = execute_condition(
            ctx, 111, 'coins >= 100', 'VIP', 'Нет',
            step_yes=7, step_no=9)
        self.assertEqual(result['message'], 'Нет')
        self.assertEqual(result['step'], 9)

    def test_empty_message_still_returns_step(self):
        ctx = _FakeCtx({'coins': 150})
        result = execute_condition(
            ctx, 111, 'coins >= 100', '', 'Нет',
            step_yes=7, step_no=0)
        self.assertEqual(result['message'], '')
        self.assertEqual(result['step'], 7)

    def test_zero_steps_by_default(self):
        ctx = _FakeCtx({'coins': 1})
        result = execute_condition(ctx, 111, 'coins >= 100', 'A', 'B')
        self.assertEqual(result['step'], 0)


if __name__ == '__main__':
    unittest.main()
