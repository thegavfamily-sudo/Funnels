# test_condition.py — parse_condition / _cond_compare: проверка тегов.

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from functions import _cond_compare, parse_condition  # noqa: E402


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


if __name__ == '__main__':
    unittest.main()
