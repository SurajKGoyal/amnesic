#!/usr/bin/env python3
"""
Branded header image for the Medium article — the "look but don't touch"
metaphor, flat style, matching the demo + defense diagram.

Output: assets/article-header.png
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SC = 2
W, H = 1200, 560

C = {
    "page":   (10, 12, 16),
    "frame":  (20, 22, 28),
    "fstroke":(42, 47, 58),
    "title":  (230, 237, 243),
    "muted":  (139, 148, 158),
    "purple": (185, 168, 245),
    "blue":   (88, 166, 255),
    "bluebg": (18, 28, 48),
    "cyan":   (86, 196, 255),
    "green":  (63, 185, 80),
    "red":    (255, 95, 87),
    "glass":  (40, 52, 74),
    "glassln":(120, 150, 200),
    "db":     (24, 36, 30),
    "dbstk":  (52, 92, 64),
    "dim":    (90, 98, 112),
}
_F = {"reg": "/System/Library/Fonts/Supplemental/Arial.ttf",
      "bold": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
      "mono": "/System/Library/Fonts/Menlo.ttc"}
_cache = {}


def font(k, sz):
    key = (k, sz)
    if key not in _cache:
        _cache[key] = ImageFont.truetype(_F[k], sz * SC)
    return _cache[key]


def s(v): return int(round(v * SC))


def rrect(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle([s(box[0]), s(box[1]), s(box[2]), s(box[3])],
                        radius=s(r), fill=fill, outline=outline, width=max(1, s(width)))


def center(d, cx, yc, parts):
    tot = sum(font(k, sz).getlength(t) for t, _, (k, sz) in parts)
    xs = s(cx) - tot / 2
    for t, col, (k, sz) in parts:
        f = font(k, sz)
        d.text((xs, s(yc)), t, font=f, fill=col, anchor="lm")
        xs += f.getlength(t)


def segs(d, x, yc, parts):
    xs = s(x)
    for t, col, (k, sz) in parts:
        f = font(k, sz)
        d.text((xs, s(yc)), t, font=f, fill=col, anchor="lm")
        xs += f.getlength(t)
    return xs


def dashed(d, x1, x2, y, color, w=2, dash=9, gap=8):
    x = s(x1)
    while x < s(x2):
        d.line([(x, s(y)), (min(x + s(dash), s(x2)), s(y))], fill=color, width=max(1, s(w)))
        x += s(dash + gap)


def arrowhead(d, x, y, color, left=False):
    xx, yy = s(x), s(y)
    if left:
        d.polygon([(xx, yy), (xx + s(9), yy - s(6)), (xx + s(9), yy + s(6))], fill=color)
    else:
        d.polygon([(xx, yy), (xx - s(9), yy - s(6)), (xx - s(9), yy + s(6))], fill=color)


def check(d, x, yc, color, sz=8):
    z = s(sz)
    d.line([(x, yc), (x + z * 0.7, yc + z * 0.85), (x + z * 1.8, yc - z * 0.95)],
           fill=color, width=max(2, s(2.5)), joint="curve")


def cross(d, cx, cy, color, sz=10):
    z = s(sz)
    d.line([(cx - z, cy - z), (cx + z, cy + z)], fill=color, width=max(2, s(3)))
    d.line([(cx - z, cy + z), (cx + z, cy - z)], fill=color, width=max(2, s(3)))


def build():
    img = Image.new("RGB", (s(W), s(H)), C["page"])
    d = ImageDraw.Draw(img)
    rrect(d, (10, 10, W - 10, H - 10), 18, fill=C["frame"], outline=C["fstroke"], width=1.5)

    # wordmark
    center(d, W / 2, 56, [("amnesic", C["purple"], ("bold", 16)),
                          ("  ·  read-only by design", C["muted"], ("reg", 14))])

    CY = 250                  # metaphor centre line
    AIX = 235                 # AI orb centre x
    GLASS = (470, 150, 520, 360)   # glass panel
    DBX = 970

    # ---- AI orb ----
    d.ellipse([s(AIX - 62), s(CY - 62), s(AIX + 62), s(CY + 62)], fill=C["bluebg"], outline=C["blue"], width=max(2, s(2)))
    # eye
    d.ellipse([s(AIX - 26), s(CY - 16), s(AIX + 26), s(CY + 22)], outline=C["blue"], width=max(2, s(2)))
    d.ellipse([s(AIX - 7), s(CY - 2), s(AIX + 9), s(CY + 14)], fill=C["cyan"])
    center(d, AIX, CY + 96, [("your AI", C["blue"], ("bold", 14))])

    # ---- glass panel ----
    rrect(d, GLASS, 8, fill=C["glass"], outline=C["glassln"], width=1.5)
    # shine lines
    d.line([(s(GLASS[0] + 10), s(GLASS[3] - 14)), (s(GLASS[2] - 12), s(GLASS[1] + 18))], fill=(150, 175, 220), width=max(1, s(1)))
    d.line([(s(GLASS[0] + 22), s(GLASS[3] - 14)), (s(GLASS[2] - 2), s(GLASS[1] + 40))], fill=(110, 135, 175), width=max(1, s(1)))
    center(d, (GLASS[0] + GLASS[2]) / 2, GLASS[1] - 18, [("read-only", C["glassln"], ("bold", 11))])

    # ---- database ----
    x0, x1 = s(DBX - 78), s(DBX + 78)
    eh = s(18)
    d.ellipse([x0, s(180), x1, s(180) + 2 * eh], fill=C["db"], outline=C["dbstk"], width=max(2, s(1.5)))
    d.rectangle([x0, s(180) + eh, x1, s(330)], fill=C["db"])
    d.line([(x0, s(180) + eh), (x0, s(330))], fill=C["dbstk"], width=max(1, s(1.5)))
    d.line([(x1, s(180) + eh), (x1, s(330))], fill=C["dbstk"], width=max(1, s(1.5)))
    d.ellipse([x0, s(330) - eh, x1, s(330) + eh], fill=C["db"], outline=C["dbstk"], width=max(2, s(1.5)))
    for i in range(3):
        yy = 232 + i * 26
        d.line([(s(DBX - 50), s(yy)), (s(DBX + 50), s(yy))], fill=(50, 90, 64), width=max(1, s(2)))
    center(d, DBX, 372, [("your data", C["green"], ("bold", 14))])

    # ---- gaze beam: AI sees through the glass to the DB ----
    GAZE = CY - 34
    dashed(d, AIX + 64, DBX - 84, GAZE, C["cyan"])
    arrowhead(d, DBX - 84, GAZE, C["cyan"])
    cx = (AIX + 64 + DBX - 84) / 2
    center(d, cx, GAZE - 16, [("sees everything", C["cyan"], ("bold", 11))])

    # ---- touch attempt: blocked at the glass ----
    TOUCH = CY + 40
    d.line([(s(AIX + 64), s(TOUCH)), (s(GLASS[0] - 6), s(TOUCH))], fill=C["red"], width=max(2, s(2.5)))
    cross(d, s(GLASS[0] - 6), s(TOUCH), C["red"], sz=11)
    center(d, (AIX + 64 + GLASS[0]) / 2 - 4, TOUCH + 22, [("touches nothing", C["red"], ("bold", 11))])

    # ---- tagline ----
    center(d, W / 2, 470, [("The AI can look. ", C["title"], ("bold", 26)),
                           ("It cannot touch.", C["purple"], ("bold", 26))])
    center(d, W / 2, 506, [("how to point an AI at your production database and still sleep", C["muted"], ("reg", 13))])

    out = Path(__file__).resolve().parent.parent / "assets" / "article-header.png"
    img.resize((W, H), Image.LANCZOS).save(out)
    print(f"wrote {out}  ({out.stat().st_size // 1024} KB, {W}x{H})")


if __name__ == "__main__":
    build()
