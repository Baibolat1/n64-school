#!/usr/bin/env python
"""Рисует иконки приложения (значок на домашнем экране и favicon).

Значок — это тот же знак, что и в шапке сайта (.topnav-mark): квадрат с
градиентом accent -> accent-2 и число 64 кремовым. Поэтому палитра берётся
из theme.css, а не подбирается заново: иконка на домашнем экране и иконка в
панели должны читаться как одна вещь.

Рисуется в четыре раза больше и уменьшается: у Pillow нет ни сглаживания
шрифтового растра, ни векторного холста, и «64» при 180 px без этого выходило
рваным по краю.

Готовые PNG лежат в репозитории (app/static/img/), как и шрифты: сборки в
проекте нет, браузеры получают файлы как есть. Скрипт нужен, только если
значок меняется, и в CI не запускается -- зависимости у него те же, что у
разработчика (Pillow и системный шрифт с засечками), в requirements.txt его
нет и быть не должно.

Usage:
    .venv/bin/python scripts/make_icons.py
"""

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "static" / "img"

# Отступление от палитры theme.css: цвета те же, но с теми светами, которые
# даёт градиент, а не плоская заливка.
ACCENT = (10, 132, 255)
ACCENT_LIGHT = (75, 166, 255)
ACCENT_2 = (109, 94, 252)
CREAM = (242, 239, 228)

SUPERSAMPLE = 4  # рисуем вчетверо и уменьшаем: это и есть сглаживание

# Кегль числа -- доля от стороны значка. Значок обрезается системой по-разному
# (iOS -- «скруглённый квадрат», Android -- круг или квадрат навыворот), поэтому
# 64 держится в центральной половине: что бы ни обрезали, число остаётся целым.
NUMBER_RATIO = 0.44

# Что выкладываем на диск: имя файла -> сторона в пикселях.
TARGETS = {
    "icon-512.png": 512,  # манифест: Android, установка на домашний экран
    "icon-192.png": 192,  # манифест и favicon вкладки
    "apple-touch-icon.png": 180,  # iOS: свой формат и свой размер
}

# Шрифт только для рисования значка, в проект не копируется. Засечки -- потому
# что на сайте число 64 набрано антиквой (Cormorant Garamond); из системных
# ближе всего к ней выглядят Times-подобные.
FONTS = [
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def gradient(size: int) -> Image.Image:
    """Диагональный градиент accent-light -> accent -> accent-2.

    Считается на маленьком холсте и растягивается: попиксельный цикл по
    1024x1024 в Python занял бы секунды, а разница после растяжения
    незаметна -- градиент и есть медленно меняющаяся заливка.
    """
    small = 256
    canvas = Image.new("RGB", (small, small))
    pixels = canvas.load()
    for y in range(small):
        for x in range(small):
            t = (x + y) / (2 * (small - 1))  # 0 в левом верхнем углу, 1 в правом нижнем
            if t < 0.45:
                a, b, k = ACCENT_LIGHT, ACCENT, t / 0.45
            else:
                a, b, k = ACCENT, ACCENT_2, (t - 0.45) / 0.55
            pixels[x, y] = tuple(round(a[i] + (b[i] - a[i]) * k) for i in range(3))
    return canvas.resize((size, size), Image.BICUBIC)


def corner_glow(size: int, at: tuple[float, float], reach: float) -> Image.Image:
    """Мягкий свет (или тень) круговой формы.

    Считается здесь, а не Image.radial_gradient: у той «радиус» задан
    манхэттенским расстоянием, то есть круг выходит ромбом, и на значке
    видны ровные диагональные границы. Здесь обычное евклидово расстояние и
    квадратичное затухание -- свет уходит в ноль без края.
    """
    small = 128
    canvas = Image.new("L", (small, small), 0)
    pixels = canvas.load()
    cx, cy = at[0] * small, at[1] * small
    radius = reach * small
    for y in range(small):
        for x in range(small):
            d = math.hypot(x + 0.5 - cx, y + 0.5 - cy) / radius
            if d < 1:
                pixels[x, y] = round(255 * (1 - d) ** 2)
    return canvas.resize((size, size), Image.BICUBIC)


def font_for(target_height: int) -> ImageFont.FreeTypeFont:
    """Шрифт с нужной высотой цифр.

    Кегль считается от замера, а не берётся долей стороны: при одном кегле
    высота цифр у разных шрифтов разная, и «на глаз» значок при смене
    системного шрифта поехал бы.
    """
    for path in FONTS:
        if Path(path).exists():
            box = ImageFont.truetype(path, 100).getbbox("64")
            ratio = (box[3] - box[1]) / 100
            return ImageFont.truetype(path, round(target_height / ratio))
    raise SystemExit("нет системного шрифта с засечками: см. FONTS в этом файле")


def draw(size: int) -> Image.Image:
    canvas = gradient(size).convert("RGBA")

    # Блик в углу и тень в противоположном: без них квадрат читается плоским.
    # Свет маленький и прижат к углу -- большой превращал верхнюю половину
    # значка в белое пятно, и число на нём терялось.
    canvas.paste(
        Image.new("RGBA", (size, size), (255, 255, 255, 46)),
        (0, 0),
        corner_glow(size, (-0.15, -0.15), 0.55),
    )
    canvas.paste(
        Image.new("RGBA", (size, size), (3, 6, 16, 90)),
        (0, 0),
        corner_glow(size, (1.15, 1.15), 0.7),
    )

    font = font_for(round(size * NUMBER_RATIO))
    box = font.getbbox("64")
    text_w, text_h = box[2] - box[0], box[3] - box[1]
    at = ((size - text_w) / 2 - box[0], (size - text_h) / 2 - box[1])

    # Тень под числом: отбрасывается самим числом, размытым и сдвинутым вниз.
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text(at, "64", font=font, fill=(3, 6, 16, 130))
    shadow = shadow.filter(ImageFilter.GaussianBlur(size * 0.012))
    canvas.alpha_composite(shadow, (0, int(size * 0.012)))

    ImageDraw.Draw(canvas).text(at, "64", font=font, fill=CREAM)
    return canvas


def main() -> None:
    big = draw(512 * SUPERSAMPLE)
    for name, side in TARGETS.items():
        path = OUT / name
        big.resize((side, side), Image.LANCZOS).convert("RGB").save(path, optimize=True)
        print(f"{path.relative_to(ROOT)}  {side}x{side}  {path.stat().st_size} Б")


if __name__ == "__main__":
    main()
