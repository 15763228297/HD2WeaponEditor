"""The build fingerprint must notice when the game is updated.

Why this is the test that matters: on 1.8.45850 the tool did not fail - it
searched for a row that had moved, never found it, and the player experienced
"the game stutters for a minute and nothing happens". Nothing in the tool said
the data was from another build. This guard exists so the next update is a
sentence in the UI instead of a mystery.

The tests drive the comparison directly rather than trusting the file that
happens to be on disk, and each one asserts the *specific* behaviour that would
otherwise regress:

  1. a matching hash is "ok", with no message to show
  2. a different hash is "stale", and the message names both versions
  3. no recorded fingerprint is "unknown" (not "ok" - absence must not read as
     approval)
  4. no game.dll is "unknown", not a crash
  5. the API payload carries the status, so the UI can act on it
  6. a stale build must not be reported as ok even when the version STRING
     matches - the hash is the authority, because a build can change without
     the exe version moving

Run:  python tests/test_build_fingerprint.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_fingerprint as bf  # noqa: E402

failures = 0
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures, checks
    checks += 1
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} {detail}")
        failures += 1


def with_state(recorded: dict | None, installed: dict | None):
    """Run `check()` against a scripted pair of states, then restore."""
    real_read, real_compute = bf.read, bf.compute
    bf.read = lambda: recorded
    bf.compute = lambda: installed or {
        "game_dll_sha256": None, "exe_version": None,
        "game_dll_path": None, "generated_at": None,
    }
    try:
        return bf.check()
    finally:
        bf.read, bf.compute = real_read, real_compute


def main() -> int:
    A = "a" * 64
    B = "b" * 64

    print("== a matching build is ok ==")
    r = with_state(
        {"game_dll_sha256": A, "exe_version": "1.8.45850.0"},
        {"game_dll_sha256": A, "exe_version": "1.8.45850.0"},
    )
    check("status is ok", r["status"] == "ok", f"got {r['status']}")
    check("no message to show", r["message"] == "", f"got {r['message']!r}")

    print()
    print("== a different build is stale ==")
    r = with_state(
        {"game_dll_sha256": A, "exe_version": "1.8.45317.0"},
        {"game_dll_sha256": B, "exe_version": "1.8.45850.0"},
    )
    check("status is stale", r["status"] == "stale", f"got {r['status']}")
    check("the message names the data's version", "1.8.45317.0" in r["message"],
          r["message"][:120])
    check("the message names the installed version", "1.8.45850.0" in r["message"],
          r["message"][:120])
    check("the message says what to do", "重新生成" in r["message"],
          r["message"][:160])

    print()
    print("== the hash is the authority, not the version string ==")
    # Same version string, different bytes: a hotfix or a rebuilt module. If
    # this reported ok, the guard would miss exactly the update it exists for.
    r = with_state(
        {"game_dll_sha256": A, "exe_version": "1.8.45850.0"},
        {"game_dll_sha256": B, "exe_version": "1.8.45850.0"},
    )
    check("still stale when only the bytes changed", r["status"] == "stale",
          f"got {r['status']}")

    print()
    print("== a missing fingerprint is unknown, not ok ==")
    r = with_state(None, {"game_dll_sha256": A, "exe_version": "1.8.45850.0"})
    check("status is unknown", r["status"] == "unknown", f"got {r['status']}")
    check("absence is not reported as ok", r["status"] != "ok")
    check("the message explains itself", "build_fingerprint" in r["message"]
          or "未记录" in r["message"], r["message"][:120])

    print()
    print("== no game.dll is unknown, not a crash ==")
    r = with_state({"game_dll_sha256": A, "exe_version": "1.8.45317.0"},
                   {"game_dll_sha256": None, "exe_version": None})
    check("status is unknown", r["status"] == "unknown", f"got {r['status']}")
    check("it does not claim stale", r["status"] != "stale")

    print()
    print("== the recorded file on disk describes this machine ==")
    rec = bf.read()
    if rec is None:
        print("  [SKIP] data/build_fingerprint.json not written yet")
    else:
        check("it records a game.dll hash", bool(rec.get("game_dll_sha256")),
              "no hash")
        check("it records the exe version", bool(rec.get("exe_version")),
              "no version")
        live = bf.compute()
        if live.get("game_dll_sha256"):
            check("and it matches the installed game",
                  rec["game_dll_sha256"] == live["game_dll_sha256"],
                  "the data on disk is for another build - regenerate it")

    print()
    print("== the API carries the status to the UI ==")
    app_path = ROOT / "gui" / "app.py"
    src = app_path.read_text(encoding="utf-8")
    check("api/weapons includes a build status", '"build": _build_status()' in src,
          "the UI has no way to learn the data is stale")
    check("a fingerprint failure is not fatal", "except Exception" in src,
          "an exception here would break the weapon list")
    tpl = (ROOT / "gui" / "templates" / "index.html").read_text(encoding="utf-8")
    check("the page has somewhere to show it", 'id="buildwarn"' in tpl)
    check("boot renders it", "renderBuildBanner()" in tpl)
    check("it only speaks up when not ok", 'b.status === "ok"' in tpl,
          "a banner on every launch trains people to ignore it")

    print()
    if failures == 0:
        print(f"test_build_fingerprint: PASS ({checks} checks)")
        return 0
    print(f"test_build_fingerprint: FAIL ({failures} of {checks} failed)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
