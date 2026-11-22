"""Pixel-art waves background for the web app (Wave 3 / W2).

Source: Katsushika Hokusai, "Under the Wave off Kanagawa (The Great Wave)".
Licence: public domain (Metropolitan Museum of Art Open Access, object 45434).
File: https://upload.wikimedia.org/wikipedia/commons/a/a5/Tsunami_by_hokusai_19th_century.jpg
      (3859x2594, sha256 30c170260de393f51bd592adacc6f0714b60f157c09744e9c5b6d46e6a613718).
The source image is NOT committed; download it and pass it with --source.

Output: indexed PNG with only three colours: transparent, #0f8b8d and #ffffff.

Command:
    uv run --with pillow --with numpy python scripts/assets/pixelate_background.py \
        --source <great-wave.jpg> --out apps/web/public/bg/waves-dark.png

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

ACCENT = (15, 139, 141)
INK = (255, 255, 255)
BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16.0
CROP_TOP = 0.18
FADE_SPAN = 0.40


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


def build(source: Path, base_w: int) -> Image.Image:
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
    idx[(deep * fade) > thr] = 1
    ink = np.where(enclosed, 1.0, pale)
    idx[((ink * fade) > thr) & (idx == 0)] = 2
    idx[sky & (idx == 1)] = 0

    out = Image.fromarray(idx, "P")
    out.putpalette([0, 0, 0, *ACCENT, *INK] + [0] * (256 - 3) * 3)
    out.info["transparency"] = 0
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=320)
    args = parser.parse_args()
    out = build(args.source, args.width)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.save(args.out, optimize=True, transparency=0)
    print(f"{out.width}x{out.height} {args.out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
