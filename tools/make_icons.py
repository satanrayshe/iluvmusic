"""Draws the iluvmusic icon (a white heart with an eighth-note cut-out on a
raspberry tile) at every size the manifest needs. Run: python tools/make_icons.py"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / 'extension' / 'icons'
TILE = (214, 51, 108, 255)   # #d6336c
INK = (255, 255, 255, 255)
S = 1024                     # draw big, then scale down for clean edges


def heart_points(cx, cy, size, steps=400):
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * size, cy - y * size))
    return pts


def draw():
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=TILE)
    d.polygon(heart_points(S * 0.5, S * 0.50, S * 0.0215), fill=INK)
    # eighth note punched out of the heart
    hx, hy, r = S * 0.455, S * 0.60, S * 0.072
    d.ellipse([hx - r * 1.25, hy - r, hx + r * 1.25, hy + r], fill=TILE)
    stem_x = hx + r * 1.25 - S * 0.03
    d.rectangle([stem_x, S * 0.33, stem_x + S * 0.03, hy], fill=TILE)
    d.polygon([(stem_x, S * 0.33), (stem_x + S * 0.13, S * 0.40),
               (stem_x + S * 0.13, S * 0.46), (stem_x, S * 0.40)], fill=TILE)
    return img


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    for size in (16, 32, 48, 128):
        big.resize((size, size), Image.LANCZOS).save(OUT / f'icon-{size}.png')
    store = OUT.parent.parent / 'store'
    store.mkdir(exist_ok=True)
    big.resize((512, 512), Image.LANCZOS).save(store / 'icon-512.png')  # for store listings
    print('wrote icons to', OUT)


if __name__ == '__main__':
    main()
