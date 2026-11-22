"""Read files from the HeartGold ROM (NDS file system + NARC archives). Read-only; no ROM changes."""
from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

from config import ROM


@lru_cache(maxsize=1)
def _rom() -> bytes:
    return Path(ROM).read_bytes()


def _fs():
    rom = _rom()
    fnt_off, fnt_size, fat_off, fat_size = struct.unpack_from("<IIII", rom, 0x40)
    return rom, fnt_off, fat_off


def file_by_path(path: str) -> bytes:
    """Return a file from the ROM's file system, e.g. 'a/0/6/5'."""
    rom, fnt, fat = _fs()
    dir_id = 0xF000
    parts = path.strip("/").split("/")
    for i, name in enumerate(parts):
        sub_off, first_file, _ = struct.unpack_from("<IHH", rom, fnt + (dir_id & 0xFFF) * 8)
        p = fnt + sub_off
        fid = first_file
        found = None
        while True:
            t = rom[p]
            p += 1
            if t == 0:
                break
            n = t & 0x7F
            entry = rom[p:p + n].decode("latin-1")
            p += n
            if t & 0x80:  # sub-directory
                sub, = struct.unpack_from("<H", rom, p)
                p += 2
                if entry == name and i < len(parts) - 1:
                    found = ("dir", sub)
                    break
            else:
                if entry == name and i == len(parts) - 1:
                    found = ("file", fid)
                    break
                fid += 1
        if not found:
            raise FileNotFoundError(path)
        if found[0] == "dir":
            dir_id = found[1]
        else:
            start, end = struct.unpack_from("<II", rom, fat + found[1] * 8)
            return rom[start:end]
    raise FileNotFoundError(path)


@lru_cache(maxsize=8)
def narc(path: str) -> list[bytes]:
    """Members of a NARC archive in the ROM."""
    data = file_by_path(path)
    assert data[:4] == b"NARC", path
    off = struct.unpack_from("<H", data, 0x0C)[0]  # header size
    # BTAF
    assert data[off:off + 4] == b"BTAF"
    btaf_size, count = struct.unpack_from("<IH", data, off + 4)
    entries = [struct.unpack_from("<II", data, off + 12 + i * 8) for i in range(count)]
    off += btaf_size
    assert data[off:off + 4] == b"BTNF"
    off += struct.unpack_from("<I", data, off + 4)[0]
    assert data[off:off + 4] == b"GMIF"
    base = off + 8
    return [data[base + s:base + e] for s, e in entries]
