#!/usr/bin/env python3
"""AETHERIA -- a travel poster for a place that does not exist.

Entry for "The Poster for a Place That Isn't There" (make.fun).

The place: AETHERIA -- a chain of green-topped islands that float in a
golden sunset sky above the sea, waterfalls pouring off their edges into
the clouds below. The landmark is a white lighthouse with red bands on the
largest isle, its beam sweeping toward the low sun.

Everything is drawn in code with the Python standard library only (no
network, no third-party packages), so the poster renders identically in
the grading sandbox. Running `python3 main.py` writes /output/entry.png
(1024 x 1024 RGB PNG, deterministic bytes on repeat runs).
"""

import hashlib
import math
import os
import struct
import zlib

W = H = 1024
HORIZON = 640


# ------------------------------------------------------------------ canvas

class Canvas:
    """An RGB raster held in one bytearray, with small drawing helpers."""

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.px = bytearray(w * h * 3)

    def hline(self, x0, x1, y, color, alpha=1.0):
        """Fill the horizontal span [x0, x1) of row y with a color."""
        y = int(y)
        if y < 0 or y >= self.h:
            return
        x0 = max(0, int(math.floor(x0)))
        x1 = min(self.w, int(math.ceil(x1)))
        if x1 <= x0:
            return
        r, g, b = color
        base = (y * self.w + x0) * 3
        n = x1 - x0
        if alpha >= 1.0:
            self.px[base:base + n * 3] = bytes((r, g, b)) * n
        elif alpha <= 0.0:
            return
        else:
            px = self.px
            ia = 1.0 - alpha
            for i in range(n):
                o = base + i * 3
                px[o] = int(px[o] * ia + r * alpha)
                px[o + 1] = int(px[o + 1] * ia + g * alpha)
                px[o + 2] = int(px[o + 2] * ia + b * alpha)

    def vgrad(self, y0, y1, stops, dither=False):
        """Paint a vertical gradient between (pos, color) stops, pos in 0..1.
        With dither=True a deterministic +/-1 noise on the green channel
        breaks the 8-bit banding of smooth gradients."""
        y0 = max(0, int(y0))
        y1 = min(self.h, int(y1))
        for y in range(y0, y1):
            t = (y - y0) / max(1, (y1 - y0 - 1))
            c = _lerp_stops(stops, t)
            base = y * self.w * 3
            self.px[base:base + self.w * 3] = bytes(c) * self.w
            if dither:
                px = self.px
                for x in range(self.w):
                    n = ((x * 2654435761) ^ (y * 40503)) & 0xFF
                    if n < 128:
                        o = base + x * 3 + 1
                        px[o] = px[o] + 1 if n < 64 else px[o] - 1

    def disc(self, cx, cy, r, color, alpha=1.0):
        if r <= 0:
            return
        r = float(r)
        y0 = max(0, int(math.floor(cy - r)))
        y1 = min(self.h - 1, int(math.ceil(cy + r)))
        for y in range(y0, y1 + 1):
            dy = y - cy
            dx = math.sqrt(max(0.0, r * r - dy * dy))
            self.hline(cx - dx, cx + dx + 1, y, color, alpha)

    def ellipse(self, cx, cy, rx, ry, color, alpha=1.0):
        if rx <= 0 or ry <= 0:
            return
        y0 = max(0, int(math.floor(cy - ry)))
        y1 = min(self.h - 1, int(math.ceil(cy + ry)))
        for y in range(y0, y1 + 1):
            t = (y - cy) / ry
            dx = rx * math.sqrt(max(0.0, 1.0 - t * t))
            self.hline(cx - dx, cx + dx + 1, y, color, alpha)

    def polygon(self, pts, color, alpha=1.0):
        """Scanline fill of a simple polygon given as [(x, y), ...]."""
        if len(pts) < 3:
            return
        ys = [p[1] for p in pts]
        y0 = max(0, int(math.floor(min(ys))))
        y1 = min(self.h - 1, int(math.ceil(max(ys))))
        n = len(pts)
        for y in range(y0, y1 + 1):
            xs = []
            yc = y + 0.5
            for i in range(n):
                x1, y1p = pts[i]
                x2, y2p = pts[(i + 1) % n]
                if (y1p <= yc < y2p) or (y2p <= yc < y1p):
                    t = (yc - y1p) / (y2p - y1p)
                    xs.append(x1 + t * (x2 - x1))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                self.hline(xs[i], xs[i + 1] + 1, y, color, alpha)

    def thick_line(self, x0, y0, x1, y1, width, color, alpha=1.0):
        """A stroked segment with round caps: a quad plus end discs."""
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 0.001:
            self.disc(x0, y0, width / 2.0, color, alpha)
            return
        nx, ny = -dy / length, dx / length
        hw = width / 2.0
        self.polygon([
            (x0 + nx * hw, y0 + ny * hw),
            (x1 + nx * hw, y1 + ny * hw),
            (x1 - nx * hw, y1 - ny * hw),
            (x0 - nx * hw, y0 - ny * hw),
        ], color, alpha)
        self.disc(x0, y0, hw, color, alpha)
        self.disc(x1, y1, hw, color, alpha)


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _lerp_stops(stops, t):
    if t <= stops[0][0]:
        return stops[0][1]
    for i in range(1, len(stops)):
        if t <= stops[i][0]:
            p0, c0 = stops[i - 1]
            p1, c1 = stops[i]
            return _lerp(c0, c1, (t - p0) / max(1e-9, p1 - p0))
    return stops[-1][1]


# ------------------------------------------------------------------ font
# Hand-built geometric capitals on a 6 x 10 grid. Each glyph is a list of
# strokes; each stroke is a polyline of (x, y) points, y=0 at the cap top.

GLYPHS = {
    'A': [[(0, 10), (3, 0), (6, 10)], [(1.3, 6.6), (4.7, 6.6)]],
    'B': [[(0, 0), (0, 10), (3.8, 10), (5.6, 8.4), (5.6, 6.6), (3.8, 5), (0, 5)],
          [(0, 0), (3.6, 0), (5.4, 1.4), (5.4, 3.6), (3.6, 5), (0, 5)]],
    'C': [[(5.6, 1.6), (4, 0), (1.6, 0), (0, 1.6), (0, 8.4), (1.6, 10), (4, 10), (5.6, 8.4)]],
    'D': [[(0, 0), (0, 10), (3, 10), (5.6, 8), (5.6, 2), (3, 0), (0, 0)]],
    'E': [[(5.6, 0), (0, 0), (0, 10), (5.6, 10)], [(0, 5), (4, 5)]],
    'F': [[(5.6, 0), (0, 0), (0, 10)], [(0, 5), (4, 5)]],
    'G': [[(5.6, 1.6), (4, 0), (1.6, 0), (0, 1.6), (0, 8.4), (1.6, 10), (4, 10), (6, 10), (6, 6), (3.4, 6)]],
    'H': [[(0, 0), (0, 10)], [(6, 0), (6, 10)], [(0, 5), (6, 5)]],
    'I': [[(3, 0), (3, 10)], [(1, 0), (5, 0)], [(1, 10), (5, 10)]],
    'J': [[(4.6, 0), (4.6, 8), (3, 10), (1.2, 10), (0, 8.2)]],
    'K': [[(0, 0), (0, 10)], [(5.6, 0), (0, 5), (5.6, 10)]],
    'L': [[(0, 0), (0, 10), (5.6, 10)]],
    'M': [[(0, 10), (0, 0), (3, 5.4), (6, 0), (6, 10)]],
    'N': [[(0, 10), (0, 0), (6, 10), (6, 0)]],
    'O': [[(1.6, 0), (4.4, 0), (6, 1.6), (6, 8.4), (4.4, 10), (1.6, 10), (0, 8.4), (0, 1.6), (1.6, 0)]],
    'P': [[(0, 10), (0, 0), (3.8, 0), (5.8, 1.6), (5.8, 3.6), (3.8, 5.2), (0, 5.2)]],
    'Q': [[(1.6, 0), (4.4, 0), (6, 1.6), (6, 8.4), (4.4, 10), (1.6, 10), (0, 8.4), (0, 1.6), (1.6, 0)],
          [(3.8, 6.8), (6.2, 10)]],
    'R': [[(0, 10), (0, 0), (3.8, 0), (5.8, 1.6), (5.8, 3.6), (3.8, 5.2), (0, 5.2)],
          [(3.4, 5.2), (6, 10)]],
    'S': [[(5.6, 1.6), (4, 0), (2, 0), (0.4, 1.6), (0.4, 3.4), (2, 5), (4, 5), (5.6, 6.6), (5.6, 8.4), (4, 10), (2, 10), (0.4, 8.4)]],
    'T': [[(0, 0), (6, 0)], [(3, 0), (3, 10)]],
    'U': [[(0, 0), (0, 8), (1.6, 10), (4.4, 10), (6, 8), (6, 0)]],
    'V': [[(0, 0), (3, 10), (6, 0)]],
    'W': [[(0, 0), (1.5, 10), (3, 4), (4.5, 10), (6, 0)]],
    'X': [[(0, 0), (6, 10)], [(6, 0), (0, 10)]],
    'Y': [[(0, 0), (3, 4.6), (6, 0)], [(3, 4.6), (3, 10)]],
    'Z': [[(0, 0), (6, 0), (0, 10), (6, 10)]],
    ' ': [],
    '.': [[(2.6, 9), (3.4, 9), (3.4, 10), (2.6, 10)]],
    '-': [[(0.8, 5), (5.2, 5)]],
}


def text_width(text, height, tracking=0.0):
    unit = height / 10.0
    adv = 6.0 + tracking
    return (len(text) * adv - tracking) * unit


def draw_text(cv, text, cx, top, height, color, tracking=0.0, stroke=None, alpha=1.0):
    """Draw centred capitals. stroke = stroke width in grid units."""
    unit = height / 10.0
    sw = (stroke if stroke is not None else 1.15) * unit
    total = text_width(text, height, tracking)
    x = cx - total / 2.0
    for ch in text:
        glyph = GLYPHS.get(ch.upper())
        if glyph:
            for stroke_pts in glyph:
                for i in range(len(stroke_pts) - 1):
                    x0 = x + stroke_pts[i][0] * unit
                    y0 = top + stroke_pts[i][1] * unit
                    x1 = x + stroke_pts[i + 1][0] * unit
                    y1 = top + stroke_pts[i + 1][1] * unit
                    cv.thick_line(x0, y0, x1, y1, sw, color, alpha)
        x += (6.0 + tracking) * unit


# ------------------------------------------------------------------ palette

INK = (29, 35, 82)          # deep indigo, shadows and dark accents
CREAM = (253, 243, 216)     # poster cream
GOLD = (255, 214, 138)
SUN = (255, 237, 184)

SKY_STOPS = [
    (0.00, (26, 32, 78)),    # indigo
    (0.22, (66, 58, 122)),   # violet
    (0.42, (126, 74, 128)),  # mauve
    (0.60, (196, 102, 108)), # rose
    (0.76, (232, 141, 104)), # peach
    (0.90, (247, 178, 103)), # amber
    (1.00, (255, 208, 138)), # horizon gold
]
SEA_STOPS = [
    (0.00, (44, 106, 122)),  # teal
    (0.30, (28, 74, 96)),
    (0.65, (16, 48, 70)),
    (1.00, (8, 28, 48)),     # deep navy
]

GRASS = (63, 125, 78)
GRASS_LT = (87, 160, 95)
ROCK = (107, 74, 63)
ROCK_DK = (58, 42, 38)
ROCK_LT = (138, 98, 82)
TOWER = (248, 242, 226)
RED = (196, 69, 60)

CLOUD_PEACH = (246, 195, 154)
CLOUD_ROSE = (238, 157, 138)
CLOUD_LILAC = (176, 139, 184)
CLOUD_CREAM = (253, 232, 201)


def cloud(cv, cx, cy, w, h, color, alpha=0.9):
    """A soft flat-bottomed cloud made of overlapping ellipses."""
    cv.ellipse(cx, cy, w * 0.5, h * 0.42, color, alpha)
    cv.ellipse(cx - w * 0.28, cy + h * 0.10, w * 0.30, h * 0.30, color, alpha)
    cv.ellipse(cx + w * 0.30, cy + h * 0.12, w * 0.28, h * 0.28, color, alpha)
    cv.ellipse(cx - w * 0.10, cy - h * 0.16, w * 0.26, h * 0.26, color, alpha)
    cv.ellipse(cx + w * 0.16, cy - h * 0.10, w * 0.22, h * 0.22, color, alpha)


def tree(cv, x, y, s, color=(38, 92, 58)):
    """A small round-topped tree standing on the grass (base at y)."""
    cv.thick_line(x, y, x, y - 10 * s, 3 * s, (58, 42, 32))
    cv.disc(x, y - 15 * s, 9 * s, color)
    cv.disc(x - 7 * s, y - 10 * s, 6 * s, color)
    cv.disc(x + 7 * s, y - 10 * s, 6 * s, color)


def island(cv, cx, top_y, w, depth, grass=GRASS):
    """A floating island: grassy mesa top, rocky underside tapering to a point."""
    cv.polygon([
        (cx - w * 0.48, top_y + 6),
        (cx + w * 0.48, top_y + 6),
        (cx + w * 0.30, top_y + depth * 0.55),
        (cx + w * 0.10, top_y + depth * 0.88),
        (cx, top_y + depth),
        (cx - w * 0.12, top_y + depth * 0.82),
        (cx - w * 0.34, top_y + depth * 0.50),
    ], ROCK)
    # darker wedge on the right, lighter ridge on the left
    cv.polygon([
        (cx + w * 0.10, top_y + 6),
        (cx + w * 0.48, top_y + 6),
        (cx + w * 0.30, top_y + depth * 0.55),
        (cx + w * 0.10, top_y + depth * 0.88),
        (cx, top_y + depth),
    ], ROCK_DK, 0.55)
    cv.polygon([
        (cx - w * 0.48, top_y + 6),
        (cx - w * 0.20, top_y + 6),
        (cx - w * 0.30, top_y + depth * 0.45),
        (cx - w * 0.12, top_y + depth * 0.82),
    ], ROCK_LT, 0.45)
    # grassy top
    cv.ellipse(cx, top_y, w * 0.52, 22, grass)
    cv.ellipse(cx - w * 0.22, top_y - 4, w * 0.24, 14, GRASS_LT, 0.8)
    cv.ellipse(cx, top_y - 2, w * 0.50, 10, GRASS_LT, 0.55)


def waterfall(cv, x, y0, y1, w):
    """A thin waterfall pouring off an island edge, swaying as it falls,
    fading into a soft mist puff where it meets the clouds below."""
    steps = max(4, int((y1 - y0) / 6))
    for i in range(steps + 1):
        t = i / steps
        y = y0 + t * (y1 - y0)
        drift = math.sin(t * 9.0 + x * 0.05) * 5
        ww = w * (1.0 - t * 0.35)
        a = 0.85 * (1.0 - t * 0.25)
        for yy in range(int(y), int(y) + 6):
            cv.hline(x + drift - ww / 2, x + drift + ww / 2, yy, (226, 240, 244), a)
            cv.hline(x + drift - ww * 0.18, x + drift + ww * 0.18, yy,
                     (255, 255, 255), a * 0.8)
    cv.ellipse(x, y1 + 4, w * 3.2, 8, (253, 240, 220), 0.28)
    cv.ellipse(x, y1 + 9, w * 2.2, 6, (255, 250, 235), 0.25)


def lighthouse(cv, cx, base_y):
    """White tower with red bands, lamp room, and beams sweeping both ways."""
    # Beams first (behind the lamp glow, in front of the sky): stepped
    # triangles of decreasing length and alpha, so each beam fades with
    # distance instead of ending in a hard edge.
    for far, a in ((900, 0.10), (770, 0.14), (630, 0.18)):
        hw = 40 - (900 - far) * 0.06
        cv.polygon([(cx + 12, base_y - 138), (far, base_y - 132 - hw),
                    (far, base_y - 132 + hw)], (255, 226, 155), a)
    for far, a in ((44, 0.10), (170, 0.14), (300, 0.18)):
        hw = 38 - (far - 44) * 0.06
        cv.polygon([(cx - 12, base_y - 138), (far, base_y - 134 - hw),
                    (far, base_y - 134 + hw)], (255, 226, 155), a)
    # tower, slightly tapered
    cv.polygon([(cx - 20, base_y), (cx + 20, base_y), (cx + 14, base_y - 118),
                (cx - 14, base_y - 118)], TOWER)
    # red bands
    cv.polygon([(cx - 18, base_y - 34), (cx + 18, base_y - 34),
                (cx + 16, base_y - 58), (cx - 16, base_y - 58)], RED)
    cv.polygon([(cx - 15, base_y - 88), (cx + 15, base_y - 88),
                (cx + 14, base_y - 108), (cx - 14, base_y - 108)], RED)
    # gallery + lamp room + dome
    cv.polygon([(cx - 18, base_y - 118), (cx + 18, base_y - 118),
                (cx + 18, base_y - 124), (cx - 18, base_y - 124)], INK)
    cv.polygon([(cx - 11, base_y - 124), (cx + 11, base_y - 124),
                (cx + 11, base_y - 146), (cx - 11, base_y - 146)], (255, 214, 106))
    cv.polygon([(cx - 13, base_y - 146), (cx + 13, base_y - 146),
                (cx, base_y - 160)], RED)
    # lamp glow
    cv.disc(cx, base_y - 135, 26, (255, 224, 150), 0.35)
    cv.disc(cx, base_y - 135, 14, (255, 240, 190), 0.6)


def bird(cv, x, y, s):
    cv.thick_line(x - 8 * s, y, x, y - 5 * s, 2.2 * s, INK, 0.85)
    cv.thick_line(x, y - 5 * s, x + 8 * s, y, 2.2 * s, INK, 0.85)


# ------------------------------------------------------------------ scene

def render():
    cv = Canvas(W, H)

    # --- sky
    cv.vgrad(0, HORIZON, SKY_STOPS, dither=True)

    # --- stars, fading out as the sky warms toward the horizon
    stars = [(70, 60), (150, 130), (240, 55), (330, 100), (430, 45), (520, 90),
             (610, 50), (700, 110), (790, 60), (880, 120), (950, 70), (120, 220),
             (300, 200), (480, 170), (850, 210), (930, 280), (60, 300), (200, 330)]
    for (sx, sy) in stars:
        a = max(0.08, 0.75 * (1.0 - sy / 380.0))
        cv.disc(sx, sy, 2.2, CREAM, a)

    # --- sun + glow, low over the sea (many fine rings keep the glow smooth)
    sun_cx, sun_cy, sun_r = 718, 512, 86
    for i in range(20):
        t = i / 19.0
        r = 305 - t * 208  # 305 -> 97
        a = 0.025 + t * 0.17
        cv.disc(sun_cx, sun_cy, r, (255, 190, 110), a)
    cv.disc(sun_cx, sun_cy, sun_r, SUN)
    cv.disc(sun_cx, sun_cy, sun_r - 14, (255, 244, 205))

    # --- high thin clouds
    cloud(cv, 210, 300, 260, 34, CLOUD_LILAC, 0.55)
    cloud(cv, 830, 250, 300, 36, CLOUD_PEACH, 0.6)
    cloud(cv, 560, 232, 200, 26, CLOUD_ROSE, 0.45)

    # --- distant isles on the horizon (silhouettes)
    cv.polygon([(60, HORIZON), (150, 606), (210, 622), (300, HORIZON)], (94, 62, 96), 0.8)
    cv.polygon([(880, HORIZON), (930, 612), (990, 626), (1024, HORIZON)], (94, 62, 96), 0.8)

    # --- sea
    cv.vgrad(HORIZON, H, SEA_STOPS, dither=True)
    # thin gold line where the sea meets the sky
    cv.hline(40, 984, HORIZON, (255, 220, 150), 0.4)
    # sun path on the water: broken glittering streaks under the sun,
    # widening and fading as they run down toward the viewer
    for i in range(44):
        t = i / 43.0
        y = HORIZON + 14 + t * 330
        half = 34 + t * 130
        a = 0.5 * (1.0 - t * 0.72)
        jit = 20 * math.sin(i * 2.1)
        seg = half * 2 / 3.0
        cv.hline(sun_cx - half + jit, sun_cx - half + seg, y, GOLD, a)
        cv.hline(sun_cx - seg * 0.4 + jit, sun_cx + seg * 0.5 + jit, y, GOLD, a * 0.8)
        cv.hline(sun_cx + half - seg + jit, sun_cx + half + jit, y, GOLD, a * 0.6)
    # wave lines
    for i, y in enumerate(range(HORIZON + 26, H - 20, 34)):
        x0 = 60 + (i % 4) * 90
        cv.hline(x0, x0 + 130, y, (120, 180, 190), 0.16)
        cv.hline(700 + (i % 3) * 60, 700 + (i % 3) * 60 + 90, y + 14, (120, 180, 190), 0.12)

    # --- clouds drifting over the sun's lower edge
    cloud(cv, 620, 470, 320, 40, CLOUD_PEACH, 0.75)
    cloud(cv, 880, 560, 260, 36, CLOUD_ROSE, 0.7)

    # --- main floating island + lighthouse (the landmark)
    island(cv, 400, 600, 380, 190)
    tree(cv, 300, 596, 1.0)
    tree(cv, 500, 598, 0.85)
    tree(cv, 338, 598, 0.7)
    lighthouse(cv, 400, 600)
    waterfall(cv, 262, 612, 896, 16)
    waterfall(cv, 540, 612, 888, 11)

    # --- smaller islet, right
    island(cv, 838, 668, 190, 120)
    tree(cv, 838, 664, 0.8)
    waterfall(cv, 778, 676, 880, 9)

    # --- tiny islet, far left
    island(cv, 150, 726, 120, 80)
    tree(cv, 150, 722, 0.6)

    # --- mist band under the floating isles (kept inside the frame)
    cloud(cv, 400, 898, 460, 50, CLOUD_CREAM, 0.5)
    cloud(cv, 700, 928, 380, 44, CLOUD_PEACH, 0.45)
    cloud(cv, 180, 918, 260, 38, CLOUD_ROSE, 0.4)

    # --- birds
    bird(cv, 250, 380, 1.0)
    bird(cv, 300, 350, 0.8)
    bird(cv, 560, 330, 0.9)
    bird(cv, 620, 370, 0.7)
    bird(cv, 880, 400, 0.9)
    bird(cv, 180, 440, 0.7)

    # --- frame: double cream rule
    for inset, wd in ((26, 3), (38, 1)):
        cv.hline(inset, W - inset, inset, CREAM, 0.95)
        cv.hline(inset, W - inset, W - inset - 1, CREAM, 0.95)
        for y in range(inset, W - inset):
            cv.hline(inset, inset + wd, y, CREAM, 0.95)
            cv.hline(W - inset - wd, W - inset, y, CREAM, 0.95)

    # --- the place name, set large across the sky
    draw_text(cv, "AETHERIA", W // 2 + 4, 84, 104, INK, tracking=1.6, alpha=0.45)
    draw_text(cv, "AETHERIA", W // 2, 78, 104, CREAM, tracking=1.6)

    # --- one short tagline, low over the sea
    draw_text(cv, "THE ISLANDS THAT FLOAT ABOVE THE SEA", W // 2 + 2, 946, 30,
              INK, tracking=1.1, alpha=0.5)
    draw_text(cv, "THE ISLANDS THAT FLOAT ABOVE THE SEA", W // 2, 943, 30,
              CREAM, tracking=1.1)
    # small diamonds flanking the tagline
    tw = text_width("THE ISLANDS THAT FLOAT ABOVE THE SEA", 30, 1.1)
    for dx in (-1, 1):
        cx = W // 2 + dx * (tw / 2 + 34)
        cv.polygon([(cx, 948), (cx + 7, 956), (cx, 964), (cx - 7, 956)], CREAM)

    return cv


# ------------------------------------------------------------------ png

def write_png(path, cv):
    raw = bytearray()
    for y in range(cv.h):
        raw.append(0)  # filter: none
        raw += cv.px[y * cv.w * 3:(y + 1) * cv.w * 3]
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", cv.w, cv.h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", comp)
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return png


def main():
    cv = render()
    try:
        os.makedirs("/output", exist_ok=True)
        out = "/output/entry.png"
        png = write_png(out, cv)
    except OSError:
        os.makedirs("output", exist_ok=True)
        out = os.path.join("output", "entry.png")
        png = write_png(out, cv)
    print("wrote %s  %d bytes  sha256=%s" % (out, len(png),
          hashlib.sha256(png).hexdigest()))


if __name__ == "__main__":
    main()
