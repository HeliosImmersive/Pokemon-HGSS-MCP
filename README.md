# Pokemon-HGSS-MCP

A tool kit for AI agents to play **Pokémon HeartGold and SoulSilver**. It's an [MCP](https://modelcontextprotocol.io)
server that drives the game in the [BizHawk](https://github.com/TASEmulators/BizHawk) emulator and gives the
agent compact, game-aware tools instead of raw screenshots and button presses.

It was built while Claude played a HeartGold Nuzlocke, and every tool exists because something was slow,
expensive or error-prone without it.

## Why it's cheap to play with

Agents usually play by looking at full screenshots and pressing one button per turn. That burns tokens fast:
every image stays in the conversation and gets re-read on each turn. This server reads the game's memory
and the ROM's map data instead, and only sends images when the agent asks:

| What the agent needs | Tool | Typical cost |
|---|---|---|
| Location, party (HP, moves, PP), live battle state | `state()` | ~150 tokens of text |
| One screen / both screens at native resolution | `look("top")` / `look()` | ~65 / ~130 tokens |
| Just the message box | `look("text")` | ~12 tokens |
| A whole conversation | `read_text()`: presses through it, returns every message stacked in one image | ~15 tokens per message |
| Walk across a route | `walk_to(x, y)`: plans with the ROM's collision map, ledges and tall grass | one call |
| A whole battle turn | `fight("Tackle")`: picks the move, follows the turn, returns its messages + live HP | one call |

## Tools

| Tool | What it does |
|---|---|
| `state()` | Location (map name, area, x/y), party with HP/moves/PP, live battle HP and stat stages, whether this area's Nuzlocke encounter is free, message-box status |
| `look(view)` | Native-resolution screenshot: `both`, `top`, `bottom`, `text` |
| `press(keys)` | Buttons in order: `"Down*2 A"`, `"A+B"`, `"wait30"`, `"hold:Right:40"` |
| `read_text()` | Press through dialogue until it closes or a real Yes/No choice appears. Handles phone calls too |
| `follow_scene()` | Same, for cutscenes that walk you around between messages |
| `answer(yes)` | Answer a Yes/No prompt by steering the highlight |
| `walk(dir, n, run)` / `walk_to(x, y)` | Movement with position checks from RAM; stops for battles, trainers and events |
| `exits()` | Doors/warps on the current map with coordinates and destinations |
| `seek_encounter()` | Walk to the nearest tall grass and pace until a wild battle starts |
| `fight(move)` / `fight_until(move)` | Use a move by name or slot and follow the turn; repeat until the battle ends (hands back control at low HP) |
| `catch(slot)` / `run_away()` | Throw a ball from the Poké Balls pocket / flee |
| `heal()` | Inside a Pokémon Center: talk to the nurse and heal |
| `open_menu(item)` / `save()` | Field menu entries; in-game save written to disk |
| `type_text(text)` | Type on naming keyboards (player, nicknames, rival) |
| `run_journal()`, `log_encounter()`, `log_death()`, `note()`, `set_goal()`, `add_badge()` | Optional Nuzlocke journal: the run's memory between sessions |

There is deliberately no savestate or reload tool, so outcomes can't be undone.

## Requirements

- Windows 10/11 (tested). BizHawk also has a Linux build; this project hasn't been tested there.
- Python 3.10+
- Your own legally obtained Pokémon HeartGold or SoulSilver ROM (`.nds`). Tested with HeartGold (USA).
  **No ROMs, BIOS files or game data are included or downloaded.**

## Setup

```powershell
git clone https://github.com/HeliosImmersive/Pokemon-HGSS-MCP.git
cd Pokemon-HGSS-MCP
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python setup.py --rom "C:\path\to\your\HeartGold.nds"
```

`setup.py` downloads BizHawk 2.11.1 from its official GitHub release (checked against a SHA-256), builds the
data tables (`data/gen4.json`) from PokeAPI and pret/pokeheartgold sources, and saves your ROM path in
`work/config.json`.

Settings can also come from environment variables: `HGSS_ROM`, `HGSS_BIZHAWK` (path to `EmuHawk.exe`),
`HGSS_WORKDIR` (screenshots, IPC files, run journal; default `work/`), `HGSS_DATA` (default `data/`).

## Connect your agent

Any MCP client that speaks stdio works. For Claude Code, put this in a `.mcp.json` (adjust paths):

```json
{
  "mcpServers": {
    "hgss": {
      "command": "C:\\path\\to\\Pokemon-HGSS-MCP\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\Pokemon-HGSS-MCP\\hgss_mcp\\server.py"]
    }
  }
}
```

The first tool call launches BizHawk and loads your save (or the title screen for a new game). For agents
without MCP there's a CLI with the same functions: `python hgss_mcp/cli.py state`, `... fight tackle`, etc.

`skills/hgss-mcp/SKILL.md` is an [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
with play habits that keep token use low; drop it into your agent's skills folder.

## How it works

- **Emulator bridge.** `hgss_mcp/bridge.lua` runs inside BizHawk and exchanges small command/response files
  with Python each frame. (BizHawk's Lua socket API blocks the emulator while waiting, so file IPC keeps the
  game running when no agent is connected.)
- **Memory.** Party Pokémon are found by scanning RAM for Gen 4 party structures and decrypting them
  (`pkm.py`). The player's location sits at a fixed offset from the party in the save block. Live battle HP
  and stat stages come from the in-battle structures.
- **Maps.** Collision, tall grass, water and ledges come from the ROM's own land data and map matrices
  (`rom.py`, `mapdata.py`), so `walk_to` plans real routes. Map names, doors and NPCs come from
  pret/pokeheartgold.
- **Screen.** Text boxes, choice prompts, battle menus, phone calls and naming keyboards are recognised from
  native screenshots (`screen.py`, `actions.py`), so tools know when to press A and when to stop.

## Known limitations

- **Touch input doesn't work**: BizHawk's melonDS core ignores Lua touch coordinates. Everything goes
  through buttons, which covers HG/SS's menus, battles and the field menu.
- Some bottom-screen-only messages aren't read by `read_text()`; use `look("bottom")` and `press()`.
- Double battles, moves that need a target, the PC box and the Poké Mart don't have helpers yet.
- RAM offsets were verified on HeartGold (USA). SoulSilver and other regions likely work but are untested.
- Windows pauses BizHawk while its window is being dragged.

Contributions welcome, especially helpers for the gaps above.

## Credits

- [BizHawk](https://github.com/TASEmulators/BizHawk) (MIT), with the melonDS core
- [PokeAPI](https://github.com/PokeAPI/pokeapi) data (BSD-3-Clause), downloaded at setup
- [pret/pokeheartgold](https://github.com/pret/pokeheartgold) map constants and event data, downloaded at setup

Pokémon is © Nintendo, Creatures Inc. and GAME FREAK inc. This project is unofficial and not affiliated with
them. Use your own legally obtained copy of the game.
