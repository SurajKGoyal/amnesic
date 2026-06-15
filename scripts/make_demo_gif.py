#!/usr/bin/env python3
"""
Render the README demo as a looping GIF (for LinkedIn / Dev.to, which don't
animate SVG). Draws each frame directly with Pillow — no cairo/ffmpeg/imagemagick
needed. Frames accumulate the conversation, then hold, then loop.

Usage:  python scripts/make_demo_gif.py  ->  assets/demo.gif
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SC = 2                      # supersample, then downscale for crisp text
W, H = 720, 580
ARROW = "->"               # ASCII arrow (guaranteed to render)

# ---- colors (RGB) ----
C = {
    "page":   (10, 12, 16),
    "frame":  (20, 22, 28),
    "fstroke":(42, 47, 58),
    "bar":    (27, 30, 38),
    "title":  (230, 237, 243),
    "muted":  (139, 148, 158),
    "purple": (185, 168, 245),
    "ubub":   (32, 36, 46),
    "ustroke":(44, 49, 61),
    "abub":   (26, 31, 43),
    "astroke":(47, 58, 77),
    "utxt":   (201, 209, 217),
    "atxt":   (205, 214, 227),
    "ai":     (88, 166, 255),
    "ok":     (63, 185, 80),
    "hl":     (255, 209, 102),
    "chip":   (42, 33, 80),
    "cstroke":(125, 106, 217),
    "arrow":  (90, 98, 112),
    "divln":  (35, 39, 47),
    "dot_r":  (255, 95, 87),
    "dot_y":  (254, 188, 46),
    "dot_g":  (40, 200, 64),
}

_FONTS = {
    "reg": "/System/Library/Fonts/Supplemental/Arial.ttf",
    "bold": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "mono": "/System/Library/Fonts/Menlo.ttc",
}
_cache: dict = {}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    if key not in _cache:
        _cache[key] = ImageFont.truetype(_FONTS[kind], size * SC)
    return _cache[key]


def s(v: float) -> int:
    return int(round(v * SC))


def rrect(d, box, radius, fill=None, outline=None, width=1, corners=None):
    x0, y0, x1, y1 = [s(v) for v in box]
    kwargs = dict(radius=s(radius), fill=fill, outline=outline, width=max(1, s(width)))
    if corners is not None:
        kwargs["corners"] = corners
    d.rounded_rectangle((x0, y0, x1, y1), **kwargs)


def segs(d, x, ycenter, parts):
    """Draw [(text, color, (kind,size)), ...] left-to-right, vertically centred.
    Returns the ending x (in SC pixels) so a vector glyph can follow."""
    xs = s(x)
    yc = s(ycenter)
    for text, color, (kind, size) in parts:
        f = font(kind, size)
        d.text((xs, yc), text, font=f, fill=color, anchor="lm")
        xs += f.getlength(text)
    return xs


def check(d, x_px, yc_px, color, sz=7):
    """Draw a checkmark as vectors (Arial lacks the ✓ glyph)."""
    z = s(sz)
    d.line(
        [(x_px, yc_px), (x_px + z * 0.7, yc_px + z * 0.85), (x_px + z * 1.8, yc_px - z * 0.95)],
        fill=color, width=max(2, s(2)), joint="curve",
    )


def center(d, cx, ycenter, parts):
    """Centre a multi-segment line horizontally on cx."""
    total = sum(font(k, sz).getlength(t) for t, _, (k, sz) in parts)
    xs = s(cx) - total / 2
    yc = s(ycenter)
    for text, color, (kind, size) in parts:
        f = font(kind, size)
        d.text((xs, yc), text, font=f, fill=color, anchor="lm")
        xs += f.getlength(text)


def base(d):
    """Always-visible chrome: frame, title bar, dots, title, architecture chain."""
    d.rectangle((0, 0, s(W), s(H)), fill=C["page"])
    rrect(d, (8, 8, 712, 572), 14, fill=C["frame"], outline=C["fstroke"], width=1.5)
    rrect(d, (8, 8, 712, 48), 14, fill=C["bar"], corners=(True, True, False, False))
    for cx, col in ((30, "dot_r"), (48, "dot_y"), (66, "dot_g")):
        d.ellipse((s(cx - 5), s(23), s(cx + 5), s(33)), fill=C[col])
    center(d, 360, 28, [("amnesic — SQL memory for your AI", C["title"], ("bold", 14))])
    center(d, 360, 68, [
        ("You", C["muted"], ("bold", 12)),
        (f"   {ARROW}   ", C["arrow"], ("reg", 12)),
        ("AI agent", C["ai"], ("bold", 12)),
        (f"   {ARROW}   ", C["arrow"], ("reg", 12)),
        ("amnesic", C["purple"], ("bold", 12)),
        (" (MCP) ", C["muted"], ("reg", 12)),
        (f"  {ARROW}   ", C["arrow"], ("reg", 12)),
        ("your DB", C["ok"], ("bold", 12)),
    ])
    d.line((s(40), s(84), s(680), s(84)), fill=C["divln"], width=max(1, s(1)))


def bullet(d, cx, cy, color):
    d.ellipse((s(cx - 3), s(cy - 3), s(cx + 3), s(cy + 3)), fill=color)


# ---- conversation steps (each draws onto the frame; they accumulate) ----

def step_s1(d):
    bullet(d, 36, 100, C["purple"])
    segs(d, 44, 100, [("SESSION 1  ·  MONDAY", C["purple"], ("bold", 12))])

def step_u1(d):
    rrect(d, (32, 114, 620, 148), 9, fill=C["ubub"], outline=C["ustroke"])
    segs(d, 48, 131, [("You   ", C["muted"], ("bold", 14)),
                      ("give me all orders where ", C["utxt"], ("reg", 14)),
                      ("status = 3", C["hl"], ("mono", 13))])

def step_a1(d):
    rrect(d, (80, 156, 620, 190), 9, fill=C["abub"], outline=C["astroke"])
    segs(d, 96, 173, [("AI   ", C["ai"], ("bold", 13)),
                      ("amnesic·db_query", C["purple"], ("mono", 12)),
                      (f"   orders WHERE status = 3   {ARROW}  42 rows", C["atxt"], ("reg", 13))])

def step_u2(d):
    rrect(d, (32, 198, 620, 232), 9, fill=C["ubub"], outline=C["ustroke"])
    segs(d, 48, 215, [("You   ", C["muted"], ("bold", 14)),
                      ("status 3 means a ", C["utxt"], ("reg", 14)),
                      ("cancelled order", C["hl"], ("bold", 14)),
                      (" — save it", C["utxt"], ("reg", 14))])

def step_a2(d):
    rrect(d, (80, 240, 620, 274), 9, fill=C["abub"], outline=C["astroke"])
    x = segs(d, 96, 257, [("AI   ", C["ai"], ("bold", 13)),
                          ("amnesic·db_annotate", C["purple"], ("mono", 12)),
                          ("   status: ", C["atxt"], ("reg", 13)),
                          ('3 ' + ARROW + ' "cancelled"   ', C["hl"], ("bold", 13))])
    check(d, x, s(257), C["ok"])

def step_chip(d):
    rrect(d, (256, 284, 464, 314), 15, fill=C["chip"], outline=C["cstroke"], width=1.5)
    center(d, 360, 299, [("saved in amnesic's memory", C["purple"], ("bold", 12))])

def step_div(d):
    d.line((s(40), s(334), s(300), s(334)), fill=C["fstroke"], width=max(1, s(1)))
    d.line((s(420), s(334), s(680), s(334)), fill=C["fstroke"], width=max(1, s(1)))
    center(d, 360, 334, [("NEW SESSION  ·  DAYS LATER", C["muted"], ("reg", 11))])

def step_s2(d):
    bullet(d, 36, 364, C["purple"])
    segs(d, 44, 364, [("SESSION 2  ·  THURSDAY   ·   ", C["purple"], ("bold", 12)),
                      ("fresh start, nothing re-explained", C["muted"], ("reg", 12))])

def step_u3(d):
    rrect(d, (32, 380, 620, 414), 9, fill=C["ubub"], outline=C["ustroke"])
    segs(d, 48, 397, [("You   ", C["muted"], ("bold", 14)),
                      ("give me all ", C["utxt"], ("reg", 14)),
                      ("cancelled", C["hl"], ("bold", 14)),
                      (" orders", C["utxt"], ("reg", 14))])

def step_a3(d):
    rrect(d, (80, 422, 620, 480), 9, fill=C["abub"], outline=C["astroke"])
    segs(d, 96, 440, [("AI   ", C["ai"], ("bold", 13)),
                      ("amnesic·db_search", C["purple"], ("mono", 12)),
                      (f"   {ARROW}   ", C["atxt"], ("reg", 13)),
                      ('"cancelled" = status 3', C["hl"], ("bold", 13))])
    x = segs(d, 96, 462, [("amnesic·db_query", C["purple"], ("mono", 12)),
                          (f"   WHERE status = 3   {ARROW}   ", C["atxt"], ("reg", 13)),
                          ("same 42 rows  ", C["ok"], ("bold", 13))])
    check(d, x, s(462), C["ok"])

def step_tag(d):
    center(d, 360, 516, [("Teach it once.  ", C["title"], ("bold", 16)),
                         ("Query in plain language forever.", C["purple"], ("bold", 16))])
    center(d, 360, 540, [("your AI carries the knowledge between sessions — via amnesic",
                          C["muted"], ("reg", 11))])


# step, hold-duration-ms
STEPS = [
    (None,      900),
    (step_s1,   650),
    (step_u1,   1200),
    (step_a1,   1300),
    (step_u2,   1200),
    (step_a2,   1300),
    (step_chip, 1000),
    (step_div,  800),
    (step_s2,   900),
    (step_u3,   1200),
    (step_a3,   1700),
    (step_tag,  2600),
]


def build():
    frames, durations, drawn = [], [], []
    for fn, dur in STEPS:
        if fn is not None:
            drawn.append(fn)
        img = Image.new("RGB", (s(W), s(H)), C["page"])
        d = ImageDraw.Draw(img)
        base(d)
        for f in drawn:
            f(d)
        frames.append(img.resize((W, H), Image.LANCZOS))
        durations.append(dur)

    # one shared palette derived from the fullest frame → no inter-frame flicker
    pal = frames[-1].convert("P", palette=Image.ADAPTIVE, colors=256)
    pframes = [f.quantize(palette=pal, dither=Image.NONE) for f in frames]

    out = Path(__file__).resolve().parent.parent / "assets" / "demo.gif"
    pframes[0].save(
        out, save_all=True, append_images=pframes[1:],
        duration=durations, loop=0, optimize=True, disposal=2,
    )
    print(f"wrote {out}  ({out.stat().st_size // 1024} KB, {len(pframes)} frames)")


if __name__ == "__main__":
    build()
