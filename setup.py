"""One-time setup: download BizHawk (official release, SHA-256 checked), build the data tables, and
remember where your ROM is.

  python setup.py --rom "C:/path/to/your/HeartGold.nds"

Nothing copyrighted is downloaded or shipped: you provide your own ROM. BizHawk is fetched from its
official GitHub release; data tables are built from PokeAPI and pret/pokeheartgold sources.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "hgss_mcp"))

BIZHAWK_VERSION = "2.11.1"
BIZHAWK = {
    "Windows": ("BizHawk-2.11.1-win-x64.zip", "dd7cbd5e205b09c5bce6afabc4f8aa525acc0bbeb9753b198a8db3fe6a5e5717"),
    "Linux": ("BizHawk-2.11.1-linux-x64.tar.gz", "38c9c12287e337a0a6923fd527767c853457d61d71e7d8ad1a772d64ce8bc93f"),
}


def get_bizhawk() -> None:
    target = ROOT / "emu" / "BizHawk"
    if (target / "EmuHawk.exe").exists() or (target / "EmuHawkMono.sh").exists():
        print(f"BizHawk already in {target}")
        return
    name, sha = BIZHAWK.get(platform.system(), (None, None))
    if name is None:
        sys.exit(f"No BizHawk build for {platform.system()}; install BizHawk {BIZHAWK_VERSION} and set HGSS_BIZHAWK")
    url = f"https://github.com/TASEmulators/BizHawk/releases/download/{BIZHAWK_VERSION}/{name}"
    dl = ROOT / "emu" / "dl" / name
    dl.parent.mkdir(parents=True, exist_ok=True)
    if not dl.exists():
        print(f"downloading {url}")
        urllib.request.urlretrieve(url, dl)
    digest = hashlib.sha256(dl.read_bytes()).hexdigest()
    if digest != sha:
        dl.unlink()
        sys.exit(f"SHA-256 mismatch for {name} (got {digest}); download removed, not extracted")
    print("SHA-256 OK, extracting")
    target.mkdir(parents=True, exist_ok=True)
    if name.endswith(".zip"):
        with zipfile.ZipFile(dl) as z:
            z.extractall(target)
    else:
        with tarfile.open(dl) as t:
            t.extractall(target, filter="data")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", help="path to your own HeartGold/SoulSilver ROM (.nds)")
    ap.add_argument("--skip-bizhawk", action="store_true", help="use your own BizHawk (set HGSS_BIZHAWK)")
    args = ap.parse_args()

    if not args.skip_bizhawk:
        get_bizhawk()
    subprocess.run([sys.executable, str(ROOT / "hgss_mcp" / "build_data.py")], check=True)

    import config
    if args.rom:
        rom = Path(args.rom).expanduser().resolve()
        if not rom.exists():
            sys.exit(f"ROM not found: {rom}")
        cfg_file = config.WORK / "config.json"
        cfg = json.loads(cfg_file.read_text()) if cfg_file.exists() else {}
        cfg["rom"] = str(rom)
        cfg_file.write_text(json.dumps(cfg, indent=2))
        print(f"ROM saved in {cfg_file}")
    print("Done. Add the MCP server to your client (see README), then call state().")


if __name__ == "__main__":
    main()
