"""Nuzlocke run journal (<work>/run.json): rules, encounters per area, graveyard, goal, notes.

This file is the run's memory between sessions: a new session reads journal() instead of any
conversation history.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from config import RUN_FILE as RUN  # noqa: E402

RULES = [
    "Only the first wild encounter in each area may be caught (areas = the game's location names).",
    "Encounters before getting Poke Balls don't count; dupes clause: re-roll species already caught.",
    "A Pokemon that faints is dead: box it for good, never use it again.",
    "Nickname every Pokemon.",
    "Battle style SET (no free switch after a knockout).",
    "No savestates or reloads to undo outcomes (the bridge exposes none).",
]


def load() -> dict:
    try:
        return json.loads(RUN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rules": RULES, "encounters": {}, "graveyard": [], "badges": [], "goal": "", "notes": []}


def save(run: dict) -> None:
    RUN.parent.mkdir(parents=True, exist_ok=True)
    RUN.write_text(json.dumps(run, indent=1, ensure_ascii=False), encoding="utf-8")


def encounter_status(area: str) -> str:
    e = load()["encounters"].get(area)
    if not e:
        return "first encounter here: still available"
    return f"first encounter here: used ({e['species']}, {e['result']})"


def log_encounter(area: str, species: str, result: str, nickname: str = "") -> str:
    run = load()
    if area in run["encounters"] and result != "dupe-reroll":
        prev = run["encounters"][area]
        return f"NOT logged: {area} already used by {prev['species']} ({prev['result']})"
    run["encounters"][area] = {"species": species, "result": result, "nickname": nickname,
                               "time": time.strftime("%Y-%m-%d %H:%M")}
    save(run)
    return f"logged {area}: {species} -> {result}"


def log_death(nickname: str, species: str, level: int, cause: str, where: str) -> str:
    run = load()
    run["graveyard"].append({"nickname": nickname, "species": species, "level": level,
                             "cause": cause, "where": where, "time": time.strftime("%Y-%m-%d %H:%M")})
    save(run)
    return f"RIP {nickname} the {species} (L{level}), {cause} at {where}"


def note(text: str) -> str:
    run = load()
    run["notes"].append({"time": time.strftime("%Y-%m-%d %H:%M"), "text": text})
    run["notes"] = run["notes"][-40:]
    save(run)
    return "noted"


def set_goal(text: str) -> str:
    run = load()
    run["goal"] = text
    save(run)
    return f"goal: {text}"


def add_badge(name: str) -> str:
    run = load()
    if name not in run["badges"]:
        run["badges"].append(name)
    save(run)
    return f"badges: {', '.join(run['badges'])}"


def summary() -> str:
    run = load()
    lines = ["RULES: " + " | ".join(run["rules"])]
    lines.append(f"goal: {run['goal'] or '(none set)'} | badges: {', '.join(run['badges']) or 'none'}")
    enc = run["encounters"]
    lines.append("encounters: " + ("; ".join(f"{a}: {e['species']} ({e['result']}"
                 + (f", {e['nickname']}" if e.get("nickname") else "") + ")" for a, e in enc.items()) or "none"))
    gy = run["graveyard"]
    lines.append("graveyard: " + ("; ".join(f"{d['nickname']} ({d['species']} L{d['level']}, {d['cause']})"
                 for d in gy) or "empty"))
    if run["notes"]:
        lines.append("recent notes: " + " / ".join(n["text"] for n in run["notes"][-6:]))
    return "\n".join(lines)
