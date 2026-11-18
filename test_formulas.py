# ============================================================================
# test_formulas.py — execute_single_formula: sum/avg/count/mul/find.
#
# Запуск:  python3 test_formulas.py   (из папки bot/)
# ============================================================================

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from functions import execute_single_formula  # noqa: E402


class TestFormulas(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {'Col': '10', 'Choice': 'A'},
            {'Col': '5', 'Choice': 'A'},
            {'Col': '4', 'Choice': 'B'},
        ]
        self.headers = ['Col', 'Choice']

    def test_sum(self):
        self.assertEqual(
            execute_single_formula('sum(Col, Choice="A")',
                                   self.rows, self.headers, 1), 15)

    def test_mul(self):
        self.assertEqual(
            execute_single_formula('mul(Col, Choice="A")',
                                   self.rows, self.headers, 1), 50)

    def test_avg(self):
        self.assertEqual(
            execute_single_formula('avg(Col, Choice="A")',
                                   self.rows, self.headers, 1), '7.50')

    def test_count_all(self):
        self.assertEqual(
            execute_single_formula('count(Col)', self.rows, self.headers, 1), 3)

    def test_count_filtered(self):
        self.assertEqual(
            execute_single_formula('count(Col, Choice="A")',
                                   self.rows, self.headers, 1), 2)

    def test_find(self):
        self.assertEqual(
            execute_single_formula('find(Col, Choice="B")',
                                   self.rows, self.headers, 1), '4')


if __name__ == '__main__':
    unittest.main(verbosity=2)
