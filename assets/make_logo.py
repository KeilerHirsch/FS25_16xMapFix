#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Logo generator for FS25 16x Map Fix.

Motif "Fit-to-Frame": four corner brackets pull an oversized map down onto a
clean farm-field square -- exactly what the tool does (shrink >8192 density
maps to the engine-safe 8192). Farming green, simple, legible at 256px.

Renders at high supersample then downsamples (LANCZOS) for crisp edges.
Outputs full-bleed square PNGs (modIcon-ready) + a rounded transparent brand PNG.
Reproducible: writes next to itself in assets/. Run on Windows (uses Arial Black).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SS = 2048                      # supersample canvas
OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)

# --- palette ---------------------------------------------------------------
BG_TOP = (39, 77, 30)          # forest green
BG_BOT = (9, 22, 7)            # near-black green
BORDER = (120, 160, 92, 160)   # subtle inner frame
BRACKET = (241, 245, 236)      # off-white
INK = (243, 247, 238)          # wordmark
FIELD_GREENS = [
    (124, 179, 66), (104, 159, 56), (139, 195, 74),
    (86, 140, 48), (154, 205, 96), (109, 76, 46),   # last = plowed brown
    (124, 179, 66), (139, 195, 74), (96, 150, 52),
]
GAP = (26, 46, 20)             # field separators / inner shadow

WORDMARK = "16× MAP FIX"
SUBTITLE = "FS25  DENSITY-MAP DOWNSCALER"


def vgradient(size: int, top, bot) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        r = round(top[0] + (bot[0] - top[0]) * t)
        g = round(top[1] + (bot[1] - top[1]) * t)
        b = round(top[2] + (bot[2] - top[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    return img


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius, fill=255)
    return m


def font(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(r"C:\Windows\Fonts\ariblk.ttf", px)


def _fit_font(d: ImageDraw.ImageDraw, text: str, start_px: int, max_w: int) -> ImageFont.FreeTypeFont:
    """Shrink the wordmark font until it fits max_w (16x MAP FIX is wider than 16x FIXER)."""
    px = start_px
    while px > 10:
        f = font(px)
        bb = d.textbbox((0, 0), text, font=f)
        if (bb[2] - bb[0]) <= max_w:
            return f
        px -= 8
    return font(px)


def build() -> Image.Image:
    img = vgradient(SS, BG_TOP, BG_BOT).convert("RGBA")
    d = ImageDraw.Draw(img)

    # subtle inner frame
    inset = int(SS * 0.035)
    d.rounded_rectangle([inset, inset, SS - inset, SS - inset],
                        radius=int(SS * 0.10), outline=BORDER, width=max(2, SS // 340))

    cx, cy = SS // 2, int(SS * 0.43)     # graphic block sits high, wordmark below

    # --- clean farm-field square (the fixed, engine-safe map) --------------
    fh = int(SS * 0.145)                 # half-size of the field square
    fx0, fy0, fx1, fy1 = cx - fh, cy - fh, cx + fh, cy + fh
    rad = int(fh * 0.16)
    # drop shadow
    sh = int(SS * 0.012)
    d.rounded_rectangle([fx0 + sh, fy0 + sh, fx1 + sh, fy1 + sh], radius=rad,
                        fill=(0, 0, 0, 90))
    # base
    d.rounded_rectangle([fx0, fy0, fx1, fy1], radius=rad, fill=(70, 110, 44, 255))
    # 3x3 field grid clipped to the rounded square
    tile = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    n = 3
    step = (fx1 - fx0) / n
    g = max(2, SS // 500)                # separator gap
    for r in range(n):
        for c in range(n):
            x0 = fx0 + c * step + g
            y0 = fy0 + r * step + g
            x1 = fx0 + (c + 1) * step - g
            y1 = fy0 + (r + 1) * step - g
            td.rectangle([x0, y0, x1, y1], fill=FIELD_GREENS[r * n + c])
    fmask = Image.new("L", (SS, SS), 0)
    ImageDraw.Draw(fmask).rounded_rectangle([fx0, fy0, fx1, fy1], radius=rad, fill=255)
    img.paste(tile, (0, 0), Image.composite(tile.split()[3], Image.new("L", (SS, SS), 0), fmask))

    # --- four corner brackets (the oversized map being pulled in) ----------
    bh = int(SS * 0.235)                 # half-span of the outer frame corners
    arm = int(SS * 0.085)                # bracket arm length
    tk = max(6, int(SS * 0.017))         # bracket thickness
    corners = [(-1, -1), (1, -1), (-1, 1), (1, 1)]
    for sx, sy in corners:
        ex, ey = cx + sx * bh, cy + sy * bh          # corner point
        # horizontal arm + vertical arm forming an L pointing inward
        d.line([(ex, ey), (ex - sx * arm, ey)], fill=BRACKET, width=tk)
        d.line([(ex, ey), (ex, ey - sy * arm)], fill=BRACKET, width=tk)
        # rounded joint
        d.ellipse([ex - tk / 2, ey - tk / 2, ex + tk / 2, ey + tk / 2], fill=BRACKET)

    # --- wordmark ----------------------------------------------------------
    f = _fit_font(d, WORDMARK, int(SS * 0.135), int(SS * 0.86))
    bbox = d.textbbox((0, 0), WORDMARK, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    ty = int(SS * 0.74)
    d.text((cx - tw / 2 - bbox[0], ty), WORDMARK, font=f, fill=INK)
    # thin accent underline
    uw = int(tw * 0.62)
    uy = ty + th + int(SS * 0.045)
    d.rounded_rectangle([cx - uw / 2, uy, cx + uw / 2, uy + max(4, SS // 300)],
                        radius=SS // 300, fill=(139, 195, 74, 255))
    # subtitle
    fs = font(int(SS * 0.032))
    sb = d.textbbox((0, 0), SUBTITLE, font=fs)
    d.text((cx - (sb[2] - sb[0]) / 2 - sb[0], uy + int(SS * 0.03)), SUBTITLE,
           font=fs, fill=(170, 195, 150, 255))
    return img


def main() -> None:
    master = build()

    # full-bleed square variants (modIcon-ready)
    for size in (1024, 512, 256):
        master.resize((size, size), Image.LANCZOS).convert("RGB").save(OUT / f"logo_{size}.png")

    # rounded transparent brand variant
    r = master.resize((512, 512), Image.LANCZOS)
    r.putalpha(rounded_mask(512, int(512 * 0.14)))
    r.save(OUT / "logo_rounded_512.png")

    print("Wrote:", *[p.name for p in sorted(OUT.glob('logo*.png'))])


if __name__ == "__main__":
    main()
