"""MCP server: play Pokemon HeartGold in BizHawk with compact state and few, cheap images.

Tools return text by default; images only from look() / read_text() (native 256x384 or crops).
Run: python hgss_mcp/server.py   (see README for MCP client config)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP, Image  # noqa: E402

import actions  # noqa: E402
import emu as emu_mod  # noqa: E402
import game as game_mod  # noqa: E402
import journal  # noqa: E402
import screen  # noqa: E402

mcp = FastMCP("heartgold")
_emu: emu_mod.Emu | None = None
_game: game_mod.Game | None = None

BUTTONS = {"a": "A", "b": "B", "x": "X", "y": "Y", "l": "L", "r": "R", "start": "Start",
           "select": "Select", "up": "Up", "down": "Down", "left": "Left", "right": "Right"}


def g() -> game_mod.Game:
    """Start BizHawk with the bridge if needed, and load the save if we're at the title."""
    global _emu, _game
    if _game is None:
        _emu = emu_mod.launch()
        _game = game_mod.Game(_emu)
        if getattr(_emu, "started", False):  # fresh BizHawk: get past the title to the save
            _game.boot()
    return _game


def _png(img) -> Image:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Image(data=buf.getvalue(), format="png")


def _status_line() -> str:
    img = actions.grab(g().emu)
    if screen.textbox_present(img):
        return "screen: message box open" + (" (waiting for A)" if screen.arrow_visible(img) else "")
    return "screen: no message box"


@mcp.tool()
def state() -> str:
    """Compact game state from RAM: location, party (HP, moves, PP), opponent in battle, whether this
    area's Nuzlocke encounter is still free, and whether a message box is open. ~100-200 tokens."""
    gm = g()
    loc = gm.location()
    return "\n".join([gm.summary(), journal.encounter_status(loc.area), _status_line()])


@mcp.tool()
def look(view: str = "both") -> Image:
    """Screenshot at native resolution. view: both (256x384, ~130 tokens), top, bottom, text (message box only)."""
    img = actions.grab(g().emu)
    if view == "top":
        img = screen.top(img)
    elif view == "bottom":
        img = screen.bottom(img)
    elif view == "text":
        img = screen.textbox(img)
    return _png(img)


@mcp.tool()
def press(keys: str) -> str:
    """Press buttons in order. Tokens separated by spaces: A, B, X, Y, L, R, Start, Select, Up/Down/Left/Right;
    'Down*3' repeats; 'A+B' presses together; 'wait30' waits 30 frames; 'hold:Right:40' holds 40 frames."""
    em = g().emu
    done = []
    for tok in keys.split():
        if tok.lower().startswith("wait"):
            em.wait(int(tok[4:] or 30))
        elif tok.lower().startswith("hold:"):
            _, btn, n = tok.split(":")
            em.press(BUTTONS[btn.lower()], int(n), 6)
        else:
            name, _, rep = tok.partition("*")
            combo = ",".join(BUTTONS[p.lower()] for p in name.split("+"))
            for _ in range(int(rep or 1)):
                em.press(combo, 4, 8)
        done.append(tok)
    return f"pressed {' '.join(done)}; {_status_line()}"


@mcp.tool()
def read_text(max_messages: int = 30) -> list:
    """Press A through dialogue until the box closes or a choice is needed. Returns one image stacking every
    message (~16 tokens each) plus why it stopped and the current state."""
    gm = g()
    res = actions.advance_text(gm.emu, max_messages=max_messages)
    out = [f"{len(res.messages)} messages; stopped: {res.stopped}"]
    if res.messages:
        out.append(_png(screen.montage(res.messages)))
    out.append(gm.summary())
    return out


@mcp.tool()
def answer(yes: bool = True) -> str:
    """Answer a Yes/No prompt (the bottom-screen YES/NO buttons)."""
    return f"{actions.answer(g().emu, yes)}; {_status_line()}"


@mcp.tool()
def seek_encounter() -> str:
    """Walk to the nearest tall grass and pace in it until a wild battle starts (for the area's
    Nuzlocke encounter or training). Returns what happened plus state()."""
    gm = g()
    return gm.seek_encounter() + chr(10) + state()


@mcp.tool()
def buy(item: str, quantity: int = 1) -> str:
    """Inside a Poke Mart: talk to the clerk, buy an item by name (e.g. "Poke Ball", "Potion"), confirm,
    leave, and report what was spent (checked from RAM). Caps the quantity at what you can afford."""
    return g().buy(item, quantity)


@mcp.tool()
def speed(percent: int = 100) -> str:
    """Emulation speed in percent (100 = normal; up to ~800 to skip ahead to decisions, at more CPU)."""
    pct = max(25, min(1600, int(percent)))
    g().emu.speed(pct)
    return f"speed {pct}%"


@mcp.tool()
def swap(a: str, b: str) -> str:
    """Swap two party members outside battle (slot numbers 1-6 or nicknames), e.g. swap("TALUS", "1") to
    put TALUS in the lead. Verified from RAM."""
    return g().swap(a, b)


@mcp.tool()
def bag() -> str:
    """Everything in the Bag by pocket (read from RAM; no menus opened)."""
    b = g().bag()
    return "\n".join(f"{k}: " + ", ".join(f"{n} x{q}" for n, q in v) for k, v in b.items()) or "bag is empty"


@mcp.tool()
def go_to(place: str) -> str:
    """Walk to a town or route by name ("Cherrygrove", "Route 30") across the overworld, avoiding tall grass
    where it can. Stops for battles, trainers and events (then read_text / battle tools, and call it again)."""
    return g().go_to(place)


@mcp.tool()
def npcs() -> str:
    """People on this map where they stand right now (read live from RAM, including people placed by
    story events), nearest first: sprite name, tile, dx/dy from you."""
    return "\n".join(f"{p['sprite']} ({p['x']},{p['y']}) [dx {p['dx']:+d}, dy {p['dy']:+d}]"
                     + (" (placed by an event)" if p.get("scripted") else "")
                     for p in g().npcs()) or "nobody on this map"


@mcp.tool()
def talk(who: str = "") -> list:
    """Walk up to a person (sprite name or part of it, from npcs(); nearest person if empty), face them,
    press A and read the conversation. Stops at Yes/No choices like read_text()."""
    gm = g()
    r = gm.talk(who)
    if not r.startswith("talking"):
        return [r, gm.summary()]
    return [r] + read_text()


@mcp.tool()
def heal() -> str:
    """Inside a Pokemon Center: walk to the nurse, accept healing, and report the party's HP."""
    return g().heal()


@mcp.tool()
def follow_scene() -> list:
    """For cutscenes that walk you around between messages (tours, rival scenes, events): reads all
    the text until the scene settles or a real Yes/No choice appears. One stacked image of messages."""
    gm = g()
    r = gm.follow_scene()
    out = [f"{len(r['messages'])} messages; {r['note']}"]
    if r["messages"]:
        out.append(_png(screen.montage(r["messages"])))
    out.append(gm.summary())
    return out


@mcp.tool()
def walk(direction: str, steps: int = 1, run: bool = False) -> str:
    """Walk up/down/left/right, checking position from RAM each step; stops if blocked, at a door, or when a
    message opens (trainer, sign, event)."""
    r = g().walk(direction.lower(), steps, run)
    return f"moved {r['moved']}/{steps}, now {r['at']}" + (f" ({r['note']})" if r["note"] else "")


@mcp.tool()
def walk_to(x: int, y: int) -> str:
    """Walk to map coordinates (same units as state()/exits()), planning around walls it has learned.
    Walking onto a door/warp tile goes through it."""
    r = g().walk_to(x, y)
    return f"{r['note']}; now {r['at']} ({r['legs']} legs)"


@mcp.tool()
def fight(move: str = "1") -> list:
    """In battle, use a move by name or slot 1-4, then follow the turn. Returns the turn's messages
    (one stacked image) and the live battle state; stops at your next move, a choice, or battle end."""
    gm = g()
    r = gm.fight(move)
    out = [f"{len(r['messages'])} messages; {r['note']}"]
    if r["messages"]:
        out.append(_png(screen.montage(r["messages"])))
    out.append(gm.summary())
    return out


@mcp.tool()
def fight_until(move: str = "1", stop_below_hp: float = 0.4) -> list:
    """Repeat a move until the battle ends (for easy wild battles). Hands control back early if your
    Pokemon drops below stop_below_hp of max HP or the game asks anything else. Image = last messages."""
    gm = g()
    r = gm.fight_until(move, stop_below_hp)
    out = [f"{len(r['messages'])} messages; {r['note']}"]
    if r["messages"]:
        out.append(_png(screen.montage(r["messages"][-8:])))
    out.append(gm.summary())
    return out


def _battle_result(r) -> list:
    out = [f"{len(r['messages'])} messages; {r['note']}"]
    if r["messages"]:
        out.append(_png(screen.montage(r["messages"])))
    out.append(g().summary())
    return out


@mcp.tool()
def switch_to(who: str) -> list:
    """In battle: switch to another party member by nickname (or party slot). Uses your turn; the foe
    attacks the incoming Pokemon. Returns the turn's messages + live HP like fight()."""
    return _battle_result(g().switch_to(who))


@mcp.tool()
def run_away() -> list:
    """Run from a wild battle (can fail; then it's the foe's turn)."""
    return _battle_result(g().run_away())


@mcp.tool()
def catch(pocket_slot: int = 1) -> list:
    """Throw a ball from the Poke Balls pocket (slot 1 = first ball) and follow the result. If caught
    ('CAUGHT' in the note), the nickname prompt follows: answer(True) then type_text(name)."""
    return _battle_result(g().catch(pocket_slot))


@mcp.tool()
def exits() -> str:
    """Doors and warps on this map with coordinates and destinations."""
    ex = g().exits()
    return "\n".join(f"({e['x']},{e['y']}) -> {e['to']}  [dx {e['dx']:+d}, dy {e['dy']:+d}]" for e in ex) or "none"


@mcp.tool()
def type_text(text: str) -> str:
    """On a naming keyboard (player/nickname/box), type text (A-Z, a-z, 0-9) and confirm with OK."""
    return actions.type_text(g().emu, text)


@mcp.tool()
def open_menu(item: str) -> str:
    """Open a field-menu entry: pokemon, bag, pokegear, trainer, save, options."""
    return actions.open_menu(g().emu, item)


@mcp.tool()
def save() -> str:
    """Save the game in-game and write it to disk. Do this before ending a session."""
    return actions.save_game(g().emu)


@mcp.tool()
def run_journal() -> str:
    """The Nuzlocke journal: rules, goal, badges, encounters by area, graveyard, recent notes. Read at the start."""
    return journal.summary()


@mcp.tool()
def log_encounter(species: str, result: str, nickname: str = "", area: str = "") -> str:
    """Record this area's first encounter. result: caught, fainted, fled, failed, dupe-reroll.
    area defaults to the current location's area."""
    return journal.log_encounter(area or g().location().area, species, result, nickname)


@mcp.tool()
def log_death(nickname: str, cause: str) -> str:
    """Record a fainted (dead) Pokemon. Box it afterwards and never use it again."""
    gm = g()
    mon = next((m for m in gm.party() if m.nickname.lower() == nickname.lower()), None)
    return journal.log_death(nickname, mon.name if mon else "?", mon.level if mon else 0, cause,
                             str(gm.location()))


@mcp.tool()
def note(text: str) -> str:
    """Add a short note to the journal (plans, hazards, what's next)."""
    return journal.note(text)


@mcp.tool()
def set_goal(text: str) -> str:
    """Set the current goal shown in the journal."""
    return journal.set_goal(text)


@mcp.tool()
def add_badge(name: str) -> str:
    """Record a gym badge."""
    return journal.add_badge(name)


if __name__ == "__main__":
    mcp.run()
