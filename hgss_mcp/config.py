"""Paths and settings.

Each value comes from an environment variable, else from <work dir>/config.json, else a default:

  HGSS_ROM       path to your own Pokemon HeartGold/SoulSilver ROM (.nds)        (required)
  HGSS_BIZHAWK   path to EmuHawk.exe                  (default: <repo>/emu/BizHawk/EmuHawk.exe)
  HGSS_WORKDIR   where IPC files, screenshots and the run journal live  (default: <repo>/work)
  HGSS_DATA      generated data tables + downloaded sources             (default: <repo>/data)

config.json keys: "rom", "bizhawk".
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
WORK = Path(os.environ.get("HGSS_WORKDIR") or ROOT / "work")
DATA = Path(os.environ.get("HGSS_DATA") or ROOT / "data")

_cfg: dict = {}
try:
    _cfg = json.loads((WORK / "config.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    pass


def _get(env: str, key: str, default=None):
    return os.environ.get(env) or _cfg.get(key) or default


ROM = Path(_get("HGSS_ROM", "rom", "")) if _get("HGSS_ROM", "rom") else None
BIZHAWK = Path(_get("HGSS_BIZHAWK", "bizhawk", ROOT / "emu" / "BizHawk" / "EmuHawk.exe"))
LUA = PKG / "bridge.lua"
IPC = WORK / "ipc"
SHOTS = WORK / "shots"
RUN_FILE = WORK / "run.json"
GEN4 = DATA / "gen4.json"

for _d in (WORK, IPC, SHOTS):
    _d.mkdir(parents=True, exist_ok=True)
