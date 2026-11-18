# ============================================================================
# test_effects.py — текстовые эффекты <effect:*>: задержки в СЕКУНДАХ
# (регресс: _delay_ms отдавал дефолт 800 «секундами» → бот замирал
#  на ~13 минут на кадр) + проигрывание кадров.
#
# Запуск:  python3 test_effects.py   (из папки bot/)
# ============================================================================

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import effects  # noqa: E402


class FakeTG:
    """Записывает editMessageText, не ходит в сеть."""

    def __init__(self):
        self.edits: list[str] = []

    def edit_message_text(self, chat_id, message_id, text, options=None):
        self.edits.append(text)
        return True


class TestDelaySeconds(unittest.TestCase):
    """_delay_ms обязан возвращать секунды и при дефолте, и при параметре."""

    def test_default_millis_to_seconds(self):
        self.assertEqual(effects._delay_ms([], 800), 0.8)
        self.assertEqual(effects._delay_ms([], 300), 0.3)

    def test_param_millis_to_seconds(self):
        self.assertEqual(effects._delay_ms(['800'], 800), 0.8)
        self.assertEqual(effects._delay_ms(['250'], 800), 0.25)

    def test_bad_param_falls_back_to_default(self):
        self.assertEqual(effects._delay_ms(['abc'], 300), 0.3)

    def test_huge_param_clamped_to_5s(self):
        # Опечатка «999999» не должна вешать бот на минуты.
        self.assertEqual(effects._delay_ms(['999999'], 800), 5.0)
        self.assertEqual(effects._delay_ms([], 900000), 5.0)

    def test_cycles_clamped(self):
        self.assertEqual(effects._cycles(['200', '999'], 1, 3), 20)
        self.assertEqual(effects._cycles([], 1, 3), 3)


class TestParse(unittest.TestCase):
    def test_has_effects(self):
        self.assertTrue(effects.has_effects('<effect:blink>Текст</effect>'))
        self.assertFalse(effects.has_effects('обычный текст'))

    def test_params_split_by_colon(self):
        parsed = effects.parse_effects('<effect:blink:300:3:❗>Текст</effect>')
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0]['params'], ['300', '3', '❗'])

    def test_initial_final_keep_prefix_suffix(self):
        content = 'Привет <effect:countdown>3|2|1|Пока</effect> хвост'
        self.assertEqual(effects.get_initial_text(content), 'Привет 3 хвост')
        self.assertEqual(effects.get_final_text(content), 'Привет Пока хвост')


class TestPlayback(unittest.TestCase):
    def test_countdown_plays_all_frames(self):
        tg = FakeTG()
        with mock.patch.object(effects.time, 'sleep') as sleep:
            ok = effects.process_effects(
                tg, 1, 5, '<effect:countdown>3|2|1|🚀</effect>')
        self.assertTrue(ok)
        # Старт '3' уже на экране (get_initial_text) — process играет шаги.
        self.assertEqual(tg.edits, ['2', '1', '🚀'])
        # Все задержки — секунды (0.8), а не 800.
        self.assertEqual(
            [c.args[0] for c in sleep.call_args_list], [0.8, 0.8, 0.8])

    def test_default_delay_for_blink(self):
        tg = FakeTG()
        with mock.patch.object(effects.time, 'sleep') as sleep:
            ok = effects.process_effects(tg, 1, 5, '<effect:blink>Текст</effect>')
        self.assertTrue(ok)
        # Дефолт blink 300мс → 0.3с: скрыть/показать × циклы.
        delays = [c.args[0] for c in sleep.call_args_list]
        self.assertTrue(delays, 'ожидались задержки')
        self.assertEqual(delays[0], 0.3)

    def test_single_frame_returns_false_without_sleep(self):
        tg = FakeTG()
        with mock.patch.object(effects.time, 'sleep') as sleep:
            ok = effects.process_effects(
                tg, 1, 5, '<effect:countdown>текст</effect>')
        self.assertFalse(ok)
        sleep.assert_not_called()
        self.assertEqual(tg.edits, [])

    def test_reveal_accumulates_lines(self):
        tg = FakeTG()
        with mock.patch.object(effects.time, 'sleep'):
            ok = effects.process_effects(
                tg, 1, 5, '<effect:reveal>Привет|Мир</effect>')
        self.assertTrue(ok)
        # Старт 'Привет' уже на экране — один шаг накопления.
        self.assertEqual(tg.edits, ['Привет\nМир'])


class TestReveal(unittest.TestCase):
    def test_final_is_accumulated(self):
        # Итог = все кадры через \n (как после анимации), а не последний.
        content = '<effect:reveal>А|Б</effect>'
        self.assertEqual(effects.get_final_text(content), 'А\nБ')

    def test_cum_mode_shows_full_frames(self):
        content = '<effect:reveal:500:cum>Привет|Привет мир</effect>'
        tg = FakeTG()
        with mock.patch.object(effects.time, 'sleep'):
            ok = effects.process_effects(tg, 1, 1, content)
        self.assertTrue(ok)
        self.assertEqual(tg.edits, ['Привет мир'])
        self.assertEqual(effects.get_final_text(content), 'Привет мир')
        self.assertEqual(effects.get_initial_text(content), 'Привет')

    def test_cum_final_equals_source_text(self):
        # Финал cum-режима совпадает с исходом побуквенно (без \n).
        source = 'Привет  мир'  # двойной пробель сохраняется
        content = f'<effect:reveal:500:cum>Привет|{source}</effect>'
        self.assertEqual(effects.get_final_text(content), source)

    def test_single_phrase_reveal_shown_whole(self):
        # Одна фраза без «|» — пауза delay, затем ВСЯ фраза целиком
        # (формат гугл-скрипта «слово|слово» здесь не нужен).
        content = '<effect:reveal:800>Без вебинаров</effect>'
        tg = FakeTG()
        with mock.patch.object(effects.time, 'sleep') as sleep:
            ok = effects.process_effects(tg, 1, 1, content)
        self.assertTrue(ok)
        self.assertEqual(tg.edits, ['Без вебинаров'])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.8])
        # Старт пустой (появление с нуля), итог = исход.
        self.assertEqual(effects.get_initial_text(content), '')
        self.assertEqual(effects.get_final_text(content), 'Без вебинаров')

    def test_one_char_reveal_shown_after_pause(self):
        content = '<effect:reveal>А</effect>'
        tg = FakeTG()
        with mock.patch.object(effects.time, 'sleep') as sleep:
            ok = effects.process_effects(tg, 1, 1, content)
        self.assertTrue(ok)
        self.assertEqual(tg.edits, ['А'])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.5])
        self.assertEqual(effects.get_initial_text(content), '')


class TestMultipleEffects(unittest.TestCase):
    def test_two_phrases_play_sequentially(self):
        # Каждая фраза в своём теге — проигрывание фраза за фразой.
        content = ('🚫 <effect:reveal:800>Без вебинаров</effect>\n'
                   '<effect:reveal:800>Без соц сетей</effect>')
        # Старт: обе пустые, сырых тегов не видно (иначе Telegram 400).
        self.assertEqual(effects.get_initial_text(content), '🚫 \n')
        tg = FakeTG()
        with mock.patch.object(effects.time, 'sleep') as sleep:
            ok = effects.process_effects(tg, 1, 1, content)
        self.assertTrue(ok)
        self.assertEqual(tg.edits, [
            '🚫 Без вебинаров\n',
            '🚫 Без вебинаров\nБез соц сетей',
        ])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.8, 0.8])
        self.assertEqual(
            effects.get_final_text(content),
            '🚫 Без вебинаров\nБез соц сетей')


if __name__ == '__main__':
    unittest.main(verbosity=2)
