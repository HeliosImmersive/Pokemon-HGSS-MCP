"""Gen 4 (HeartGold/SoulSilver) Pokemon structures: decryption, decoding, party scanning.

Party Pokemon are 236 bytes: PID, checksum, four 32-byte blocks (shuffled by PID, encrypted with
an LCG seeded by the checksum) and 100 bytes of battle stats (encrypted with the LCG seeded by PID).
A Party in RAM is {u32 capacity (=6), u32 count, 6 x 236-byte Pokemon}.
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from config import GEN4  # noqa: E402

try:
    DATA = json.loads(GEN4.read_text(encoding="utf-8"))
except OSError as e:
    raise SystemExit(f"{GEN4} missing: run  python setup.py  first (builds the data tables)") from e
SPECIES = {int(k): v for k, v in DATA["species"].items()}
MOVES = {int(k): v for k, v in DATA["moves"].items()}
ITEMS = {int(k): v for k, v in DATA["items"].items()}
TYPES = DATA["types"]

PKM_SIZE = 236
ORDERS = ["ABCD", "ABDC", "ACBD", "ACDB", "ADBC", "ADCB", "BACD", "BADC", "BCAD", "BCDA", "BDAC", "BDCA",
          "CABD", "CADB", "CBAD", "CBDA", "CDAB", "CDBA", "DABC", "DACB", "DBAC", "DBCA", "DCAB", "DCBA"]
NATURES = ["Hardy", "Lonely", "Brave", "Adamant", "Naughty", "Bold", "Docile", "Relaxed", "Impish", "Lax",
           "Timid", "Hasty", "Serious", "Jolly", "Naive", "Modest", "Mild", "Quiet", "Bashful", "Rash",
           "Calm", "Gentle", "Sassy", "Careful", "Quirky"]


def _crypt(data: bytes, seed: int) -> bytes:
    words = struct.unpack(f"<{len(data) // 2}H", data)
    out = []
    for w in words:
        seed = (seed * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
        out.append(w ^ (seed >> 16))
    return struct.pack(f"<{len(out)}H", *out)


def gen4_text(raw: bytes) -> str:
    """Decode a Gen 4 string (u16 per character, 0xFFFF terminator); covers letters, digits, basics."""
    out = []
    for (c,) in struct.iter_unpack("<H", raw):
        if c == 0xFFFF:
            break
        if 0x0121 <= c <= 0x012A:
            out.append(chr(ord("0") + c - 0x0121))
        elif 0x012B <= c <= 0x0144:
            out.append(chr(ord("A") + c - 0x012B))
        elif 0x0145 <= c <= 0x015E:
            out.append(chr(ord("a") + c - 0x0145))
        else:
            out.append({0x01DE: " ", 0x01AB: "!", 0x01AC: "?", 0x01AD: ",", 0x01AE: ".", 0x01B3: "-",
                        0x01B4: "'", 0x01C4: ":", 0x01BB: "♂", 0x01BC: "♀", 0x01E8: "é"}.get(c, "?"))
    return "".join(out)


STATUS_NAMES = [(0x07, "SLP"), (0x08, "PSN"), (0x10, "BRN"), (0x20, "FRZ"), (0x40, "PAR"), (0x80, "TOX")]


@dataclass
class Pokemon:
    pid: int
    species: int
    name: str
    nickname: str
    level: int
    hp: int
    max_hp: int
    stats: tuple  # atk def spe spa spd (as stored: atk def spe spa spd)
    moves: list = field(default_factory=list)  # [(move_id, pp)]
    item: int = 0
    ability: int = 0
    nature: str = ""
    status: str = ""
    exp: int = 0
    friendship: int = 0
    is_egg: bool = False
    ot_id: int = 0
    met_level: int = 0
    gender: str = ""

    def types(self) -> list:
        return SPECIES.get(self.species, {}).get("types", [])

    def line(self) -> str:
        """One-line summary for prompts (~25 tokens)."""
        nick = f"{self.nickname} ({self.name})" if self.nickname and self.nickname.lower() != self.name.lower() else self.name
        mv = ", ".join(f"{MOVES.get(m, {}).get('name', m)} {pp}" for m, pp in self.moves if m)
        st = f" {self.status}" if self.status else ""
        it = f" @{ITEMS.get(self.item, self.item)}" if self.item else ""
        return f"{nick} L{self.level} {self.hp}/{self.max_hp}{st}{it} [{mv}]"


def decode(raw: bytes) -> Pokemon | None:
    """Decode a 236-byte party Pokemon; None if the checksum doesn't validate."""
    if len(raw) < PKM_SIZE:
        return None
    pid, _, checksum = struct.unpack_from("<IHH", raw, 0)
    if pid == 0 and checksum == 0:
        return None
    body = _crypt(raw[8:136], checksum)
    if sum(struct.unpack("<64H", body)) & 0xFFFF != checksum:
        return None
    order = ORDERS[((pid & 0x3E000) >> 13) % 24]
    blocks = {letter: body[i * 32:(i + 1) * 32] for i, letter in enumerate(order)}
    a, b, c, d = blocks["A"], blocks["B"], blocks["C"], blocks["D"]
    battle = _crypt(raw[136:236], pid)

    species, item, ot_id, _sid, exp, friendship, ability = struct.unpack_from("<HHHHIBB", a, 0)
    moves = struct.unpack_from("<4H", b, 0)
    pps = struct.unpack_from("<4B", b, 8)
    ivword, = struct.unpack_from("<I", b, 16)
    flags = b[24]
    status_word, level = struct.unpack_from("<IB", battle, 0)
    hp, max_hp, *stats = struct.unpack_from("<7H", battle, 6)
    if not (1 <= species <= 493) or not (1 <= level <= 100):
        return None
    status = next((n for mask, n in STATUS_NAMES if status_word & mask), "")
    gender = "" if flags & 4 else ("F" if flags & 2 else "M")
    return Pokemon(
        pid=pid, species=species, name=SPECIES[species]["name"], nickname=gen4_text(c[:22]),
        level=level, hp=hp, max_hp=max_hp, stats=tuple(stats),
        moves=[(m, p) for m, p in zip(moves, pps) if m], item=item, ability=ability,
        nature=NATURES[pid % 25], status=status, exp=exp, friendship=friendship,
        is_egg=bool(ivword >> 30 & 1), ot_id=ot_id, met_level=d[0x1C] & 0x7F, gender=gender)


@dataclass
class Party:
    addr: int
    members: list


_PARTY_HDR = re.compile(rb"\x06\x00\x00\x00[\x01-\x06]\x00\x00\x00", re.S)


def scan_parties(ram: bytes, base: int = 0x02000000) -> list[Party]:
    """Find every valid Party struct in a RAM dump (player's party, battle copies, opponents)."""
    found = []
    for m in _PARTY_HDR.finditer(ram):
        off = m.start()
        if off % 4:
            continue
        count = ram[off + 4]
        mons = [decode(ram[off + 8 + i * PKM_SIZE: off + 8 + (i + 1) * PKM_SIZE]) for i in range(count)]
        if mons and all(mons):
            found.append(Party(base + off, mons))
    return found


def read_party(raw: bytes, addr: int) -> Party | None:
    """Re-read a known party address; None if it no longer holds a valid party."""
    if len(raw) < 8 or raw[:4] != b"\x06\x00\x00\x00" or not 1 <= raw[4] <= 6:
        return None
    mons = [decode(raw[8 + i * PKM_SIZE: 8 + (i + 1) * PKM_SIZE]) for i in range(raw[4])]
    return Party(addr, mons) if all(mons) else None


def effectiveness(move_type: str, defender_types: list) -> float:
    f = 1.0
    for t in defender_types:
        f *= TYPES.get(move_type, {}).get(t, 1.0)
    return f
