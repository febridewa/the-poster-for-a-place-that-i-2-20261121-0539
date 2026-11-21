#!/usr/bin/env python3
"""AETHERIA -- a travel poster for a place that is not real.

Pure-stdlib Python 3.13. Renders a 1024x1024 poster of the floating
islands of Aetheria at sunset and writes it to /output/entry.png.

No network, no randomness, no clock: two runs produce identical bytes.
"""

import hashlib
import math
import os
import struct
import sys
import zlib

W = 1024
H = 1024
OUT_PRIMARY = "/output/entry.png"
OUT_FALLBACK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "output", "entry.png"
)


def hex_rgb(text):
    """'#rrggbb' -> (r, g, b)."""
    text = text.lstrip("#")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def mix(c1, c2, t):
    """Linear blend of two colours, t in [0, 1]."""
    return (
        int(c1[0] + (c2[0] - c1[0]) * t + 0.5),
        int(c1[1] + (c2[1] - c1[1]) * t + 0.5),
        int(c1[2] + (c2[2] - c1[2]) * t + 0.5),
    )


def clamp(v, lo=0, hi=255):
    return lo if v < lo else hi if v > hi else v


def pseudo(x, y):
    """Cheap deterministic hash in [0, 1) for dither and jitter."""
    n = (x * 374761393 + y * 668265263) & 0xFFFFFFFF
    n ^= n >> 13
    n = (n * 1274126177) & 0xFFFFFFFF
    n ^= n >> 16
    return (n & 0xFFFF) / 65535.0


class Sheet:
    """A plain RGB canvas held in a single bytearray."""

    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.px = bytearray(width * height * 3)

    def put(self, x, y, rgb):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i] = rgb[0]
            self.px[i + 1] = rgb[1]
            self.px[i + 2] = rgb[2]

    def blend(self, x, y, rgb, alpha):
        if alpha <= 0.0 or not (0 <= x < self.w and 0 <= y < self.h):
            return
        if alpha >= 1.0:
            self.put(x, y, rgb)
            return
        i = (y * self.w + x) * 3
        inv = 1.0 - alpha
        self.px[i] = clamp(int(self.px[i] * inv + rgb[0] * alpha + 0.5))
        self.px[i + 1] = clamp(int(self.px[i + 1] * inv + rgb[1] * alpha + 0.5))
        self.px[i + 2] = clamp(int(self.px[i + 2] * inv + rgb[2] * alpha + 0.5))

    def hline(self, x0, x1, y, rgb, alpha=1.0):
        if y < 0 or y >= self.h:
            return
        x0 = max(0, int(x0))
        x1 = min(self.w - 1, int(x1))
        if x1 < x0:
            return
        if alpha >= 1.0:
            base = (y * self.w + x0) * 3
            for _ in range(x0, x1 + 1):
                self.px[base] = rgb[0]
                self.px[base + 1] = rgb[1]
                self.px[base + 2] = rgb[2]
                base += 3
        else:
            for x in range(x0, x1 + 1):
                self.blend(x, y, rgb, alpha)

    def vline(self, x, y0, y1, rgb, alpha=1.0):
        if x < 0 or x >= self.w:
            return
        y0 = max(0, int(y0))
        y1 = min(self.h - 1, int(y1))
        for y in range(y0, y1 + 1):
            self.blend(x, y, rgb, alpha)

    def fill_row(self, y, rgb):
        """Paint one whole row opaque, fast."""
        if 0 <= y < self.h:
            base = y * self.w * 3
            self.px[base:base + self.w * 3] = bytes(rgb) * self.w


def _chunk(kind, data):
    body = kind + data
    return (
        struct.pack(">I", len(data))
        + body
        + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    )


def write_png(sheet, path):
    """Encode the sheet as a deterministic PNG (filter 0, level 9)."""
    raw = bytearray()
    stride = sheet.w * 3
    for y in range(sheet.h):
        raw.append(0)
        raw += sheet.px[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", sheet.w, sheet.h, 8, 2, 0, 0, 0)
    blob = b"\x89PNG\r\n\x1a\n"
    blob += _chunk(b"IHDR", ihdr)
    blob += _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    blob += _chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(blob)
    return len(blob)


# ---------------------------------------------------------------- shapes


def disc(sheet, cx, cy, r, rgb, alpha=1.0):
    """Filled circle."""
    cx = int(round(cx))
    cy = int(round(cy))
    r = int(round(r))
    if r < 1:
        sheet.blend(int(round(cx)), int(round(cy)), rgb, alpha)
        return
    for dy in range(-r, r + 1):
        half = int(math.sqrt(max(0, r * r - dy * dy)))
        sheet.hline(cx - half, cx + half, cy + dy, rgb, alpha)


def ellipse(sheet, cx, cy, rx, ry, rgb, alpha=1.0):
    """Filled axis-aligned ellipse."""
    cx = int(round(cx))
    cy = int(round(cy))
    rx = int(round(rx))
    ry = int(round(ry))
    if rx < 1 or ry < 1:
        return
    for dy in range(-ry, ry + 1):
        t = 1.0 - (dy * dy) / float(ry * ry)
        half = int(rx * math.sqrt(max(0.0, t)))
        sheet.hline(cx - half, cx + half, cy + dy, rgb, alpha)


def polygon(sheet, pts, rgb, alpha=1.0):
    """Scanline-filled polygon (even-odd rule)."""
    ys = [p[1] for p in pts]
    top = int(math.floor(min(ys)))
    bot = int(math.ceil(max(ys)))
    n = len(pts)
    for y in range(top, bot + 1):
        xs = []
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            if (y0 <= y < y1) or (y1 <= y < y0):
                t = (y - y0) / float(y1 - y0)
                xs.append(x0 + t * (x1 - x0))
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            sheet.hline(int(math.ceil(xs[k])), int(math.floor(xs[k + 1])),
                        y, rgb, alpha)


def stroke(sheet, x0, y0, x1, y1, width, rgb, alpha=1.0):
    """A thick line segment as a quad plus round end caps."""
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 0.001:
        disc(sheet, x0, y0, width / 2.0, rgb, alpha)
        return
    nx = -dy / length * width / 2.0
    ny = dx / length * width / 2.0
    polygon(sheet, [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny),
                    (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)], rgb, alpha)
    disc(sheet, x0, y0, width / 2.0, rgb, alpha)
    disc(sheet, x1, y1, width / 2.0, rgb, alpha)


def polyline(sheet, pts, width, rgb, alpha=1.0):
    for a, b in zip(pts, pts[1:]):
        stroke(sheet, a[0], a[1], b[0], b[1], width, rgb, alpha)


def vgrad(sheet, y0, y1, stops, dither=False):
    """Vertical gradient between (pos 0..1, colour) stops."""
    for y in range(int(y0), int(y1)):
        t = (y - y0) / float(max(1, y1 - 1 - y0))
        rgb = stops[-1][1]
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                f = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
                rgb = mix(c0, c1, f)
                break
        if dither:
            base = y * sheet.w * 3
            for x in range(sheet.w):
                g = clamp(rgb[1] + ((x * 7 + y * 13) % 3) - 1)
                sheet.px[base] = rgb[0]
                sheet.px[base + 1] = g
                sheet.px[base + 2] = rgb[2]
                base += 3
        else:
            sheet.fill_row(y, rgb)


def cloud(sheet, cx, cy, width, rgb, alpha=0.9):
    """A flat layered cloud: a row of overlapping soft ellipses."""
    h = width * 0.16
    bumps = [(-0.38, 0.55), (-0.18, 0.38), (0.0, 0.32),
             (0.2, 0.42), (0.38, 0.6)]
    for fx, fy in bumps:
        ellipse(sheet, cx + fx * width, cy - h * (1.0 - fy),
                width * 0.26, h * fy, rgb, alpha)
    ellipse(sheet, cx, cy, width * 0.5, h * 0.55, rgb, alpha)


def tree(sheet, x, y, scale=1.0):
    """One small round-topped tree on a mesa."""
    trunk = hex_rgb("#3a2a1e")
    leaf = hex_rgb("#2e6b38")
    leaf2 = hex_rgb("#3d8a46")
    stroke(sheet, x, y, x, y - 12 * scale, 3 * scale, trunk)
    disc(sheet, x, y - 16 * scale, 11 * scale, leaf)
    disc(sheet, x - 6 * scale, y - 12 * scale, 7 * scale, leaf2)
    disc(sheet, x + 6 * scale, y - 12 * scale, 7 * scale, leaf2)


def waterfall(sheet, x, y0, y1, phase):
    """A smooth wavy ribbon of falling water between two heights."""
    pale = hex_rgb("#d7ecf5")
    bright = hex_rgb("#f4fbff")
    outer = []
    inner = []
    y = y0
    while y <= y1:
        sway = 6.0 * math.sin(y * 0.035 + phase)
        outer.append((x + sway, y))
        inner.append((x + sway - 1.5, y))
        y += 6
    polyline(sheet, outer, 9, pale, 0.85)
    polyline(sheet, inner, 3.5, bright, 0.9)


def bird(sheet, x, y, size=9):
    """One small dark 'v' bird silhouette."""
    ink = hex_rgb("#1a204e")
    polyline(sheet, [(x - size, y + size * 0.35), (x, y),
                     (x + size, y + size * 0.35)], 3, ink, 0.9)


# ------------------------------------------------------- vector capitals
# Geometric capitals drawn as polylines on a 6-wide by 10-tall grid.
# Each glyph is a list of strokes; a stroke is a list of (x, y) points.

GLYPHS = {
    "A": [[(0, 9), (2.5, 0), (5, 9)], [(1.2, 6), (3.8, 6)]],
    "B": [[(0, 0), (0, 9)],
          [(0, 0), (3.6, 0), (5, 1.2), (5, 3), (3.8, 4.2), (0, 4.2)],
          [(0, 4.2), (3.8, 4.2), (5, 5.4), (5, 7.6), (3.8, 9), (0, 9)]],
    "C": [[(5, 1.2), (3.8, 0), (1.2, 0), (0, 1.2), (0, 7.8),
           (1.2, 9), (3.8, 9), (5, 7.8)]],
    "D": [[(0, 0), (0, 9), (3, 9), (5, 7), (5, 2), (3, 0), (0, 0)]],
    "E": [[(0, 0), (0, 9), (5, 9)], [(0, 0), (5, 0)],
          [(0, 4.5), (3.8, 4.5)]],
    "F": [[(0, 0), (0, 9)], [(0, 0), (5, 0)], [(0, 4.5), (3.8, 4.5)]],
    "G": [[(5, 1.2), (3.8, 0), (1.2, 0), (0, 1.2), (0, 7.8),
           (1.2, 9), (3.8, 9), (5, 7.8), (5, 4.8)],
          [(3, 4.8), (5, 4.8)]],
    "H": [[(0, 0), (0, 9)], [(5, 0), (5, 9)], [(0, 4.5), (5, 4.5)]],
    "I": [[(0, 0), (5, 0)], [(2.5, 0), (2.5, 9)], [(0, 9), (5, 9)]],
    "L": [[(0, 0), (0, 9), (5, 9)]],
    "M": [[(0, 9), (0, 0), (2.5, 4.2), (5, 0), (5, 9)]],
    "N": [[(0, 9), (0, 0), (5, 9), (5, 0)]],
    "O": [[(1.2, 0), (3.8, 0), (5, 1.2), (5, 7.8), (3.8, 9),
           (1.2, 9), (0, 7.8), (0, 1.2), (1.2, 0)]],
    "P": [[(0, 0), (0, 9)],
          [(0, 0), (3.8, 0), (5, 1.2), (5, 3), (3.8, 4.2), (0, 4.2)]],
    "R": [[(0, 0), (0, 9)],
          [(0, 0), (3.8, 0), (5, 1.2), (5, 3), (3.8, 4.2), (0, 4.2)],
          [(2.8, 4.2), (5, 9)]],
    "S": [[(5, 1.2), (3.8, 0), (1.2, 0), (0, 1.2), (0, 3.2),
           (1.2, 4.5), (3.8, 4.5), (5, 5.8), (5, 7.8), (3.8, 9),
           (1.2, 9), (0, 7.8)]],
    "T": [[(0, 0), (5, 0)], [(2.5, 0), (2.5, 9)]],
    "U": [[(0, 0), (0, 7.8), (1.2, 9), (3.8, 9), (5, 7.8), (5, 0)]],
    "V": [[(0, 0), (2.5, 9), (5, 0)]],
    "W": [[(0, 0), (1.2, 9), (2.5, 4.8), (3.8, 9), (5, 0)]],
    "Y": [[(0, 0), (2.5, 4.2), (5, 0)], [(2.5, 4.2), (2.5, 9)]],
    " ": [],
    ".": [[(2.5, 8.8)]],
    "-": [[(1, 4.5), (4, 4.5)]],
}


def _glyph(sheet, ch, ox, oy, unit, width, rgb, alpha):
    """Draw one glyph with its top-left cell at (ox, oy)."""
    for pts in GLYPHS.get(ch, []):
        scaled = [(ox + px * unit, oy + py * unit) for px, py in pts]
        if len(scaled) == 1:
            disc(sheet, scaled[0][0], scaled[0][1], width * 0.55,
                 rgb, alpha)
        else:
            polyline(sheet, scaled, width, rgb, alpha)


def text_width(text, height, track):
    """Total pixel width of `text` at the given cap height."""
    unit = height / 10.0
    step = 6.0 * unit * track
    return len(text) * step - 6.0 * unit * (track - 1.0)


def draw_text(sheet, text, cx, y, height, track, rgb, shadow=None,
              width_factor=0.11):
    """Centre `text` at (cx, y); optional (dx, dy, colour, alpha) shadow."""
    unit = height / 10.0
    step = 6.0 * unit * track
    total = text_width(text, height, track)
    width = max(2.0, height * width_factor)
    x = cx - total / 2.0
    for ch in text:
        if shadow is not None:
            sdx, sdy, scol, salpha = shadow
            _glyph(sheet, ch, x + sdx, y + sdy, unit, width, scol, salpha)
        _glyph(sheet, ch, x, y, unit, width, rgb, 1.0)
        x += step
    return total


def diamond(sheet, cx, cy, r, rgb, alpha=1.0):
    polygon(sheet, [(cx, cy - r), (cx + r, cy), (cx, cy + r),
                    (cx - r, cy)], rgb, alpha)


# ------------------------------------------------------------- the scene

HORIZON = 640
CREAM = hex_rgb("#fdf3d8")
INDIGO = hex_rgb("#1a204e")


def paint_sky(sheet):
    stops = [
        (0.00, hex_rgb("#1a204e")),
        (0.18, hex_rgb("#423a7a")),
        (0.36, hex_rgb("#7e4a80")),
        (0.52, hex_rgb("#c4666c")),
        (0.68, hex_rgb("#e88d68")),
        (0.84, hex_rgb("#f7b267")),
        (1.00, hex_rgb("#ffd08a")),
    ]
    vgrad(sheet, 0, HORIZON, stops, dither=True)


def paint_stars(sheet):
    for i in range(18):
        x = 60 + int(pseudo(i, 7) * 900)
        y = 40 + int(pseudo(i, 13) * 170)
        r = 1 if pseudo(i, 3) < 0.6 else 2
        disc(sheet, x, y, r, CREAM, 0.9 - y / 300.0)


def paint_sun(sheet):
    cx, cy = 718, 512
    glow = hex_rgb("#ffbe6e")
    for i in range(20):
        f = i / 19.0
        r = 305 - f * (305 - 97)
        disc(sheet, cx, cy, r, glow, 0.025 + f * 0.17)
    disc(sheet, cx, cy, 86, hex_rgb("#ffedb8"))
    disc(sheet, cx, cy, 58, hex_rgb("#fff6d8"))


def paint_clouds(sheet):
    cloud(sheet, 210, 300, 300, hex_rgb("#b08bb8"), 0.85)
    cloud(sheet, 850, 245, 280, hex_rgb("#f2a988"), 0.85)
    cloud(sheet, 560, 232, 220, hex_rgb("#d97a7e"), 0.85)
    cloud(sheet, 540, 482, 230, hex_rgb("#ffe9c4"), 0.7)
    cloud(sheet, 905, 598, 260, hex_rgb("#e8907f"), 0.8)


def paint_distant_isles(sheet):
    mauve = hex_rgb("#5a3a5e")
    polygon(sheet, [(55, HORIZON), (118, 600), (185, 614), (252, HORIZON)],
            mauve)
    polygon(sheet, [(862, HORIZON), (918, 606), (988, HORIZON)], mauve)


def paint_sea(sheet):
    stops = [(0.0, hex_rgb("#2c6a7a")), (1.0, hex_rgb("#081c30"))]
    vgrad(sheet, HORIZON, H, stops, dither=False)
    gold = hex_rgb("#f7b267")
    sheet.hline(0, W - 1, HORIZON - 1, gold, 0.9)
    sheet.hline(0, W - 1, HORIZON, gold, 0.7)


def paint_sun_path(sheet):
    gold = hex_rgb("#ffd08a")
    bright = hex_rgb("#ffdf9e")
    for i in range(44):
        y = 652 + i * 6
        half = 30 + i * 4.2
        alpha = 0.5 * (1.0 - i / 44.0) + 0.06
        sheet.hline(718 - half * 0.5, 718 + half * 0.5, y, bright,
                    alpha * 0.7)
        for k in range(4):
            cx = 718 + (pseudo(i, k) - 0.5) * 2 * half * 0.9
            ln = 8 + pseudo(k, i) * 26
            sheet.hline(cx - ln, cx + ln, y, gold, alpha)


def floating_isle(sheet, cx, cy, rx, ry, depth, trees):
    """One floating island: rock underside, grassy mesa, trees."""
    rock = hex_rgb("#6e4a38")
    dark = hex_rgb("#54382c")
    light = hex_rgb("#7d5640")
    grass = hex_rgb("#478e4e")
    top = hex_rgb("#62b968")
    polygon(sheet, [(cx - rx, cy), (cx + rx, cy), (cx, cy + depth)], rock)
    polygon(sheet, [(cx - rx, cy), (cx, cy + depth), (cx, cy)], dark)
    polygon(sheet, [(cx, cy), (cx + rx, cy), (cx, cy + depth)], light)
    ellipse(sheet, cx, cy, rx, ry, grass)
    ellipse(sheet, cx, cy - ry * 0.25, rx * 0.97, ry * 0.72, top)
    for tx, ts in trees:
        tree(sheet, tx, cy - ry * 0.4, ts)


def paint_islands(sheet):
    floating_isle(sheet, 150, 726, 62, 12, 76, [(150, 0.7)])
    floating_isle(sheet, 838, 668, 96, 15, 124, [(838, 0.8)])
    floating_isle(sheet, 400, 600, 202, 26, 195,
                  [(288, 1.0), (330, 0.85), (512, 0.9)])
    waterfall(sheet, 262, 612, 898, 0.0)
    waterfall(sheet, 540, 612, 898, 2.1)
    waterfall(sheet, 800, 678, 878, 1.2)


def paint_mist(sheet):
    mist = hex_rgb("#f7ead0")
    rose = hex_rgb("#e8b48f")
    peach = hex_rgb("#f2c9a0")
    ellipse(sheet, 400, 908, 360, 16, mist, 0.8)
    ellipse(sheet, 700, 918, 240, 12, peach, 0.7)
    ellipse(sheet, 170, 916, 200, 11, rose, 0.7)
    ellipse(sheet, 850, 903, 160, 9, CREAM, 0.65)


def paint_lighthouse(sheet):
    lamp = (400, 449)
    beam = hex_rgb("#ffd9a0")
    for reach, alpha in ((360, 0.10), (240, 0.14), (140, 0.18)):
        polygon(sheet, [lamp, (400 + reach, 449 - 29),
                        (400 + reach, 449 + 29)], beam, alpha)
        polygon(sheet, [lamp, (400 - reach, 449 - 29),
                        (400 - reach, 449 + 29)], beam, alpha)
    # Tapered white tower, base y=600 width 46, top y=470 width 30.
    white = hex_rgb("#fdf6e3")
    polygon(sheet, [(377, 600), (423, 600), (415, 470), (385, 470)], white)

    def band(y0, y1, rgb):
        def hw(y):
            return (30 + (600 - y) / 130.0 * 16) / 2.0
        polygon(sheet, [(400 - hw(y0), y0), (400 + hw(y0), y0),
                        (400 + hw(y1), y1), (400 - hw(y1), y1)], rgb)

    red = hex_rgb("#c0392b")
    band(500, 522, red)
    band(542, 564, red)
    # Gallery, lamp room and dome.
    dark = hex_rgb("#2e2a4a")
    polygon(sheet, [(382, 470), (418, 470), (418, 458), (382, 458)], dark)
    disc(sheet, 400, 449, 24, hex_rgb("#ffe9a8"), 0.3)
    disc(sheet, 400, 449, 14, hex_rgb("#ffe9a8"), 0.45)
    polygon(sheet, [(388, 458), (412, 458), (412, 440), (388, 440)],
            hex_rgb("#ffd76e"))
    ellipse(sheet, 400, 441, 13, 7, red)
    disc(sheet, 400, 434, 3, dark)


def paint_birds(sheet):
    for x, y, s in ((260, 360, 9), (310, 395, 8), (560, 330, 9),
                    (620, 375, 8), (880, 400, 9), (180, 455, 8)):
        bird(sheet, x, y, s)


def paint_frame(sheet):
    for inset, thick in ((26, 3), (38, 1)):
        for off in range(thick):
            i = inset + off
            sheet.hline(i, W - 1 - i, i, CREAM, 0.95)
            sheet.hline(i, W - 1 - i, H - 1 - i, CREAM, 0.95)
            sheet.vline(i, i, H - 1 - i, CREAM, 0.95)
            sheet.vline(W - 1 - i, i, H - 1 - i, CREAM, 0.95)


def paint_type(sheet):
    shadow = (4, 5, INDIGO, 0.45)
    draw_text(sheet, "AETHERIA", 512, 78, 104, 1.6, CREAM, shadow, 0.15)
    tag = "THE ISLANDS THAT FLOAT ABOVE THE SEA"
    wide = draw_text(sheet, tag, 512, 943, 30, 1.1, CREAM,
                     width_factor=0.13)
    diamond(sheet, 512 - wide / 2 - 30, 943 + 15, 9, CREAM)
    diamond(sheet, 512 + wide / 2 + 30, 943 + 15, 9, CREAM)


def render():
    sheet = Sheet(W, H)
    paint_sky(sheet)
    paint_stars(sheet)
    paint_sun(sheet)
    paint_clouds(sheet)
    paint_distant_isles(sheet)
    paint_sea(sheet)
    paint_sun_path(sheet)
    paint_islands(sheet)
    paint_mist(sheet)
    paint_lighthouse(sheet)
    paint_birds(sheet)
    paint_frame(sheet)
    paint_type(sheet)
    return sheet


def main():
    sheet = render()
    try:
        os.makedirs("/output", exist_ok=True)
        path = OUT_PRIMARY
        size = write_png(sheet, path)
    except OSError:
        os.makedirs(os.path.dirname(OUT_FALLBACK), exist_ok=True)
        path = OUT_FALLBACK
        size = write_png(sheet, path)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    print(f"wrote {path} {size} bytes sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
