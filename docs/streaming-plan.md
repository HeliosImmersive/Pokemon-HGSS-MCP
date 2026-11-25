# Streaming plan: game-aware graphics for "Claude plays a HeartGold Nuzlocke"

Draft, 2026-11-24. Nothing here is built yet.

## Shape

```
BizHawk ──(bridge.lua: file IPC + telemetry file)──► hgss_mcp ──► MCP tools (the agent plays)
                                                        │
                                                        └──► stream server (localhost HTTP + SSE)
                                                                 ├─ /overlay/*  browser sources for OBS
                                                                 └─ /api/*      JSON + event stream
OBS: game capture of BizHawk + browser sources on top (party bar, tracker, captions)
```

- **One reader of the emulator.** The bridge has a single command/response file pair, so a second process
  polling it would collide with the agent's calls. Two options:
  1. *Telemetry file* (preferred): `bridge.lua` writes a small snapshot every ~30 frames (party block bytes,
     Location record, play time, money, BattleMon structs, bag pockets) to `ipc/telemetry.bin`. The stream
     server only reads that file and decodes it with the same `pkm.py` code. No IPC contention, no impact on
     the agent's latency.
  2. Run the stream server as a thread inside the MCP server process, sharing one `Emu` behind a lock.
- **No new heavy dependencies.** Python's `http.server` + Server-Sent Events is enough for a local overlay.
- **Assets stay local.** Sprites/icons are extracted from the user's own ROM at runtime (same NARC code as
  `rom.py`), never committed or downloaded.

## Endpoints

Read-only state (JSON, polled or pushed):

| Endpoint | Content | Overlay use |
|---|---|---|
| `/api/party` | nickname, species, level, HP/max, status, EXP to next level, held item, moves + PP, sprite id | party bar with live HP |
| `/api/battle` | both sides' active mon, HP, stat stages, wild vs trainer, trainer name, turn number | battle HUD, "Claude's matchup notes" |
| `/api/location` | map/area name, x/y, region-map position | location banner, mini map |
| `/api/nuzlocke` | encounters per area (species, result, nickname), graveyard (cause, level, where), badges, rules | the classic Nuzlocke tracker panel |
| `/api/run` | play time, money, Poke Ball count, current goal, session timer, deaths count | header strip |
| `/api/agent` | the agent's latest caption, current plan, last decision + reason | "Claude says" caption box |

Events (SSE `/api/events`), derived by diffing snapshots, each with a timestamp:

- `area_entered`, `wild_encounter` (with "first encounter here: free/used"), `trainer_battle`
- `throw` + `caught` / `broke_free` (with catch probability), `nickname`
- `level_up`, `move_learned`, `evolution`
- `low_hp` (tension cue when a party member drops under ~25%), `critical_hit`, `super_effective`
- `fainted` → in a Nuzlocke this is a **death**: memorial card (nickname, level, cause, time with the team)
- `badge_earned`, `rival_battle`, `whiteout`
- `speed_changed` (show a "fast-forward" banner when grinding at >100%)

Agent-facing tools (MCP) so Claude can talk to viewers without spending game tokens:

- `narrate(text)`: a one-line caption on stream (rate-limited, profanity-free by construction).
- `decision(choice, reason)`: shows a card like "Throwing a Poké Ball: ~64% catch chance" or
  "Switching to TALUS: Rock resists Flying".
- `poll(question, options, seconds)` / `poll_result()`: chat votes for nicknames or optional choices.
  Votes never press buttons directly; the agent reads the result and acts.
- `mark(label)`: stream marker / clip point on catches, deaths and badges (Twitch "create stream marker").

Derived numbers worth computing server-side for **viewers only** (never fed back to the agent: strategy
stays with the model): catch probability for the next ball,
type effectiveness of each move against the current foe, damage range estimates, EXP needed to the next
level, encounter table for the current area (from pret data) with "already caught" ticks.

## Overlay pages (OBS browser sources)

- `/overlay/party`: six slots with sprite, nickname, level, HP bar (animated on damage), status icon.
- `/overlay/tracker`: route list with the encounter result per area and the graveyard.
- `/overlay/caption`: Claude's narration and decision cards.
- `/overlay/battle`: matchup panel during battles only (hides itself otherwise).
- `/overlay/alerts`: full-screen moments (catch, death memorial, badge).

## Build order (suggested)

1. Telemetry snapshot in `bridge.lua` + decoder + `/api/party`, `/api/location`, `/api/run` (an afternoon).
2. Event differ + SSE + the Nuzlocke tracker from `run.json`.
3. Overlay pages (plain HTML/CSS/JS, no framework).
4. `narrate` / `decision` MCP tools.
5. Chat polls and stream markers (needs the user's Twitch/YouTube credentials: the user sets these up).
