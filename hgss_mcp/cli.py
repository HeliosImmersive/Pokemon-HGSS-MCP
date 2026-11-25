"""Command-line version of the heartgold MCP tools (for sessions without the MCP server).

  python hgss_mcp/cli.py state | look [both|top|bottom|text] | press "Down*2 A" | text [max] | walk DIR [N] [run]
  python hgss_mcp/cli.py fight MOVE | auto MOVE | catch | run | buy ITEM [QTY] | heal | to X Y | go PLACE | exits | npcs | talk [WHO]
  python hgss_mcp/cli.py speed [PCT] | swap A B | switch NAME | bag | grass | scene | yes | no | menu ITEM | type TEXT | save
  python hgss_mcp/cli.py journal | note TEXT | goal TEXT | enc SPECIES RESULT [NICK] | death NICK CAUSE | badge NAME
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
    elif cmd == "speed":
        print(mcp_server.speed(int(args[0]) if args else 100))
    elif cmd == "switch":
        r = g.switch_to(" ".join(args))
        print(f"{len(r['messages'])} messages; {r['note']}")
        if r["messages"]:
            screen.montage(r["messages"]).save(SHOTS / "text.png")
            print(SHOTS / "text.png")
        print(g.summary())
    elif cmd == "swap":
        print(mcp_server.swap(args[0], args[1]))
    elif cmd == "bag":
        print(mcp_server.bag())
    elif cmd == "go":
        print(mcp_server.go_to(" ".join(args)))
    elif cmd == "npcs":
        print(mcp_server.npcs())
    elif cmd == "talk":
        r = g.talk(" ".join(args))
        print(r)
        if r.startswith("talking"):
            main(["text"])
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
    elif cmd == "buy":
        print(g.buy(" ".join(args[:-1]) if len(args) > 1 and args[-1].isdigit() else " ".join(args),
                    int(args[-1]) if len(args) > 1 and args[-1].isdigit() else 1))
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
    elif cmd == "enc":  # enc SPECIES RESULT [NICKNAME]
        print(mcp_server.log_encounter(args[0], args[1], args[2] if len(args) > 2 else ""))
    elif cmd == "death":  # death NICKNAME CAUSE...
        print(mcp_server.log_death(args[0], " ".join(args[1:])))
    elif cmd == "badge":
        print(mcp_server.add_badge(" ".join(args)))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:] or ["help"])
