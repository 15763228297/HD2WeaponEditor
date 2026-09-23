"""Record which game build the derived data describes, and detect when it goes stale.

WHY THIS EXISTS

The editor's data pipeline reads the game's tables and writes `data/*.json`.
Those files are only meaningful for the build they were read from: 1.8.45850
renumbered the damage rows, moved R-4 from position 137 to 147, and added ten
rows. A tool built from the previous build's data does not fail loudly - it
searches for a row that no longer exists, never finds it, and the player
experiences it as "the game stutters for a minute and nothing changes". That is
exactly how the 1.8.45850 update presented, and it took a log reading to
diagnose.

So the build identity is written down when the data is generated, and checked
when the tool runs. A mismatch is reported in the UI before the user wastes a
game launch on it.

WHAT IS COMPARED

`game.dll`'s sha256 is the anchor. It changes on every patch that touches the
tables, and it is cheap to hash (16 MB). The exe's file version is recorded too,
because it is the number a user can read off their own install and quote in a
bug report - the hash cannot be checked by eye.

WHY A HASH RATHER THAN THE TABLE COUNTS

Row counts would also detect the update, but they move on their own: a build
that only rebalances values keeps the same counts while changing every number.
The hash changes whenever the module does, which is the property needed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FINGERPRINT = ROOT / "data" / "build_fingerprint.json"

GAME_DLL_CANDIDATES = [
    Path(r"D:\program files (x86)\steam\steamapps\common\Helldivers 2\data\game\game.dll"),
]


def find_game_dll() -> Path | None:
    """The installed game.dll, or None when the game is not on this machine.

    Reuses the Steam-library discovery written for the lua51.dll lookup, so a
    game installed outside the default library is still found.
    """
    for p in GAME_DLL_CANDIDATES:
        if p.exists():
            return p
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        from ljcompile import _steam_roots, _candidate_dll_paths  # type: ignore

        for root in _steam_roots() or []:
            cand = Path(root) / "steamapps" / "common" / "Helldivers 2" / "data" / "game" / "game.dll"
            if cand.exists():
                return cand
        # The library walk also yields lua51.dll paths; game.dll sits beside it.
        for cand in _candidate_dll_paths() or []:
            g = Path(cand).parent.parent / "data" / "game" / "game.dll"
            if g.exists():
                return g
    except Exception:
        pass
    return None


def _exe_version(game_dir: Path) -> str | None:
    """The installed exe's file version, for humans to quote."""
    exe = game_dir / "helldivers2.exe"
    if not exe.exists():
        return None
    try:
        import subprocess

        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Item '{exe}').VersionInfo.FileVersion"],
            capture_output=True, text=True, timeout=20,
        )
        v = out.stdout.strip()
        return v or None
    except Exception:
        return None


def compute() -> dict:
    """Describe the game build this machine has installed."""
    dll = find_game_dll()
    if dll is None:
        return {"game_dll_sha256": None, "exe_version": None,
                "game_dll_path": None, "generated_at": None}
    h = hashlib.sha256(dll.read_bytes()).hexdigest()
    return {
        "game_dll_sha256": h,
        "exe_version": _exe_version(dll.parent.parent.parent / "bin"),
        "game_dll_path": str(dll),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write(table_counts: dict | None = None) -> dict:
    """Record the current build alongside the table sizes the data was built from."""
    doc = compute()
    if table_counts:
        doc["tables"] = table_counts
    FINGERPRINT.write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    return doc


def read() -> dict | None:
    if not FINGERPRINT.exists():
        return None
    try:
        return json.loads(FINGERPRINT.read_text(encoding="utf-8"))
    except Exception:
        return None


def check() -> dict:
    """Compare the recorded build against the installed one.

    Returns a dict with `status`:
      "ok"       - same build, data is current
      "stale"    - the game changed; data must be regenerated
      "unknown"  - no fingerprint recorded, or no game.dll to compare against
    plus `recorded` / `installed` and a `message` suitable for the UI.
    """
    rec = read()
    cur = compute()
    if rec is None:
        return {
            "status": "unknown",
            "recorded": None,
            "installed": cur,
            "message": "未记录数据对应的游戏版本（data/build_fingerprint.json 不存在）。"
                       "如果游戏刚更新过，请重新生成数据。",
        }
    if cur["game_dll_sha256"] is None:
        return {
            "status": "unknown",
            "recorded": rec,
            "installed": cur,
            "message": "找不到 game.dll，无法确认数据是否对应当前游戏版本。",
        }
    if rec.get("game_dll_sha256") == cur["game_dll_sha256"]:
        return {
            "status": "ok",
            "recorded": rec,
            "installed": cur,
            "message": "",
        }
    rec_v = rec.get("exe_version") or "未知版本"
    cur_v = cur.get("exe_version") or "未知版本"
    return {
        "status": "stale",
        "recorded": rec,
        "installed": cur,
        "message": (
            f"游戏已更新（数据基于 {rec_v}，当前 {cur_v}）。\n"
            "当前数据可能已经过期：武器行号会随更新变化，用过期数据生成的 Mod 会改错武器或完全找不到目标。\n"
            "请重新生成数据后再使用。"
        ),
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="record the installed build as the data's baseline")
    ap.add_argument("--check", action="store_true",
                    help="compare the recorded build against the installed one")
    args = ap.parse_args()

    if args.write:
        counts = {}
        for name in ("damage_records", "weapon_names"):
            p = ROOT / "data" / f"{name}.json"
            if p.exists():
                doc = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(doc, list):
                    counts[name] = len(doc)
                elif isinstance(doc, dict) and "weapons" in doc:
                    counts[name] = len(doc["weapons"])
        doc = write(counts)
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    elif args.check:
        print(json.dumps(check(), indent=2, ensure_ascii=False))
    else:
        ap.print_help()
