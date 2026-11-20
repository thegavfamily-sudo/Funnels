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
from steps import get_slot_symbols, normalize_type_value, parse_poll_data, \
    parse_quiz_data  # noqa: E402
from storage import parse_bot_flags  # noqa: E402
import users  # noqa: E402


class TestParseRegularKeyboardData(unittest.TestCase):
    """parseRegularKeyboardData (:3044): строки ||, кнопки ,, поля |."""

    def test_two_rows_command_and_text(self):
        result = parse_regular_keyboard_data('Далее|command|next||Назад|text')
        self.assertIsNotNone(result)
        self.assertEqual(len(result['buttons']), 2)
        first = result['buttons'][0][0]
        self.assertEqual(first['text'], 'Далее')
        self.assertEqual(first['type'], 'command')
        self.assertEqual(first['command'], 'next')
        second = result['buttons'][1][0]
        self.assertEqual(second['text'], 'Назад')
        self.assertEqual(second['type'], 'text')

    def test_settings_tail_and_types(self):
        data = ('Контакт|contact,Сайт|url|https://example.com'
                '||action:resize,one_time,placeholder=Меню')
        result = parse_regular_keyboard_data(data)
        self.assertIsNotNone(result)
        self.assertEqual(len(result['buttons']), 1)
        self.assertEqual(result['buttons'][0][0]['type'], 'contact')
        self.assertEqual(result['buttons'][0][1]['type'], 'url')
        self.assertEqual(result['buttons'][0][1]['command'], 'https://example.com')
        self.assertTrue(result['settings']['resize_keyboard'])
        self.assertTrue(result['settings']['one_time_keyboard'])
        # оригиналь приводит настройки к нижнему регистру
        self.assertEqual(result['settings']['input_field_placeholder'], 'меню')

    def test_invalid_type_falls_back_to_text_and_role_lowercased(self):
        result = parse_regular_keyboard_data('Btn|weird|cmd|ADMIN')
        self.assertIsNotNone(result)
        button = result['buttons'][0][0]
        self.assertEqual(button['type'], 'text')
        self.assertEqual(button['command'], 'cmd')
        self.assertEqual(button['role'], 'admin')

    def test_empty_returns_none(self):
        self.assertIsNone(parse_regular_keyboard_data(''))
        self.assertIsNone(parse_regular_keyboard_data('   '))
        self.assertIsNone(parse_regular_keyboard_data(None))


class TestParseInlineKeyboardData(unittest.TestCase):
    """parseInlineKeyboardData (:3216): text|type|step|method|action|points|role."""

    def test_defaults_and_steps(self):
        result = parse_inline_keyboard_data('Далее|callback|5||Назад|callback|4')
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
        result = parse_inline_keyboard_data('Go|command|0|update step|hide')
        self.assertIsNotNone(result)
        button = result['buttons'][0][0]
        self.assertEqual(button['type'], 'command')
        self.assertEqual(button['step'], 0)          # 0 не принимается (>0)
        self.assertEqual(button['method'], 'update step')
        self.assertEqual(button['action'], 'hide')

    def test_invalid_type_consumes_position_like_original(self):
        # невалидный type не сдвигает paramIndex → '3' читается как method,
        # а step остаётся 0 (parseInt('nope') = NaN)
        result = parse_inline_keyboard_data('X|nope|3')
        self.assertIsNotNone(result)
        button = result['buttons'][0][0]
        self.assertEqual(button['type'], 'callback')
        self.assertEqual(button['step'], 0)
        self.assertEqual(button['method'], 'step by step')

    def test_check_channel_and_points_role(self):
        result = parse_inline_keyboard_data(
            'Подписаться|check channel|7|step by step|keep|10|vip')
        self.assertIsNotNone(result)
        button = result['buttons'][0][0]
        self.assertEqual(button['type'], 'check channel')
        self.assertEqual(button['step'], 7)
        self.assertEqual(button['points'], 10)
        self.assertEqual(button['role'], 'vip')

    def test_empty_returns_none(self):
        self.assertIsNone(parse_inline_keyboard_data(''))
        self.assertIsNone(parse_inline_keyboard_data(None))


class TestInlineValuePayload(unittest.TestCase):
    """Формат приложения: payload (URL/команда/шаг) лежит в поле step/value."""

    def test_url_payload_kept_as_value(self):
        # Так сохраняет Flutter-приложение: text|type|value|method|action
        result = parse_inline_keyboard_data(
            'Сайт|url|https://example.com|step by step|keep')
        button = result['buttons'][0][0]
        self.assertEqual(button['value'], 'https://example.com')
        self.assertEqual(button['step'], 0)  # URL — не число

    def test_numeric_step_still_parsed(self):
        result = parse_inline_keyboard_data(
            'Далее|callback|5|step by step|keep')
        button = result['buttons'][0][0]
        self.assertEqual(button['value'], '5')
        self.assertEqual(button['step'], 5)

    def test_build_url_prefers_value_over_text(self):
        parsed = parse_inline_keyboard_data(
            'Открыть|url|https://example.com|step by step|keep')
        built = build_inline_keyboard_from_parsed_data(parsed, None)
        btn = built['inline_keyboard'][0][0]
        self.assertEqual(btn['text'], 'Открыть')
        self.assertEqual(btn['url'], 'https://example.com')

    def test_build_command_prefers_value_over_text(self):
        parsed = parse_inline_keyboard_data(
            'Помощь|command|/help|step by step|keep')
        built = build_inline_keyboard_from_parsed_data(parsed, None)
        btn = built['inline_keyboard'][0][0]
        self.assertEqual(btn['text'], 'Помощь')
        self.assertTrue(btn['callback_data'].startswith('cmd_help'))

    def test_build_url_falls_back_to_text_legacy(self):
        # Старый формат: payload в самом text
        parsed = parse_inline_keyboard_data('https://old.example|url')
        built = build_inline_keyboard_from_parsed_data(parsed, None)
        btn = built['inline_keyboard'][0][0]
        self.assertEqual(btn['url'], 'https://old.example')

    def test_empty_value_keeps_action_aligned(self):
        # Приложение пишет '0' вместо пустого value — method/action не сдвигаются
        result = parse_inline_keyboard_data(
            'Кнопка|callback|0|step by step|hide')
        button = result['buttons'][0][0]
        self.assertEqual(button['method'], 'step by step')
        self.assertEqual(button['action'], 'hide')
        self.assertEqual(button['step'], 0)


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


class TestParsePollData(unittest.TestCase):
    """parsePollData (:2605): question|answers|multiple|anonymous|logic|steps."""

    def test_full_format(self):
        result = parse_poll_data('Вопрос|Да,Нет|true|false|1,2|3,4')
        self.assertIsNotNone(result)
        self.assertEqual(result['question'], 'Вопрос')
        self.assertEqual(result['answers'], ['Да', 'Нет'])
        self.assertTrue(result['multiple'])
        self.assertFalse(result['anonymous'])
        self.assertEqual(result['logicValues'], [1, 2])
        self.assertEqual(result['steps'], [3, 4])

    def test_defaults(self):
        result = parse_poll_data('Q|A,B')
        self.assertIsNotNone(result)
        self.assertFalse(result['multiple'])
        self.assertTrue(result['anonymous'])
        self.assertEqual(result['logicValues'], [])
        self.assertEqual(result['steps'], [])

    def test_default_question_and_step_filter(self):
        result = parse_poll_data('|A,B|0|1|9|0,7')
        self.assertIsNotNone(result)
        self.assertEqual(result['question'], 'Вопрос')
        self.assertEqual(result['steps'], [7])        # 0 отбрасывается

    def test_empty_returns_none(self):
        self.assertIsNone(parse_poll_data(''))
        self.assertIsNone(parse_poll_data(None))


class TestParseQuizData(unittest.TestCase):
    """parseQuizData (:2659): question|answers|correct|explanation|anon|logic|steps."""

    def test_full_format(self):
        result = parse_quiz_data(
            'Вопрос|Да,Нет,Может|1|Это объяснение|true|2|3,4')
        self.assertIsNotNone(result)
        self.assertEqual(result['question'], 'Вопрос')
        self.assertEqual(result['answers'], ['Да', 'Нет', 'Может'])
        self.assertEqual(result['correctOption'], 1)
        self.assertEqual(result['explanation'], 'Это объяснение')
        self.assertTrue(result['anonymous'])
        self.assertEqual(result['logicValues'], [2])
        self.assertEqual(result['steps'], [3, 4])

    def test_correct_option_out_of_range_falls_back_to_zero(self):
        result = parse_quiz_data('Q|A,B|5')
        self.assertIsNotNone(result)
        self.assertEqual(result['correctOption'], 0)

    def test_defaults(self):
        result = parse_quiz_data('Q|A,B|0|')
        self.assertIsNotNone(result)
        self.assertEqual(result['correctOption'], 0)
        self.assertEqual(result['explanation'], '')
        self.assertTrue(result['anonymous'])
        self.assertEqual(result['steps'], [])

    def test_empty_returns_none(self):
        self.assertIsNone(parse_quiz_data(''))
        self.assertIsNone(parse_quiz_data(None))


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

    def test_function_prefixes(self):
        for prefix in ('system', 'custom', 'builtin', 'logical'):
            params = extract_callback_params(
                f'{prefix}_fn_12_row_2_col_0_action_remove')
            self.assertEqual(params['functionId'], 'fn')
            self.assertEqual(params['row'], '2')
            self.assertEqual(params['action'], 'remove')


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


if __name__ == '__main__':
    unittest.main(verbosity=2)
