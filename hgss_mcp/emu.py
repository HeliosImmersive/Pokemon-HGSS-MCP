"""Low-level controller for BizHawk running bridge.lua (file-based IPC, see bridge.lua)."""
from __future__ import annotations

import itertools
import os
import subprocess
import time
from pathlib import Path

from config import BIZHAWK, IPC, LUA, ROM, ROOT, SHOTS  # noqa: F401  (re-exported)

# BizHawk's melonDS core has no separate main-RAM domain; read through the ARM9 bus with CPU
# addresses (main RAM is 0x02000000-0x023FFFFF).
MAIN = "ARM9 System Bus"


class EmuError(RuntimeError):
    pass


class Emu:
    def __init__(self, ipc: Path = IPC):
        self.ipc = ipc
        self.ipc.mkdir(parents=True, exist_ok=True)
        self.cmd = ipc / "cmd.txt"
        self.resp = ipc / "resp.txt"
        self._ids = itertools.count(int(time.time() * 1000) % 1_000_000_000)

    # -- transport -------------------------------------------------------------------------
    def run(self, ops: list[str], timeout: float = 30.0) -> dict[str, list[str]]:
        """Send ops as one command and wait until BizHawk has executed all of them."""
        cid = str(next(self._ids))
        if self.resp.exists():
            self.resp.unlink()
        tmp = self.ipc / "cmd.tmp"
        tmp.write_text("id " + cid + "\n" + "\n".join(ops) + "\n", encoding="utf-8")
        os.replace(tmp, self.cmd)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.resp.exists():
                try:
                    lines = self.resp.read_text(encoding="utf-8").splitlines()
                except OSError:
                    time.sleep(0.005)
                    continue
                out: dict[str, list[str]] = {}
                for line in lines:
                    k, _, v = line.partition(" ")
                    out.setdefault(k, []).append(v)
                if out.get("id", [None])[0] == cid:
                    self.resp.unlink(missing_ok=True)
                    if "error" in out:
                        raise EmuError("; ".join(out["error"]))
                    return out
            time.sleep(0.004)
        if self.cmd.exists():
            self.cmd.unlink(missing_ok=True)
        raise EmuError("BizHawk did not answer (is EmuHawk running with bridge.lua?)")

    # -- helpers ---------------------------------------------------------------------------
    def frame(self) -> int:
        return int(self.run([])["frame"][0])

    def press(self, buttons: str, hold: int = 4, after: int = 4) -> None:
        """Tap buttons ("A", "Up", "A,B"...) for `hold` frames, then release for `after` frames."""
        ops = [f"press {buttons} {hold}"]
        if after:
            ops.append(f"wait {after}")
        self.run(ops)

    def ops(self, ops: list[str]) -> dict[str, list[str]]:
        return self.run(ops, timeout=120)

    def wait(self, frames: int) -> None:
        self.run([f"wait {frames}"], timeout=max(30, frames / 30))

    def touch(self, x: int, y: int, frames: int = 3) -> None:
        self.run([f"touch {x} {y} {frames}", "wait 4"])

    def screenshot(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run([f"shot {path}"])
        return path

    def read(self, addr: int, length: int, domain: str = MAIN) -> bytes:
        """Read bytes at a CPU address (default domain: the ARM9 bus)."""
        out = self.ipc / f"read_{os.getpid()}.bin"
        self.run([f"read {domain.replace(' ', '_SP_')} {addr} {length} {out}"])
        data = out.read_bytes()
        out.unlink(missing_ok=True)
        return data

    def speed(self, percent: int) -> None:
        self.run([f"speed {percent}"])

    def info(self) -> dict[str, list[str]]:
        return self.run(["info"])


def launch(wait: float = 20.0) -> Emu:
    """Start EmuHawk with HeartGold and the bridge (if it isn't running) and wait for it."""
    emu = Emu()
    emu.started = False
    try:
        emu.run([], timeout=1.0)
        return emu
    except EmuError:
        pass
    emu.started = True  # we launched it: the caller should boot() past the title screen
    for stale in (emu.cmd, emu.resp):
        stale.unlink(missing_ok=True)
    if ROM is None or not Path(ROM).exists():
        raise EmuError("Set HGSS_ROM (or 'rom' in work/config.json) to your HeartGold/SoulSilver ROM")
    if not Path(BIZHAWK).exists():
        raise EmuError(f"EmuHawk not found at {BIZHAWK}; run python -m setup or set HGSS_BIZHAWK")
    env = dict(os.environ, HGSS_IPC=str(IPC))  # bridge.lua reads its IPC folder from here
    subprocess.Popen([str(BIZHAWK), f"--lua={LUA}", str(ROM)], cwd=str(Path(BIZHAWK).parent), env=env,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            emu.run([], timeout=2.0)
            return emu
        except EmuError:
            time.sleep(0.5)
    raise EmuError("EmuHawk started but the bridge never answered")
