# ============================================================================
# test_barcode.py — локальная генерация QR: сплошные цвета, градиенты,
# подпись, разбор колонки qr (parse_qr_config).
#
# Запуск:  python3 test_barcode.py   (из папки bot/)
# ============================================================================

from __future__ import annotations

import io
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image  # noqa: E402

from step_model import parse_qr_config  # noqa: E402
from steps import (  # noqa: E402
    _gradient_image,
    _parse_gradient,
    generate_barcode_image,
)


def _png(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert('RGB')


class TestGradientSpec(unittest.TestCase):
    def test_parse_valid(self):
        g = _parse_gradient('linear-gradient(135deg, #FF0000, #0000FF)')
        self.assertEqual(g, ('linear', 135.0, ['#FF0000', '#0000FF']))

    def test_parse_negative_angle(self):
        g = _parse_gradient('linear-gradient(-45deg, #abc, #def012)')
        self.assertIsNotNone(g)

    def test_parse_rejects_garbage(self):
        self.assertIsNone(_parse_gradient('zzz'))
        self.assertIsNone(_parse_gradient('#FF0000'))
        self.assertIsNone(_parse_gradient('linear-gradient(135deg, red, blue)'))
        self.assertIsNone(_parse_gradient(None))


class TestGenerateBarcode(unittest.TestCase):
    def test_solid_default(self):
        img = _png(generate_barcode_image('solid'))
        self.assertEqual(img.size, (300, 300))

    def test_gradient_dark_paints_modules(self):
        img = _png(generate_barcode_image('gd', qr_config={
            'dark': 'linear-gradient(90deg, #FF0000, #0000FF)',
            'light': '#FFFFFF',
            'size': 300,
        }))
        px = img.load()
        left, right = [], []
        for y in range(0, 300, 3):
            for x in range(0, 300, 3):
                if px[x, y] == (255, 255, 255):
                    continue
                (left if x < 100 else right if x > 200 else []).append(px[x, y])
        self.assertTrue(left and right, 'модули QR не найдены')
        self.assertGreater(sum(p[0] for p in left) / len(left),
                           sum(p[0] for p in right) / len(right))
        self.assertGreater(sum(p[2] for p in right) / len(right),
                           sum(p[2] for p in left) / len(left))

    def test_gradient_light_background(self):
        img = _png(generate_barcode_image('gl', qr_config={
            'light': 'linear-gradient(90deg, #EEF2FF, #FFFFFF)',
            'size': 300,
        }))
        px = img.load()
        self.assertNotEqual(px[2, 2], px[297, 297])

    def test_caption_extends_height(self):
        img = _png(generate_barcode_image('cap', qr_config={
            'caption': 'Скидка 10%',
            'captionFontSize': 18,
            'size': 300,
        }))
        self.assertGreater(img.size[1], img.size[0])

    def test_gradient_with_caption(self):
        img = _png(generate_barcode_image('gcap', qr_config={
            'dark': 'linear-gradient(0deg, #111111, #555555)',
            'light': 'linear-gradient(180deg, #FFFFFF, #DDDDDD)',
            'caption': 'Подпись',
            'size': 300,
        }))
        self.assertGreater(img.size[1], img.size[0])

    def test_garbage_params_fall_back(self):
        img = _png(generate_barcode_image('bad', qr_config={
            'size': 'abc', 'dark': 'zzzz', 'ecl': 'X',
        }))
        self.assertEqual(img.size, (300, 300))

    def test_gradient_image_geometry(self):
        grad = _gradient_image(200, 100, 0, ['#FF0000', '#0000FF'])
        self.assertEqual(grad.size, (200, 100))
        # 0deg = снизу вверх: внизу преобладает первый цвет (красный),
        # вверху — второй (синий); у границ интерполяция не даёт 255.
        bottom = grad.getpixel((100, 95))
        top = grad.getpixel((100, 5))
        self.assertGreater(bottom[0], bottom[2])
        self.assertGreater(top[2], top[0])
        # 90deg = слева направо.
        grad90 = _gradient_image(200, 100, 90, ['#FF0000', '#0000FF'])
        left = grad90.getpixel((5, 50))
        right = grad90.getpixel((195, 50))
        self.assertGreater(left[0], left[2])
        self.assertGreater(right[2], right[0])


    def test_eyes_round_independently_of_modules(self):
        # Внешний контур угловых квадратов скругляется своим eyeRadius:
        # при 0.5 угол рамки вырезан (фон), рамка тёмная. Настройки
        # независимы — радиус модулей угловые квадраты не трогает.
        import qrcode

        data = 'https://example.com/qr-round'

        def render(extra):
            png = generate_barcode_image(
                data, qr_config={'size': 600, 'margin': 4,
                                 'dark': '000000', 'light': 'ffffff', **extra})
            return _png(png).convert('L')

        q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                          border=4)
        q.add_data(data)
        q.make(fit=True)
        box = max(1, 600 // (q.modules_count + 8))
        pad = (600 - (q.modules_count + 8) * box) // 2
        corner = (pad + 4 * box, pad + 4 * box)   # угол квадрата слева-сверху
        frame = (corner[0] + 3 * box + box // 2, corner[1] + box // 2)

        square = render({})
        eyes = render({'eyeRadius': 0.5})
        modules = render({'dotRadius': 0.5})
        self.assertLess(square.getpixel(corner), 128)
        self.assertGreaterEqual(eyes.getpixel(corner), 128)
        self.assertLess(modules.getpixel(corner), 128)
        self.assertLess(square.getpixel(frame), 128)
        self.assertLess(eyes.getpixel(frame), 128)

        # Внутри не скругляется: просвет (5 модулей) и центральная
        # точка (3 модуля) квадратные — при небольшом радиусе угол
        # просвета светлый, угол точки тёмный.
        mild = render({'eyeRadius': 0.05})
        hole_corner = (corner[0] + box, corner[1] + box)
        dot_corner = (corner[0] + 2 * box, corner[1] + 2 * box)
        self.assertGreaterEqual(mild.getpixel(hole_corner), 128)
        self.assertLess(mild.getpixel(dot_corner), 128)


    def test_module_style_diamond(self):
        # Ромб отличается от базовых квадратов; чёрных/белых пикселей
        # достаточно — код читается.
        base = _png(generate_barcode_image('diamond', qr_config={
            'size': 300, 'dark': '000000', 'light': 'FFFFFF',
        }))
        dia = _png(generate_barcode_image('diamond', qr_config={
            'size': 300, 'dark': '000000', 'light': 'FFFFFF',
            'moduleStyle': 'diamond',
        }))
        self.assertNotEqual(base.tobytes(), dia.tobytes())
        px = dia.load()
        dark = sum(1 for y in range(300) for x in range(300)
                   if sum(px[x, y]) < 128)
        light = sum(1 for y in range(300) for x in range(300)
                    if sum(px[x, y]) > 600)
        self.assertGreater(dark, 1000)
        self.assertGreater(light, 1000)

    def test_module_style_drop_changes_corners(self):
        # drop возвращает один угол квадратом — изображение отличается
        # от базового при том же dotRadius.
        cfg = {'size': 300, 'dark': '000000', 'light': 'FFFFFF',
               'dotRadius': 0.4}
        base = _png(generate_barcode_image('drop', qr_config=cfg))
        drop = _png(generate_barcode_image('drop', qr_config={
            **cfg, 'moduleStyle': 'drop',
        }))
        self.assertNotEqual(base.tobytes(), drop.tobytes())

    def test_eye_color_overrides_eyes(self):
        # Центр глаза: с eyeColor=FF0000 — красный; без — чёрный.
        import qrcode

        data = 'https://example.com/qr-eye-color'
        q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                          border=4)
        q.add_data(data)
        q.make(fit=True)
        box = max(1, 600 // (q.modules_count + 8))
        pad = (600 - (q.modules_count + 8) * box) // 2
        corner = (pad + 4 * box, pad + 4 * box)
        center = (corner[0] + 3 * box + box // 2,
                  corner[1] + 3 * box + box // 2)

        red = _png(generate_barcode_image(data, qr_config={
            'size': 600, 'margin': 4, 'dark': '000000', 'light': 'ffffff',
            'eyeColor': 'FF0000',
        })).load()
        r, g, b = red[center]
        self.assertGreater(r, 200)
        self.assertLess(g, 80)

        plain = _png(generate_barcode_image(data, qr_config={
            'size': 600, 'margin': 4, 'dark': '000000', 'light': 'ffffff',
        })).load()
        self.assertLess(sum(plain[center]), 100)

    def test_radial_gradient_generates(self):
        # Радиальный градиент принимается и в dark, и в light.
        dark_rad = generate_barcode_image('radial', qr_config={
            'size': 300, 'dark': 'radial-gradient(#FF0000, #0000FF)',
            'light': 'FFFFFF',
        })
        self.assertEqual(_png(dark_rad).size, (300, 300))
        light_rad = generate_barcode_image('radial', qr_config={
            'size': 300, 'dark': '000000',
            'light': 'radial-gradient(#FF0000, #0000FF)',
        })
        self.assertEqual(_png(light_rad).size, (300, 300))
        # Радиальный свет реально применён — не совпадает со сплошным.
        solid = generate_barcode_image('radial', qr_config={
            'size': 300, 'dark': '000000', 'light': 'FFFFFF',
        })
        self.assertNotEqual(light_rad, solid)

    def test_bg_image_error_keeps_valid(self):
        # Несуществующий URL фона: requests замокан, картинка валидна.
        from unittest import mock
        with mock.patch('requests.get', side_effect=Exception('offline')):
            data = generate_barcode_image('bgfail', qr_config={
                'size': 300, 'bgImageUrl': 'https://example.com/bg.png',
            })
        self.assertIsNotNone(data)
        self.assertEqual(_png(data).size, (300, 300))

    def test_frame_color_and_width(self):
        # Рамка: (0,0) окрашен; frameWidth без frameColor — не рисуем.
        framed = _png(generate_barcode_image('frm', qr_config={
            'size': 300, 'frameColor': 'FF0000', 'frameWidth': 9,
        }))
        self.assertEqual(framed.getpixel((0, 0)), (255, 0, 0))
        no_color = _png(generate_barcode_image('frm', qr_config={
            'size': 300, 'frameWidth': 9,
        }))
        self.assertNotEqual(no_color.getpixel((0, 0)), (255, 0, 0))

    def test_logo_border(self):
        # Обводка подложки: контур красный, внутренняя часть — подложка.
        from unittest import mock
        buf = io.BytesIO()
        Image.new('RGB', (40, 40), (0, 0, 255)).save(buf, format='PNG')
        logo_png = buf.getvalue()
        resp = mock.Mock(status_code=200, content=logo_png)
        with mock.patch('requests.get', return_value=resp):
            img = _png(generate_barcode_image('lg', qr_config={
                'size': 300, 'centerImageUrl': 'https://example.com/logo.png',
                'logoBorderWidth': 6, 'logoBorderColor': 'FF0000',
                'dark': '000000', 'light': 'FFFFFF',
            }))
        # Подложка 48px + обводка 6px с каждой стороны → 60px, позиция 120.
        edge = img.getpixel((122, 150))     # в обводке
        plate = img.getpixel((127, 150))    # подложка без логотипа
        self.assertEqual(edge, (255, 0, 0))
        self.assertNotEqual(edge, plate)

    def test_logo_plate_off_transparent(self):
        # logoPlate=0: подложки нет — сквозь прозрачный логотип виден код;
        # с подложкой (дефолт) центр сплошь светлый.
        from unittest import mock
        buf = io.BytesIO()
        Image.new('RGBA', (40, 40), (0, 0, 0, 0)).save(buf, format='PNG')
        resp = mock.Mock(status_code=200, content=buf.getvalue())
        base = {
            'size': 300, 'centerImageUrl': 'https://example.com/logo.png',
            'dark': '000000', 'light': 'FFFFFF',
        }
        with mock.patch('requests.get', return_value=resp):
            with_plate = _png(generate_barcode_image('lp1',
                                                     qr_config=dict(base)))
            without = _png(generate_barcode_image(
                'lp2', qr_config={**base, 'logoPlate': 0}))
        # Подложка 48px по центру закрывает модули — ровный светлый.
        self.assertEqual(with_plate.getpixel((150, 150)), (255, 255, 255))
        # Без подложки в той же области остаются модули кода.
        px = without.load()
        dark = sum(1 for y in range(130, 170) for x in range(130, 170)
                   if px[x, y] == (0, 0, 0))
        self.assertGreater(dark, 0, 'модули должны быть видны без подложки')

    def test_caption_inside_banner(self):
        # captionPos=inside не добавляет полосу; баннер на нижней строке.
        img = _png(generate_barcode_image('inb', qr_config={
            'size': 300, 'caption': 'SALE', 'captionPos': 'inside',
            'captionStyle': 'banner', 'dark': '112233', 'light': 'FFFFFF',
        }))
        self.assertEqual(img.size, (300, 300))
        px = img.load()
        self.assertEqual(px[10, 299], (0x11, 0x22, 0x33))
        self.assertEqual(px[290, 299], (0x11, 0x22, 0x33))

    def test_caption_pill_valid(self):
        img = _png(generate_barcode_image('pill', qr_config={
            'size': 300, 'caption': 'Скидка 10%', 'captionStyle': 'pill',
            'captionFontSize': 18,
        }))
        self.assertGreater(img.size[1], img.size[0])
        # Плашка присутствует: под текстом светлые пиксели поверх фона.
        self.assertEqual(img.getpixel((2, img.size[1] - 2)),
                         (255, 255, 255))


class TestSvgFormat(unittest.TestCase):
    def test_svg_returns_valid_xml(self):
        # format='svg' → UTF-8 bytes, валидный SVG с размером полотна.
        data = generate_barcode_image('svg-basic', qr_config={
            'size': 300, 'format': 'svg',
        })
        self.assertIsInstance(data, bytes)
        self.assertTrue(data.startswith(b'<svg'))
        root = ET.fromstring(data)
        self.assertEqual(root.get('width'), '300')
        self.assertEqual(root.get('height'), '300')

    def test_svg_full_style(self):
        # Полный стиль: капли, глаза отдельным цветом/скруглением,
        # радиальный градиент, рамка, баннер внутри, логотип с обводкой.
        from unittest import mock
        buf = io.BytesIO()
        Image.new('RGB', (40, 40), (0, 0, 255)).save(buf, format='PNG')
        resp = mock.Mock(status_code=200, content=buf.getvalue(),
                         headers={'Content-Type': 'image/png'})
        with mock.patch('requests.get', return_value=resp):
            data = generate_barcode_image('svg-full', qr_config={
                'size': 300, 'format': 'svg',
                'moduleStyle': 'drop', 'dotRadius': 0.4,
                'eyeStyle': 'circle', 'eyeRadius': 0.3,
                'eyeColor': 'FF0000',
                'dark': 'radial-gradient(#111111, #555555)',
                'light': '#FFFFFF',
                'frameColor': 'FF0000', 'frameWidth': 6,
                'caption': 'SALE & "hit" <x>', 'captionPos': 'inside',
                'captionStyle': 'banner',
                'centerImageUrl': 'https://example.com/logo.png',
                'logoShape': 'circle', 'logoBorderWidth': 6,
            })
        ET.fromstring(data)  # валидность XML (эскейп текста)
        text = data.decode('utf-8')
        self.assertIn('id="g_dark"', text)      # id градиента
        self.assertIn('#FF0000', text)          # eyeColor (нормализован)
        self.assertIn('<text', text)            # подпись
        self.assertIn('data:image', text)       # логотип встроен
        self.assertIn('stroke=', text)          # рамка/глаза контуром

    def test_png_format_still_png(self):
        # Регресс: format='png' по-прежнему отдаёт PNG.
        data = generate_barcode_image('png-fmt', qr_config={
            'size': 300, 'format': 'png',
        })
        self.assertTrue(data.startswith(b'\x89PNG'))


class TestParseQrConfig(unittest.TestCase):
    def test_gradient_dark_accepted(self):
        # Кодировка как у Dart Uri.encodeComponent: пробелы = %20.
        p = parse_qr_config(
            'dark=linear-gradient%28135deg%2C%20%23FF0000%2C%20%230000FF%29'
            '&size=400')
        self.assertEqual(p['dark'],
                         'linear-gradient(135deg, #FF0000, #0000FF)')
        self.assertEqual(p['size'], 400)

    def test_solid_still_works(self):
        p = parse_qr_config('dark=%23FF0000&light=ffffff&ecl=Q')
        self.assertEqual(p['dark'], 'FF0000')
        self.assertEqual(p['light'], 'ffffff')
        self.assertEqual(p['ecl'], 'Q')

    def test_garbage_dropped(self):
        p = parse_qr_config('dark=zzzzzz&size=abc&captionFontColor=nope')
        self.assertEqual(p, {})

    def test_dot_radius_and_logo_shape(self):
        p = parse_qr_config(
            'dotRadius=0.35&eyeRadius=0.2&logoShape=CIRCLE')
        self.assertEqual(p['dotRadius'], 0.35)
        self.assertEqual(p['eyeRadius'], 0.2)
        self.assertEqual(p['logoShape'], 'circle')
        # Радиусы вне 0..0.5 и неизвестная форма — отбрасываются.
        self.assertEqual(
            parse_qr_config('dotRadius=0.9&eyeRadius=1&logoShape=oval'), {})


if __name__ == '__main__':
    unittest.main()
