"""The shipped EXE must not contain the game's own asset files.

Why this is a test and not a note in the build script: the v0.3.0 release shipped
24.6 MB of game data inside the exe. The build script passed `--add-data "data;data"`,
which copies the whole directory - the unpacked .dl_bin tables, the game's own
.strings exports, data/wiki, all_names.txt and the simulated memory images. None
of that is redistributable, and the tool does not need any of it: the GUI was run
with every one of those files absent and listed 98 weapons and generated a mod.

A comment would not have caught it, and did not - the v0.3.0 zip was built,
released and downloaded before anyone looked inside the exe.

Two things are checked, and they fail for different reasons:
  * no game-asset entries in the archive (packaging regression)
  * the three derived files the GUI reads ARE present (over-trimming regression)

Run:  python tests/test_exe_packaging.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_exe_contents as C  # noqa: E402

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  -- {detail}" if detail else ""))


def main() -> int:
    # -- 1. normalise collapses the listing's escaped separators -----------
    # The listing renders one separator as two literal backslashes. Getting this
    # wrong is not cosmetic: the first version of the checker matched the raw
    # string, found nothing, and reported "no game data" while 8.7 MB of
    # strings.json sat in the archive.
    check("a doubled backslash path normalises to a plain path",
          C.normalise(r"data\\raw\\entities.dl_bin") == "data/raw/entities.dl_bin",
          C.normalise(r"data\\raw\\entities.dl_bin"))
    check("a forward-slash path is unchanged",
          C.normalise("data/raw/x") == "data/raw/x")
    check("a quadrupled backslash also collapses",
          C.normalise(r"data\\\\strings.json") == "data/strings.json",
          C.normalise(r"data\\\\strings.json"))

    # -- 2. each game-data pattern fires on its real name ------------------
    cases = [
        (r"data\\raw\\entities.dl_bin", "unpacked game tables (.dl_bin)"),
        (r"data\\raw\\generated_damage_settings.dl_bin", "unpacked game tables (.dl_bin)"),
        (r"data\\strings_out\\0x0bd1bd2352123535.strings.json", "raw .strings exports"),
        (r"data\\strings.json", "merged string table"),
        (r"data\\all_names.txt", "name dump"),
        (r"data\\all_names.err", "name dump"),
        (r"data\\wiki\\pages\\R-4_Hyena.json", "wiki page cache"),
        (r"data\\sim_memory.bin", "simulated memory image"),
    ]
    for name, want in cases:
        norm = C.normalise(name)
        hit = next((label for pat, label in C.GAME_DATA_PATTERNS if pat.search(norm)), None)
        check(f"{norm} is flagged as game data", hit == want, f"got {hit!r}")

    # -- 3. the files the tool needs are NOT flagged ----------------------
    for keep in C.NEEDED:
        norm = C.normalise(keep)
        hit = next((label for pat, label in C.GAME_DATA_PATTERNS if pat.search(norm)), None)
        check(f"{norm} is not flagged as game data", hit is None, f"flagged as {hit!r}")

    # -- 4. the build script does not add the whole data directory --------
    bat = (ROOT / "打包EXE.bat").read_text(encoding="utf-8", errors="replace")
    check("the build script does not bundle all of data/",
          '--add-data "data;data"' not in bat)
    for want in ("data/damage_records.json", "data/weapon_names.json",
                 "data/build_fingerprint.json"):
        check(f"the build script bundles {want}",
              want in bat.replace("\\", "/"), want)
    check("the build script runs the packaging check",
          "check_exe_contents.py" in bat and "--fail-on-game-data" in bat)

    # -- 5. the shipped exe, if built, passes the real check --------------
    exe = ROOT / "dist" / "HD2WeaponEditor-Standalone.exe"
    if exe.is_file():
        rows = C.listing(exe)
        check("the archive listing is readable", len(rows) > 50, f"{len(rows)} entries")

        names = {C.normalise(n) for _, n in rows}
        offenders = sorted(n for n in names
                           if any(p.search(n) for p, _ in C.GAME_DATA_PATTERNS))
        check("the shipped exe contains no game data", not offenders,
              f"{len(offenders)} entries, e.g. {offenders[:3]}")

        missing = [w for w in C.NEEDED if w not in names]
        check("the shipped exe contains every file the GUI reads", not missing,
              f"missing {missing}")

        check("the shipped exe is smaller than the 24.6 MB of game data it used to carry",
              exe.stat().st_size < 30_000_000,
              f"{exe.stat().st_size:,} bytes")
    else:
        print("  (dist exe not built - skipping the archive checks)")

    print(f"\n{PASS} PASS, {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
