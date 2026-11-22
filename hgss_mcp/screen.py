"""Screen analysis on native BizHawk screenshots (256x384: top screen y 0-191, bottom y 192-383)."""
from __future__ import annotations

import hashlib

from PIL import Image

W, H = 256, 192
TEXTBOX = (6, 147, 236, 188)     # message box at the bottom of the top screen (text area + frame)
TEXT_INNER = (10, 150, 230, 184)  # white area holding the text
ARROW = (238, 166, 251, 186)     # "press A" arrow in the box's right margin


def top(img: Image.Image) -> Image.Image:
    return img.crop((0, 0, W, H))


def bottom(img: Image.Image) -> Image.Image:
    return img.crop((0, H, W, 2 * H))


def _white_frac(img: Image.Image, box) -> float:
    data = img.crop(box).convert("L").tobytes()
    return sum(1 for v in data if v > 235) / len(data)


def textbox_present(img: Image.Image) -> bool:
    """A message box is up: mostly-white text area with a non-white frame column beside it."""
    if _white_frac(img, TEXT_INNER) < 0.55:
        return False
    frame = img.crop((TEXT_INNER[2] + 7, 150, TEXT_INNER[2] + 9, 184)).convert("L")
    return sum(1 for v in frame.tobytes() if v > 235) / (2 * 34) < 0.3


def arrow_visible(img: Image.Image) -> bool:
    return sum(1 for v in img.crop(ARROW).convert("L").tobytes() if v > 235) >= 6


def ink(img: Image.Image) -> int:
    """Dark (text) pixels in the message area; grows while a message is still printing."""
    return sum(1 for v in img.crop(TEXT_INNER).convert("L").tobytes() if v < 128)


FIELD_GREEN = (24, 203, 113)


def field_menu(img: Image.Image) -> bool:
    """The overworld's touch menu (green panel on the bottom screen) is showing: we're in the field,
    not on the title screen, in a battle, or in a menu."""
    px = img.load()
    pts = [(x, y) for x in range(12, 158, 4) for y in range(215, 380, 4)]
    return sum(1 for p in pts if px[p] == FIELD_GREEN) / len(pts) > 0.4


CALL_BOX = (8, 140, 248, 188)  # Pokegear call: white text on black, blue caller header above


def phone_call(img: Image.Image) -> bool:
    px = img.load()
    header = sum(1 for x in range(10, 246, 4) if px[x, 130] == (32, 65, 227)) / len(range(10, 246, 4))
    box = sum(1 for x in range(10, 246, 4) for y in (152, 168, 182) if px[x, y] == (0, 0, 0))
    return header > 0.6 and box > 0.6 * 3 * len(range(10, 246, 4))


def call_hangup(img: Image.Image) -> bool:
    """The caller's "Click!" line (call over): a single short word on the first line, then only
    a row of dots. Detected by where the text pixels are, not by matching game graphics."""
    px = img.load()
    first = [x for y in range(144, 160) for x in range(8, 248) if min(px[x, y]) > 200]
    second = [x for y in range(160, 186) for x in range(8, 248) if min(px[x, y]) > 200]
    return bool(first) and max(first) < 64 and len(first) < 140 and len(second) < 60

def call_ink(img: Image.Image) -> int:
    return sum(1 for v in img.crop(CALL_BOX).convert("L").tobytes() if v > 200)


def _red_top(img):
    r, g, b = img.getpixel((60, 250))
    return r > 200 and g < 90 and b < 90


def battle_menu(img: Image.Image) -> bool:
    """The battle menu (big red FIGHT + orange BAG bottom-left) is showing: our turn to act."""
    r, g, b = img.getpixel((30, 360))
    return _red_top(img) and r > 190 and 90 < g < 210 and b < 120


def battle_yesno(img: Image.Image) -> bool:
    """Battle-style Yes (red) / No (blue) buttons, e.g. "Give a nickname to the caught X?"."""
    r, g, b = img.getpixel((60, 332))
    lr, lg, lb = img.getpixel((30, 360))
    return _red_top(img) and b > r + 40 and min(lr, lg, lb) > 180


def yesno_buttons(img: Image.Image) -> bool:
    """The bottom-screen YES/NO buttons (two wide light bars, or the battle-style red/blue pair)."""
    if battle_yesno(img):
        return True
    px = img.load()
    def bar(y):
        return sum(1 for x in range(20, 236, 6) if min(px[x, y]) > 200) > 30
    return bar(192 + 72) and bar(192 + 120)


def choice_prompt(img: Image.Image) -> bool:
    """Yes/No (and other choice) prompts show a small white+pink marker inside the message box."""
    px = img.load()
    return all(abs(a - b) < 12 for a, b in zip(px[220, 173], (251, 186, 195)))


def textbox(img: Image.Image) -> Image.Image:
    return img.crop(TEXTBOX)


def digest(img: Image.Image) -> str:
    return hashlib.md5(img.tobytes()).hexdigest()


def montage(images: list[Image.Image], gap: int = 2) -> Image.Image:
    """Stack images vertically (e.g. every message seen during advance_text)."""
    if not images:
        return Image.new("RGB", (1, 1))
    w = max(i.width for i in images)
    h = sum(i.height for i in images) + gap * (len(images) - 1)
    out = Image.new("RGB", (w, h), (40, 40, 40))
    y = 0
    for i in images:
        out.paste(i, (0, y))
        y += i.height + gap
    return out
