"""Build <data>/gen4.json: species, moves, items and type chart as of HeartGold/SoulSilver, plus maps
(names, areas, warps, signs, NPCs, collision matrix ids).

Sources are downloaded on first run (nothing game-derived is shipped in this repository):
  - PokeAPI CSVs        github.com/PokeAPI/pokeapi (BSD-3-Clause)
  - pret/pokeheartgold   map constants, map headers, zone events (github.com/pret/pokeheartgold)
Run: python setup.py   (or python hgss_mcp/build_data.py)
"""
import csv
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DATA  # noqa: E402


SRC = DATA / "pokeapi"
HGSS = 10  # version group id; Gen 4

POKEAPI = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/"
POKEAPI_FILES = ["pokemon_species", "moves", "items", "item_game_indices", "types", "type_efficacy",
                 "pokemon_types", "pokemon_stats", "move_changelog", "pokemon_types_past",
                 "type_efficacy_past", "move_damage_classes"]
PRET_RAW = "https://raw.githubusercontent.com/pret/pokeheartgold/master/"
PRET_FILES = ["include/constants/maps.h", "include/constants/map_sections.h", "src/data/map_headers.h"]


def _download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Pokemon-HGSS-MCP data build"})
    with urllib.request.urlopen(req, timeout=60) as r:
        dest.write_bytes(r.read())


def fetch_sources() -> None:
    jobs = [(POKEAPI + f + ".csv", SRC / f"{f}.csv") for f in POKEAPI_FILES]
    jobs += [(PRET_RAW + f, DATA / "pret" / Path(f).name) for f in PRET_FILES]
    ze = DATA / "pret" / "zone_event"
    if not ze.exists() or len(list(ze.glob("*.json"))) < 400:
        req = urllib.request.Request("https://api.github.com/repos/pret/pokeheartgold/git/trees/master?recursive=1",
                                     headers={"User-Agent": "Pokemon-HGSS-MCP data build"})
        with urllib.request.urlopen(req, timeout=60) as r:
            tree = json.load(r)["tree"]
        for e in tree:
            p = e["path"]
            if p.startswith("files/fielddata/eventdata/zone_event/") and p.endswith(".json"):
                jobs.append((PRET_RAW + p, ze / Path(p).name))
    with ThreadPoolExecutor(16) as pool:
        list(pool.map(lambda j: _download(*j), jobs))
    print(f"sources ready ({len(jobs)} files checked)")


fetch_sources()


def rows(name):
    with open(SRC / f"{name}.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pretty(identifier):
    return " ".join(w.capitalize() for w in identifier.split("-"))


types = {int(r["id"]): pretty(r["identifier"]) for r in rows("types") if int(r["id"]) < 10000}
types[10001] = "???"  # Curse's type before Gen 5
classes = {int(r["id"]): r["identifier"] for r in rows("move_damage_classes")}

# Pokemon types: current, overridden by the newest "past" entry that still covers generation 4.
ptypes = {}
for r in rows("pokemon_types"):
    pid = int(r["pokemon_id"])
    if pid <= 493:
        ptypes.setdefault(pid, {})[int(r["slot"])] = types[int(r["type_id"])]
past = {}
for r in rows("pokemon_types_past"):
    pid, gen = int(r["pokemon_id"]), int(r["generation_id"])
    if pid <= 493 and gen >= 4:
        past.setdefault(pid, {}).setdefault(gen, {})[int(r["slot"])] = types[int(r["type_id"])]
for pid, by_gen in past.items():
    ptypes[pid] = by_gen[min(by_gen)]

stats = {}
for r in rows("pokemon_stats"):
    pid = int(r["pokemon_id"])
    if pid <= 493:
        stats.setdefault(pid, {})[int(r["stat_id"])] = int(r["base_stat"])

species = {}
for r in rows("pokemon_species"):
    sid = int(r["id"])
    if sid > 493:
        continue
    s = stats.get(sid, {})
    species[sid] = {
        "name": pretty(r["identifier"]),
        "types": [ptypes[sid][k] for k in sorted(ptypes.get(sid, {}))],
        "base": [s.get(i, 0) for i in range(1, 7)],  # hp atk def spa spd spe
        "catch_rate": int(r["capture_rate"]),
        "evolves_from": int(r["evolves_from_species_id"] or 0),
    }

# Moves: current values, rolled back with the changelog (each row holds the value *before*
# the version group it names, so the earliest change after HGSS gives the Gen 4 value).
moves = {}
for r in rows("moves"):
    mid = int(r["id"])
    if int(r["generation_id"]) > 4 or mid > 10000:
        continue
    moves[mid] = {
        "name": pretty(r["identifier"]),
        "type": types.get(int(r["type_id"]), "?"),
        "power": int(r["power"]) if r["power"] else None,
        "pp": int(r["pp"]) if r["pp"] else None,
        "acc": int(r["accuracy"]) if r["accuracy"] else None,
        "class": classes.get(int(r["damage_class_id"]), "?"),
        "priority": int(r["priority"]),
    }
changes = sorted(rows("move_changelog"), key=lambda r: -int(r["changed_in_version_group_id"]))
for r in changes:  # newest first, so older (closer to Gen 4) rows win
    mid = int(r["move_id"])
    if mid in moves and int(r["changed_in_version_group_id"]) > HGSS:
        if r["type_id"]:
            moves[mid]["type"] = types[int(r["type_id"])]
        for key, col in (("power", "power"), ("pp", "pp"), ("acc", "accuracy"), ("priority", "priority")):
            if r[col]:
                moves[mid][key] = int(r[col])
for m in moves.values():  # no Fairy type before Gen 6
    if m["type"] == "Fairy":
        m["type"] = "Normal"

item_names = {int(r["id"]): pretty(r["identifier"]) for r in rows("items")}
items = {}
for r in rows("item_game_indices"):
    if int(r["generation_id"]) == 4:
        items[int(r["game_index"])] = item_names.get(int(r["item_id"]), f"item {r['item_id']}")

chart = {}
for r in rows("type_efficacy"):
    a, d = int(r["damage_type_id"]), int(r["target_type_id"])
    if a in types and d in types and types[a] != "Fairy" and types[d] != "Fairy":
        chart.setdefault(types[a], {})[types[d]] = int(r["damage_factor"]) / 100
by_gen = {}
for r in rows("type_efficacy_past"):
    if int(r["generation_id"]) >= 4:
        key = (types[int(r["damage_type_id"])], types[int(r["target_type_id"])])
        by_gen.setdefault(key, {})[int(r["generation_id"])] = int(r["damage_factor"]) / 100
for (a, d), g in by_gen.items():
    chart.setdefault(a, {})[d] = g[min(g)]

out = {"species": species, "moves": moves, "items": items, "types": chart}
(DATA / "gen4.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
print(f"species {len(species)}, moves {len(moves)}, items {len(items)}, types {len(chart)}")
print("check:", species[35]["types"], moves[204]["name"], moves[204]["type"], chart["Ghost"]["Steel"], items[4])

# Maps (pret/pokeheartgold: include/constants/maps.h, map_sections.h, src/data/map_headers.h).
import re  # noqa: E402

PRET = DATA / "pret"
map_ids = {m[0]: int(m[1]) for m in re.findall(r"#define (MAP_\w+)\s+(\d+)", (PRET / "maps.h").read_text())}
headers = (PRET / "map_headers.h").read_text()
maps = {}
for m in re.finditer(r"\[(MAP_\w+)\]\s*=\s*\{(.*?)\n\s*\},", headers, re.S):
    name, body = m.group(1), m.group(2)
    sec = re.search(r"\.mapsec\s*=\s*MAPSEC_(\w+)", body)
    enc = re.search(r"\.wildEncounterBank\s*=\s*(\w+)", body)
    if name in map_ids:
        maps[map_ids[name]] = {
            "name": pretty(name[4:].lower().replace("_", "-")),
            "area": pretty(sec.group(1).lower().replace("_", "-")) if sec else "?",
            "wild": bool(enc and enc.group(1) != "ENCDATA_NA"),
        }
out["maps"] = maps
(DATA / "gen4.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
print(f"maps {len(maps)}: 64 = {maps.get(64)}, 60 = {maps.get(60)}")
print("route 29:", [ (k,v) for k,v in maps.items() if v['area']=='Route 29'])

# Map events (doors/warps, signs, NPCs) from pret zone_event JSON, linked via map_headers eventsBank.
ZE = PRET / "zone_event"
for m in re.finditer(r"\[(MAP_\w+)\]\s*=\s*\{(.*?)\n\s*\},", headers, re.S):
    name, body = m.group(1), m.group(2)
    bank = re.search(r"\.eventsBank\s*=\s*NARC_zone_event_(\w+?)_bin", body)
    mid = map_ids.get(name)
    if mid is None or not bank or mid not in maps:
        continue
    f = ZE / f"{bank.group(1)}.json"
    if not f.exists():
        continue
    ev = json.loads(f.read_text(encoding="utf-8"))
    maps[mid]["warps"] = [[w["x"], w["z"], map_ids.get(w["header"], -1)] for w in ev.get("warps", [])]
    maps[mid]["signs"] = [[b["x"], b["z"]] for b in ev.get("bgs", [])]
    maps[mid]["npcs"] = [[o["x"], o["z"], o["spriteId"].replace("SPRITE_", "").lower()] for o in ev.get("objects", [])]
(DATA / "gen4.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
print("New Bark warps:", maps[60].get("warps"))

# Map matrix index per map (for collision from the ROM: bridge/mapdata.py).
for m in re.finditer(r"\[(MAP_\w+)\]\s*=\s*\{(.*?)\n\s*\},", headers, re.S):
    mx = re.search(r"\.matrixId\s*=\s*NARC_map_matrix_map_matrix_(\d+)", m.group(2))
    mid = map_ids.get(m.group(1))
    if mx and mid in maps:
        maps[mid]["matrix"] = int(mx.group(1))
(DATA / "gen4.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
print("matrix ids:", {k: maps[k].get("matrix") for k in (33, 60, 61, 63)})

# Poke Mart inventories (pret src/scrcmd_mart.c: badge-gated general list + specialty clerk lists) and
# item prices (PokeAPI items.csv cost, keyed by the Gen 4 item index the game uses).
for f in ("include/constants/items.h", "src/scrcmd_mart.c"):
    _download(PRET_RAW + f, PRET / Path(f).name)
item_const = {m[0]: int(m[1]) for m in re.findall(r"#define (ITEM_\w+)\s+(\d+)", (PRET / "items.h").read_text())}
mart_src = (PRET / "scrcmd_mart.c").read_text()
badge_table = re.search(r"BadgeMartItems\s+\w+\[\]\s*=\s*\{(.*?)\};", mart_src, re.S)
badge_items = [[item_const[n], int(b)] for n, b in re.findall(r"\{\s*(ITEM_\w+),\s*(\d+)\s*\}", badge_table.group(1))
               if n in item_const] if badge_table else []
lists = []
for body in re.findall(r"const u16 \w+\[\]\s*=\s*\{([^}]*ITEM_[^}]*)\}", mart_src):
    ids = [item_const[n] for n in re.findall(r"ITEM_\w+", body) if n in item_const]
    if ids:
        lists.append(ids)
gen4_index = {int(r["item_id"]): int(r["game_index"]) for r in rows("item_game_indices") if int(r["generation_id"]) == 4}
prices = {gen4_index[int(r["id"])]: int(r["cost"]) for r in rows("items") if int(r["id"]) in gen4_index and r["cost"]}
out["marts"] = {"badge": badge_items, "lists": lists}
out["prices"] = prices
(DATA / "gen4.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
print(f"marts: {len(badge_items)} badge items, {len(lists)} lists; Poke Ball costs {prices.get(4)}")
