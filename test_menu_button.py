# ============================================================================
# test_menu_button.py — parse_menu_commands + apply_menu_settings
#
# Запуск:  python3 test_menu_button.py   (из папки bot/)
# ============================================================================

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from menu_button import apply_menu_settings, parse_menu_commands  # noqa: E402


class _FakeTg:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def call(self, method: str, payload: dict | None = None) -> bool:
        self.calls.append((method, payload or {}))
        return True


class _FakeCtx:
    def __init__(self, menu: dict | None = None):
        self.menu = menu or {}
        self.tg = _FakeTg()


class TestParseMenuCommands(unittest.TestCase):
    def test_with_descriptions(self):
        result = parse_menu_commands('start: Запустить бота, help: Помощь')
        self.assertEqual(result, [
            {'command': 'start', 'description': 'Запустить бота'},
            {'command': 'help', 'description': 'Помощь'},
        ])

    def test_without_description(self):
        result = parse_menu_commands('start, shop')
        self.assertEqual(result, [
            {'command': 'start', 'description': 'start'},
            {'command': 'shop', 'description': 'shop'},
        ])

    def test_invalid_and_duplicates_skipped(self):
        result = parse_menu_commands('плохая команда, Start, start, ok')
        self.assertEqual([c['command'] for c in result], ['Start', 'ok'])

    def test_empty(self):
        self.assertEqual(parse_menu_commands(''), [])
        self.assertEqual(parse_menu_commands(None), [])


class TestApplyMenuSettings(unittest.TestCase):
    def test_empty_type_no_calls(self):
        ctx = _FakeCtx({'type': '', 'value': 'start', 'url': ''})
        self.assertFalse(apply_menu_settings(ctx))
        self.assertEqual(ctx.tg.calls, [])

    def test_command_type(self):
        ctx = _FakeCtx({
            'type': 'command',
            'value': 'start: Запустить, help',
            'url': '',
        })
        self.assertTrue(apply_menu_settings(ctx))
        methods = [m for m, _ in ctx.tg.calls]
        self.assertEqual(methods, ['setMyCommands', 'setChatMenuButton'])
        self.assertEqual(ctx.tg.calls[0][1]['commands'][0], {
            'command': 'start', 'description': 'Запустить',
        })
        self.assertEqual(ctx.tg.calls[1][1],
                         {'menu_button': {'type': 'command'}})

    def test_command_without_list_is_skipped(self):
        ctx = _FakeCtx({'type': 'command', 'value': ''})
        self.assertFalse(apply_menu_settings(ctx))
        self.assertEqual(ctx.tg.calls, [])

    def test_web_app_type(self):
        ctx = _FakeCtx({
            'type': 'web_app',
            'value': 'Открыть',
            'url': 'https://example.com/app',
        })
        self.assertTrue(apply_menu_settings(ctx))
        self.assertEqual(ctx.tg.calls, [('setChatMenuButton', {
            'menu_button': {
                'type': 'web_app',
                'text': 'Открыть',
                'web_app': {'url': 'https://example.com/app'},
            },
        })])

    def test_web_app_without_url_is_skipped(self):
        ctx = _FakeCtx({'type': 'web_app', 'value': 'Открыть',
                        'url': 'example.com'})
        self.assertFalse(apply_menu_settings(ctx))
        self.assertEqual(ctx.tg.calls, [])

    def test_web_app_default_text(self):
        ctx = _FakeCtx({'type': 'web_app', 'value': '',
                        'url': 'https://x.io'})
        self.assertTrue(apply_menu_settings(ctx))
        payload = ctx.tg.calls[0][1]
        self.assertEqual(payload['menu_button']['text'], 'Меню')


if __name__ == '__main__':
    unittest.main(verbosity=2)
