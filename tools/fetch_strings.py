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
OUT = ROOT / "data" / "strings_out"

# Where a filediver install may live. Searched in order; the newest wins.
#
# Why this is a search rather than one fixed path: filediver parses the game's
# own resource formats, so a build of it that predates a game update can fail
# outright on the new data - "error parsing hash lookup ... expected final bytes
# read to be 0xDEADBEE7 but were 0x00000000". That is a filediver limitation, not
# a corrupt install, and the fix is a newer filediver. A hardcoded path silently
# kept using the old one, so a working install elsewhere was never found.
FD_SEARCH = [
    Path.home() / "AppData/Local/Temp/fd_new/filediver-cli",
    Path.home() / "AppData/Local/Temp/hd2mod/assessment/fdtool/filediver-cli",
    Path.home() / "AppData/Local/filediver",
    ROOT / ".tools" / "filediver-cli",
]
FD_DOWNLOAD = ("https://github.com/xypwn/filediver/releases/latest/download/"
               "filediver-cli-windows.zip")

# Language preference when a key exists in several packs.
PREFERRED = ["English (US)", "English (UK)"]

# Packs whose Language block carries only a hash and no friendly name.
#
# The hash is the game's own stable identifier, so it is keyed on rather than
# guessed at. The evidence for this one: its 18 packs are the only unnamed ones
# in the build, they hold 4,419 entries containing Simplified-only characters
# and none containing Traditional-only ones, and their values differ from the
# Traditional pack on 2,507 of 2,807 shared keys - the same relationship those
# two scripts have everywhere else.
#
# A "no name means Simplified" rule would have been shorter and would mislabel
# the next unnamed pack, which is why the hash is spelled out instead.
LANG_BY_HASH = {"0x5942ccf7": "Chinese (Simplified)"}


def find_filediver() -> Path:
    """The newest filediver.exe among the known locations.

    Newest by mtime, not by list order: the point is to prefer a build that
    postdates the installed game, and the one that does is the one most recently
    downloaded. A stale build still parses the *old* formats, so picking the
    wrong one fails in a way that looks like the game data is broken.
    """
    found = [d / "filediver.exe" for d in FD_SEARCH
             if (d / "filediver.exe").is_file()]
    if not found:
        sys.exit(
            f"filediver not found. Looked in:\n"
            + "\n".join(f"  {d}" for d in FD_SEARCH)
            + f"\n\nDownload it with:\n"
              f"  curl -sL {FD_DOWNLOAD} -o fd.zip && unzip fd.zip -d "
              f"{FD_SEARCH[0].parent}"
        )
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0]


def extract() -> None:
    fd = find_filediver()
    if fd.parent != FD_SEARCH[0]:
        print(f"note: using {fd} (newest of {len(FD_SEARCH)} known locations)")
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(fd),
        "--gamedir", str(GAME),
        "-i", "*.strings",
        "-o", str(OUT),
        "--text-format", "json",
    ]
    print(" ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        sys.exit(
            f"\nfilediver exited {exc.returncode}.\n\n"
            "If the error mentions a hash lookup or 'expected final bytes read',\n"
            "this filediver predates the installed game build and cannot parse\n"
            "the new resource formats. Get a newer one:\n\n"
            f"  curl -sL {FD_DOWNLOAD} -o fd.zip\n"
            f"  unzip -o fd.zip -d {FD_SEARCH[0].parent}\n\n"
            "Do not try to narrow the selection around it: the failure happens\n"
            "while reading metadata, before -i or --exclude are applied, so no\n"
            "combination of filters avoids it. Only a newer build will.\n\n"
            "Then re-run. Nothing is wrong with the game files or the mod."
        )


def build() -> None:
    if not OUT.exists():
        sys.exit(f"no exports at {OUT}; run without --build first")
    merged: dict[str, dict[str, str]] = {}
    files = 0
    for f in sorted(OUT.glob("*.strings.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        language = data.get("Language") or {}
        lang = language.get("KnownFriendlyName") or LANG_BY_HASH.get(
            str(language.get("Hash")))
        if not lang:
            # Skip rather than mislabel: an unrecognised hash is a language this
            # script has not been taught, not one to guess at.
            print(f"  skip (unknown language hash {language.get('Hash')!r}): "
                  f"{f.name}")
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
