"""High-level actions built on Emu: each one runs many frames/inputs and reports back compactly."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

import screen
from config import SHOTS
from emu import Emu


def grab(emu: Emu, name: str = "_live") -> Image.Image:
    path = emu.screenshot(SHOTS / f"{name}.png")
    img = Image.open(path)
    img.load()
    return img.convert("RGB")


@dataclass
class TextResult:
    messages: list = field(default_factory=list)  # text-box crops, one per message
    stopped: str = ""                              # why it stopped
    final: Image.Image | None = None


def read_call(emu: Emu, max_steps: int = 120) -> TextResult:
    """Pokegear phone call: press A through it, keeping each finished line, until it hangs up."""
    res = TextResult()
    last, cur, cur_ink, gone = None, None, 0, 0
    for _ in range(max_steps):
        img = grab(emu)
        if not screen.phone_call(img):
            gone += 1
            if gone >= 4:
                res.stopped = "call ended"
                break
            emu.wait(15)
            continue
        gone = 0
        ink = screen.call_ink(img)
        if cur is not None and ink < cur_ink and screen.digest(cur) != last:
            res.messages.append(cur)
            last = screen.digest(cur)
        cur, cur_ink = img.crop(screen.CALL_BOX), ink
        if screen.call_hangup(img):  # "Click!": leave the Pokegear (A here would dial someone)
            for _ in range(8):  # phone list -> Pokegear menu -> close
                emu.press("B", 4, 30)
                if screen.field_menu(grab(emu)):
                    break
            res.stopped = "call ended"
            break
        emu.wait(20)
        if screen.call_ink(grab(emu)) == ink:  # line finished printing: next
            emu.press("A", 3, 10)
    if cur is not None and screen.digest(cur) != last:
        res.messages.append(cur)
    res.final = grab(emu)
    return res


def advance_text(emu: Emu, max_messages: int = 40, max_steps: int = 400) -> TextResult:
    """Press through dialogue. Stops when the box closes, a choice/menu needs input, or limits hit.

    While a message prints, A makes it print at once; once the arrow shows, A goes to the next
    message. A message box with no arrow that stops changing means the game wants a decision.
    """
    res = TextResult()
    last_msg, still, gone = None, 0, 0
    prev = None
    for _ in range(max_steps):
        img = grab(emu)
        if screen.phone_call(img):
            call = read_call(emu)
            res.messages += call.messages
            res.stopped = "phone call ended"
            res.final = call.final
            return res
        if not screen.textbox_present(img):
            gone += 1
            if gone >= 12:  # ~2 s with no box: cutscenes walk people around between messages
                res.stopped = "text box closed"
                break
            emu.wait(10)
            continue
        gone = 0
        if screen.battle_menu(img):
            res.stopped = "battle: your move (use fight/catch, not A)"
            break
        if screen.arrow_visible(img):
            crop = screen.textbox(img)
            d = screen.digest(crop)
            if d != last_msg:
                res.messages.append(crop)
                last_msg = d
                if len(res.messages) >= max_messages:
                    res.stopped = "message limit"
                    break
            emu.press("A", 3, 6)
            still, prev = 0, None
            continue
        # Still printing while the amount of text grows (blinking cursors/sprites don't count).
        n = screen.ink(img)
        still = 0 if prev is None or n > prev else still + 1
        prev = n if prev is None else max(prev, n)
        if still >= 25:  # ~2.5 s with the box up, no arrow, no new text
            crop = screen.textbox(img)
            if screen.digest(crop) != last_msg:
                res.messages.append(crop)
                last_msg = screen.digest(crop)
            if screen.battle_menu(img):
                res.stopped = "battle: your move (use fight/catch, not A)"
                break
            if screen.choice_prompt(img) or screen.yesno_buttons(img):
                res.stopped = "waiting for a choice (Yes/No)"
                break
            # The last line of a conversation has no arrow. Only close it when the plain overworld
            # menu is underneath: anything else on the bottom screen (a picker, a shop, a keyboard,
            # the starter machine...) means A would choose something.
            if screen.field_menu(img):
                emu.press("A", 3, 10)
                still, prev = 0, None
                continue
            res.stopped = "message waiting without an arrow; a menu or picker may be open (look('bottom'))"
            break
        emu.wait(6)
    else:
        res.stopped = "step limit"
    res.final = grab(emu)
    return res


# -- naming keyboard (player name, nicknames, box names) --------------------------------------
# Bottom-screen grid: columns at x = 34 + 16*c, rows at these y (full 256x384 image).
KB_ROWS = [292, 310, 328, 350]
KB_LAYOUT = ["ABCDEFGHIJ", "KLMNOPQRST", "UVWXYZ", "0123456789"]
KB_TABS = {"upper": 40, "lower": 73}  # tab x positions (y 258); the selected tab is bright yellow
CURSOR_RGB = (235, 73, 0)


def _kb_cursor(img: Image.Image):
    px = img.load()
    # the cursor pulses between orange shades ((235, 73, 0) ... (235, 121, 0))
    pts = [(x, y) for y in range(280, 366) for x in range(20, 240)
           if px[x, y][0] > 220 and 50 < px[x, y][1] < 140 and px[x, y][2] < 30]
    if len(pts) < 8:
        return None
    cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    cy = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
    row = min(range(len(KB_ROWS)), key=lambda r: abs(KB_ROWS[r] - cy))
    return row, round((cx - 34) / 16)


def _kb_page(img: Image.Image) -> str | None:
    px = img.load()
    for name, x in KB_TABS.items():
        r, g, b = px[x, 258]
        if r > 240 and g > 200 and b < 60:
            return name
    return None


def _kb_find(emu: Emu):
    for _ in range(12):  # the cursor blinks
        pos = _kb_cursor(grab(emu))
        if pos:
            return pos
        emu.wait(5)
    return None


def type_text(emu: Emu, text: str, confirm: bool = True) -> str:
    """Type on the naming keyboard (A-Z, a-z, 0-9), steering by the on-screen cursor, then OK."""
    for _ in range(30):  # the keyboard slides in after "Your name?"; press A if a message is waiting
        img = grab(emu)
        if _kb_page(img) is not None:
            break
        if screen.arrow_visible(img):
            emu.press("A", 3, 10)
        else:
            emu.wait(10)
    for ch in text:
        page = "lower" if ch.islower() else "upper"
        target = next(((r, row.index(ch.upper() if ch.isalpha() else ch)) for r, row in enumerate(KB_LAYOUT)
                       if (ch.upper() if ch.isalpha() else ch) in row), None)
        if target is None:
            return f"can't type {ch!r}"
        for _ in range(4):
            if _kb_page(grab(emu)) == page or ch.isdigit():
                break
            emu.press("Select", 3, 10)
        for _ in range(40):
            pos = _kb_find(emu)
            if pos is None:
                return "keyboard cursor not found"
            if pos == target:
                break
            (r, c), (tr, tc) = pos, target
            if r != tr:
                emu.press("Down" if tr > r else "Up", 3, 6)
            else:
                emu.press("Right" if tc > c else "Left", 3, 6)
        else:
            return f"couldn't reach {ch!r}"
        emu.press("A", 3, 8)
    if confirm:
        emu.press("Start", 3, 8)
        emu.press("A", 3, 30)
    return f"typed {text}"


# -- field menu (bottom screen; X switches it to button control) ----------------------------
MENU_SLOTS = {"pokegear": (44, 226), "trainer": (124, 226), "pokemon": (44, 266), "save": (124, 266),
              "bag": (44, 306), "options": (124, 306)}
HILITE_RGB = (243, 24, 24)


def _menu_cursor(img: Image.Image):
    px = img.load()
    pts = [(x, y) for y in range(200, 384) for x in range(0, 170) if px[x, y] == HILITE_RGB]
    if len(pts) < 20:
        return None
    cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    cy = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
    return min(MENU_SLOTS, key=lambda k: (MENU_SLOTS[k][0] - cx) ** 2 + (MENU_SLOTS[k][1] - cy) ** 2)


def open_menu(emu: Emu, item: str) -> str:
    """Open a field-menu entry (pokegear, trainer, pokemon, save, bag, options) by steering the highlight."""
    item = item.lower().replace("é", "e")
    if item not in MENU_SLOTS:
        return f"unknown menu item {item}; options: {', '.join(MENU_SLOTS)}"
    for _ in range(3):
        if _menu_cursor(grab(emu)):
            break
        emu.press("X", 3, 12)
    for _ in range(12):
        cur = _menu_cursor(grab(emu))
        if cur is None:
            return "menu highlight not found (is a message or another screen open?)"
        if cur == item:
            emu.press("A", 3, 30)
            return f"opened {item}"
        (cx, cy), (tx, ty) = MENU_SLOTS[cur], MENU_SLOTS[item]
        emu.press(("Down" if ty > cy else "Up") if ty != cy else ("Right" if tx > cx else "Left"), 3, 8)
    return f"couldn't reach {item}"


def save_game(emu: Emu) -> str:
    """In-game save via the field menu (progress survives closing BizHawk). The prompt and the
    "saving..." messages are on the bottom screen; it's done when the menu highlight returns."""
    r = open_menu(emu, "save")
    if not r.startswith("opened"):
        return r
    emu.wait(20)
    emu.press("A", 3, 30)  # Yes
    for _ in range(20):  # up to ~20 s (the first save writes everything)
        emu.wait(60)
        if _menu_cursor(grab(emu)) == "save":
            emu.press("B", 3, 10)
            emu.run(["flush"])  # BizHawk otherwise writes SaveRAM only when the game closes
            return "saved (and written to disk)"
        emu.press("A", 3, 6)  # dismiss "saved the game" / confirm overwrite if asked
    return "save may not have finished; check with look()"


# -- bottom-screen YES/NO buttons ------------------------------------------------------------
YES_Y, NO_Y = 192 + 72, 192 + 120  # button centres in the full image


def _yesno_highlight(img: Image.Image):
    """Which YES/NO button has the red highlight border (None if neither, e.g. not yet activated)."""
    px = img.load()
    rows = {}
    for name, y in (("yes", YES_Y), ("no", NO_Y)):
        band = [px[x, yy] for x in range(4, 252, 3) for yy in (y - 22, y - 21, y + 21, y + 22)]
        rows[name] = sum(1 for r, g, b in band if r > 200 and g < 90 and b < 90)
    best = max(rows, key=rows.get)
    return best if rows[best] > 20 else None


def answer(emu: Emu, yes: bool = True) -> str:
    """Answer a YES/NO prompt by steering the highlight (the first press may only wake the buttons)."""
    want = "yes" if yes else "no"
    for _ in range(5):
        img = grab(emu)
        if screen.battle_yesno(img):  # battle style: red corner brackets mark the selection
            px = img.load()
            ys = [y for y in range(196, 384, 2) for x in range(0, 256, 4) if px[x, y] == (251, 0, 0)]
            cur = None if not ys else ("yes" if (min(ys) + max(ys)) / 2 < 192 + 104 else "no")
        else:
            cur = _yesno_highlight(img)
        if cur == want:
            emu.press("A", 3, 20)
            return f"answered {want}"
        emu.press("Up" if yes else "Down", 3, 10)
    return "YES/NO buttons not found (check look('bottom'))"


def save_result(res: TextResult, name: str = "text") -> tuple[Path, Path]:
    """Write the message montage and the final screen; returns their paths."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    m = SHOTS / f"{name}_messages.png"
    screen.montage(res.messages).save(m)
    f = SHOTS / f"{name}_final.png"
    res.final.save(f)
    return m, f
