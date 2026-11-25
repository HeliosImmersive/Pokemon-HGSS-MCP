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
PLAYTIME_FROM_PARTY = -0x0A  # u16 hours, u8 minutes, u8 seconds (save offset 0x86; party header at 0x90)
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
        if (x, y) == goal:
            return True
        if (x, y) in warps:  # stepping on another door/stairs on the way would warp us off the path
            return False
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
            else:  # new game, no Pokemon yet: find the record itself
                self.loc_addr = self._scan_location() or self.loc_addr
            m, w, x, y = struct.unpack("<4I", self.emu.read(self.loc_addr, 16))
        return Location(m, x, y)

    def _scan_location(self) -> int | None:
        """Before the first Pokemon there's no party to anchor on: look for the save block's
        Location record directly (a real map id, a warp index or -1, small x/y) in the heap."""
        # Save block layout around it: trainer name (8 x u16, 0xFFFF-terminated), ID (u32), money (u32),
        # ... party (money + 0x18), ... Location (money + 0x11BC). Require all three to look right.
        ram = self.ram()
        for off in range(0x200000, len(ram) - 0x1200, 4):
            money, = struct.unpack_from("<I", ram, off)
            if money > 999999:
                continue
            m, w, x, y = struct.unpack_from("<4I", ram, off + 0x11BC)
            if not (4 <= m < 600 and m in MAPS and (w == 0xFFFFFFFF or w < 64) and 0 < x < 2048 and 0 < y < 2048):
                continue
            name = struct.unpack_from("<8H", ram, off - 0x14)
            if 0xFFFF not in name or not all(0x0121 <= c <= 0x015E for c in name[:name.index(0xFFFF)]) \
                    or name.index(0xFFFF) == 0:
                continue
            return RAM_BASE + off + 0x11BC
        return None

    # -- party / battle --------------------------------------------------------------------------
    def parties(self) -> list[pkm.Party]:
        return pkm.scan_parties(self.ram())

    def party_address(self) -> int | None:
        """The player's party: the Party struct whose save block has a valid Location record after it.
        (The save block moves between a new game and a loaded save, so this is found by scanning.)"""
        if self.party_addr:
            for _ in range(3):  # a member can be mid-update for a frame (healing, EXP, walking counters)
                p = pkm.read_party(self.emu.read(self.party_addr, 8 + 6 * pkm.PKM_SIZE), self.party_addr)
                if p:
                    return self.party_addr
                self.emu.wait(2)
        self.party_addr = None
        # Two copies of the save block can hold a party: the live one and the save as loaded at the title
        # screen (stale). The live one's play-time counter keeps ticking, so the copy with the most play
        # time wins (ties: lowest address). Scan twice: a copy can be unreadable for a frame mid-update.
        cands = {}
        ram = self.ram()
        for hit in pkm._PARTY_HDR.finditer(ram):
            off = hit.start()
            if off % 4 or off + PARTY_TO_LOC + 16 > len(ram) or off < 0x10:
                continue
            m, w, x, y = struct.unpack_from("<4I", ram, off + PARTY_TO_LOC)
            # warp = index of the door used to arrive (-1 if none)
            if not (self._plausible_loc(m, w, x, y) and m > 3 and x and y):
                continue
            addr = RAM_BASE + off
            # a member can be caught mid-update (checksum fails for a frame): re-read just this party
            for _ in range(4):
                if pkm.read_party(self.emu.read(addr, 8 + 6 * pkm.PKM_SIZE), addr):
                    hrs, mins, secs = struct.unpack_from("<HBB", ram, off + PLAYTIME_FROM_PARTY)
                    cands[addr] = hrs * 3600 + mins * 60 + secs
                    break
                self.emu.wait(2)
        if cands:
            self.party_addr = max(cands, key=lambda a: (cands[a], -a))
        return self.party_addr

    def party(self) -> list[pkm.Pokemon]:
        addr = self.party_address()
        if not addr:
            return []
        for _ in range(3):
            p = pkm.read_party(self.emu.read(addr, 8 + 6 * pkm.PKM_SIZE), addr)
            if p:
                return p.members
            self.emu.wait(2)
        return []

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
    def _warp_route(self, loc, goal, max_nodes: int = 300):
        """Shortest walk through doors/stairs from where we stand to any map satisfying goal(map_id), over
        indoor maps (towers and caves split floors into sections, so walkability between warp tiles is
        checked with the planner). Returns the first warp tile to walk onto, or None. Arrival tiles are
        approximated: the destination's warp back to where we came from, nearest the door we took."""
        import heapq
        def indoor(mid):
            m = MAPS.get(mid, {}).get("matrix")
            return not (m is not None and mapdata.chunk_maps(m))
        start = (loc.map_id, loc.x, loc.y)
        best, first, heap, n = {start: 0}, {start: None}, [(0, start)], 0
        while heap and n < max_nodes:
            cost, node = heapq.heappop(heap)
            n += 1
            m, x, y = node
            if node != start and goal(m):
                return first[node]
            if node != start and not indoor(m):
                continue  # don't wander the overworld here; go_to's outdoor branch does that
            for wx, wy, dest in MAPS.get(m, {}).get("warps", []):
                if dest not in MAPS or ((wx, wy) == (x, y) and node != start):
                    continue
                path = [] if (wx, wy) == (x, y) else _plan(m, (x, y), (wx, wy), margin=20)
                if path is None:
                    continue
                back = [(ax, ay) for ax, ay, d2 in MAPS[dest].get("warps", []) if d2 == m]
                if back:
                    ax, ay = min(back, key=lambda t: abs(t[0] - wx) + abs(t[1] - wy))
                else:
                    ax, ay = wx, wy
                nxt, c = (dest, ax, ay), cost + len(path) + 1
                if c < best.get(nxt, 1 << 30):
                    best[nxt] = c
                    first[nxt] = first[node] or (wx, wy)
                    heapq.heappush(heap, (c, nxt))
        return None

    def go_to(self, place: str, max_legs: int = 120, came_from: int | None = None) -> str:
        """Walk to a town/route on the current overworld by name ("Cherrygrove", "Route 30"), using the
        ROM's chunk -> map table to find where it starts. Stops for battles, trainers and events like walk_to."""
        loc = self.location()
        mi = MAPS.get(loc.map_id, {}).get("matrix")
        cm = mapdata.chunk_maps(mi) if mi is not None else None
        if not cm:  # inside: take a door that leads there, else one that leads outside
            if max_legs <= 0:
                return f"stuck going between buildings; now {loc}"
            norm = lambda t: t.lower().replace("route ", "route")
            def outdoors(mid):
                m = MAPS.get(mid, {}).get("matrix")
                return m is not None and bool(mapdata.chunk_maps(m))
            doors = self.exits()
            named = lambda mid: norm(place) in norm(MAPS.get(mid, {}).get("name", ""))
            # "violet" means the city, not "Violet Pokecenter B1f": only route to a building by name when
            # no outdoor map answers to that name
            outdoor_place = any(named(mid) and outdoors(mid) for mid in MAPS)
            hop = ((None if outdoor_place else self._warp_route(loc, lambda mid: named(mid) and not outdoors(mid)))
                   or self._warp_route(loc, lambda mid: outdoors(mid) and mid != came_from)
                   or self._warp_route(loc, outdoors))
            door = next((d for d in doors if hop and (d["x"], d["y"]) == hop), None)
            if door is None:
                return "not on an overworld map and no door out found (use exits())"
            r = self.walk_to(door["x"], door["y"])
            now = self.location()
            if now.map_id == loc.map_id:
                return f"couldn't leave through ({door['x']},{door['y']}): {r['note']}"
            if norm(place) in norm(now.name):
                return f"reached: {r['note']}; now {now}"
            return self.go_to(place, max_legs - r["legs"] - 5, came_from=loc.map_id)
        w, h, ids = cm
        want = place.lower().replace("route ", "route")
        def matches(mid):
            m = MAPS.get(mid, {})
            names = (m.get("name", ""), m.get("area", ""))
            return any(want in n.lower().replace("route ", "route") for n in names)
        chunks = [(i % w, i // w) for i, mid in enumerate(ids) if matches(mid)]
        if not chunks:  # maybe a building or a floor of one ("sprout tower 3f", "violet gym")
            norm = lambda t: t.lower().replace("route ", "route")
            hop = self._warp_route(loc, lambda mid: norm(place) in norm(MAPS.get(mid, {}).get("name", "")))
            if hop is None:
                return f"'{place}' isn't on this map or reachable through a door from here"
            r = self.walk_to(*hop)
            if self.location().map_id == loc.map_id:
                return f"stopped on the way to the door at {hop}: {r['note']}; now {r['at']}"
            if norm(place) in norm(self.location().name):
                return f"reached: {r['note']}; now {self.location()}"
            return self.go_to(place, max_legs - r["legs"] - 5, came_from=loc.map_id)
        if matches(loc.map_id):
            return f"already in {loc.name}"
        # candidate tiles: walkable tiles of the target chunks, nearest first (straight-line)
        cands = []
        warps = {(x, y) for m in MAPS.values() if m.get("matrix") == mi for x, y, _ in m.get("warps", [])}
        for cx, cy in chunks:
            for ty in range(cy * 32, cy * 32 + 32):
                for tx in range(cx * 32, cx * 32 + 32):
                    if (not mapdata.solid(mi, tx, ty) and (tx, ty) not in warps
                            and mapdata.behavior(mi, tx, ty) not in mapdata.LEDGES):
                        cands.append((abs(tx - loc.x) + abs(ty - loc.y), tx, ty))
        cands.sort()
        goal = None
        for _, tx, ty in cands[:: max(1, len(cands) // 40)][:40]:  # spread-out sample of the nearest side
            if _plan(loc.map_id, (loc.x, loc.y), (tx, ty), margin=60) is not None:
                goal = (tx, ty)
                break
        if goal is None:
            # gatehouse pattern: a building whose other door opens onto a different outdoor map
            if max_legs > 0:
                def leads_there(d):  # a door out of that building opens onto the destination
                    return any(matches(dest) for _, _, dest in MAPS.get(d["id"], {}).get("warps", []))
                for d in sorted(self.exits(), key=lambda d: (not leads_there(d), abs(d["dx"]) + abs(d["dy"]))):
                    inner = MAPS.get(d["id"], {})
                    if inner.get("matrix") is not None and mapdata.chunk_maps(inner["matrix"]):
                        continue  # leads outdoors directly
                    if not any(dest != loc.map_id and MAPS.get(dest, {}).get("matrix") == mi
                               for _, _, dest in inner.get("warps", [])):
                        continue
                    r = self.walk_to(d["x"], d["y"])
                    if self.location().map_id == d["id"]:
                        return self.go_to(place, max_legs - r["legs"] - 5, came_from=loc.map_id)
                    if any(k in r["note"] for k in ("battle", "message box", "phone")):
                        return f"stopped on the way to the {d['to']} door: {r['note']}; now {r['at']}"
            return f"no walkable way to {place} found from here (water/cut trees/ledges?)"
        r = self.walk_to(*goal, max_moves=max_legs)
        now = self.location()
        reached = matches(now.map_id)
        return (f"{'reached' if reached else 'stopped'}: {r['note']}; now {r['at']}"
                + ("" if reached else f" (target tile {goal})"))

    def exits(self) -> list[dict]:
        """Doors/warps on the current map with where they lead (from the game's event data)."""
        loc = self.location()
        out = []
        for x, y, dest in MAPS.get(loc.map_id, {}).get("warps", []):
            out.append({"x": x, "y": y, "id": dest, "to": MAPS.get(dest, {}).get("name", f"map {dest}"),
                        "dx": x - loc.x, "dy": y - loc.y})
        return out

    def _trigger_warp(self, map_id: int, tx: int, ty: int, last_dir: str) -> str | None:
        """Standing on a warp tile that didn't fire. Doors and exit mats fire when you push into the wall
        behind them; stairs fire only when stepped onto from their open side. Try pushing toward walls
        first, then step off and back on from each side. Returns the "entered ..." note, or None."""
        back = {"up": "down", "down": "up", "left": "right", "right": "left"}
        mi = MAPS.get(map_id, {}).get("matrix")
        walls = [d for d, (dx, dy) in DIRS.items() if mi is None or mapdata.solid(mi, tx + dx, ty + dy)]
        for d in dict.fromkeys(([last_dir] if last_dir in walls else []) + walls):
            r = self.walk(d, 1)
            if "entered" in r["note"]:
                return r["note"]
            if (self.location().x, self.location().y) != (tx, ty):
                break
        for d in [d for d in DIRS if d not in walls]:
            here = self.location()
            if here.map_id != map_id:
                return f"entered {here.name}"
            if (here.x, here.y) == (tx, ty):
                r = self.walk(d, 1)  # step off...
                if "entered" in r["note"]:
                    return r["note"]
                if (self.location().x, self.location().y) == (tx, ty):
                    continue
            r = self.walk(back[d], 1)  # ...and back on, facing the other way
            if "entered" in r["note"]:
                return r["note"]
        return None

    def walk_to(self, tx: int, ty: int, max_moves: int = 40, run: bool = True) -> dict:
        """Path to (tx, ty) with BFS over tiles, learning walls as it bumps into them.
        Stops on arrival, on a map change (door), or when a message box opens."""
        start = self.location()
        # Real walls come from the ROM's collision map; what we bump into on top of that is usually
        # a person standing in the way, so it's only remembered for this walk.
        bumped = {}  # tile -> leg it was bumped at; expires (people move, timing glitches happen)
        npcs = {(x, y) for x, y, _ in MAPS.get(start.map_id, {}).get("npcs", [])}
        note, legs, last_dir = "", 0, "down"
        world = MAPS.get(start.map_id, {}).get("matrix")
        while legs < max_moves:
            cur = self.location()
            if cur.map_id != start.map_id and MAPS.get(cur.map_id, {}).get("matrix") != world:
                note = f"entered {cur.name}"  # went through a door into another map
                break
            if (cur.x, cur.y) == (tx, ty):
                note = "arrived"
                warps = {(x, y) for x, y, _ in MAPS.get(cur.map_id, {}).get("warps", [])}
                if (tx, ty) in warps and legs:  # exit mats/stairs/doors need one more push to warp
                    note = self._trigger_warp(cur.map_id, tx, ty, last_dir) or note
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
            last_dir = d0
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

    def npcs(self) -> list:
        """People on the current map: spawn tile + sprite name from the event data (they may wander)."""
        loc = self.location()
        return [{"x": x, "y": y, "sprite": spr, "dx": x - loc.x, "dy": y - loc.y}
                for x, y, spr in MAPS.get(loc.map_id, {}).get("npcs", [])]

    def talk(self, who: str = "") -> str:
        """Walk next to a person (sprite-name substring from npcs(); the nearest one if empty), face them and
        press A. Tries each side of them, then across a counter. Follow with read_text()."""
        loc = self.location()
        people = [p for p in self.npcs() if who.lower() in p["sprite"]]
        if not people:
            names = ", ".join(sorted({p["sprite"] for p in self.npcs()})) or "nobody"
            return f"no '{who}' on this map (here: {names})"
        mi = MAPS.get(loc.map_id, {}).get("matrix")
        others = {(p["x"], p["y"]) for p in self.npcs()}
        for person in sorted(people, key=lambda p: abs(p["dx"]) + abs(p["dy"])):
            x, y = person["x"], person["y"]
            spots = []
            for d, (dx, dy) in DIRS.items():  # stand on the side opposite d, facing d
                for reach in (1, 2):  # 2 = across a counter (the tile between must be solid)
                    sx, sy = x - dx * reach, y - dy * reach
                    if mi is not None and mapdata.solid(mi, sx, sy):
                        continue
                    if reach == 2 and (mi is None or not mapdata.solid(mi, x - dx, y - dy)):
                        continue
                    here = (loc.x, loc.y) == (sx, sy)
                    path = [] if here else _plan(loc.map_id, (loc.x, loc.y), (sx, sy), avoid=others)
                    if path is not None:
                        spots.append((len(path) + 10 * (reach - 1), (sx, sy), d))
            for _, (sx, sy), d in sorted(spots)[:3]:
                if (self.location().x, self.location().y) != (sx, sy):
                    self.walk_to(sx, sy)
                cur = self.location()
                if (cur.x, cur.y) != (sx, sy):
                    if screen.textbox_present(grab(self.emu)):
                        return f"interrupted on the way at {cur}: a message is open (read_text)"
                    continue
                self.emu.press(d.capitalize(), 3, 8)  # a tap turns without stepping (they block the tile)
                if (self.location().x, self.location().y) != (sx, sy):
                    continue  # they had wandered off and we stepped: try again from here
                self.emu.press("A", 3, 30)
                if screen.textbox_present(grab(self.emu)) or not screen.field_menu(grab(self.emu)):
                    return f"talking to {person['sprite']} (from {cur}, facing {d}); use read_text"
            # nobody answered at this spot: they've probably wandered; try the next match
        return f"couldn't reach or get a reply from '{who or 'anyone'}' (people wander: look() and walk next to them)"

    def _party_slot(self, who) -> int | None:
        party = self.party()
        if isinstance(who, int) or str(who).isdigit():
            n = int(who)
            return n if 1 <= n <= len(party) else None
        names = [(m.nickname or m.name).lower() for m in party]
        return names.index(str(who).lower()) + 1 if str(who).lower() in names else None

    def _grid_move(self, frm: int, to: int) -> None:
        """Party screen: 2 columns x 3 rows, slot 1 top-left, 2 top-right, 3 below 1..."""
        (r0, c0), (r1, c1) = divmod(frm - 1, 2), divmod(to - 1, 2)
        for _ in range(abs(r1 - r0)):
            self.emu.press("Down" if r1 > r0 else "Up", 3, 10)
        for _ in range(abs(c1 - c0)):
            self.emu.press("Right" if c1 > c0 else "Left", 3, 10)

    @staticmethod
    def _submenu_buttons(img) -> int:
        """Orange/yellow option buttons stacked in the party submenu's right column (SUMMARY, SWITCH, ITEM,
        plus field moves); QUIT sits apart in the corner."""
        px = img.load()
        runs, run = 0, 0
        for y in range(192 + 10, 192 + 150):
            r, g, b = px[190, y]
            if r > 200 and g > 100 and b < 170:
                run += 1
            else:
                if run >= 10:
                    runs += 1
                run = 0
        return runs + (1 if run >= 10 else 0)

    def swap(self, a, b) -> str:
        """Swap two party members (slot number 1-6 or nickname) from the field: X -> POKEMON -> pick ->
        SWITCH -> pick the other. Checked against the party order in RAM."""
        import actions
        sa, sb = self._party_slot(a), self._party_slot(b)
        party = self.party()
        if not sa or not sb or sa == sb:
            names = ", ".join(f"{i + 1} {m.nickname or m.name}" for i, m in enumerate(party))
            return f"pick two different party members (slots or nicknames): {names}"
        before = [m.pid for m in party]
        r = actions.open_menu(self.emu, "pokemon")
        if not r.startswith("opened"):
            return r
        self.emu.wait(40)
        self._grid_move(1, sa)
        self.emu.press("A", 3, 30)
        n = self._submenu_buttons(grab(self.emu))
        if n < 3:
            self.emu.press("B", 3, 20)
        else:
            for _ in range(n - 2):  # SWITCH is the second-to-last button (just above ITEM)
                self.emu.press("Down", 3, 10)
            self.emu.press("A", 3, 30)
            self._grid_move(sa, sb)
            self.emu.press("A", 3, 60)
        for _ in range(4):  # back out to the field
            if screen.field_menu(grab(self.emu)) and actions._menu_cursor(grab(self.emu)) is not None:
                break
            self.emu.press("B", 3, 30)
        self.emu.press("B", 3, 10)  # close the field menu's button mode
        after = [m.pid for m in self.party()]
        want = before[:]
        want[sa - 1], want[sb - 1] = want[sb - 1], want[sa - 1]
        order = ", ".join(f"{i + 1} {m.nickname or m.name}" for i, m in enumerate(self.party()))
        if after == want:
            return f"swapped; party: {order}"
        return f"swap didn't take (submenu had {n} buttons); party: {order} (look('bottom'))"

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
        """Walk to the nearest tall grass and pace inside it until a wild battle starts. Without grass
        (caves, towers: encounters on the floor itself), pace back and forth on open floor."""
        g = self.nearest_grass()
        if g is None:
            loc = self.location()
            mi = MAPS.get(loc.map_id, {}).get("matrix")
            warps = {(x, y) for x, y, _ in MAPS.get(loc.map_id, {}).get("warps", [])}
            npcs = {(x, y) for x, y, _ in MAPS.get(loc.map_id, {}).get("npcs", [])}
            free = lambda x, y: (mi is None or not mapdata.solid(mi, x, y)) and (x, y) not in warps | npcs
            pair = next(((d, back) for d, back in (("left", "right"), ("up", "down"))
                         if free(loc.x + DIRS[d][0], loc.y + DIRS[d][1])), None)
            if pair is None:
                return "no tall grass nearby and no open floor to pace on"
            steps = 0
            while steps < max_steps:
                for step_dir in pair:
                    w = self.walk(step_dir, 1)
                    steps += 1
                    if "battle" in w["note"] or "screen changed" in w["note"]:
                        return f"battle started after {steps} steps on the floor (no grass here)"
                    if "message box" in w["note"]:
                        return f"a message opened after {steps} steps (trainer or event)"
                    if "entered" in w["note"]:
                        return f"walked through a warp while pacing: {w['note']}"
            return f"no encounter in {steps} steps on the floor: this map may have no wild Pokemon"
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

    # Bag pockets in the save block, as offsets from the party header (save offsets 0x644.. minus 0x90),
    # with their slot counts. Each slot = u16 item id + u16 quantity.
    BAG_POCKETS = [("Items", 0x5B4, 165), ("Key Items", 0x848, 50), ("TMs & HMs", 0x910, 101),
                   ("Mail", 0xAA4, 12), ("Medicine", 0xAD4, 40), ("Berries", 0xB74, 64),
                   ("Poke Balls", 0xC74, 24), ("Battle Items", 0xCD4, 30)]

    def bag(self) -> dict[str, list[tuple[str, int]]]:
        """Items per Bag pocket, read from the save block in RAM: {"Poke Balls": [("Poke Ball", 5)], ...}."""
        party = self.party_address()
        if not party:
            return {}
        out = {}
        for name, off, n in self.BAG_POCKETS:
            raw = self.emu.read(party + off, n * 4)
            slots = [struct.unpack_from("<HH", raw, i * 4) for i in range(n)]
            items = [(pkm.ITEMS.get(i, f"item {i}"), q) for i, q in slots if i and q and i < 600 and q < 1000]
            if items:
                out[name] = items
        return out

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
            # the border's row shifts by a few pixels (menu slide-in): take the best row near y
            n = max(sum(1 for x in range(8, 248, 3) if px[x, yy][0] > 200 and px[x, yy][1] < 90 and px[x, yy][2] < 90)
                    for yy in range(y - 6, y + 7))
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
        for _ in range(8):  # steer to SEE YA! and confirm; stop once the overworld menu is back
            img = grab(self.emu)
            if screen.field_menu(img):
                break
            cur = self._mart_menu_cursor(img)
            if cur == "quit":
                self.emu.press("A", 3, 30)
            elif cur is not None:
                self.emu.press("Down", 3, 10)
                continue
            elif self._shop_cursor(img) is not None:
                self.emu.press("B", 3, 30)  # still in the item grid
            else:
                self.emu.press("A", 3, 20)  # "Please come again!"
            self.emu.wait(20)
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
    TID_FROM_PARTY = -0x1C  # u16 trainer ID, then u16 secret ID (save offsets 0x74/0x76)

    def is_shiny(self, mon) -> bool:
        """Gen 4: shiny when TID ^ SID ^ PID-high ^ PID-low < 8."""
        party = self.party_address()
        if not party:
            return False
        tid, sid = struct.unpack("<HH", self.emu.read(party + self.TID_FROM_PARTY, 4))
        return (tid ^ sid ^ (mon.pid >> 16) ^ (mon.pid & 0xFFFF)) < 8

    @staticmethod
    def family(species: int) -> int:
        """Root of the evolution line (Pidgeotto -> Pidgey)."""
        seen = set()
        while species not in seen:
            seen.add(species)
            prev = pkm.SPECIES.get(species, {}).get("evolves_from") or 0
            if not prev:
                break
            species = prev
        return species

    def dupe_of(self, species: int) -> str | None:
        """Dupes clause: name of an owned Pokemon (party or caught in the journal) in the same line."""
        import journal
        fam = self.family(species)
        for m in self.party():
            if self.family(m.species) == fam:
                return m.nickname or m.name
        by_name = {v.get("name", "").lower(): int(k) for k, v in pkm.DATA.get("species", {}).items()}
        for e in journal.load().get("encounters", {}).values():
            sp = by_name.get(str(e.get("species", "")).lower())
            if e.get("result") == "caught" and sp and self.family(sp) == fam:
                return e.get("nickname") or e.get("species")
        return None

    def summary(self) -> str:
        loc = self.location()
        money = self.money()
        lines = [f"at: {loc} | area: {loc.area}" + (f" | money P{money}" if money is not None else "")]
        party = self.party()
        lines.append("party: " + ("; ".join(m.line() for m in party) if party else "none"))
        if party:
            balls = self.bag().get("Poke Balls", [])
            lines.append("balls: " + (", ".join(f"{n} x{q}" for n, q in balls) if balls else "none"))
        names = ["hp", "atk", "def", "spe", "spa", "spd", "acc", "eva"]
        for b in self.battlers():  # only while a battle is on screen (old opponents linger in RAM)
            m = b["mon"]
            st = " ".join(f"{n}{v:+d}" for n, v in zip(names, b["stages"]) if v)  # announced in battle
            if b["side"] == "you":
                line = (f"battle YOU: {m.nickname or m.name} ({m.name}) L{m.level} HP {b['hp']}/{b['max']} "
                        f"{'/'.join(m.types())}")
                if st:
                    line += f" stages {st}"
            else:
                # Only what the game shows the player: name, level, gender, HP bar, status icon, announced
                # stat changes and the moves it has used. No types, exact HP or unseen moves.
                pct = b["hp"] * 100 // b["max"] if b["max"] else 0
                bar = "green" if pct > 50 else "yellow" if pct > 20 else "red"
                shown = 0 if b["hp"] == 0 else max(5, int(round(pct / 5.0)) * 5)
                sex = {0: " ♂", 1: " ♀"}.get(b.get("gender"), "")
                status = self.status_text(b.get("status", 0))
                line = (f"battle FOE: {m.nickname or m.name} L{b['level']}{sex} HP bar ~{shown}% ({bar})"
                        + (f" {status}" if status else ""))
                if st:
                    line += f" stages {st}"
                seen = self.seen_moves(b)
                line += " | moves seen: " + (", ".join(seen) if seen else "none yet")
                if self.is_shiny(m):
                    line += " | SHINY (shiny clause: may be caught, doesn't use the area's encounter)"
                dupe = self.dupe_of(m.species)
                if dupe:
                    line += f" | if wild: DUPE of {dupe}'s line (dupes clause: doesn't count, keep looking)"
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

    def switch_to(self, who) -> dict:
        """In battle: send in another party member (nickname, or slot in the party order). The battle's
        party screen puts the active Pokemon first and swaps on every switch; that order is read from the
        battle's own copy of the party (the copy of my team whose first member is the active battler)."""
        active = next((b["mon"] for b in self.battlers() if b["side"] == "you"), None)
        if active is None:
            return {"note": "not in a battle", "messages": []}
        party = self.party()
        slot = self._party_slot(who)
        if not slot:
            return {"note": f"no party member '{who}'", "messages": []}
        target = party[slot - 1].pid
        if target == active.pid:
            return {"note": f"{who} is already in battle", "messages": []}
        mine = {m.pid for m in party}
        if not self._wait_battle_menu() or not self.battle_select("pokemon"):
            return {"note": "couldn't open the POKEMON menu", "messages": []}
        self.emu.wait(40)
        # the screen's order lives in a party copy that exists only while this screen is open
        copies = [q for q in self.parties() if {m.pid for m in q.members} == mine and q.members[0].pid == active.pid]
        order = max(copies, key=lambda q: q.addr).members if copies else party
        pos = next((i + 1 for i, m in enumerate(order) if m.pid == target), None)
        if pos is None or pos == 1 or order[pos - 1].hp <= 0:
            self.emu.press("B", 3, 30)
            why = "has fainted" if pos and order[pos - 1].hp <= 0 else "couldn't be found on the screen"
            return {"note": f"{who} {why}", "messages": []}
        self._grid_move(1, pos)
        self.emu.press("A", 3, 30)   # -> big SHIFT button (highlighted)
        self.emu.press("A", 3, 20)   # SHIFT
        return self.watch_turn()

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
            side = "you" if k % 2 == 0 else "foe"
            own = {x.species: x for x in (party if side == "you" else opp)}
            m = own.get(sp, mons[sp])  # the foe's Rattata isn't my NIBBLES just because it's a Rattata
            # BattleMon (pret include/battle/battle.h): moves +0x0C (4 x u16), current PP +0x2C, PP Ups +0x30,
            # status +0x6C, gender +0x7E (low 4 bits)
            moves = struct.unpack_from("<4H", block, o + 0x0C)
            out.append({"mon": m, "side": side, "hp": hp, "max": mx,
                        "level": block[o + 0x34], "stages": [v - 6 for v in block[o + 0x18:o + 0x20]],
                        "moves": [(mv, block[o + 0x2C + i], block[o + 0x30 + i]) for i, mv in enumerate(moves)],
                        "status": struct.unpack_from("<I", block, o + 0x6C)[0],
                        "gender": block[o + 0x7E] & 0x0F})
        return out

    @staticmethod
    def seen_moves(b: dict) -> list[str]:
        """A foe's moves the game has shown: any move with PP spent (using a move costs PP even when it
        misses or fails; Spite's message names the move too)."""
        out = []
        for mv, pp, ups in b.get("moves", []):
            base = pkm.MOVES.get(mv, {}).get("pp")
            if mv and base and pp < base * (5 + ups) // 5:
                out.append(pkm.MOVES[mv]["name"])
        return out

    @staticmethod
    def status_text(st: int) -> str:
        if st & 0x7:
            return "SLP"
        return next((name for bit, name in ((0x8, "PSN"), (0x80, "PSN"), (0x10, "BRN"), (0x20, "FRZ"), (0x40, "PAR"))
                     if st & bit), "")
