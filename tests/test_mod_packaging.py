"""Verify the mod packaging chain end to end.

What this proves, in the order the pieces depend on each other:

  1. a source string compiles to real LuaJIT bytecode via the game's own
     `bin/lua51.dll` (no external LuaJIT toolchain needed);
  2. the bytecode gets the 8-byte resource header the engine expects;
  3. an archive holding one Lua resource round-trips byte-for-byte;
  4. the resource name hashes to the ID a loader slot is registered under.

Step 4 is the one worth stating plainly: a mod is not "a file in a folder", it
is a resource whose *name* hashes to a 64-bit Stingray ID. Get the name wrong
and the archive loads happily while nothing registers. Verified against the
installed Codex mod, whose slot `mods/codex/constitution_bolt_amr` hashes to
exactly the resource ID in its shipped archive.

Run:  python tests/test_mod_packaging.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OLD / "tools"))

from hd2archive import Archive, Resource, resource_hash, wrap_lua  # noqa: E402
from ljcompile import compile_source  # noqa: E402

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


CODEX_NAME = "mods/codex/constitution_bolt_amr"
CODEX_ARCHIVE = Path(
    r"C:\Users\miaomiao\AppData\Local\hd2arsenal\mods"
    r"\R2124-Constitution-Bolt-AMR-Bridge-v4_AR130189\data\9ba626afa44a3aa3.patch_0"
)


def main() -> int:
    print("== 1. source compiles with the game's own LuaJIT ==")
    src = (
        'local ffi = require("ffi")\n'
        'ffi.cdef[[ void *GetModuleHandleA(const char *name); ]]\n'
        'return type(ffi.C.GetModuleHandleA(nil))\n'
    )
    bc = compile_source(src, chunkname="=packaging_test")
    check("bytecode produced", len(bc) > 0, f"{len(bc)} bytes")
    check("bytecode has LuaJIT magic", bc[:4] == b"\x1bLJ\x02", repr(bc[:4]))

    print()
    print("== 2. the 8-byte resource header wraps it ==")
    wrapped = wrap_lua(bc)
    check("wrapped is 8 bytes longer", len(wrapped) == len(bc) + 8)
    size, ver = struct.unpack_from("<II", wrapped, 0)
    check("declared size matches payload", size == len(bc), f"{size} vs {len(bc)}")
    check("resource version is 2", ver == 2, f"got {ver}")

    print()
    print("== 3. an archive round-trips byte-for-byte ==")
    res = Resource(type=0xA14E8DFA2CD117E2, name_hash=resource_hash(CODEX_NAME),
                   data=wrapped)
    arch = Archive(resources=[res])
    blob = arch.build()
    back = Archive.parse(blob)
    check("archive reparses", len(back.resources) == 1)
    check("payload survives the round trip",
          back.resources[0].data == wrapped,
          f"{len(back.resources[0].data)} vs {len(wrapped)}")

    print()
    print("== 4. the slot NAME hashes to the shipped resource ID ==")
    # This is the join that is easy to get silently wrong.
    claim = resource_hash(CODEX_NAME)
    check(f"{CODEX_NAME} hashes to 0x{claim:016X}", claim == 0x29DFF1A59E274D97,
          f"got 0x{claim:016X}")
    if CODEX_ARCHIVE.exists():
        real = Archive.from_file(str(CODEX_ARCHIVE))
        ids = [int(n, 16) for n in real.names()]
        check("the installed mod's resource carries that ID", claim in ids,
              f"ids={[hex(i) for i in ids]}")
    else:
        print("  [SKIP] Codex archive not found; name hash checked on its own")

    print()
    print("== 5. a wrong name cannot collide with the slot ==")
    wrong = resource_hash("mods/codex/constitution_bolt_amr.wrapper")
    check("a near-miss name yields a different ID", wrong != claim,
          f"0x{wrong:016X}")

    print()
    print(f"test_mod_packaging: {'PASS' if not failures else 'FAIL'} ({checks} checks)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
