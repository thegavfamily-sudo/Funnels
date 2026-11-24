# ============================================================================
# test_advent.py — адвент-календарь: индекс дня, клавиатура, открытие двери
#
# Запуск:  python3 test_advent.py   (из папки bot git/)
# ============================================================================

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from step_model import (advent_current_day, build_step,  # noqa: E402
                        normalize_type_value, parse_advent_days,
                        parse_advent_prizes, parse_advent_tz,
                        split_advent_prize)
from steps import (advent_text, build_advent_keyboard,  # noqa: E402
                   open_advent_door)


class _FakeStorage:
    def __init__(self, data=None, current_step=1):
        self.row = {'current_step': current_step, 'data': dict(data or {})}
        self.saved = 0
        self.functions_rows = []

    def get_user(self, funnel_id, user_id):
        return self.row

    def save_user_row(self, funnel_id, user_id, current_step, data):
        self.row = {'current_step': current_step, 'data': dict(data)}
        self.saved += 1


class _FakeCtx:
    funnel_id = 'f1'

    def __init__(self, data=None):
        self.storage = _FakeStorage(data)


def mk(data: dict, number: int = 1):
    """Сырая строка шага → Step (как при загрузке воронки)."""
    return build_step(data, number, 'f1')


NOW = datetime(2024, 12, 25, 12, 0, tzinfo=timezone.utc)


class TestAdventCurrentDay(unittest.TestCase):
    """Индекс дня: (дата(now+tz) − дата(start)).days + 1, кламп 0..days."""

    def test_start_yesterday_is_day_two(self):
        self.assertEqual(advent_current_day('2024-12-24', 24, 3, NOW), 2)

    def test_start_today_is_day_one(self):
        self.assertEqual(advent_current_day('2024-12-25', 24, 3, NOW), 1)

    def test_start_tomorrow_is_zero(self):
        # Старт ещё не наступил — ни одна дверь не открыта.
        self.assertEqual(advent_current_day('2024-12-26', 24, 3, NOW), 0)
        self.assertEqual(advent_current_day('2030-01-01', 24, 3, NOW), 0)

    def test_empty_start_opens_all_days(self):
        self.assertEqual(advent_current_day('', 24, 3, NOW), 24)
        self.assertEqual(advent_current_day('   ', 10, 3, NOW), 10)

    def test_clamped_to_days(self):
        # Старт давно прошёл — кламп к количеству дверей.
        self.assertEqual(advent_current_day('2020-01-01', 5, 3, NOW), 5)
        self.assertEqual(advent_current_day('2020-01-01', 24, 3, NOW), 24)

    def test_tz_shifts_local_date(self):
        # 23:00 UTC + 3 ч = 02:00 следующего дня — день уже наступил.
        now = datetime(2024, 12, 24, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(advent_current_day('2024-12-25', 24, 3, now), 1)
        # Без сдвига (tz=0) тот же момент — ещё 24 декабря.
        self.assertEqual(advent_current_day('2024-12-25', 24, 0, now), 0)

    def test_invalid_date_treated_as_empty(self):
        self.assertEqual(advent_current_day('не дата', 24, 3, NOW), 24)


class TestAdventParsers(unittest.TestCase):
    """Поле-парсеры шага advent_*."""

    def test_days_defaults_and_clamps(self):
        self.assertEqual(parse_advent_days(''), 24)
        self.assertEqual(parse_advent_days(None), 24)
        self.assertEqual(parse_advent_days('10'), 10)
        self.assertEqual(parse_advent_days('0'), 24)
        self.assertEqual(parse_advent_days('32'), 24)
        self.assertEqual(parse_advent_days('мусор'), 24)

    def test_tz_defaults(self):
        self.assertEqual(parse_advent_tz(''), 3)
        self.assertEqual(parse_advent_tz(None), 3)
        self.assertEqual(parse_advent_tz('5'), 5)
        self.assertEqual(parse_advent_tz('-4'), -4)
        self.assertEqual(parse_advent_tz('мусор'), 3)

    def test_prizes_json_array(self):
        raw = json.dumps(['100 монет | 100', 'Скидка 10%'])
        self.assertEqual(parse_advent_prizes(raw),
                         ['100 монет | 100', 'Скидка 10%'])

    def test_prizes_already_list_and_garbage(self):
        self.assertEqual(parse_advent_prizes(['Приз']), ['Приз'])
        self.assertEqual(parse_advent_prizes('не json'), [])
        self.assertEqual(parse_advent_prizes(''), [])
        self.assertEqual(parse_advent_prizes('{"a":1}'), [])

    def test_split_prize_with_amount(self):
        self.assertEqual(split_advent_prize('100 монет | 100'),
                         ('100 монет', 100.0))
        self.assertEqual(split_advent_prize('Скидка | 10,5'),
                         ('Скидка', 10.5))

    def test_split_prize_without_amount(self):
        self.assertEqual(split_advent_prize('Скидка 10%'),
                         ('Скидка 10%', None))
        # После '|' не число — вся строка текстом, монет нет.
        self.assertEqual(split_advent_prize('Скидка | ок'),
                         ('Скидка | ок', None))

    def test_build_step_advent_fields(self):
        step = mk({
            'type': 'advent',
            'advent_start': '2024-12-01',
            'advent_days': '10',
            'advent_tz': '5',
            'advent_prizes': json.dumps(['Приз 1', 'Приз 2 | 50']),
            'advent_early': 'Жди {day}',
            'advent_done': 'Уже',
        })
        self.assertEqual(step.type, 'advent')
        self.assertEqual(step.advent.start, '2024-12-01')
        self.assertEqual(step.advent.days, 10)
        self.assertEqual(step.advent.tz, 5)
        self.assertEqual(step.advent.prizes, ['Приз 1', 'Приз 2 | 50'])
        self.assertEqual(step.advent.early, 'Жди {day}')
        self.assertEqual(step.advent.done, 'Уже')

    def test_build_step_advent_defaults(self):
        step = mk({'type': 'advent'})
        self.assertEqual(step.advent.days, 24)
        self.assertEqual(step.advent.tz, 3)
        self.assertEqual(step.advent.prizes, [])
        self.assertIn('{day}', step.advent.early)
        self.assertTrue(step.advent.done)

    def test_normalize_type_aliases(self):
        self.assertEqual(normalize_type_value('advent'), 'advent')
        self.assertEqual(normalize_type_value('Advent'), 'advent')
        self.assertEqual(normalize_type_value('Адвент'), 'advent')
        self.assertEqual(normalize_type_value('Адвент-календарь'), 'advent')


class TestAdventKeyboard(unittest.TestCase):
    """Клавиатура строится при отправке: ряды по 8, emoji статуса, adv_<N>."""

    @staticmethod
    def _texts(keyboard):
        return [btn['text'] for row in keyboard['inline_keyboard']
                for btn in row]

    def test_24_doors_three_rows_of_eight(self):
        step = mk({'type': 'advent', 'advent_start': ''})
        keyboard = build_advent_keyboard(_FakeCtx(), step, 'u1')
        rows = keyboard['inline_keyboard']
        self.assertEqual([len(row) for row in rows], [8, 8, 8])
        self.assertEqual(len(self._texts(keyboard)), 24)

    def test_callback_data_adv_n(self):
        step = mk({'type': 'advent', 'advent_start': ''})
        keyboard = build_advent_keyboard(_FakeCtx(), step, 'u1')
        callbacks = [btn['callback_data'] for row in keyboard['inline_keyboard']
                     for btn in row]
        self.assertEqual(callbacks,
                         [f'adv_{n}' for n in range(1, 25)])

    def test_empty_start_all_doors_available(self):
        # Пустая дата старта — все двери 🎁 (доступны).
        step = mk({'type': 'advent', 'advent_start': ''})
        keyboard = build_advent_keyboard(_FakeCtx(), step, 'u1')
        self.assertEqual(self._texts(keyboard),
                         [f'🎁 {n}' for n in range(1, 25)])

    def test_future_start_all_doors_closed(self):
        step = mk({'type': 'advent', 'advent_start': '2099-01-01'})
        keyboard = build_advent_keyboard(_FakeCtx(), step, 'u1')
        self.assertEqual(self._texts(keyboard),
                         [f'🔒 {n}' for n in range(1, 25)])

    def test_opened_available_closed_emoji(self):
        # day_index = 3: двери 1..3 доступны 🎁, дальше 🔒; 2-я открыта ✅.
        step = mk({'type': 'advent', 'advent_start': '2099-01-01',
                   'advent_days': '6'})
        ctx = _FakeCtx({'advent_opened_2': '1'})
        with mock.patch('steps.advent_current_day', return_value=3):
            keyboard = build_advent_keyboard(ctx, step, 'u1')
        self.assertEqual(self._texts(keyboard),
                         ['🎁 1', '✅ 2', '🎁 3', '🔒 4', '🔒 5', '🔒 6'])

    def test_non_one_tag_is_not_opened(self):
        # Тег без значения '1' не считается открытием.
        step = mk({'type': 'advent', 'advent_start': ''})
        ctx = _FakeCtx({'advent_opened_1': '0'})
        keyboard = build_advent_keyboard(ctx, step, 'u1')
        self.assertEqual(self._texts(keyboard)[0], '🎁 1')

    def test_no_user_id_no_opened(self):
        step = mk({'type': 'advent', 'advent_start': ''})
        keyboard = build_advent_keyboard(_FakeCtx({'advent_opened_1': '1'}),
                                         step, None)
        self.assertEqual(self._texts(keyboard)[0], '🎁 1')


class TestAdventText(unittest.TestCase):
    """{advent_day}/{advent_opened}/{advent_total} + обычные теги."""

    def test_substitutions(self):
        step = mk({'type': 'advent', 'advent_start': ''})
        ctx = _FakeCtx({'coins': 50, 'advent_opened_1': '1',
                        'advent_opened_3': '1'})
        text = advent_text(
            ctx, step,
            'Баланс {coins}. День {advent_day}, открыто '
            '{advent_opened} из {advent_total}.', 'u1')
        self.assertEqual(text,
                         'Баланс 50. День 24, открыто 2 из 24.')

    def test_day_index_respected(self):
        step = mk({'type': 'advent', 'advent_start': '2099-01-01'})
        text = advent_text(_FakeCtx(), step, 'День {advent_day}', 'u1')
        self.assertEqual(text, 'День 0')


class TestOpenAdventDoor(unittest.TestCase):
    """Открытие двери: рано / повторно / успех с призом."""

    def test_early_tap_shows_early_text(self):
        step = mk({'type': 'advent', 'advent_start': '2099-01-01',
                   'advent_early': 'Ещё рано, жди {day}'})
        ctx = _FakeCtx()
        result = open_advent_door(ctx, step, 'u1', 7)
        self.assertFalse(result['opened'])
        self.assertEqual(result['popup'], 'Ещё рано, жди 7')
        self.assertEqual(ctx.storage.saved, 0)
        self.assertNotIn('advent_opened_7', ctx.storage.row['data'])

    def test_default_early_text(self):
        step = mk({'type': 'advent', 'advent_start': '2099-01-01'})
        result = open_advent_door(_FakeCtx(), step, 'u1', 3)
        self.assertIn('3', result['popup'])
        self.assertIn('рано', result['popup'])

    def test_reopen_shows_done_text(self):
        step = mk({'type': 'advent', 'advent_start': '',
                   'advent_done': 'Эта дверь уже открыта!'})
        ctx = _FakeCtx({'advent_opened_4': '1'})
        result = open_advent_door(ctx, step, 'u1', 4)
        self.assertFalse(result['opened'])
        self.assertEqual(result['popup'], 'Эта дверь уже открыта!')
        self.assertEqual(ctx.storage.saved, 0)

    def test_open_writes_tag_and_awards_coins(self):
        step = mk({'type': 'advent', 'advent_start': '',
                   'advent_prizes': json.dumps(
                       ['100 монет | 100', 'Скидка 10%'])})
        ctx = _FakeCtx({'coins': 50})
        result = open_advent_door(ctx, step, 'u1', 1)
        self.assertTrue(result['opened'])
        self.assertEqual(result['popup'], '100 монет')
        self.assertEqual(ctx.storage.row['data']['advent_opened_1'], '1')
        self.assertEqual(ctx.storage.row['data']['coins'], 150)
        self.assertEqual(ctx.storage.saved, 1)

    def test_open_without_coin_prize_keeps_balance(self):
        step = mk({'type': 'advent', 'advent_start': '',
                   'advent_prizes': json.dumps(['Скидка 10%'])})
        ctx = _FakeCtx()
        result = open_advent_door(ctx, step, 'u1', 1)
        self.assertTrue(result['opened'])
        self.assertEqual(result['popup'], 'Скидка 10%')
        self.assertEqual(ctx.storage.row['data']['advent_opened_1'], '1')
        # Без числа после '|' монеты не начисляются.
        self.assertNotIn('coins', ctx.storage.row['data'])

    def test_open_without_prize_uses_default_popup(self):
        step = mk({'type': 'advent', 'advent_start': ''})
        ctx = _FakeCtx()
        result = open_advent_door(ctx, step, 'u1', 5)
        self.assertTrue(result['opened'])
        self.assertEqual(result['popup'], '🎁 Дверь 5 открыта!')
        self.assertEqual(ctx.storage.row['data']['advent_opened_5'], '1')

    def test_string_balance_coerced(self):
        step = mk({'type': 'advent', 'advent_start': '',
                   'advent_prizes': json.dumps(['Приз | 25'])})
        ctx = _FakeCtx({'coins': '100'})
        open_advent_door(ctx, step, 'u1', 1)
        self.assertEqual(ctx.storage.row['data']['coins'], 125)

    def test_invalid_day_is_noop(self):
        step = mk({'type': 'advent', 'advent_start': ''})
        ctx = _FakeCtx()
        result = open_advent_door(ctx, step, 'u1', 0)
        self.assertEqual(result, {'popup': '', 'opened': False})
        self.assertEqual(ctx.storage.saved, 0)

    def test_preserves_other_tags(self):
        step = mk({'type': 'advent', 'advent_start': ''})
        ctx = _FakeCtx({'role': 'vip'})
        open_advent_door(ctx, step, 'u1', 1)
        self.assertEqual(ctx.storage.row['data']['role'], 'vip')
        self.assertEqual(ctx.storage.row['data']['advent_opened_1'], '1')


if __name__ == '__main__':
    unittest.main(verbosity=2)
