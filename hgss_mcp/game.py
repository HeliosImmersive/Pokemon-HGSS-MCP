"""Game-level view of HeartGold: location, party, battle, and movement, read from RAM."""
from __future__ import annotations

import struct
from pathlib import Path
from dataclasses import dataclass

import pkm
import mapdata
import screen
from actions import grab
from emu import Emu

MAPS = {int(k): v for k, v in pkm.DATA.get("maps", {}).items()}
RAM_BASE, RAM_SIZE = 0x02000000, 0x400000
# Save-data Location {u32 mapId, s32 warpId, u32 x, u32 y}; found at this address in this build
# of the game. Verified each read and re-found by scanning if it ever moves.
LOC_GUESS = 0x0227D4F4  # new game, before the first Pokemon (no party to anchor on yet)
PARTY_TO_LOC = 0x11A4    # save block: Location record sits this far after the player's Party
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
STEP_FRAMES = 16  # walking speed: one tile per 16 frames


def _plan(map_id: int, start, goal, learned=frozenset(), avoid=frozenset(), margin: int = 40,
          grass_cost: int = 6):
    """Cheapest path using the ROM's collision map (+ walls learned by bumping, + avoid tiles).
    Tall grass costs extra (fewer wild battles on the way); ledges are one-way and jump two tiles.
    Returns a list of directions, or None."""
    import heapq
    info = MAPS.get(map_id, {})
    mi = info.get("matrix")
    warps = {(x, y) for x, y, _ in info.get("warps", [])}

    def open_(x, y):
        if (x, y) == goal or (x, y) in warps:
            return True
        if (x, y) in learned or (x, y) in avoid:
            return False
        return True if mi is None else not mapdata.solid(mi, x, y)

    order = [("up", 0, -1), ("down", 0, 1), ("left", -1, 0), ("right", 1, 0)]
    x_lo, x_hi = min(start[0], goal[0]) - margin, max(start[0], goal[0]) + margin
    y_lo, y_hi = min(start[1], goal[1]) - margin, max(start[1], goal[1]) + margin
    best = {start: 0}
    prev = {start: None}
    heap = [(0, start)]
    while heap:
        cost, p = heapq.heappop(heap)
        if p == goal:
            path = []
            while prev[p] is not None:
                p, d = prev[p]
                path.append(d)
            return path[::-1]
        if cost > best.get(p, 1 << 30):
            continue
        for d, dx, dy in order:
            n = (p[0] + dx, p[1] + dy)
            if mi is not None and mapdata.behavior(mi, *n) in mapdata.LEDGES:
                if mapdata.LEDGES[mapdata.behavior(mi, *n)] != (dx, dy):
                    continue
                n = (n[0] + dx, n[1] + dy)  # jump lands two tiles on
            if not (x_lo <= n[0] <= x_hi and y_lo <= n[1] <= y_hi) or not open_(*n):
                continue
            step = 1 + (grass_cost if mi is not None and mapdata.behavior(mi, *n) in mapdata.TALL_GRASS else 0)
            if cost + step < best.get(n, 1 << 30):
                best[n] = cost + step
                prev[n] = (p, d)
                heapq.heappush(heap, (cost + step, n))
    return None


@dataclass
class Location:
    map_id: int
    x: int
    y: int

    @property
    def name(self) -> str:
        return MAPS.get(self.map_id, {}).get("name", f"map {self.map_id}")

    @property
    def area(self) -> str:
        return MAPS.get(self.map_id, {}).get("area", "?")

    def __str__(self) -> str:
        return f"{self.name} ({self.x},{self.y})"


class Game:
    def __init__(self, emu: Emu):
        self.emu = emu
        self.loc_addr = LOC_GUESS
        self.party_addr: int | None = None

    # -- memory --------------------------------------------------------------------------------
    def ram(self) -> bytes:
        return self.emu.read(RAM_BASE, RAM_SIZE)

    @staticmethod
    def _plausible_loc(m, w, x, y) -> bool:
        return m in MAPS and (w == 0xFFFFFFFF or w < 64) and x < 4096 and y < 4096

    def location(self) -> Location:
        """Location record {mapId, warp, x, y}: in the save block, PARTY_TO_LOC after the party."""
        party = self.party_address()
        if party:
            self.loc_addr = party + PARTY_TO_LOC
        m, w, x, y = struct.unpack("<4I", self.emu.read(self.loc_addr, 16))
        if not self._plausible_loc(m, w, x, y):
            self.party_addr = None
            party = self.party_address()
            if party:
                self.loc_addr = party + PARTY_TO_LOC
                m, w, x, y = struct.unpack("<4I", self.emu.read(self.loc_addr, 16))
        return Location(m, x, y)

    # -- party / battle --------------------------------------------------------------------------
    def parties(self) -> list[pkm.Party]:
        return pkm.scan_parties(self.ram())

    def party_address(self) -> int | None:
        """The player's party: the Party struct whose save block has a valid Location record after it.
        (The save block moves between a new game and a loaded save, so this is found by scanning.)"""
        if self.party_addr:
            p = pkm.read_party(self.emu.read(self.party_addr, 8 + 6 * pkm.PKM_SIZE), self.party_addr)
            if p:
                return self.party_addr
        self.party_addr = None
        for p in sorted(self.parties(), key=lambda p: p.addr):
            m, w, x, y = struct.unpack("<4I", self.emu.read(p.addr + PARTY_TO_LOC, 16))
            # warp = index of the door used to arrive (-1 if none). The live save block is the
            # lower one; a stale copy (the save as read at the title screen) sits higher in RAM.
            if self._plausible_loc(m, w, x, y) and m > 3 and x and y:
                self.party_addr = p.addr
                break
        return self.party_addr

    def party(self) -> list[pkm.Pokemon]:
        addr = self.party_address()
        if not addr:
            return []
        p = pkm.read_party(self.emu.read(addr, 8 + 6 * pkm.PKM_SIZE), addr)
        return p.members if p else []

    def opponents(self) -> list[pkm.Pokemon]:
        """Pokemon in party structs that aren't the player's (a battle's opposing side)."""
        mine = {m.pid for m in self.party()}
        out = []
        for p in self.parties():
            if p.addr == self.party_addr:
                continue
            if any(m.pid not in mine for m in p.members):
                out.extend(m for m in p.members if m.pid not in mine)
        seen, uniq = set(), []
        for m in out:
            if m.pid not in seen:
                seen.add(m.pid)
                uniq.append(m)
        return uniq

    # -- movement -------------------------------------------------------------------------------
    def walk(self, direction: str, steps: int = 1, run: bool = False) -> dict:
        """Walk tile by tile, checking position after each step. Stops early if blocked or if a
        message box opens (an event/trainer/sign)."""
        dx, dy = DIRS[direction]
        btn = direction.capitalize() + (",B" if run else "")
        start = self.location()
        cur, moved, note = start, 0, ""
        frames = STEP_FRAMES // (2 if run else 1)
        tries = 0
        while moved < steps:
            self.emu.press(btn, frames, 0)
            if not screen.field_menu(grab(self.emu)):  # battle, menu, event, or a door fade
                self.emu.wait(30)
                for _ in range(6):  # door/stairs fades go black for a moment: wait them out
                    img = grab(self.emu)
                    if screen.field_menu(img) or max(img.crop((0, 0, 256, 192)).convert("L").getextrema()) > 40:
                        break
                    self.emu.wait(20)
                if not screen.field_menu(grab(self.emu)):
                    note = "screen changed (menu/event)"
                    if screen.phone_call(grab(self.emu)):
                        note = "phone call (use read_text)"
                        break
                    for _ in range(4):  # battle structs appear a moment after the transition
                        if self.battlers():
                            note = "battle started"
                            break
                        self.emu.wait(30)
                    break
            new = self.location()
            if new.map_id != cur.map_id:
                moved += 1
                note = f"entered {new.name}"
                same_world = MAPS.get(new.map_id, {}).get("matrix") == MAPS.get(cur.map_id, {}).get("matrix")
                cur = new
                if same_world:  # walked across an area border (route -> town): keep going
                    continue
                self.emu.wait(40)
                break
            if (new.x, new.y) in ((cur.x + dx, cur.y + dy), (cur.x + 2 * dx, cur.y + 2 * dy)):
                moved += 1  # one step (or a ledge jump)
                cur, tries = new, 0
                if (new.x, new.y) == (cur.x + 2 * dx, cur.y + 2 * dy):
                    self.emu.wait(16)
                continue
            tries += 1  # first press may only turn the player; running steps can register late
            if tries >= 3:
                note = "blocked"
                break
        self.emu.wait(frames)  # let the last step finish
        end = self.location()
        if end.map_id != start.map_id:
            if MAPS.get(end.map_id, {}).get("matrix") != MAPS.get(start.map_id, {}).get("matrix"):
                self.emu.wait(60)  # door/stairs fade
                end = self.location()
            note = f"entered {end.name}"
        if screen.textbox_present(grab(self.emu)):
            note = (note + "; " if note else "") + "message box open"
        return {"moved": moved, "at": str(self.location()), "note": note}

    # -- boot ----------------------------------------------------------------------------------
    def in_game(self) -> bool:
        """Save loaded AND the overworld is showing. (The title screen already holds the save in RAM
        to show CONTINUE, so RAM alone can't tell the title from the game.)"""
        try:
            return bool(self.party_address()) and screen.field_menu(grab(self.emu))
        except Exception:
            return False

    def boot(self) -> str:
        """From power-on: press A through the intro and title, then CONTINUE, until the overworld shows.
        Only call this right after launching BizHawk (A presses mid-game would talk to things)."""
        if self.in_game():
            return f"already in game at {self.location()}"
        self.emu.wait(240)
        for _ in range(40):
            self.emu.press("A", 4, 45)
            if self.in_game():
                self.emu.wait(60)
                return f"loaded save; at {self.location()}"
        return "couldn't reach the game (check look())"

    # -- navigation -----------------------------------------------------------------------------
    def exits(self) -> list[dict]:
        """Doors/warps on the current map with where they lead (from the game's event data)."""
        loc = self.location()
        out = []
        for x, y, dest in MAPS.get(loc.map_id, {}).get("warps", []):
            out.append({"x": x, "y": y, "to": MAPS.get(dest, {}).get("name", f"map {dest}"),
                        "dx": x - loc.x, "dy": y - loc.y})
        return out

    def walk_to(self, tx: int, ty: int, max_moves: int = 40, run: bool = True) -> dict:
        """Path to (tx, ty) with BFS over tiles, learning walls as it bumps into them.
        Stops on arrival, on a map change (door), or when a message box opens."""
        start = self.location()
        # Real walls come from the ROM's collision map; what we bump into on top of that is usually
        # a person standing in the way, so it's only remembered for this walk.
        bumped = {}  # tile -> leg it was bumped at; expires (people move, timing glitches happen)
        npcs = {(x, y) for x, y, _ in MAPS.get(start.map_id, {}).get("npcs", [])}
        note, legs = "", 0
        world = MAPS.get(start.map_id, {}).get("matrix")
        while legs < max_moves:
            cur = self.location()
            if cur.map_id != start.map_id and MAPS.get(cur.map_id, {}).get("matrix") != world:
                note = f"entered {cur.name}"  # went through a door into another map
                break
            if (cur.x, cur.y) == (tx, ty):
                note = "arrived"
                break
            blocked = {t for t, leg in bumped.items() if legs - leg < 4}
            path = _plan(cur.map_id, (cur.x, cur.y), (tx, ty), blocked, npcs - {(tx, ty)})
            if path is None:  # NPC spawn spots can be stale (they wander): try without them
                path = _plan(cur.map_id, (cur.x, cur.y), (tx, ty), blocked)
            if path is None and blocked:  # forget recent bumps rather than give up
                bumped.clear()
                path = _plan(cur.map_id, (cur.x, cur.y), (tx, ty))
            if not path:
                note = "no path (walls/water in the way)"
                break
            # take the first straight run of the path
            d0 = path[0]
            run_len = 1
            while run_len < len(path) and path[run_len] == d0:
                run_len += 1
            r = self.walk(d0, run_len, run=run)
            legs += 1
            if any(k in r["note"] for k in ("message box", "battle", "screen changed")):
                note = r["note"]  # interrupted: don't mistake it for a wall
                break
            if r["moved"] < run_len and "entered" not in r["note"]:
                after = self.location()
                ddx, ddy = DIRS[d0]
                bumped[(after.x + ddx, after.y + ddy)] = legs
        end = self.location()
        return {"at": str(end), "note": note or "gave up", "legs": legs}

    def heal(self) -> str:
        """In a Pokemon Center: talk to the nurse across the counter, answer Yes, wait for the heal."""
        import actions
        self.emu.wait(40)  # in case we just came through the door
        loc = self.location()
        nurse = next(((x, y) for x, y, spr in MAPS.get(loc.map_id, {}).get("npcs", []) if spr == "pcwoman1"), None)
        if nurse is None:
            return "no nurse on this map (not a Pokemon Center?)"
        r = self.walk_to(nurse[0], nurse[1] + 2)
        if (self.location().x, self.location().y) != (nurse[0], nurse[1] + 2):
            return f"couldn't reach the counter: {r['note']}"
        self.emu.press("Up", 3, 6)
        self.emu.press("A", 3, 20)
        for _ in range(4):
            res = actions.advance_text(self.emu)
            if res.stopped.startswith("waiting for a choice"):
                actions.answer(self.emu, True)
                continue
            if res.stopped == "text box closed":
                break
        party = self.party()
        return "healed: " + "; ".join(f"{m.nickname or m.name} {m.hp}/{m.max_hp}" for m in party)

    def nearest_grass(self, radius: int = 25):
        """Closest reachable tall-grass tile (walking distance), from the ROM's tile behaviors."""
        loc = self.location()
        mi = MAPS.get(loc.map_id, {}).get("matrix")
        if mi is None:
            return None
        from collections import deque
        start = (loc.x, loc.y)
        seen, q = {start}, deque([start])
        while q:
            p = q.popleft()
            if p != start and mapdata.behavior(mi, *p) in mapdata.TALL_GRASS:
                return p
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                n = (p[0] + dx, p[1] + dy)
                if n in seen or abs(n[0] - start[0]) > radius or abs(n[1] - start[1]) > radius:
                    continue
                seen.add(n)
                if not mapdata.solid(mi, *n) and mapdata.behavior(mi, *n) not in mapdata.LEDGES:
                    q.append(n)
        return None

    def seek_encounter(self, max_steps: int = 80) -> str:
        """Walk to the nearest tall grass and pace inside it until a wild battle starts."""
        g = self.nearest_grass()
        if g is None:
            return "no tall grass nearby"
        r = self.walk_to(*g, run=False)
        if "battle" in r["note"]:
            return f"battle started on the way ({self.location()})"
        loc = self.location()
        mi = MAPS[loc.map_id]["matrix"]
        steps = 0
        while steps < max_steps:
            for d, (dx, dy) in DIRS.items():  # step to a neighbouring grass tile and back
                nx, ny = loc.x + dx, loc.y + dy
                if mapdata.behavior(mi, nx, ny) in mapdata.TALL_GRASS:
                    break
            else:
                return f"standing in grass alone at {loc}; no grass neighbour to pace to"
            back = {"up": "down", "down": "up", "left": "right", "right": "left"}[d]
            for step_dir in (d, back):
                w = self.walk(step_dir, 1)
                steps += 1
                if "battle" in w["note"] or "screen changed" in w["note"]:
                    return f"battle started after {steps} steps in the grass"
                if "message box" in w["note"]:
                    return f"a message opened after {steps} steps (trainer or event)"
        return f"no encounter in {steps} steps"

    # -- money and Poke Marts -------------------------------------------------------------------
    MONEY_FROM_PARTY = -0x18  # player money (u32) sits just before the party in the save block
    SHOP_CELLS = [(64, 248), (192, 248), (64, 288), (192, 288), (64, 328), (192, 328)]  # 2x3 grid
    SHOP_HILITE = (227, 0, 0)

    def money(self) -> int | None:
        party = self.party_address()
        return struct.unpack("<I", self.emu.read(party + self.MONEY_FROM_PARTY, 4))[0] if party else None

    def shop_list(self) -> list[int] | None:
        """The open shop's items (Gen 4 item ids): the longest known Mart list present in RAM. Known lists
        come from pret's scrcmd_mart.c (badge-gated general stock + every specialty clerk)."""
        marts = pkm.DATA.get("marts", {})
        badge = marts.get("badge", [])
        cands = [[i for i, b in badge if b <= n] for n in range(17)] + marts.get("lists", [])
        # The game's code and static tables (including every specialty list) are loaded in RAM too,
        # around 0x020Fxxxx; the open shop's working copy is on the heap, well above that.
        heap = 0x150000
        ram = self.ram()
        best = None
        for c in cands:
            if len(c) >= 2 and ram.find(struct.pack(f"<{len(c)}H", *c), heap) >= 0 and (best is None or len(c) > len(best)):
                best = c
        return best

    def _shop_cursor(self, img):
        px = img.load()
        pts = [(x, y) for y in range(196, 384, 2) for x in range(0, 256, 2) if px[x, y] == self.SHOP_HILITE]
        if len(pts) < 20:
            return None
        cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
        cy = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
        if cy > 350:
            return "cancel"
        return int((cy - 230) // 40) * 2 + (0 if cx < 128 else 1)

    @staticmethod
    def _mart_menu_cursor(img):
        """BUY / SELL / SEE YA! menu (three wide buttons): which one has the red border, or None."""
        px = img.load()
        best, best_n = None, 0
        for name, y in (("buy", 192 + 30), ("sell", 192 + 78), ("quit", 192 + 126)):
            n = sum(1 for x in range(8, 248, 3) if px[x, y][0] > 200 and px[x, y][1] < 90 and px[x, y][2] < 90)
            if n > best_n:
                best, best_n = name, n
        return best if best_n > 20 else None

    def buy(self, item: str, qty: int = 1) -> str:
        """In a Poke Mart: talk to the clerk (unless the shop is already open), buy qty of an item by name,
        confirm, leave, and verify the money spent from RAM."""
        import actions
        qty = max(1, min(99, int(qty)))
        if self._shop_cursor(grab(self.emu)) is None and self._mart_menu_cursor(grab(self.emu)) is None:
            loc = self.location()
            clerk = next(((x, y) for x, y, spr in MAPS.get(loc.map_id, {}).get("npcs", [])
                          if spr.startswith("shopm")), None)
            if clerk is None:
                return "no shop clerk on this map (not in a Poke Mart?)"
            # the general-stock clerk is the lower one at the counter
            clerks = sorted(((x, y) for x, y, spr in MAPS[loc.map_id]["npcs"] if spr.startswith("shopm")),
                            key=lambda p: -p[1])
            cx, cy = clerks[0]
            r = self.walk_to(cx + 2, cy)
            if (self.location().x, self.location().y) != (cx + 2, cy):
                return f"couldn't reach the counter: {r['note']}"
            self.emu.press("Left", 3, 6)
            self.emu.press("A", 3, 20)
            for _ in range(60):  # greeting text until the BUY/SELL/SEE YA menu
                img = grab(self.emu)
                if self._mart_menu_cursor(img) or self._shop_cursor(img) is not None:
                    break
                if screen.arrow_visible(img):
                    self.emu.press("A", 3, 6)
                else:
                    self.emu.wait(10)
        if self._mart_menu_cursor(grab(self.emu)):
            for _ in range(4):
                cur = self._mart_menu_cursor(grab(self.emu))
                if cur == "buy":
                    self.emu.press("A", 3, 30)
                    break
                self.emu.press("Up", 3, 10)
        for _ in range(20):
            if self._shop_cursor(grab(self.emu)) is not None:
                break
            self.emu.wait(10)
        stock = self.shop_list()
        if not stock:
            return "shop is open but its item list wasn't recognised (look('bottom'))"
        names = [pkm.ITEMS.get(i, str(i)) for i in stock]
        want = item.lower().replace("é", "e").replace(".", "").replace(" ", "")
        idx = next((k for k, n in enumerate(names) if n.lower().replace(" ", "") == want), None)
        if idx is None:
            return f"{item} isn't sold here; stock: {', '.join(names)}"
        if idx >= len(self.SHOP_CELLS):
            return f"{item} is on a later page of this shop; paging isn't scripted yet (stock: {', '.join(names)})"
        before = self.money()
        price = pkm.DATA.get("prices", {}).get(str(stock[idx]))
        if price and before is not None and price * qty > before:
            qty = before // price
            if qty == 0:
                return f"can't afford {item} (P{price}, you have P{before})"
        bought, start_money = 0, before
        for _attempt in range(3):  # buy, check the money spent, buy the rest if a press was missed
            remaining = qty - bought
            if remaining <= 0:
                break
            for _ in range(10):  # steer the highlight to the item's cell
                cur = self._shop_cursor(grab(self.emu))
                if cur == idx:
                    break
                if cur == "cancel" or cur is None:
                    self.emu.press("Down", 3, 10)
                    continue
                (cx, cy), (tx, ty) = self.SHOP_CELLS[cur], self.SHOP_CELLS[idx]
                self.emu.press(("Down" if ty > cy else "Up") if ty != cy else ("Right" if tx > cx else "Left"), 3, 10)
            self.emu.press("A", 4, 70)  # pick the item -> "How many?" + quantity picker (starts at 1)
            for _ in range((remaining - 1) // 10):
                self.emu.press("Right", 4, 8)  # +10
            for _ in range((remaining - 1) % 10):
                self.emu.press("Up", 4, 8)  # +1
            self.emu.press("A", 4, 40)  # BUY
            self.emu.press("A", 4, 40)  # "That will be P..." -> YES
            for _ in range(8):  # "Here you are!", "You put away...", maybe a Premier Ball bonus
                if self._shop_cursor(grab(self.emu)) is not None:
                    break
                self.emu.press("A", 3, 30)
            now = self.money()
            if price and now is not None and before is not None:
                bought += (before - now) // price
                before = now
            else:
                break
        before = start_money
        self.emu.press("B", 3, 40)  # leave the item grid -> "Anything else?" BUY/SELL/SEE YA menu
        for _ in range(4):  # B on that menu is SEE YA!; stop once the overworld menu is back
            if screen.field_menu(grab(self.emu)):
                break
            self.emu.press("B", 3, 30)
            actions.advance_text(self.emu, max_messages=3)
        after = self.money()
        spent = (before - after) if before is not None and after is not None else None
        got = spent // price if spent is not None and price else None
        return (f"bought {got if got is not None else '?'} x {names[idx]} for P{spent}; money P{after}"
                if spent else f"nothing bought (money P{after}); check look()")

    def follow_scene(self, max_rounds: int = 30) -> dict:
        """Cutscenes that walk you around between messages (tours, rivals, events): keep reading
        text while the game moves the player, until it settles or asks a real choice."""
        import actions
        msgs, note = [], "scene over"
        for _ in range(max_rounds):
            r = actions.advance_text(self.emu)
            msgs += r.messages
            if r.stopped.startswith("waiting for a choice"):
                note = r.stopped
                break
            a = self.location()
            self.emu.wait(60)
            b = self.location()
            if (a.map_id, a.x, a.y) != (b.map_id, b.x, b.y) or screen.textbox_present(grab(self.emu)):
                continue  # still being walked around / next message coming
            self.emu.wait(90)
            c = self.location()
            if (c.map_id, c.x, c.y) == (b.map_id, b.x, b.y) and not screen.textbox_present(grab(self.emu)):
                break
        return {"messages": msgs, "note": note}

    # -- summary --------------------------------------------------------------------------------
    def summary(self) -> str:
        loc = self.location()
        money = self.money()
        lines = [f"at: {loc} | area: {loc.area}" + (f" | money P{money}" if money is not None else "")]
        party = self.party()
        lines.append("party: " + ("; ".join(m.line() for m in party) if party else "none"))
        names = ["hp", "atk", "def", "spe", "spa", "spd", "acc", "eva"]
        for b in self.battlers():  # only while a battle is on screen (old opponents linger in RAM)
            m = b["mon"]
            st = " ".join(f"{n}{v:+d}" for n, v in zip(names, b["stages"]) if v)
            line = (f"battle {'YOU' if b['side'] == 'you' else 'FOE'}: {m.nickname or m.name} ({m.name}) "
                    f"L{m.level} HP {b['hp']}/{b['max']} {'/'.join(m.types())}")
            if st:
                line += f" stages {st}"
            if b["side"] == "foe":
                line += " moves " + ", ".join(pkm.MOVES.get(mv, {}).get("name", str(mv)) for mv, _ in m.moves)
            lines.append(line)
        return "\n".join(lines)

    # -- battle --------------------------------------------------------------------------------
    # Bottom-screen battle menu (y offset 192 in the full image): big FIGHT button; the move grid
    # replaces it with four move boxes + CANCEL. The button-mode highlight is red corner brackets.
    MOVE_SLOTS = {1: (64, 244), 2: (192, 244), 3: (64, 300), 4: (192, 300), 0: (128, 357)}

    @staticmethod
    def _fight_menu(img) -> bool:
        r, g_, b = img.getpixel((60, 250))
        return r > 200 and g_ < 90 and b < 90  # big red FIGHT button (left of its label)

    @staticmethod
    def _bracket_slot(img):
        px = img.load()
        pts = [(x, y) for y in range(196, 384, 2) for x in range(0, 256, 2) if px[x, y] == (251, 0, 0)]
        if len(pts) < 6:
            return None
        cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
        cy = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
        return min(Game.MOVE_SLOTS, key=lambda k: (Game.MOVE_SLOTS[k][0] - cx) ** 2 + (Game.MOVE_SLOTS[k][1] - cy) ** 2)

    @staticmethod
    def _bracket_bbox(img):
        px = img.load()
        pts = [(x, y) for y in range(196, 384, 2) for x in range(0, 256, 2) if px[x, y] == (251, 0, 0)]
        if len(pts) < 6:
            return None
        return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)

    def _main_cursor(self, img):
        """Which battle-menu button is highlighted: fight, bag, run, pokemon (None if none yet)."""
        bb = self._bracket_bbox(img)
        if bb is None:
            return None
        if bb[2] - bb[0] > 150:
            return "fight"
        cx = (bb[0] + bb[2]) / 2
        return "bag" if cx < 85 else ("run" if cx < 170 else "pokemon")

    def battle_select(self, target: str) -> bool:
        """Highlight a battle-menu button (fight/bag/run/pokemon) and press A on it."""
        moves = {("fight", "bag"): "Down", ("fight", "run"): "Down", ("fight", "pokemon"): "Down",
                 ("bag", "fight"): "Up", ("run", "fight"): "Up", ("pokemon", "fight"): "Up",
                 ("bag", "run"): "Right", ("run", "pokemon"): "Right", ("bag", "pokemon"): "Right",
                 ("pokemon", "run"): "Left", ("run", "bag"): "Left", ("pokemon", "bag"): "Left"}
        for _ in range(8):
            img = grab(self.emu)
            if not screen.battle_menu(img):
                return False
            cur = self._main_cursor(img)
            if cur is None:
                self.emu.press("Up", 3, 10)  # wakes button mode (highlights FIGHT)
                continue
            if cur == target:
                self.emu.press("A", 3, 14)
                return True
            self.emu.press(moves[(cur, target)], 3, 10)
        return False

    def _wait_battle_menu(self, frames: int = 600) -> bool:
        """Press through intro/turn text until the battle menu (our action) is up."""
        for _ in range(frames // 10):
            img = grab(self.emu)
            if screen.battle_menu(img):
                return True
            if screen.field_menu(img):
                return False
            if screen.arrow_visible(img):
                self.emu.press("A", 3, 6)
            else:
                self.emu.wait(10)
        return False

    def run_away(self) -> dict:
        """Choose RUN in a wild battle and follow the result (escapes can fail)."""
        if not self._wait_battle_menu() or not self.battle_select("run"):
            return {"note": "couldn't choose RUN", "messages": []}
        return self.watch_turn()

    def catch(self, pocket_slot: int = 1) -> dict:
        """Throw a ball (first ball in the Poke Balls pocket by default) and follow the result.
        Caller handles the nickname prompt afterwards (answer + type_text)."""
        if not self.battlers():
            return {"note": "not in a battle", "messages": []}
        before = len(self.party())
        if not self._wait_battle_menu() or not self.battle_select("bag"):
            return {"note": "couldn't open the bag", "messages": []}
        self.emu.wait(20)
        # pockets: HP/PP (top-left), POKE BALLS (top-right), STATUS (bottom-left), BATTLE ITEMS (bottom-right)
        for _ in range(6):
            bb = self._bracket_bbox(grab(self.emu))
            if bb is None:
                self.emu.wait(8)
                continue
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            if cx > 128 and cy < 192 + 96:
                break
            self.emu.press("Right" if cx <= 128 else "Up", 3, 10)
        self.emu.press("A", 3, 20)          # open Poke Balls
        for _ in range(pocket_slot - 1):
            self.emu.press("Down" if pocket_slot > 2 else "Right", 3, 8)
        self.emu.press("A", 3, 20)          # pick the ball
        self.emu.press("A", 3, 10)          # USE
        r = self.watch_turn(max_frames=2400)
        party = self.party()
        caught = len(party) > before
        r["note"] = ("CAUGHT; " if caught else "") + r["note"]
        return r

    def fight(self, move: str | int = 1) -> dict:
        """Use a move (name or slot 1-4) from the battle menu, then watch the turn: collects the
        auto-advancing battle messages and stops at the next "What will X do?", a choice prompt,
        or the end of the battle."""
        lead = next((b["mon"] for b in self.battlers() if b["side"] == "you"), None)
        if lead is None:
            return {"note": "not in a battle", "messages": []}
        if isinstance(move, str) and not move.isdigit():
            names = [pkm.MOVES.get(m, {}).get("name", "").lower() for m, _ in lead.moves]
            if move.lower() not in names:
                return {"note": f"{lead.nickname or lead.name} doesn't know {move}; has {names}", "messages": []}
            slot = names.index(move.lower()) + 1
        else:
            slot = int(move)
        # get to the action prompt, then highlight FIGHT and open it
        in_grid = self._bracket_slot(grab(self.emu)) in (1, 2, 3, 4) and not screen.battle_menu(grab(self.emu))
        if not in_grid:
            if not self._wait_battle_menu() or not self.battle_select("fight"):
                return {"note": "couldn't open FIGHT", "messages": []}
            self.emu.wait(12)
        for _ in range(8):
            cur = self._bracket_slot(grab(self.emu))
            if cur is None:
                self.emu.wait(6)
                continue
            if cur == slot:
                break
            (cx, cy), (tx, ty) = self.MOVE_SLOTS[cur], self.MOVE_SLOTS[slot]
            self.emu.press(("Down" if ty > cy else "Up") if abs(ty - cy) > 10 else ("Right" if tx > cx else "Left"), 3, 8)
        prompt = screen.digest(screen.textbox(grab(self.emu)))
        self.emu.press("A", 3, 10)
        return self.watch_turn(skip=prompt)

    def fight_until(self, move: str | int = 1, stop_below: float = 0.4, max_turns: int = 12) -> dict:
        """Repeat a move until the battle ends. Stops early (returning control) if our active
        Pokemon falls below stop_below of its max HP, or on any prompt that isn't our next move."""
        msgs, note, turns = [], "turn limit", 0
        while turns < max_turns:
            r = self.fight(move)
            turns += 1
            msgs += r["messages"]
            if r["note"] != "your move":
                note = r["note"]
                break
            me = next((b for b in self.battlers() if b["side"] == "you"), None)
            if me and me["hp"] < stop_below * me["max"]:
                note = f"stopped: HP low ({me['hp']}/{me['max']})"
                break
        return {"note": f"{note} after {turns} turns", "messages": msgs}

    def watch_turn(self, max_frames: int = 1800, skip: str | None = None) -> dict:
        """Follow a battle turn: keep each finished message (they auto-advance), press A where the game
        waits for it, stop at the next action prompt, a choice, or the end of the battle."""
        msgs, last, cur_crop, cur_ink, frames, note = [], skip, None, 0, 0, "timeout"
        started = False  # the old "What will X do?" prompt lingers for a moment after choosing
        idle, idle_ink = 0, 0
        while frames < max_frames:
            img = grab(self.emu)
            if not started:
                if screen.choice_prompt(img) and frames < 240:
                    self.emu.wait(8)
                    frames += 8
                    continue
                started = True
            if screen.field_menu(img):
                note = "battle over"
                break
            if screen.textbox_present(img):
                crop, ink = screen.textbox(img), screen.ink(img)
                if cur_crop is not None and ink < cur_ink:  # a new message replaced the old one
                    d = screen.digest(cur_crop)
                    if d != last:
                        msgs.append(cur_crop)
                        last = d
                cur_crop, cur_ink = crop, ink
                if screen.choice_prompt(img):
                    # battle menu showing = our turn again; otherwise e.g. learn a move / switch in.
                    # The menu slides in a moment after the prompt, so give it a few frames.
                    for _ in range(6):
                        if self._fight_menu(img) or self._bracket_slot(img) is not None:
                            break
                        self.emu.wait(8)
                        img = grab(self.emu)
                    if screen.battle_menu(img):
                        note = "your move"
                        break
                    if screen.yesno_buttons(img):
                        note = "choice prompt (Yes/No)"  # e.g. nickname, learn a move, switch
                        break
                    # marker without menu/buttons: transient (ball shaking, message changing) - keep going
                if screen.arrow_visible(img):
                    self.emu.press("A", 3, 6)
                    frames += 9
                    idle = 0
                    continue
                idle = idle + 1 if ink <= idle_ink else 0
                idle_ink = max(idle_ink, ink) if idle else ink
                if idle >= 12:  # ~1.5 s, nothing printing, no arrow, no choice: e.g. level-up stats
                    self.emu.press("A", 3, 10)
                    idle, idle_ink = 0, 0
                    continue
            self.emu.wait(8)
            frames += 8
        if cur_crop is not None and screen.digest(cur_crop) != last:
            msgs.append(cur_crop)
        return {"note": note, "messages": msgs}

    def battlers(self) -> list[dict]:
        """Live battle data from the BattleMon structs (0xC0 apart; HP +0x4C, max HP +0x50, stat
        stages +0x18, level +0x34). The block is found once per battle by species + stats (copied from
        the party data) and then read by address, so level-ups mid-battle don't lose it."""
        if screen.field_menu(grab(self.emu)):
            self.battle_base = None
            return []
        party, opp = self.party(), self.opponents()
        mons = {m.species: m for m in opp}
        mons.update({m.species: m for m in party})  # if both sides share a species, prefer ours
        mine = {m.pid for m in party}
        base = getattr(self, "battle_base", None)
        if base is None:
            ram = self.ram()
            for m in party + opp:
                i = ram.find(struct.pack("<6H", m.species, *m.stats))
                if i >= 0:
                    # step back to slot 0 while the previous 0xC0 slot also looks like a BattleMon
                    while i >= 0xC0 and struct.unpack_from("<H", ram, i - 0xC0)[0] in mons:
                        i -= 0xC0
                    base = RAM_BASE + i
                    break
            if base is None:
                return []
            self.battle_base = base
        block = self.emu.read(base, 4 * 0xC0)
        out = []
        for k in range(4):
            o = k * 0xC0
            sp, = struct.unpack_from("<H", block, o)
            if sp not in mons:
                continue
            hp, = struct.unpack_from("<H", block, o + 0x4C)
            mx, = struct.unpack_from("<H", block, o + 0x50)
            if not 0 < mx < 1000 or hp > mx:
                continue
            m = mons[sp]
            out.append({"mon": m, "side": "you" if k % 2 == 0 else "foe", "hp": hp, "max": mx,
                        "level": block[o + 0x34], "stages": [v - 6 for v in block[o + 0x18:o + 0x20]]})
        return out
