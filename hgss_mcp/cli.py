"""Command-line version of the heartgold MCP tools (for sessions without the MCP server).

  python hgss_mcp/cli.py state | look [both|top|bottom|text] | press "Down*2 A" | text [max] | walk DIR [N] [run]
  python hgss_mcp/cli.py fight MOVE | to X Y | exits | menu ITEM | type TEXT | save | journal | note TEXT | goal TEXT
Images are written to <work>/shots/look.png or text.png (read them with your image/file tool).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

import actions  # noqa: E402
import emu as emu_mod  # noqa: E402
import game as game_mod  # noqa: E402
import journal  # noqa: E402
import server as mcp_server  # noqa: E402  (reuses press() parsing and _status_line)
import screen  # noqa: E402

from config import SHOTS  # noqa: E402


def main(argv):
    cmd, args = argv[0], argv[1:]
    g = mcp_server.g()
    if cmd == "state":
        print(mcp_server.state())
    elif cmd == "look":
        img = actions.grab(g.emu)
        view = args[0] if args else "both"
        img = {"top": screen.top, "bottom": screen.bottom, "text": screen.textbox}.get(view, lambda i: i)(img)
        img.save(SHOTS / "look.png")
        print(SHOTS / "look.png", img.size)
    elif cmd == "press":
        print(mcp_server.press(" ".join(args)))
    elif cmd == "text":
        res = actions.advance_text(g.emu, max_messages=int(args[0]) if args else 30)
        print(f"{len(res.messages)} messages; stopped: {res.stopped}")
        if res.messages:
            screen.montage(res.messages).save(SHOTS / "text.png")
            print(SHOTS / "text.png")
        print(g.summary())
    elif cmd == "walk":
        print(mcp_server.walk(args[0], int(args[1]) if len(args) > 1 else 1, len(args) > 2))
    elif cmd == "to":
        print(mcp_server.walk_to(int(args[0]), int(args[1])))
    elif cmd == "exits":
        print(mcp_server.exits())
    elif cmd == "fight":
        r = g.fight(" ".join(args) if args else 1)
        print(f"{len(r['messages'])} messages; {r['note']}")
        if r["messages"]:
            screen.montage(r["messages"]).save(SHOTS / "text.png")
            print(SHOTS / "text.png")
        print(g.summary())
    elif cmd == "auto":
        r = g.fight_until(args[0] if args else 1)
        print(f"{len(r['messages'])} messages; {r['note']}")
        if r["messages"]:
            screen.montage(r["messages"][-8:]).save(SHOTS / "text.png")
            print(SHOTS / "text.png")
        print(g.summary())
    elif cmd in ("yes", "no"):
        print(actions.answer(g.emu, cmd == "yes"))
    elif cmd == "grass":
        print(g.seek_encounter())
        print(mcp_server.state())
    elif cmd in ("run", "catch"):
        r = g.run_away() if cmd == "run" else g.catch(int(args[0]) if args else 1)
        print(f"{len(r['messages'])} messages; {r['note']}")
        if r["messages"]:
            screen.montage(r["messages"]).save(SHOTS / "text.png")
            print(SHOTS / "text.png")
        print(g.summary())
    elif cmd == "heal":
        print(g.heal())
    elif cmd == "scene":
        r = g.follow_scene()
        print(f"{len(r['messages'])} messages; {r['note']}")
        if r["messages"]:
            screen.montage(r["messages"]).save(SHOTS / "text.png")
            print(SHOTS / "text.png")
        print(g.summary())
    elif cmd == "menu":
        print(mcp_server.open_menu(args[0]))
    elif cmd == "type":
        print(mcp_server.type_text(" ".join(args)))
    elif cmd == "save":
        print(mcp_server.save())
    elif cmd == "journal":
        print(journal.summary())
    elif cmd == "note":
        print(journal.note(" ".join(args)))
    elif cmd == "goal":
        print(journal.set_goal(" ".join(args)))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:] or ["help"])
