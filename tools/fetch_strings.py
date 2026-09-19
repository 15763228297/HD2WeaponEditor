"""Extract the game's current string tables with filediver and build a key->name map.

Why this step exists: `English.json` from the datamine repo predates recent
weapons, so a 2026 weapon's display name is simply absent from it (`Hyena` ->
0 hits). The game itself ships `.strings` resources that **do** contain it, and
filediver can extract them from the local install - so the name source is the
game, not a third-party dump.

Verified: a `.strings` export contains `"Hyena"` and `"HYENA"` as separate keys
(3318361937 / 111718644), and the localisation set includes Chinese.

Usage:
    python tools/fetch_strings.py            # extract with filediver
    python tools/fetch_strings.py --build    # build data/strings.json from exports

Output: `data/strings.json` = { str(key): { lang: value } } with English
preferred when several languages share a key.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAME = Path(r"D:\program files (x86)\steam\steamapps\common\Helldivers 2")
FD = Path.home() / "AppData/Local/Temp/hd2mod/assessment/fdtool/filediver-cli"
OUT = ROOT / "data" / "strings_out"

# Language preference when a key exists in several packs.
PREFERRED = ["English (US)", "English (UK)"]


def extract() -> None:
    if not FD.exists():
        sys.exit(f"filediver not found at {FD}; download filediver-cli-windows.zip")
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(FD / "filediver.exe"),
        "--gamedir", str(GAME),
        "-i", "*.strings",
        "-o", str(OUT),
        "--text-format", "json",
    ]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)


def build() -> None:
    if not OUT.exists():
        sys.exit(f"no exports at {OUT}; run without --build first")
    merged: dict[str, dict[str, str]] = {}
    files = 0
    for f in sorted(OUT.glob("*.strings.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        lang = (data.get("Language") or {}).get("KnownFriendlyName")
        if not lang:
            # One pack has no Language block; skip rather than mislabel it.
            print(f"  skip (no language): {f.name}")
            continue
        files += 1
        for item in data.get("Items") or []:
            key = str(item["Key"])
            merged.setdefault(key, {})[lang] = item["Value"]

    out = ROOT / "data" / "strings.json"
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"merged {files} files -> {len(merged)} keys -> {out}")

    for probe in ("Hyena", "HYENA"):
        hits = [k for k, v in merged.items() if v.get("English (US)") == probe
                or v.get("English (UK)") == probe]
        print(f"  probe {probe!r}: {hits}")


def english_map() -> dict[str, str]:
    """key -> best English value."""
    data = json.loads((ROOT / "data" / "strings.json").read_text(encoding="utf-8"))
    out = {}
    for key, langs in data.items():
        for lang in PREFERRED:
            if lang in langs:
                out[key] = langs[lang]
                break
        else:
            # Fall back to any language so the key still resolves.
            out[key] = next(iter(langs.values()))
    return out


if __name__ == "__main__":
    if "--build" in sys.argv:
        build()
    else:
        extract()
