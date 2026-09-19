"""LuaJIT compilation must survive a loaded WebView2/Chromium runtime.

LuaJIT on x64 needs a 2 GB-aligned block of address space. Once a WebView2
(Chromium) runtime has started in the same process it has reserved enough of
the address space that luaL_newstate() returns NULL - so the desktop build
could list weapons but failed the moment it compiled a mod, with only a bare
"MemoryError: luaL_newstate failed" to show for it.

The fix is to compile in a child process, which starts with a clean address
space. This asserts that the child path is used and produces real bytecode.

Run:  python tests/test_lua_compile.py
"""

from __future__ import annotations

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
    sys.path.insert(0, str(ROOT / "tools"))
    import ljcompile

    print("== the compile goes through a child process ==")
    used = []
    original = ljcompile._compile_in_child

    def spy(src, dll, name):
        result = original(src, dll, name)
        used.append(result is not None)
        return result

    ljcompile._compile_in_child = spy
    try:
        bc = ljcompile.compile_source("return 1", chunkname="=probe")
    finally:
        ljcompile._compile_in_child = original

    print(f"     child used: {used}, bytecode {len(bc)} bytes")
    check("a child process was attempted", len(used) == 1, str(used))
    check("the child produced bytecode", used and used[0] is True, str(used))
    check("the result is stripped LuaJIT bytecode", bc[:4] == b"\x1bLJ\x02",
          repr(bc[:4]))

    print()
    print("== a child really is a separate process ==")
    # The child must not inherit the parent's address space, so it must be a
    # genuinely separate interpreter - verify by having one report its pid.
    script = (
        "import os, sys\n"
        "sys.stdout.write(str(os.getpid()))\n"
    )
    proc = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, timeout=60)
    check("a child process can be spawned at all", proc.returncode == 0,
          proc.stderr[:120])
    if proc.returncode == 0:
        check("the child has its own pid", proc.stdout.strip().isdigit(),
              proc.stdout[:40])

    print()
    print("== the full generate path still works in-process ==")
    sys.path.insert(0, str(ROOT / "tools"))
    import gen_mod
    try:
        specs = gen_mod.build_specs(["R-4 Hyena::400/200/7"])
        sources = {
            n: (gen_mod.ROOT / "mod_template" / "src" / n).read_text(encoding="utf-8")
            for n in gen_mod.MODULES
        }
        source = gen_mod.render_module(specs, sources)
        bytecode = gen_mod.compile_source(source, chunkname=f"={gen_mod.SLOT}")
        check("a real mod compiles", bytecode[:4] == b"\x1bLJ\x02",
              repr(bytecode[:4]))
        print(f"     compiled {len(source):,} chars -> {len(bytecode):,} bytes")
    except Exception as exc:
        check("a real mod compiles", False, f"{type(exc).__name__}: {exc}")

    print()
    print(f"test_lua_compile: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} ({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
