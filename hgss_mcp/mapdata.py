"""Collision and tile behaviors from the ROM: map matrix (a/0/4/1) + land data (a/0/6/5).

Overworld coordinates (as in the save's Location record) are global tiles on the map's matrix:
chunk = (x // 32, y // 32), tile in chunk = (x % 32, y % 32). Each land-data file holds a 32x32
permission table of u16: low byte = behavior (grass, water, ledge...), bit 15 = solid.
"""
from __future__ import annotations

import struct
from functools import lru_cache

import rom

# Behavior bytes (Gen 4)
TALL_GRASS = {0x02, 0x03}
WATER = set(range(0x10, 0x18)) | {0x19, 0x1A}
LEDGES = {0x38: (1, 0), 0x39: (-1, 0), 0x3A: (0, -1), 0x3B: (0, 1)}  # jump direction


@lru_cache(maxsize=None)
def matrix(index: int):
    data = rom.narc("a/0/4/1")[index]
    w, h, has_headers, has_alt, name_len = data[:5]
    off = 5 + name_len
    if has_headers:
        off += w * h * 2
    if has_alt:
        off += w * h
    land = struct.unpack_from(f"<{w * h}H", data, off)
    return w, h, land


@lru_cache(maxsize=None)
def permissions(land_id: int):
    data = rom.narc("a/0/6/5")[land_id]
    perm_size, = struct.unpack_from("<I", data, 0)
    off = 0x10
    magic, sound_size = struct.unpack_from("<HH", data, 0x10)
    if magic == 0x1234:  # HeartGold/SoulSilver: background-sound section before the permissions
        off = 0x14 + sound_size
    return struct.unpack_from(f"<{perm_size // 2}H", data, off)


def tile(matrix_index: int, x: int, y: int) -> int | None:
    """Raw permission u16 at global tile (x, y), or None outside the map."""
    w, h, land = matrix(matrix_index)
    cx, cy = x // 32, y // 32
    if not (0 <= cx < w and 0 <= cy < h):
        return None
    lid = land[cy * w + cx]
    if lid == 0xFFFF:
        return None
    return permissions(lid)[(y % 32) * 32 + (x % 32)]


def solid(matrix_index: int, x: int, y: int) -> bool:
    t = tile(matrix_index, x, y)
    return t is None or bool(t & 0x8000) or (t & 0xFF) in WATER


def behavior(matrix_index: int, x: int, y: int) -> int:
    t = tile(matrix_index, x, y)
    return -1 if t is None else t & 0xFF


def ascii_map(matrix_index: int, x0: int, y0: int, w: int, h: int, me=None) -> str:
    rows = []
    for y in range(y0, y0 + h):
        row = []
        for x in range(x0, x0 + w):
            t = tile(matrix_index, x, y)
            if me == (x, y):
                row.append("@")
            elif t is None:
                row.append(" ")
            elif (t & 0xFF) in LEDGES:
                row.append({(1, 0): ">", (-1, 0): "<", (0, -1): "^", (0, 1): "v"}[LEDGES[t & 0xFF]])
            elif t & 0x8000:
                row.append("#")
            elif (t & 0xFF) in TALL_GRASS:
                row.append('"')
            elif (t & 0xFF) in WATER:
                row.append("~")
            else:
                row.append(".")
        rows.append("".join(row))
    return "\n".join(rows)
