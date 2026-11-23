"""Pixel-art waves background for the web app (Wave 3 / W2, light theme in Wave 4 / W2).

Source: Katsushika Hokusai, "Under the Wave off Kanagawa (The Great Wave)".
Licence: public domain (Metropolitan Museum of Art Open Access, object 45434).
File: https://upload.wikimedia.org/wikipedia/commons/a/a5/Tsunami_by_hokusai_19th_century.jpg
      (3859x2594, sha256 30c170260de393f51bd592adacc6f0714b60f157c09744e9c5b6d46e6a613718).
The source image is NOT committed; download it and pass it with --source.

Output: indexed PNG, 320x176, with three palette entries per theme:
  --theme dark  (default): transparent, #0f8b8d and #ffffff.
  --theme light: transparent, #8e51ff (Tailwind violet-500, oklch(60.6% 0.25 292.717)) and
                 #000000. Paper sky and enclosed paper (foam) are transparent, the wave body
                 is violet and the darkest strokes are black.

Commands:
    uv run --with pillow --with numpy python scripts/assets/pixelate_background.py \
        --source <great-wave.jpg> --out apps/web/public/bg/waves-dark.png
    uv run --with pillow --with numpy python scripts/assets/pixelate_background.py \
        --theme light --source <great-wave.jpg> --out apps/web/public/bg/waves-light.png

Pipeline: crop cartouche and most of the sky -> BOX downscale to 320px wide -> colour
classification -> sky = paper connected to the top edge (transparent) while enclosed
paper (foam, spray) becomes white ink -> 4x4 Bayer dithering -> baked dithered fade to
transparent over the top of the image. Deterministic: same input, byte-identical output.
"""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

DARK_ACCENT = (15, 139, 141)
DARK_INK = (255, 255, 255)
LIGHT_INK = (0, 0, 0)
LIGHT_OKLCH = (0.606, 0.25, 292.717)
BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16.0
CROP_TOP = 0.18
FADE_SPAN = 0.40


def oklch_to_srgb(lightness: float, chroma: float, hue_deg: float) -> tuple[int, int, int]:
    """OKLCH -> OKLab -> linear sRGB -> gamma-encoded 8-bit sRGB."""
    hue = np.radians(hue_deg)
    a, b = chroma * np.cos(hue), chroma * np.sin(hue)
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    lin = np.array(
        [
            4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
            -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
            -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
        ]
    )
    lin = np.clip(lin, 0, 1)
    enc = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - 0.055)
    r, g, bl = (round(float(v) * 255) for v in enc)
    return r, g, bl


def light_accent() -> tuple[int, int, int]:
    accent = oklch_to_srgb(*LIGHT_OKLCH)
    assert accent == (142, 81, 255), accent
    return accent


def dilate(mask: np.ndarray) -> np.ndarray:
    out = mask.copy()
    out[1:, :] |= mask[:-1, :]
    out[:-1, :] |= mask[1:, :]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


def close(mask: np.ndarray) -> np.ndarray:
    return ~dilate(~dilate(mask))


def sky_from_top(paper: np.ndarray) -> np.ndarray:
    """Paper pixels 4-connected to the top row (flood fill)."""
    h, w = paper.shape
    sky = np.zeros_like(paper)
    queue = deque((0, x) for x in range(w) if paper[0, x])
    for _, x in queue:
        sky[0, x] = True
    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and paper[ny, nx] and not sky[ny, nx]:
                sky[ny, nx] = True
                queue.append((ny, nx))
    return sky


def build(source: Path, base_w: int, theme: str = "dark") -> Image.Image:
    img = Image.open(source).convert("RGB")
    w, h = img.size
    img = img.crop((0, round(h * CROP_TOP), w, h))
    base_h = round(img.height * base_w / img.width)
    a = np.asarray(img.resize((base_w, base_h), Image.Resampling.BOX), dtype=float) / 255.0
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    blue = np.clip(b - (r + g) / 2, 0, None)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b

    deep = np.clip((blue - 0.05) / 0.25, 0, 1) * np.clip((0.75 - lum) / 0.45, 0, 1)
    pale = np.clip((blue - 0.02) / 0.10, 0, 1) * np.clip((lum - 0.55) / 0.25, 0, 1)
    paper = (lum > 0.62) & (blue < 0.06)
    sky = sky_from_top(close(paper))
    enclosed = paper & ~sky

    fade = np.clip((np.arange(base_h) / base_h - 0.02) / FADE_SPAN, 0, 1)[:, None]
    thr = np.tile(BAYER, (base_h // 4 + 1, base_w // 4 + 1))[:base_h, :base_w]

    idx = np.zeros((base_h, base_w), dtype=np.uint8)
    if theme == "light":
        # Violet body, black only where the print is darkest; pale tints and foam stay
        # transparent so the page shows through and the wave stays airy.
        strokes = np.clip((0.27 - lum) / 0.08, 0, 1) * np.clip((blue - 0.03) / 0.10, 0, 1)
        body = np.maximum(deep, 0.35 * pale) * (1 - strokes)
        idx[(body * fade) > thr] = 1
        idx[(strokes * fade) > thr] = 2
        idx[sky] = 0
        out = Image.fromarray(idx, "P")
        out.putpalette([0, 0, 0, *light_accent(), *LIGHT_INK] + [0] * (256 - 3) * 3)
        out.info["transparency"] = 0
        return out
    idx[(deep * fade) > thr] = 1
    ink = np.where(enclosed, 1.0, pale)
    idx[((ink * fade) > thr) & (idx == 0)] = 2
    idx[sky & (idx == 1)] = 0

    out = Image.fromarray(idx, "P")
    out.putpalette([0, 0, 0, *DARK_ACCENT, *DARK_INK] + [0] * (256 - 3) * 3)
    out.info["transparency"] = 0
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--theme", choices=("dark", "light"), default="dark")
    parser.add_argument("--width", type=int, default=320)
    args = parser.parse_args()
    out = build(args.source, args.width, args.theme)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.save(args.out, optimize=True, transparency=0)
    print(f"{out.width}x{out.height} {args.out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
