# ============================================================================
# test_dice_flow.py — ветвление по броску кубика: маппинг «исход → шаг»
# (0 = без перехода, исход без пары → следующий шаг) и ожидание окончания
# анимации перед переходом.
#
# Запуск:  python3 test_dice_flow.py   (из папки bot/)
# ============================================================================

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from step_model import build_step  # noqa: E402
from steps import (dice_animation_seconds, handle_dice_message,  # noqa: E402
                   resolve_dice_next_step)


def mk(data: dict, number: int = 1):
    """Сырая строка шага → Step (как при загрузке воронки)."""
    return build_step(data, number, 'f1')


COMMON = {
    'type': 'dice', 'dice': '🎲',
    'dice_logic_value': '1,2,3,4,5,6',
    'dice_step': '7,7,7,7,7,7',
}
ZERO = {
    'type': 'dice', 'dice': '🎲',
    'dice_logic_value': '1,2,3,4,5,6',
    'dice_step': '0,0,0,0,0,0',
}
PARTIAL = {
    'type': 'dice', 'dice': '🎲',
    'dice_logic_value': '1,3',
    'dice_step': '10,30',
}
SLOTS = {
    'type': 'dice', 'dice': '🎰',
    'dice_logic_value': '1,2,3,4,0',
    'dice_step': '5,6,7,8,0',
}


class TestResolveDiceNextStep(unittest.TestCase):
    """Маппинг «исход → шаг»: явный номер = переход, 0 = без перехода."""

    def test_common_step_applies_to_every_outcome(self):
        step = mk(COMMON)
        for value in (1, 3, 6):
            self.assertEqual(resolve_dice_next_step(step, value), 7)

    def test_zero_is_explicit_no_transition(self):
        step = mk(ZERO)
        self.assertEqual(resolve_dice_next_step(step, 2), 0)

    def test_outcome_without_pair_is_none(self):
        # Исхода 2 нет в маппинге — вызывающий идёт на следующий шаг.
        step = mk(PARTIAL)
        self.assertIsNone(resolve_dice_next_step(step, 2))
        self.assertEqual(resolve_dice_next_step(step, 1), 10)

    def test_no_mapping_is_none(self):
        step = mk({'type': 'dice', 'dice': '🎲'})
        self.assertIsNone(resolve_dice_next_step(step, 4))

    def test_slots_use_combination_number(self):
        step = mk(SLOTS)
        # 🔔🔔🔔 → комбинация 3 → шаг 7.
        self.assertEqual(resolve_dice_next_step(step, 1), 7)
        # Разные символы (значение не из SAME_SYMBOLS_VALUES) → логическое 0
        # → явный переход отсутствует.
        different = next(v for v in range(1, 65)
                         if v not in config.SAME_SYMBOLS_VALUES['🍒🍒🍒']
                         and v not in config.SAME_SYMBOLS_VALUES['🍋🍋🍋']
                         and v not in config.SAME_SYMBOLS_VALUES['🔔🔔🔔']
                         and v not in config.SAME_SYMBOLS_VALUES['7️⃣7️⃣7️⃣'])
        self.assertEqual(resolve_dice_next_step(step, different), 0)

    def test_slots_combination_without_pair_is_none(self):
        step = mk({
            'type': 'dice', 'dice': '🎰',
            'dice_logic_value': '1,0',
            'dice_step': '9,0',
        })
        # 🍋🍋🍋 → комбинация 2, пары нет → None (следующий шаг).
        self.assertEqual(resolve_dice_next_step(step, 43), None)


class TestAnimationWait(unittest.TestCase):
    """Переход ждёт окончания анимации броска."""

    def test_slots_spin_longer_than_other_dice(self):
        self.assertGreater(dice_animation_seconds('🎰'),
                           dice_animation_seconds('🎲'))
        self.assertEqual(dice_animation_seconds('🎲'),
                         dice_animation_seconds('🎯'))

    def test_unknown_emoji_falls_back_to_default(self):
        self.assertEqual(dice_animation_seconds('что-то'),
                         dice_animation_seconds('🎲'))

    def test_handle_dice_waits_full_animation_before_processing(self):
        class _FakeTG:
            def send_dice(self, chat_id, emoji, options=None):
                return {'message_id': 5, 'dice_value': 30}

        ctx = types.SimpleNamespace(
            tg=_FakeTG(),
            storage=types.SimpleNamespace(
                dice_results={},
                cleanup_game_caches=lambda: None,
            ),
        )
        with mock.patch('steps.time.sleep') as sleep, \
                mock.patch('steps.process_dice_result') as process:
            handle_dice_message(ctx, 1, mk(SLOTS), {}, None)

        sleep.assert_called_once_with(dice_animation_seconds('🎰'))
        process.assert_called_once_with(ctx, 5)


if __name__ == '__main__':
    unittest.main()
