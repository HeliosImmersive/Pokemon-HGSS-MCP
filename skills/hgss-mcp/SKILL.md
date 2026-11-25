---
name: hgss-mcp
description: Play Pokémon HeartGold/SoulSilver through the Pokemon-HGSS-MCP tools (state, read_text, walk_to, fight, catch, heal...) cheaply and reliably, optionally as a Nuzlocke. Use this whenever you're asked to play, continue, or check on a HeartGold/SoulSilver game, do a timed play session, run a Nuzlocke, or fix/extend the HGSS MCP tools, even if the request just says "go play Pokémon".
---

# Playing HeartGold/SoulSilver with the HGSS MCP

You're the player. Play with personality (nicknames, real strategic calls) while spending tokens carefully.
The first tool call launches BizHawk and loads the save.

## Start and end of a session

- Start: `run_journal()` (if you're doing a Nuzlocke or a long run: it's your memory between sessions),
  then `state()`. Agree on a time cap if none was given.
- End: `save()`, then `note()` with what's next and `set_goal()` if the goal changed. Give the user a short recap.

## Spend tokens like PP

Every image stays in the conversation and is re-read on each turn, so prefer text:

- `state()` (~150 tokens of text) tells you location, party, your battler's HP, and whether a message is open.
  A foe is shown the way the game shows it: name, level, gender, HP bar %, status, announced stat changes
  and the moves it has used so far. Types and the rest are yours to know (or guess).
- `read_text()` presses through dialogue and returns all messages stacked in one small image.
  `follow_scene()` does the same for cutscenes that move you around between messages.
- `look("text")` (~12 tokens) for just the message box; `look("top")`/`look("bottom")` (~65); `look()` (~130).
  Look only when text results don't tell you enough (a new menu, an unexpected stop).
- Travel with `go_to("Cherrygrove")` (towns/routes by name, out of buildings and through gatehouses) or
  `walk_to(x, y)`: both use the ROM's collision map, jump ledges, avoid tall grass, and walking onto a door
  goes through it. `exits()` lists doors/warps with coordinates. Walking stops on battles, trainers and
  events: then use `read_text()` / `follow_scene()` or the battle tools, and call `go_to` again.
- People: `talk("mama")` walks up to someone (sprite name from `npcs()`, nearest person if empty), faces
  them and reads the conversation. `bag()` lists items; `state()` shows the Poké Ball count.
- Think briefly on routine steps; save careful thinking for battles, catches and team decisions.

## Battles: use the battle tools, not raw presses

Raw `press("A")` in battle is how runs get ruined: A on the battle menu picks FIGHT and then a move.

- `fight("Move Name")` uses a move and follows the turn (messages + live HP for both sides).
- `fight_until("Move")` repeats a move until the battle ends, handing control back if your HP drops below 40%
  or the game asks something. Good for easy wild battles.
- `catch()` throws the first ball in the Poké Balls pocket. On "CAUGHT", the nickname prompt follows:
  `answer(True)` then `type_text("NAME")`.
- `switch_to("TALUS")` swaps in another Pokémon mid-battle (switching happens before the foe moves: use it
  to pull a Pokémon out of a bad matchup). `swap(a, b)` reorders the party outside battle.
- `run_away()` flees a wild battle. `heal()` inside a Pokémon Center heals the party.
- `buy("Poke Ball", 10)` inside a Poké Mart buys by name and reports what was spent; `state()` shows money.
- Weaken before catching when it's safe. Remember critical hits can knock out a weakened target.

## Encounters and Nuzlocke rules (if playing one)

- `seek_encounter()` walks to the nearest tall grass (or paces on a cave/tower floor) until a battle starts.
  `state()` flags a foe as SHINY or as a dupe of an owned evolution line.
- First encounter per area only (area = the location name in `state()`, which also says whether it's still
  free). Log it right away: `log_encounter(species, result, nickname)`.
- A Pokémon that faints is dead: `log_death(nickname, cause)`. Nickname everything.
- No undoing outcomes: the server has no savestate/reload tool on purpose.

## Known gaps and workarounds

- Touch input doesn't work; buttons cover menus and battles (X toggles the field menu into button mode).
- Bottom-screen-only messages (some prompts) aren't read by `read_text()`: `look("bottom")` + `press()`.
- No helpers yet for double battles, targeting, learning a fifth move ("Forget a move!" -> pick -> FORGET),
  using Bag items or the PC box; `buy()` only covers a shop's first page.
- The tools never judge matchups for you (by design): reading type danger, deciding when to switch, run
  or heal, and catch odds are your calls. `fight_until` only hands back at low HP.
- `catch()` reports "timeout" on a species' first catch: that's the Pokédex page; press A, then the
  nickname prompt.
- If BizHawk stops answering, the next call relaunches it and reloads the last in-game save, so save at
  checkpoints (Pokémon Centers).

## Extending the tools

When you repeat the same manual sequence twice, it's a candidate for a new tool in `hgss_mcp/` (see the
README's "How it works"). That's how every tool here came about. Tools cover mechanics (inputs, menus,
movement, memory reading); never build ones that make strategic decisions or warn about them.
