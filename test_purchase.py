# ============================================================================
# test_purchase.py — execute_purchase: проверка баланса + списание по кнопке
#
# Запуск:  python3 test_purchase.py   (из папки bot git/)
# ============================================================================

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from functions import execute_purchase  # noqa: E402


class _FakeStorage:
    def __init__(self, data=None):
        self.row = {'current_step': 1, 'data': dict(data or {})}
        self.saved = 0

    def get_user(self, funnel_id, user_id):
        return self.row

    def save_user_row(self, funnel_id, user_id, current_step, data):
        self.row = {'current_step': current_step, 'data': dict(data)}
        self.saved += 1


class _FakeCtx:
    funnel_id = 'f1'

    def __init__(self, data=None):
        self.storage = _FakeStorage(data)


class TestExecutePurchase(unittest.TestCase):
    def test_sufficient_balance_deducts(self):
        ctx = _FakeCtx({'coins': 150})
        result = execute_purchase(ctx, 111, 100, 'Ок: {price}/{coins}', '')
        self.assertTrue(result['success'])
        self.assertEqual(ctx.storage.row['data']['coins'], 50)
        self.assertEqual(result['message'], 'Ок: 100/50')
        self.assertEqual(ctx.storage.saved, 1)

    def test_exact_balance_passes(self):
        ctx = _FakeCtx({'coins': 300})
        result = execute_purchase(ctx, 111, 300, 'Куплено', '')
        self.assertTrue(result['success'])
        self.assertEqual(ctx.storage.row['data']['coins'], 0)

    def test_insufficient_keeps_balance(self):
        ctx = _FakeCtx({'coins': 150})
        result = execute_purchase(ctx, 111, 300, '', 'Не хватает: {price}')
        self.assertFalse(result['success'])
        self.assertEqual(ctx.storage.row['data']['coins'], 150)
        self.assertEqual(result['message'], 'Не хватает: 300')
        self.assertEqual(ctx.storage.saved, 0)

    def test_missing_tag_is_zero(self):
        ctx = _FakeCtx({})
        result = execute_purchase(ctx, 111, 100, '', 'Нет монет')
        self.assertFalse(result['success'])
        self.assertEqual(result['message'], 'Нет монет')

    def test_string_balance_coerced(self):
        ctx = _FakeCtx({'coins': '250'})
        result = execute_purchase(ctx, 111, 100, 'Ок', '')
        self.assertTrue(result['success'])
        self.assertEqual(ctx.storage.row['data']['coins'], 150)

    def test_fractional_price(self):
        ctx = _FakeCtx({'coins': 100})
        result = execute_purchase(ctx, 111, 30.5, 'Ок: {coins}', '')
        self.assertTrue(result['success'])
        self.assertEqual(ctx.storage.row['data']['coins'], 69.5)

    def test_zero_price_always_ok(self):
        ctx = _FakeCtx({})
        result = execute_purchase(ctx, 111, 0, 'Бесплатно', '')
        self.assertTrue(result['success'])

    def test_negative_price_rejected(self):
        ctx = _FakeCtx({'coins': 100})
        result = execute_purchase(ctx, 111, -50, '', '')
        self.assertFalse(result['success'])
        self.assertEqual(ctx.storage.row['data']['coins'], 100)

    def test_no_user_id(self):
        ctx = _FakeCtx({'coins': 100})
        result = execute_purchase(ctx, None, 50, '', '')
        self.assertFalse(result['success'])
        self.assertEqual(ctx.storage.saved, 0)

    def test_default_texts(self):
        ctx = _FakeCtx({'coins': 500})
        result = execute_purchase(ctx, 111, 200, '', '')
        self.assertTrue(result['success'])
        self.assertIn('200', result['message'])
        self.assertIn('300', result['message'])  # новый баланс

        ctx2 = _FakeCtx({'coins': 10})
        result2 = execute_purchase(ctx2, 111, 200, '', '')
        self.assertFalse(result2['success'])
        self.assertIn('200', result2['message'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
