"""Exercise the packaged EXE the way a user does: launch it, click Generate.

Why this exists: three separate frozen-only bugs (missing `tools/`, a LuaJIT
address-space conflict with WebView2, and ZIPs written into the _MEI temp dir)
were invisible in source mode and only appeared when the packaged EXE generated
a mod. A green test suite proves nothing about the frozen build.

This drives the real GUI API of the running EXE rather than importing its code,
so it covers packaging, path resolution and subprocess compilation together.
No window is shown: the EXE is launched headless and polled over HTTP.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "HD2WeaponEditor-Standalone.exe"
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"

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


def http(path: str, data: dict | None = None, timeout: float = 20.0):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": "application/json"} if body else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def main() -> int:
    print("test_frozen_build: the packaged EXE must generate a working mod")

    if not EXE.exists():
        print(f"  [FAIL] {EXE} not found - build it first")
        return 1

    # Launch with the env var the desktop shell reads to select a port, so the
    # test cannot collide with a dev server on 8777.
    import os
    env = dict(os.environ)
    env["HD2_WEAPON_EDITOR_PORT"] = str(PORT)
    env["HD2_WEAPON_EDITOR_HEADLESS"] = "1"

    proc = subprocess.Popen(
        [str(EXE)], env=env, cwd=str(EXE.parent),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    try:
        ready = False
        for _ in range(90):
            if proc.poll() is not None:
                break
            try:
                status, data = http("/api/weapons", timeout=3.0)
                if status == 200:
                    ready = True
                    check("the packaged EXE serves its API",
                          True)
                    check("it reports the full weapon list",
                          len(data.get("weapons", [])) > 80,
                          f"got {len(data.get('weapons', []))}")
                    break
            except Exception:
                time.sleep(1)

        if not ready:
            check("the packaged EXE serves its API", False,
                  "it never answered on the chosen port")
            return 1

        # The click that matters.
        status, out = http("/api/generate", {
            "edits": [{"weapon": "R-4 Hyena", "damage": 400,
                       "durable": 200, "ap": 7}],
        }, timeout=180)
        check("Generate returns 200", status == 200, f"status {status}")

        if isinstance(out, dict) and out.get("ok") is False:
            check("Generate succeeds", False, str(out.get("error"))[:300])
            return 1

        sha = out.get("archive_sha256", "")
        check("Generate produced an archive", bool(sha), f"got {sha!r}")
        check("the archive reports its size",
              int(out.get("archive_bytes", 0)) > 1000,
              f"got {out.get('archive_bytes')}")

        # The frozen-only bug: the ZIP must land somewhere the user can find,
        # not in the _MEI temp directory that is deleted on exit.
        zips = list(EXE.parent.rglob("HD2-Weapon-Stat-Editor.zip"))
        zips = [z for z in zips if "_MEI" not in str(z)]
        check("the ZIP is written next to the EXE, not in the temp dir",
              len(zips) > 0,
              f"found {[str(z) for z in EXE.parent.rglob('*.zip')]}")

        if zips:
            size = zips[0].stat().st_size
            check("the ZIP is not empty", size > 1000, f"{size} bytes")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()

    print(f"\n{checks - failures}/{checks} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
