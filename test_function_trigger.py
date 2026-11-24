# test_function_trigger.py — триггеры calc/condition:
# command | step | dice | poll | quiz.

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from functions import (  # noqa: E402
    execute_function,
    get_function_config,
    run_functions_for_step,
    run_functions_for_trigger,
)


class _FakeStorage:
    def __init__(self, functions_rows=None, data=None):
        self.functions_rows = functions_rows or []
        self.row = {'current_step': 1, 'data': dict(data or {})}

    def get_user(self, funnel_id, user_id):
        return self.row

    def save_user_row(self, funnel_id, user_id, step, data):
        self.row = {'current_step': step, 'data': dict(data)}


class _FakeCtx:
    funnel_id = 'f1'

    def __init__(self, functions_rows=None, data=None):
        self.storage = _FakeStorage(functions_rows, data)


def _fn(name, ftype, **extra):
    data = {
        'type': ftype,
        'function_name': name,
        'message': extra.pop('message', ''),
        'condition': extra.pop('condition', ''),
        'function': extra.pop('function', ''),
        'trigger': extra.pop('trigger', ''),
        'trigger_step': extra.pop('trigger_step', ''),
        'step_yes': extra.pop('step_yes', ''),
        'step_no': extra.pop('step_no', ''),
    }
    data.update(extra)
    return {'id': f'id_{name}', 'funnel_id': 'f1', 'data': data}


class TestGetFunctionConfigTrigger(unittest.TestCase):
    def test_empty_trigger_is_command(self):
        ctx = _FakeCtx([_fn('m', 'Custom')])
        cfg = get_function_config(ctx, 'm')
        self.assertEqual(cfg['trigger'], 'command')
        self.assertEqual(cfg['trigger_step'], 0)

    def test_trigger_fields(self):
        ctx = _FakeCtx([
            _fn('c', 'calc', trigger='dice', trigger_step=3),
        ])
        cfg = get_function_config(ctx, 'c')
        self.assertEqual(cfg['trigger'], 'dice')
        self.assertEqual(cfg['trigger_step'], 3)


class TestRunFunctionsForStep(unittest.TestCase):
    def test_step_trigger_runs_and_sends_message(self):
        ctx = _FakeCtx(
            [
                _fn('vip', 'condition',
                    trigger='step', trigger_step=5,
                    condition='coins >= 100',
                    message='VIP', function='Нет',
                    step_yes='7', step_no='9'),
                _fn('other', 'condition',
                    trigger='step', trigger_step=6,
                    condition='coins >= 100',
                    message='SKIP', function=''),
                _fn('cmd', 'condition',
                    trigger='command',
                    condition='coins >= 100',
                    message='CMD', function=''),
            ],
            data={'coins': 150},
        )
        msgs, branch = run_functions_for_step(ctx, 'u1', 5)
        self.assertEqual(msgs, ['VIP'])
        self.assertEqual(branch, 7)

    def test_no_match(self):
        ctx = _FakeCtx([
            _fn('vip', 'condition',
                trigger='step', trigger_step=5,
                condition='coins >= 1', message='A', function=''),
        ])
        msgs, branch = run_functions_for_step(ctx, 'u1', 1)
        self.assertEqual(msgs, [])
        self.assertEqual(branch, 0)

    def test_calc_writes_tag(self):
        ctx = _FakeCtx(
            [_fn('bonus', 'calc',
                 trigger='step', trigger_step=2,
                 function='coins = coins + 100')],
            data={'coins': 50},
        )
        msgs, branch = run_functions_for_step(ctx, 'u1', 2)
        self.assertEqual(msgs, [])
        self.assertEqual(branch, 0)
        self.assertEqual(ctx.storage.row['data']['coins'], 150)


class TestRunFunctionsForTrigger(unittest.TestCase):
    def test_dice_trigger(self):
        ctx = _FakeCtx(
            [
                _fn('on_dice', 'calc',
                    trigger='dice', function='coins = coins * dice'),
                _fn('on_poll', 'calc',
                    trigger='poll', function='coins = coins + 1'),
            ],
            data={'coins': 10, 'dice': 4},
        )
        msgs, branch = run_functions_for_trigger(ctx, 'u1', 'dice')
        self.assertEqual(msgs, [])
        self.assertEqual(branch, 0)
        self.assertEqual(ctx.storage.row['data']['coins'], 40)

        run_functions_for_trigger(ctx, 'u1', 'poll')
        self.assertEqual(ctx.storage.row['data']['coins'], 41)

    def test_quiz_condition_branch(self):
        ctx = _FakeCtx(
            [_fn('ok', 'condition',
                 trigger='quiz',
                 condition='quiz_correct = 1',
                 message='Верно!', function='Мимо',
                 step_yes='10', step_no='11')],
            data={'quiz_correct': 1},
        )
        msgs, branch = run_functions_for_trigger(ctx, 'u1', 'quiz')
        self.assertEqual(msgs, ['Верно!'])
        self.assertEqual(branch, 10)

    def test_command_not_in_trigger_run(self):
        ctx = _FakeCtx(
            [_fn('m', 'Custom',
                 trigger='command', message='Hello')],
        )
        msgs, branch = run_functions_for_trigger(ctx, 'u1', 'dice')
        self.assertEqual(msgs, [])
        res = execute_function(ctx, 'm', 'u1', 1)
        self.assertEqual(res['message'], 'Hello')


if __name__ == '__main__':
    unittest.main()


class TestConditionPlaceholder(unittest.TestCase):
    """{имя} условия в replace_tags_in_text: верно → текст, иначе ''."""

    def test_true_replaces(self):
        import users
        ctx = _FakeCtx(
            [_fn('vip', 'condition',
                 condition='coins >= 100',
                 message='VIP для {Username}')],
            data={'coins': 150, 'Username': 'Gav'},
        )
        out = users.replace_tags_in_text(ctx, 'Привет! {vip}', 'u1')
        self.assertEqual(out, 'Привет! VIP для Gav')

    def test_false_empty(self):
        import users
        ctx = _FakeCtx(
            [_fn('vip', 'condition',
                 condition='coins >= 100',
                 message='VIP')],
            data={'coins': 10},
        )
        self.assertEqual(
            users.replace_tags_in_text(ctx, 'A {vip} B', 'u1'), 'A  B')

    def test_unknown_keeps_braces(self):
        import users
        ctx = _FakeCtx([])
        self.assertEqual(
            users.replace_tags_in_text(ctx, '{x}', 'u1'), '{x}')

    def test_no_self_recursion(self):
        import users
        ctx = _FakeCtx(
            [_fn('vip', 'condition',
                 condition='coins >= 100',
                 message='A {vip} B')],
            data={'coins': 200},
        )
        self.assertEqual(users.replace_tags_in_text(ctx, '{vip}', 'u1'),
                         'A  B')
