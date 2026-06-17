#!/usr/bin/env python3
"""
Render amnesic's two-layer read-only defense as a clean PNG diagram
(for the Medium article — Medium needs raster, not SVG).

Drawn directly with Pillow. Output: assets/defense-diagram.png
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SC = 2
W, H = 1200, 640
ARROW = "->"

C = {
    "page":   (10, 12, 16),
    "frame":  (20, 22, 28),
    "fstroke":(42, 47, 58),
    "title":  (230, 237, 243),
    "muted":  (139, 148, 158),
    "purple": (185, 168, 245),
    "gate":   (26, 22, 46),
    "gatestk":(75, 61, 134),
    "green":  (63, 185, 80),
    "greenbg":(20, 40, 26),
    "greenstk":(46, 92, 56),
    "red":    (255, 95, 87),
    "redbg":  (46, 24, 24),
    "redstk": (110, 52, 52),
    "blue":   (88, 166, 255),
    "mono_c": (216, 204, 255),
    "db":     (28, 33, 44),
    "dbstk":  (60, 74, 100),
    "yellow": (255, 209, 102),
    "dim":    (90, 98, 112),
}

_F = {
    "reg": "/System/Library/Fonts/Supplemental/Arial.ttf",
    "bold": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "mono": "/System/Library/Fonts/Menlo.ttc",
}
_cache = {}


def font(kind, size):
    k = (kind, size)
    if k not in _cache:
        _cache[k] = ImageFont.truetype(_F[kind], size * SC)
    return _cache[k]


def s(v): return int(round(v * SC))


def rrect(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle([s(box[0]), s(box[1]), s(box[2]), s(box[3])],
                        radius=s(r), fill=fill, outline=outline, width=max(1, s(width)))


def segs(d, x, yc, parts):
    xs = s(x)
    for t, col, (k, sz) in parts:
        f = font(k, sz)
        d.text((xs, s(yc)), t, font=f, fill=col, anchor="lm")
        xs += f.getlength(t)
    return xs


def center(d, cx, yc, parts):
    tot = sum(font(k, sz).getlength(t) for t, _, (k, sz) in parts)
    xs = s(cx) - tot / 2
    for t, col, (k, sz) in parts:
        f = font(k, sz)
        d.text((xs, s(yc)), t, font=f, fill=col, anchor="lm")
        xs += f.getlength(t)


def check(d, x, yc, color, sz=9):
    z = s(sz)
    d.line([(x, yc), (x + z * 0.7, yc + z * 0.85), (x + z * 1.8, yc - z * 0.95)],
           fill=color, width=max(2, s(2.5)), joint="curve")


def cross(d, cx, cy, color, sz=9):
    z = s(sz)
    d.line([(cx - z, cy - z), (cx + z, cy + z)], fill=color, width=max(2, s(2.5)))
    d.line([(cx - z, cy + z), (cx + z, cy - z)], fill=color, width=max(2, s(2.5)))


def arrow(d, x1, y, x2, color, dashed=False):
    y_ = s(y)
    if dashed:
        x = s(x1)
        while x < s(x2) - s(8):
            d.line([(x, y_), (min(x + s(7), s(x2) - s(8)), y_)], fill=color, width=max(1, s(1.5)))
            x += s(13)
    else:
        d.line([(s(x1), y_), (s(x2) - s(7), y_)], fill=color, width=max(2, s(2)))
    # arrowhead
    xe = s(x2)
    d.polygon([(xe, y_), (xe - s(8), y_ - s(5)), (xe - s(8), y_ + s(5))], fill=color)


def db_cylinder(d, cx, top, bot, w):
    x0, x1 = s(cx - w / 2), s(cx + w / 2)
    eh = s(16)
    d.ellipse([x0, s(top), x1, s(top) + 2 * eh], fill=C["db"], outline=C["dbstk"], width=max(1, s(1.5)))
    d.rectangle([x0, s(top) + eh, x1, s(bot)], fill=C["db"])
    d.line([(x0, s(top) + eh), (x0, s(bot))], fill=C["dbstk"], width=max(1, s(1.5)))
    d.line([(x1, s(top) + eh), (x1, s(bot))], fill=C["dbstk"], width=max(1, s(1.5)))
    d.ellipse([x0, s(bot) - eh, x1, s(bot) + eh], fill=C["db"], outline=C["dbstk"], width=max(1, s(1.5)))


def build():
    img = Image.new("RGB", (s(W), s(H)), C["page"])
    d = ImageDraw.Draw(img)
    rrect(d, (10, 10, W - 10, H - 10), 16, fill=C["frame"], outline=C["fstroke"], width=1.5)

    # title
    center(d, W / 2, 46, [("amnesic — letting an AI query production, safely", C["title"], ("bold", 20))])
    center(d, W / 2, 74, [("The AI can look. ", C["muted"], ("reg", 13)),
                          ("It cannot touch.", C["purple"], ("bold", 13))])

    TOP, BOT = 268, 446          # lane centres
    G1 = (330, 150, 590, 520)    # gate 1 box
    G2 = (628, 150, 892, 520)    # gate 2 box

    # ---- query pills ----
    segs(d, 40, 130, [("AI sends SQL", C["muted"], ("bold", 12))])
    rrect(d, (40, TOP - 19, 300, TOP + 19), 10, fill=C["greenbg"], outline=C["greenstk"], width=1.5)
    segs(d, 56, TOP, [("SELECT ", C["green"], ("mono", 12)), ("* FROM orders", C["mono_c"], ("mono", 12))])
    rrect(d, (40, BOT - 19, 300, BOT + 19), 10, fill=C["redbg"], outline=C["redstk"], width=1.5)
    segs(d, 56, BOT, [("DROP TABLE ", C["red"], ("mono", 12)), ("orders", C["mono_c"], ("mono", 12))])

    # ---- gate 1 ----
    rrect(d, G1, 14, fill=C["gate"], outline=C["gatestk"], width=2)
    segs(d, G1[0] + 18, G1[1] + 30, [("GATE 1", C["purple"], ("bold", 12))])
    segs(d, G1[0] + 18, G1[1] + 54, [("The Paranoid Linter", C["title"], ("bold", 15))])
    segs(d, G1[0] + 18, G1[1] + 76, [("static analysis · before we connect", C["muted"], ("reg", 11))])
    d.line([(s(G1[0] + 18), s(G1[1] + 92)), (s(G1[2] - 18), s(G1[1] + 92))], fill=C["fstroke"], width=max(1, s(1)))
    bullets = ["strip comments first", "reject 14 write keywords",
               "catch the CTE smuggle", "block SELECT … INTO", "validate identifiers"]
    for i, b in enumerate(bullets):
        by = G1[1] + 116 + i * 26
        d.ellipse([s(G1[0] + 20), s(by - 2), s(G1[0] + 26), s(by + 4)], fill=C["purple"])
        segs(d, G1[0] + 34, by, [(b, C["mono_c"] if False else (200, 209, 217), ("reg", 12))])

    # ---- gate 2 ----
    rrect(d, G2, 14, fill=C["gate"], outline=C["gatestk"], width=2)
    segs(d, G2[0] + 18, G2[1] + 30, [("GATE 2", C["purple"], ("bold", 12))])
    segs(d, G2[0] + 18, G2[1] + 54, [("The Transaction Born to Die", C["title"], ("bold", 15))])
    segs(d, G2[0] + 18, G2[1] + 76, [("every query, no exceptions", C["muted"], ("reg", 11))])
    d.line([(s(G2[0] + 18), s(G2[1] + 92)), (s(G2[2] - 18), s(G2[1] + 92))], fill=C["fstroke"], width=max(1, s(1)))
    rrect(d, (G2[0] + 18, G2[1] + 110, G2[2] - 18, G2[1] + 146), 8, fill=(16, 18, 26), outline=C["gatestk"], width=1)
    center(d, (G2[0] + G2[2]) / 2, G2[1] + 128, [("BEGIN  ", C["green"], ("mono", 12)),
                                                 (ARROW + " run " + ARROW, C["muted"], ("mono", 12)),
                                                 ("  ROLLBACK", C["red"], ("mono", 12))])
    segs(d, G2[0] + 20, G2[1] + 174, [("no ", (200, 209, 217), ("reg", 12)),
                                      ("commit()", C["yellow"], ("mono", 12)),
                                      (". ever.", (200, 209, 217), ("reg", 12))])
    segs(d, G2[0] + 20, G2[1] + 200, [("a write that slips Gate 1", C["muted"], ("reg", 11))])
    segs(d, G2[0] + 20, G2[1] + 218, [("is undone here.", C["muted"], ("reg", 11))])

    # ---- database ----
    DBX = 1070
    db_cylinder(d, DBX, 250, 400, 150)
    center(d, DBX, 430, [("your database", C["muted"], ("bold", 12))])

    # ---- SELECT lane (passes everything) ----
    arrow(d, 300, TOP, G1[0], C["green"])
    check(d, s(G1[2]) - s(26), s(TOP), C["green"])
    arrow(d, G1[2], TOP, G2[0], C["green"])
    check(d, s(G2[2]) - s(26), s(TOP), C["green"])
    arrow(d, G2[2], TOP, DBX - 78, C["green"])
    segs(d, DBX - 70, TOP - 22, [("rows", C["green"], ("bold", 11))])
    ck_x = segs(d, DBX - 70, TOP, [("returned ", C["green"], ("bold", 12))])
    check(d, ck_x + s(2), s(TOP), C["green"])

    # ---- WRITE lane (bounced at gate 1) ----
    arrow(d, 300, BOT, G1[0] - 4, C["red"])
    cross(d, s(G1[0] + 30), s(BOT), C["red"], sz=11)
    segs(d, G1[0] + 50, BOT, [("BLOCKED", C["red"], ("bold", 13))])
    segs(d, G1[0] + 50, BOT + 22, [("before it ever connects", C["muted"], ("reg", 11))])

    # footnote
    center(d, W / 2, H - 34, [
        ("Two independent layers. Slip past one ", C["muted"], ("reg", 12)),
        ("— land in the arms of the other.", C["purple"], ("reg", 12)),
    ])

    out = Path(__file__).resolve().parent.parent / "assets" / "defense-diagram.png"
    img.resize((W, H), Image.LANCZOS).save(out)
    print(f"wrote {out}  ({out.stat().st_size // 1024} KB, {W}x{H})")


if __name__ == "__main__":
    build()
