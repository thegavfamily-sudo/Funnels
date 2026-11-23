# ============================================================================
# test_parsers.py — юнит-тесты парсеров Python-бота (порт из БОТ_готовый.gs)
#
# Запуск:  python3 test_parsers.py   (из папки bot/)
# ============================================================================

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keyboards import (extract_callback_params,  # noqa: E402
                       build_inline_keyboard_from_parsed_data,
                       parse_inline_keyboard_data,
                       parse_regular_keyboard_data)
from step_model import normalize_type_value  # noqa: E402
from steps import get_slot_symbols  # noqa: E402
from storage import is_funnel_served, parse_bot_flags  # noqa: E402
import users  # noqa: E402


class TestParseRegularKeyboardData(unittest.TestCase):
    """parseRegularKeyboardData: JSON {buttons, settings}."""

    def test_two_rows_command_and_text(self):
        result = parse_regular_keyboard_data({
            'buttons': [
                [{'text': 'Далее', 'type': 'command', 'command': 'next'}],
                [{'text': 'Назад', 'type': 'text'}],
            ],
        })
        self.assertIsNotNone(result)
        self.assertEqual(len(result['buttons']), 2)
        first = result['buttons'][0][0]
        self.assertEqual(first['text'], 'Далее')
        self.assertEqual(first['type'], 'command')
        self.assertEqual(first['command'], 'next')
        second = result['buttons'][1][0]
        self.assertEqual(second['text'], 'Назад')
        self.assertEqual(second['type'], 'text')

    def test_settings_and_types(self):
        data = {
            'buttons': [[
                {'text': 'Контакт', 'type': 'contact'},
                {'text': 'Сайт', 'type': 'url',
                 'command': 'https://example.com'},
            ]],
            'settings': {
                'resize_keyboard': True,
                'one_time_keyboard': True,
                'input_field_placeholder': 'Меню',
            },
        }
        result = parse_regular_keyboard_data(data)
        self.assertIsNotNone(result)
        self.assertEqual(len(result['buttons']), 1)
        self.assertEqual(result['buttons'][0][0]['type'], 'contact')
        self.assertEqual(result['buttons'][0][1]['type'], 'url')
        self.assertEqual(result['buttons'][0][1]['command'],
                         'https://example.com')
        self.assertTrue(result['settings']['resize_keyboard'])
        self.assertTrue(result['settings']['one_time_keyboard'])
        self.assertEqual(result['settings']['input_field_placeholder'], 'Меню')

    def test_invalid_type_falls_back_to_text(self):
        result = parse_regular_keyboard_data({
            'buttons': [[{'text': 'Btn', 'type': 'weird',
                          'command': 'cmd'}]],
        })
        button = result['buttons'][0][0]
        self.assertEqual(button['type'], 'text')
        self.assertEqual(button['command'], 'cmd')

    def test_command_step_after_function(self):
        result = parse_regular_keyboard_data({
            'buttons': [[{'text': 'Счёт', 'type': 'command',
                          'command': 'Coins', 'step': 7}]],
        })
        button = result['buttons'][0][0]
        self.assertEqual(button['type'], 'command')
        self.assertEqual(button['command'], 'Coins')
        self.assertEqual(button['step'], 7)

    def test_command_without_step(self):
        result = parse_regular_keyboard_data({
            'buttons': [[{'text': 'Далее', 'type': 'command',
                          'command': 'next'}]],
        })
        button = result['buttons'][0][0]
        self.assertEqual(button['step'], 0)

    def test_json_string_accepted(self):
        result = parse_regular_keyboard_data(
            '{"buttons":[[{"text":"A","type":"text"}]]}')
        self.assertEqual(result['buttons'][0][0]['text'], 'A')

    def test_empty_returns_none(self):
        self.assertIsNone(parse_regular_keyboard_data(''))
        self.assertIsNone(parse_regular_keyboard_data(None))
        self.assertIsNone(parse_regular_keyboard_data({}))
        self.assertIsNone(parse_regular_keyboard_data('не JSON'))


class TestParseInlineKeyboardData(unittest.TestCase):
    """parseInlineKeyboardData: JSON [[кнопка,…],…]."""

    def test_defaults_and_steps(self):
        result = parse_inline_keyboard_data([
            [{'text': 'Далее', 'step': 5}],
            [{'text': 'Назад', 'step': 4}],
        ])
        self.assertIsNotNone(result)
        self.assertEqual(len(result['buttons']), 2)
        first = result['buttons'][0][0]
        self.assertEqual(first['text'], 'Далее')
        self.assertEqual(first['type'], 'callback')
        self.assertEqual(first['step'], 5)
        self.assertEqual(first['method'], 'step by step')
        self.assertEqual(first['action'], 'keep')
        self.assertEqual(result['buttons'][1][0]['step'], 4)

    def test_method_action_and_zero_step(self):
        result = parse_inline_keyboard_data([
            [{'text': 'Go', 'type': 'command', 'method': 'update step',
              'action': 'hide'}],
        ])
        button = result['buttons'][0][0]
        self.assertEqual(button['type'], 'command')
        self.assertEqual(button['step'], 0)
        self.assertEqual(button['method'], 'update step')
        self.assertEqual(button['action'], 'hide')

    def test_invalid_type_falls_back_to_callback(self):
        result = parse_inline_keyboard_data([
            [{'text': 'X', 'type': 'nope'}],
        ])
        button = result['buttons'][0][0]
        self.assertEqual(button['type'], 'callback')

    def test_check_channel_button(self):
        result = parse_inline_keyboard_data([
            [{'text': 'Подписаться', 'type': 'check channel', 'step': 7}],
        ])
        button = result['buttons'][0][0]
        self.assertEqual(button['type'], 'check channel')
        self.assertEqual(button['step'], 7)
        self.assertNotIn('points', button)
        self.assertNotIn('role', button)

    def test_json_string_accepted(self):
        result = parse_inline_keyboard_data(
            '[[{"text":"A","type":"callback","step":3}]]')
        self.assertEqual(result['buttons'][0][0]['step'], 3)

    def test_empty_returns_none(self):
        self.assertIsNone(parse_inline_keyboard_data(''))
        self.assertIsNone(parse_inline_keyboard_data(None))
        self.assertIsNone(parse_inline_keyboard_data([]))
        self.assertIsNone(parse_inline_keyboard_data('не JSON'))


class TestInlineValuePayload(unittest.TestCase):
    """Payload (URL/команда/цена) — в поле value."""

    def test_url_payload_kept_as_value(self):
        result = parse_inline_keyboard_data([
            [{'text': 'Сайт', 'type': 'url',
              'value': 'https://example.com'}],
        ])
        button = result['buttons'][0][0]
        self.assertEqual(button['value'], 'https://example.com')
        self.assertEqual(button['step'], 0)  # URL — не число

    def test_numeric_value_still_kept(self):
        # В JSON value — строка; шаг задаётся полем step отдельно.
        result = parse_inline_keyboard_data([
            [{'text': 'Далее', 'step': 5}],
        ])
        button = result['buttons'][0][0]
        self.assertEqual(button['step'], 5)

    def test_build_url_prefers_value_over_text(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Открыть', 'type': 'url',
              'value': 'https://example.com'}],
        ])
        built = build_inline_keyboard_from_parsed_data(parsed, None)
        btn = built['inline_keyboard'][0][0]
        self.assertEqual(btn['text'], 'Открыть')
        self.assertEqual(btn['url'], 'https://example.com')

    def test_build_command_prefers_value_over_text(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Помощь', 'type': 'command', 'value': '/help'}],
        ])
        built = build_inline_keyboard_from_parsed_data(parsed, None)
        btn = built['inline_keyboard'][0][0]
        self.assertEqual(btn['text'], 'Помощь')
        self.assertTrue(btn['callback_data'].startswith('cmd_help'))

    def test_command_step_after_function(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Счёт', 'type': 'command', 'value': 'Coins',
              'step': 7}],
        ])
        button = parsed['buttons'][0][0]
        self.assertEqual(button['type'], 'command')
        self.assertEqual(button['value'], 'Coins')
        self.assertEqual(button['step'], 7)
        built = build_inline_keyboard_from_parsed_data(parsed, None)
        btn = built['inline_keyboard'][0][0]
        self.assertIn('cmd_Coins', btn['callback_data'])
        self.assertIn('_step_7', btn['callback_data'])

    def test_command_without_step_has_no_step_suffix(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Счёт', 'type': 'command', 'value': 'Coins'}],
        ])
        button = parsed['buttons'][0][0]
        self.assertEqual(button['step'], 0)
        built = build_inline_keyboard_from_parsed_data(parsed, None)
        btn = built['inline_keyboard'][0][0]
        self.assertNotIn('_step_', btn['callback_data'])

    def test_build_url_falls_back_to_text(self):
        # Без value URL берётся из text (кнопка-ссылка).
        parsed = parse_inline_keyboard_data([
            [{'text': 'https://old.example', 'type': 'url'}],
        ])
        built = build_inline_keyboard_from_parsed_data(parsed, None)
        btn = built['inline_keyboard'][0][0]
        self.assertEqual(btn['url'], 'https://old.example')

    def test_zero_step_keeps_action(self):
        result = parse_inline_keyboard_data([
            [{'text': 'Кнопка', 'action': 'hide'}],
        ])
        button = result['buttons'][0][0]
        self.assertEqual(button['method'], 'step by step')
        self.assertEqual(button['action'], 'hide')
        self.assertEqual(button['step'], 0)


class TestInlineTransitionDelay(unittest.TestCase):
    """Целевой шаг и задержка (сек) у command/web app/url."""

    def test_url_step_and_delay(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Сайт', 'type': 'url',
              'value': 'https://example.com', 'step': 7, 'delay': 10}],
        ])
        button = parsed['buttons'][0][0]
        self.assertEqual(button['step'], 7)
        self.assertEqual(button['delay'], 10)

    def test_command_step_and_delay(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Счёт', 'type': 'command', 'value': 'Coins',
              'step': 7, 'delay': 3}],
        ])
        button = parsed['buttons'][0][0]
        self.assertEqual(button['step'], 7)
        self.assertEqual(button['delay'], 3)

    def test_web_app_step_and_delay(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Магазин', 'type': 'web app',
              'value': 'https://t.me/app', 'step': 5, 'delay': 10}],
        ])
        button = parsed['buttons'][0][0]
        self.assertEqual(button['step'], 5)
        self.assertEqual(button['delay'], 10)

    def test_without_delay_key_no_delay(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Сайт', 'type': 'url',
              'value': 'https://example.com', 'step': 7}],
        ])
        button = parsed['buttons'][0][0]
        self.assertEqual(button['step'], 7)
        self.assertNotIn('delay', button)

    def test_zero_step_keeps_delay_but_no_transition(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Сайт', 'type': 'url',
              'value': 'https://example.com', 'delay': 10}],
        ])
        button = parsed['buttons'][0][0]
        self.assertEqual(button['step'], 0)
        self.assertEqual(button['delay'], 10)


class TestNormalizeTypeValue(unittest.TestCase):
    """normalizeTypeValue (:280)."""

    def test_plain_and_emoji_types(self):
        self.assertEqual(normalize_type_value('photo'), 'photo')
        self.assertEqual(normalize_type_value('📝 Text'), 'text')
        self.assertEqual(normalize_type_value('Video note'), 'video_note')
        self.assertEqual(normalize_type_value('video_note'), 'video_note')
        self.assertEqual(normalize_type_value('Media group'), 'media_group')
        self.assertEqual(normalize_type_value('input'), 'input')

    def test_qr_aliases_to_barcode(self):
        self.assertEqual(normalize_type_value('qr'), 'barcode')
        self.assertEqual(normalize_type_value('QR code'), 'barcode')
        self.assertEqual(normalize_type_value('qr-code'), 'barcode')
        self.assertEqual(normalize_type_value('Barcode'), 'barcode')

    def test_cyrillic_cleaned_like_js(self):
        # в JS \w только ASCII — кириллица вырезается (как в оригинале)
        self.assertEqual(normalize_type_value('Стикер'), '')
        self.assertEqual(normalize_type_value(''), '')


class TestBuildPoll(unittest.TestCase):
    """_build_poll: только объектный формат poll (JSON)."""

    @staticmethod
    def _build(poll):
        from step_model import _build_poll
        return _build_poll({'poll': poll})

    def test_full_object(self):
        result = self._build({
            'question': 'Вопрос',
            'answers': ['Да', 'Нет'],
            'multiple': True,
            'anonymous': False,
            'logic_values': [1, 2],
            'steps': [3, 4],
        })
        self.assertIsNotNone(result)
        self.assertEqual(result.question, 'Вопрос')
        self.assertEqual(result.answers, ['Да', 'Нет'])
        self.assertTrue(result.multiple)
        self.assertFalse(result.anonymous)
        self.assertEqual(result.logic_values, [1, 2])
        self.assertEqual(result.steps, [3, 4])

    def test_defaults(self):
        result = self._build({'question': 'Q', 'answers': ['A', 'B']})
        self.assertFalse(result.multiple)
        self.assertTrue(result.anonymous)
        self.assertEqual(result.logic_values, [])

    def test_not_object_returns_none(self):
        from step_model import _build_poll
        self.assertIsNone(_build_poll({'poll': 'Q|A,B'}))
        self.assertIsNone(_build_poll({}))
        self.assertIsNone(_build_poll({'poll': {}}))


class TestGetSlotSymbols(unittest.TestCase):
    """getSlotSymbols (:504) + таблицы SAME_SYMBOLS/SYMBOL_TO_NUMBER."""

    def test_same_symbol_values(self):
        self.assertEqual(get_slot_symbols(22), '🍒🍒🍒')
        self.assertEqual(get_slot_symbols(43), '🍋🍋🍋')
        self.assertEqual(get_slot_symbols(1), '🔔🔔🔔')
        self.assertEqual(get_slot_symbols(64), '7️⃣7️⃣7️⃣')

    def test_out_of_range(self):
        self.assertEqual(get_slot_symbols(0), '❓❓❓')
        self.assertEqual(get_slot_symbols(65), '❓❓❓')

    def test_mixed_combinations_cycle(self):
        # (v-1) % 16 по списку из 16 комбинаций
        self.assertEqual(get_slot_symbols(2), '🍋🍒7️⃣')    # index 1
        self.assertEqual(get_slot_symbols(17), '🍒🍋🔔')   # index 0
        self.assertEqual(get_slot_symbols(16), '7️⃣🍒7️⃣')  # index 15


class TestParseBotFlags(unittest.TestCase):
    """parseConfig-аналог для bool-флагов funnels.data (:160)."""

    def test_defaults(self):
        flags = parse_bot_flags({})
        self.assertTrue(flags['cache'])
        self.assertFalse(flags['typing'])
        self.assertTrue(flags['referral'])
        self.assertTrue(flags['autocolumns'])
        self.assertTrue(flags['saveresponses'])
        self.assertTrue(flags['savestepprogress'])
        self.assertTrue(flags['systemmessages'])

    def test_bool_overrides(self):
        flags = parse_bot_flags({'cache': False, 'typing': True,
                                 'systemmessages': False})
        self.assertFalse(flags['cache'])
        self.assertTrue(flags['typing'])
        self.assertFalse(flags['systemmessages'])

    def test_string_values_including_legacy_active(self):
        flags = parse_bot_flags({'cache': 'active', 'typing': 'on',
                                 'saveresponses': 'false', 'autocolumns': '0',
                                 'referral': 'yes'})
        self.assertTrue(flags['cache'])        # старый синтаксис Cache:Active
        self.assertTrue(flags['typing'])
        self.assertFalse(flags['saveresponses'])
        self.assertFalse(flags['autocolumns'])
        self.assertTrue(flags['referral'])

    def test_none_data_keeps_defaults(self):
        self.assertTrue(parse_bot_flags(None)['cache'])


class TestExtractCallbackParams(unittest.TestCase):
    """Бонус: extractCallbackParams (:902)."""

    def test_step_prefix(self):
        params = extract_callback_params('step_5_row_1_col_2_action_hide')
        self.assertEqual(params['step'], '5')
        self.assertEqual(params['row'], '1')
        self.assertEqual(params['col'], '2')
        self.assertEqual(params['action'], 'hide')

    def test_check_sub_with_channel_underscores(self):
        params = extract_callback_params(
            'check_sub_3_row_0_col_1_action_keep_channel_my_channel_name')
        self.assertEqual(params['check'], '3')
        self.assertEqual(params['row'], '0')
        self.assertEqual(params['col'], '1')
        self.assertEqual(params['channel'], 'my_channel_name')

    def test_cmd_prefix(self):
        params = extract_callback_params('cmd_next_row_0_col_0_action_keep')
        self.assertEqual(params['cmd'], 'next')

    def test_cmd_with_target_step(self):
        params = extract_callback_params(
            'cmd_Coins_row_0_col_0_action_keep_step_7')
        self.assertEqual(params['cmd'], 'Coins')
        self.assertEqual(params['step'], '7')
        self.assertEqual(params['row'], '0')
        self.assertEqual(params['action'], 'keep')

    def test_cmd_named_step_still_parses(self):
        # Имя функции «step» не должно съедать токен целевого шага.
        params = extract_callback_params(
            'cmd_step_row_1_col_0_action_keep_step_4')
        self.assertEqual(params['cmd'], 'step')
        self.assertEqual(params['step'], '4')
        self.assertEqual(params['row'], '1')

    def test_unknown_prefix_no_function_id(self):
        # Легаси-префиксы (system_/custom_/builtin_) убраны.
        self.assertEqual(extract_callback_params('custom_fn_1_row_0_col_0'), {})

    def test_buy_prefix(self):
        params = extract_callback_params('buy_row_1_col_2_action_keep')
        self.assertEqual(params['row'], '1')
        self.assertEqual(params['col'], '2')
        self.assertEqual(params['action'], 'keep')


class TestPurchaseButton(unittest.TestCase):
    """Кнопка purchase: цена в value, целевой шаг в step."""

    def test_price_is_not_step(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Купить за 200', 'type': 'purchase', 'value': '200'}],
        ])
        button = parsed['buttons'][0][0]
        self.assertEqual(button['type'], 'purchase')
        self.assertEqual(button['value'], '200')
        self.assertEqual(button['step'], 0)

    def test_step_is_target_step(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Купить за 300', 'type': 'purchase',
              'value': '300', 'step': 7}],
        ])
        button = parsed['buttons'][0][0]
        self.assertEqual(button['value'], '300')
        self.assertEqual(button['step'], 7)

    def test_three_prices_in_row(self):
        parsed = parse_inline_keyboard_data([[
            {'text': '200', 'type': 'purchase', 'value': '200', 'step': 5},
            {'text': '300', 'type': 'purchase', 'value': '300', 'step': 6},
            {'text': '400', 'type': 'purchase', 'value': '400'},
        ]])
        buttons = parsed['buttons'][0]
        self.assertEqual([b['value'] for b in buttons], ['200', '300', '400'])
        self.assertEqual([b['step'] for b in buttons], [5, 6, 0])

    def test_build_buy_callback(self):
        parsed = parse_inline_keyboard_data([
            [{'text': 'Купить', 'type': 'purchase',
              'value': '100', 'step': 3}],
        ])
        built = build_inline_keyboard_from_parsed_data(parsed, None)
        btn = built['inline_keyboard'][0][0]
        self.assertEqual(btn['callback_data'],
                         'buy_row_0_col_0_action_keep')

    def test_build_three_buttons_positions(self):
        parsed = parse_inline_keyboard_data([[
            {'text': '200', 'type': 'purchase', 'value': '200'},
            {'text': '300', 'type': 'purchase', 'value': '300'},
            {'text': '400', 'type': 'purchase', 'value': '400'},
        ]])
        built = build_inline_keyboard_from_parsed_data(parsed, None)
        datas = [b['callback_data'] for b in built['inline_keyboard'][0]]
        self.assertEqual(datas, ['buy_row_0_col_0_action_keep',
                                 'buy_row_0_col_1_action_keep',
                                 'buy_row_0_col_2_action_keep'])


class TestExtractSourceCodes(unittest.TestCase):
    """extract_source_codes_from_start_command: src_ и src_--creative."""

    def test_source_only(self):
        self.assertEqual(
            users.ReferralManager.extract_source_codes_from_start_command(
                '/start src_ads1'),
            ('ads1', None))

    def test_source_and_creative(self):
        self.assertEqual(
            users.ReferralManager.extract_source_codes_from_start_command(
                '/start src_insta--video1'),
            ('insta', 'video1'))

    def test_no_param(self):
        self.assertEqual(
            users.ReferralManager.extract_source_codes_from_start_command(
                '/start'),
            (None, None))

    def test_referral_param_not_source(self):
        self.assertEqual(
            users.ReferralManager.extract_source_codes_from_start_command(
                '/start ref_123'),
            (None, None))

    def test_plain_text(self):
        self.assertEqual(
            users.ReferralManager.extract_source_codes_from_start_command(
                'привет'),
            (None, None))

    def test_empty_payload(self):
        self.assertEqual(
            users.ReferralManager.extract_source_codes_from_start_command(
                '/start src_'),
            (None, None))


class TestIsFunnelServed(unittest.TestCase):
    """Статус управляет режимом бота, а не питанием (кроме Archived).

    Active — кэш памяти, Inactive — обслуживается и читает таблицу
    напрямую, Archived — не обслуживается.
    """

    @staticmethod
    def _row(**data):
        return {'token': '123456:ABC-DEF', 'data': data}

    def test_active_served(self):
        self.assertTrue(is_funnel_served(self._row(status='Active')))

    def test_inactive_still_served(self):
        self.assertTrue(is_funnel_served(
            self._row(status='Inactive', cache=False)))

    def test_empty_status_served(self):
        self.assertTrue(is_funnel_served(self._row()))

    def test_archived_not_served(self):
        self.assertFalse(is_funnel_served(self._row(status='Archived')))

    def test_no_token_not_served(self):
        self.assertFalse(is_funnel_served(
            {'token': '', 'data': {'status': 'Active'}}))


class TestJsonKeyboardFormat(unittest.TestCase):
    """Формат клавиатур: list/dict или JSON-строка → структура."""

    def test_inline_list_of_rows(self):
        rows = [[
            {'text': 'Купить', 'type': 'purchase', 'value': '300',
             'step': 7},
            {'text': 'Назад', 'type': 'callback', 'step': 3},
        ]]
        parsed = parse_inline_keyboard_data(rows)
        self.assertEqual(len(parsed['buttons']), 1)
        first = parsed['buttons'][0][0]
        self.assertEqual(first['type'], 'purchase')
        self.assertEqual(first['value'], '300')
        self.assertEqual(first['step'], 7)
        self.assertEqual(first['method'], 'step by step')
        self.assertEqual(first['action'], 'keep')
        second = parsed['buttons'][0][1]
        self.assertEqual(second['type'], 'callback')
        self.assertEqual(second['step'], 3)

    def test_inline_json_string(self):
        import json
        raw = json.dumps([[{'text': 'A', 'type': 'url',
                            'value': 'https://x.example'}]])
        parsed = parse_inline_keyboard_data(raw)
        btn = parsed['buttons'][0][0]
        self.assertEqual(btn['type'], 'url')
        self.assertEqual(btn['value'], 'https://x.example')

    def test_inline_non_json_string_returns_none(self):
        # Строки с '|' больше не парсятся.
        self.assertIsNone(parse_inline_keyboard_data('A|callback|5'))

    def test_inline_build_from_json(self):
        parsed = parse_inline_keyboard_data(
            [[{'text': 'A', 'type': 'purchase', 'value': '100', 'step': 4}]])
        built = build_inline_keyboard_from_parsed_data(parsed, None)
        btn = built['inline_keyboard'][0][0]
        self.assertEqual(btn['callback_data'],
                         'buy_row_0_col_0_action_keep')

    def test_regular_dict_settings(self):
        data = {
            'buttons': [[{'text': 'Меню', 'type': 'command',
                          'command': '/menu', 'step': 5}]],
            'settings': {'resize_keyboard': True},
        }
        parsed = parse_regular_keyboard_data(data)
        btn = parsed['buttons'][0][0]
        self.assertEqual(btn['type'], 'command')
        self.assertEqual(btn['command'], '/menu')
        self.assertEqual(btn['step'], 5)
        self.assertTrue(parsed['settings']['resize_keyboard'])

    def test_regular_json_string(self):
        import json
        raw = json.dumps({'buttons': [[{'text': 'Стоп', 'type': 'text'}]]})
        parsed = parse_regular_keyboard_data(raw)
        self.assertEqual(parsed['buttons'][0][0]['text'], 'Стоп')

    def test_empty_values(self):
        self.assertIsNone(parse_inline_keyboard_data([]))
        self.assertIsNone(parse_inline_keyboard_data(''))
        self.assertIsNone(parse_regular_keyboard_data({}))
        self.assertIsNone(parse_regular_keyboard_data(''))


class TestNewlineEffectFrames(unittest.TestCase):
    """Кадры эффектов: только перевод строки."""

    def test_newline_frames_countdown(self):
        from effects import _play_plan
        initial, steps = _play_plan({
            'type': 'countdown', 'params': {},
            'inner_content': '3\n2\n1',
        })
        self.assertEqual(initial, '3')
        self.assertEqual([s[1] for s in steps], ['2', '1'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
