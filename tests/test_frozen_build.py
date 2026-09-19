"""The frozen (PyInstaller) build must be able to generate, and must write
the ZIP somewhere the user can still find afterwards.

Two failures this pins down, both found only by actually clicking Generate in
the packaged exe - every source-mode test passed the whole time:

  1. `ModuleNotFoundError: No module named 'gen_mod'`. ROOT was computed from
     __file__, which in a frozen build points into the temp extraction dir, so
     `ROOT / "tools"` did not exist. The path must come from sys._MEIPASS.

  2. `MemoryError: luaL_newstate failed`. LuaJIT needs a 2 GB-aligned block of
     address space on x64, and a running WebView2/Chromium runtime has already
     reserved enough that the allocation fails. Compiling must therefore happen
     in a child process, which gets a clean address space.

  3. The ZIP landed in the temp extraction dir, which is deleted on exit, so
     the path shown to the user was dead by the time they looked for it.

Run:  python tests/test_frozen_build.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402


def main() -> int:
    check = base_mod.check

    print("== frozen mode resolves its root from the bundle ==")
    app_src = (ROOT / "gui" / "app.py").read_text(encoding="utf-8")
    gen_src = (ROOT / "tools" / "gen_mod.py").read_text(encoding="utf-8")
    check("app.py checks sys._MEIPASS",
          "_MEIPASS" in app_src, "frozen root not handled in app.py")
    check("gen_mod.py checks sys._MEIPASS",
          "_MEIPASS" in gen_src, "frozen root not handled in gen_mod.py")
    check("gen_mod imports its helpers from its own tree, not another checkout",
          "HD2StratagemHotkey" not in gen_src,
          "gen_mod still points at an external project's tools dir")

    print()
    print("== the Lua compiler runs in a child process ==")
    lj = (ROOT / "tools" / "ljcompile.py").read_text(encoding="utf-8")
    check("a child-process path exists", "_compile_in_child" in lj,
          "no child compile path")
    check("the child is re-entrant via a flag",
          "COMPILE_FLAG" in lj and "--compile-stdin" in lj,
          "no compiler flag")
    check("the desktop shell handles that flag before importing webview",
          "COMPILE_FLAG" in (ROOT / "gui" / "desktop.py").read_text(encoding="utf-8"),
          "desktop.py does not handle compiler mode")

    # Prove the child path is actually taken, not silently falling back to
    # in-process compilation (which would still work here and fail in the exe).
    print()
    print("== the child path is really used, not a fallback ==")
    probe = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "import ljcompile as L\n"
        "class Boom:\n"
        "    def __init__(self, *a, **k):\n"
        "        raise AssertionError('fell back to in-process LuaJIT')\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *a): pass\n"
        "L.LuaJIT = Boom\n"
        "bc = L.compile_source('return 1', chunkname='=t')\n"
        "print('BYTES', len(bc))\n" % (ROOT / "tools")
    )
    r = subprocess.run([sys.executable, "-c", probe],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    ok = "BYTES" in r.stdout
    check("compiling does not use in-process LuaJIT",
          ok, (r.stdout + r.stderr).strip()[:160])

    print()
    print("== the ZIP is written somewhere that survives ==")
    check("output_dir() exists and branches on frozen",
          "def output_dir" in app_src and "sys.executable" in app_src,
          "no frozen-aware output dir")
    check("generate uses it instead of the bundle root",
          "out_dir = output_dir()" in app_src,
          "generate still writes into the bundle root")

    print()
    print(f"test_frozen_build: {'PASS' if not base_mod.failures else 'FAIL'} "
          f"({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
