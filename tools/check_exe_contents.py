"""What game data got bundled into the shipped EXE?

The build script passes `--add-data "data;data"`, which copies the whole data
directory - including the unpacked game tables and the raw .strings exports,
none of which are redistributable. This reads the archive listing and reports
exactly which entries those are and how much they cost, so the fix is aimed at
named files rather than "exclude data somehow".

Run:  python tools/check_exe_contents.py dist/HD2WeaponEditor-Standalone.exe
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files that are copies of the game's own assets, in whole or in part.
#
# The archive listing escapes each separator as a literal backslash pair, so a
# name arrives as `data\\\\raw\\\\entities.dl_bin`. Patterns therefore match on a
# normalised name (see `normalise`) rather than the raw string - matching the raw
# string is what made the first version of this check report "no game data found"
# while the listing plainly showed 8.7 MB of strings.json.
GAME_DATA_PATTERNS = [
    (re.compile(r"^data/raw/"), "unpacked game tables (.dl_bin)"),
    (re.compile(r"^data/strings_out/"), "raw .strings exports"),
    (re.compile(r"^data/strings\.json$"), "merged string table"),
    (re.compile(r"^data/all_names"), "name dump"),
    (re.compile(r"^data/wiki/"), "wiki page cache"),
    (re.compile(r"^data/sim_memory"), "simulated memory image"),
]


def normalise(name: str) -> str:
    """Archive name -> a comparable path with single forward slashes.

    The listing renders a separator as two literal backslashes, so collapsing
    runs of them is required before any pattern can match.
    """
    return re.sub(r"[\\/]+", "/", name).lstrip("./")

# Files the tool genuinely needs at runtime, derived from game data but authored
# by this project: these are the numbers the GUI displays.
#
# Kept deliberately short and verified by running the GUI with everything else
# absent (98 weapons listed, mod generated, build fingerprint read). mapping.json,
# damage_ids.json, route_evidence.json and runtime_layout_evidence.json are NOT
# here: nothing on the GUI or mod-generation path reads them, so bundling them
# only added weight.
NEEDED = [
    "data/damage_records.json",
    "data/weapon_names.json",
    "data/build_fingerprint.json",
]


def listing(exe: Path) -> list[tuple[int, str]]:
    out = subprocess.run(
        [sys.executable, "-m", "PyInstaller.utils.cliutils.archive_viewer",
         "-l", str(exe)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    rows = []
    for line in out.stdout.splitlines():
        m = re.search(r"^\s*(\d+), (\d+), (\d+), (\d), '(\w)', '([^']+)'$", line)
        if m:
            rows.append((int(m.group(2)), m.group(6)))
    return rows


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fail_on_game_data = "--fail-on-game-data" in sys.argv

    exe = Path(args[0] if args else ROOT / "dist" / "HD2WeaponEditor-Standalone.exe")
    if not exe.is_file():
        print(f"no such exe: {exe}")
        return 1

    rows = listing(exe)
    print(f"{exe.name}: {exe.stat().st_size:,} bytes, {len(rows)} archive entries")
    print()

    groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for size, raw_name in rows:
        name = normalise(raw_name)
        for pat, label in GAME_DATA_PATTERNS:
            if pat.search(name):
                groups[label].append((size, raw_name))
                break

    if not groups:
        print("no game-data entries found")
    else:
        total = 0
        print("== game data found in the archive ==")
        for label, items in sorted(groups.items(),
                                   key=lambda x: -sum(s for s, _ in x[1])):
            csum = sum(s for s, _ in items)
            total += csum
            print(f"  {label:32s} {len(items):4d} entries  {csum:12,} bytes")
        print(f"  {'TOTAL':32s} {'':4s}          {total:12,} bytes")
        print()
        print("  sample entries:")
        for label, items in groups.items():
            for size, name in items[:3]:
                print(f"    {size:10,}  {name}")

    print()
    print("== files the tool needs, present or not ==")
    present = {normalise(n) for _, n in rows}
    missing = [w for w in NEEDED if w not in present]
    for want in NEEDED:
        got = want in present
        print(f"  {'ok  ' if got else 'MISS'} {want}")

    unmatched = [(s, n) for s, n in sorted(rows)
                 if normalise(n).startswith("data/")
                 and not any(p.search(normalise(n)) for p, _ in GAME_DATA_PATTERNS)]
    if unmatched:
        print()
        print("== other data/ entries (not matched above) ==")
        for size, name in unmatched:
            print(f"  {size:10,}  {name}")

    if fail_on_game_data and (groups or missing):
        print()
        print("RESULT: NOT SHIPPABLE")
        if groups:
            print("  game data is bundled - remove it from the --add-data list")
        if missing:
            print(f"  required files absent: {', '.join(missing)}")
        return 1

    if groups or missing:
        print()
        print("RESULT: not shippable (see above)")
    else:
        print()
        print("RESULT: clean - no game data, all required files present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
