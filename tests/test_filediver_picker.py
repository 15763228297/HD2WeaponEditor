"""The filediver picker must prefer a build that can still parse the game.

Why this needs a test rather than a comment: the failure it guards against is
silent and looks like something else. `extract()` used to name one fixed path.
When the game updated, that binary started failing with

    error parsing hash lookup ... expected final bytes read to be 0xDEADBEE7
    but were 0x00000000

which reads like corrupt game data. The actual cause is that filediver parses the
game's own resource formats and a build older than the game cannot handle the new
ones. A newer filediver was sitting right there and was never considered.

So the test drives the real search over a synthetic filesystem: it builds
candidate directories containing stand-ins with controlled mtimes, and asserts
which one is chosen. It also checks the two error paths produce something the
reader can act on, because "not found" and "this build is too old" have different
fixes.

Run:  python tests/test_filediver_picker.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fetch_strings as F  # noqa: E402

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  -- {detail}" if detail else ""))


def make_install(root: Path, name: str, mtime: float) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    exe = d / "filediver.exe"
    exe.write_bytes(b"MZ fake")
    os.utime(exe, (mtime, mtime))
    return d


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fd_pick_"))
    old_m, new_m = 1_600_000_000.0, 1_700_000_000.0

    # -- 1. newest wins, regardless of list order --------------------------
    d_old = make_install(tmp, "old", old_m)
    d_new = make_install(tmp, "new", new_m)
    saved = F.FD_SEARCH
    try:
        # Deliberately list the stale one FIRST, which is the shape of the real
        # bug: a hardcoded path pointed at the stale install and won by default.
        F.FD_SEARCH = [d_old, d_new]
        got = F.find_filediver()
        check("the newest install is chosen even when listed last",
              got == d_new / "filediver.exe", f"got {got}")

        # And when listed first, it still must not beat the newer one.
        F.FD_SEARCH = [d_new, d_old]
        got = F.find_filediver()
        check("list order does not override mtime",
              got == d_new / "filediver.exe", f"got {got}")

        # -- 2. a single stale install is still returned -------------------
        # The picker must not invent a newer one; extract() reports the version
        # problem instead. Silently refusing to run would hide a valid install.
        F.FD_SEARCH = [d_old]
        got = F.find_filediver()
        check("a lone stale install is still returned",
              got == d_old / "filediver.exe", f"got {got}")

        # -- 3. missing everywhere -> actionable message -------------------
        F.FD_SEARCH = [tmp / "nope", tmp / "also_nope"]
        try:
            F.find_filediver()
            check("no install raises", False, "returned instead of exiting")
        except SystemExit as exc:
            msg = str(exc)
            check("no install names every place it looked",
                  "nope" in msg and "also_nope" in msg, msg[:120])
            check("no install gives a download command",
                  "filediver-cli-windows.zip" in msg and "curl" in msg, msg[:120])

        # -- 4. directories without the exe are not candidates -------------
        empty = tmp / "empty"
        empty.mkdir(exist_ok=True)
        F.FD_SEARCH = [empty, d_new]
        try:
            got = F.find_filediver()
            check("a directory with no exe is skipped",
                  got == d_new / "filediver.exe", f"got {got}")
        except FileNotFoundError as exc:
            # Without the is_file() filter the sort calls stat() on a path that
            # does not exist. Report that as a failed check rather than letting
            # it escape as an exception, so the reason is visible.
            check("a directory with no exe is skipped", False, f"crashed: {exc}")
    finally:
        F.FD_SEARCH = saved

    # -- 5. the real search finds the newest filediver on this machine -----
    #
    # The invariant is not "the newest of the entries it happens to list" - that
    # holds trivially however short the list is. It is "nothing reachable from
    # the search roots is newer than what it chose", which is what makes the list
    # itself load-bearing. An earlier version compared against a fixed mtime
    # threshold; the stale binary's timestamp passed it, so dropping the working
    # install from the search roots went unnoticed.
    real = F.find_filediver()
    check("the real picker returns an existing file", real.is_file(), str(real))

    roots = set(F.FD_SEARCH) | {d.parent for d in F.FD_SEARCH}
    reachable = []
    for r in roots:
        if r.is_dir():
            reachable += [p for p in r.glob("*/filediver.exe") if p.is_file()]
    if reachable:
        newest = max(reachable, key=lambda p: p.stat().st_mtime)
        check("the picker chooses the newest filediver reachable from its roots",
              real.stat().st_mtime >= newest.stat().st_mtime,
              f"chose {real} ({real.stat().st_mtime}), newest is {newest} "
              f"({newest.stat().st_mtime})")
    else:
        check("at least one filediver is reachable from the roots", False,
              f"searched {sorted(str(r) for r in roots)}")

    # -- 6. the download instruction lands where the search looks ----------
    #
    # The error message says to unzip into FD_SEARCH[0].parent. The archive
    # contains `filediver-cli/filediver.exe`, so unzipping there must produce a
    # path the picker would then find. If those two drift apart the user follows
    # the instruction and the tool still reports "not found", which is the
    # worst kind of error message.
    dest = F.FD_SEARCH[0].parent
    unzipped = dest / "filediver-cli" / "filediver.exe"
    check("unzipping where the message says produces a searched location",
          unzipped.parent in F.FD_SEARCH,
          f"{unzipped.parent} not in {[str(d) for d in F.FD_SEARCH]}")

    # -- 7. the version-mismatch hint mentions the right fix --------------
    src = (ROOT / "tools" / "fetch_strings.py").read_text(encoding="utf-8")
    check("the failure path explains it is a filediver version problem",
          "predates the installed game build" in src)
    check("the failure path does not blame the game files",
          "Nothing is wrong with the game files" in src)
    check("the stale path is no longer a single hardcoded constant",
          "\nFD = Path.home()" not in src)

    print(f"\n{PASS} PASS, {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
