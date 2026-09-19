"""Reproduce the two P0 defects found by adversarial review, before fixing them.

P0-1: `WriteProcessMemory` is never declared in any `ffi.cdef`. The resolver
declares ReadProcessMemory, GetModuleHandleA, GetCurrentProcess, VirtualQuery
and ShMemoryRegion - not WriteProcessMemory. LuaJIT resolves ffi.load'd symbols
at declaration time, so the call raises "missing declaration for symbol".

P0-2: `gen_mod.py` indexes `damages[weapon["damage_index"]]`, but `parse_damages`
is keyed by array POSITION while `weapon_names.json` stores a `+0` value
(type_id). They differ on 519 of 634 rows.

Both are invisible to the existing suite: every test injects `api.write` (so the
kernel branch is dead code), and every test target - R-4 at 137, Constitution at
132 - is one of the rows where type_id happens to equal position.

Run:  python tests/test_review_findings.py
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import build_map  # noqa: E402

failures = 0
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures, checks
    checks += 1
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {label}" + ("" if cond else f"  {detail}"))
    if not cond:
        failures += 1


def real_kernel_call_works() -> tuple[bool, str]:
    """Try the un-injected path: does `kernel.WriteProcessMemory` resolve?"""
    from ljcompile import LuaJIT
    import ctypes

    lua = LuaJIT()
    lua.__enter__()
    lib, state = lua.lib, lua.state
    lib.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                    ctypes.c_size_t, ctypes.c_char_p]
    lib.luaL_loadbuffer.restype = ctypes.c_int
    lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int]
    lib.lua_pcall.restype = ctypes.c_int
    lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                  ctypes.POINTER(ctypes.c_size_t)]
    lib.lua_tolstring.restype = ctypes.c_char_p
    lib.lua_settop.argtypes = [ctypes.c_void_p, ctypes.c_int]

    resolver = (ROOT / "mod_template" / "src" / "10_resolver.lua").read_text(
        encoding="utf-8")
    writer = (ROOT / "mod_template" / "src" / "30_write.lua").read_text(
        encoding="utf-8")

    # Ask the resolver's own bind() - the thing 30_write actually uses - rather
    # than declaring the symbol here. A local cdef in the test would pass even
    # when the shipped file forgot to declare it, which is the whole defect.
    probe = f"""
local ffi = require("ffi")
local R = (function()
  local s = [====[
{resolver}
]====]
  return assert(loadstring(s, "resolver"))()
end)()
local api = R.bind()
if api == nil then return "resolved=false|err=bind returned nil" end
local ok, err = pcall(function() return api.kernel.WriteProcessMemory end)
return "resolved=" .. tostring(ok) .. "|err=" .. tostring(err)
"""
    status = lib.luaL_loadbuffer(state, probe.encode(), len(probe), b"=p")
    if status != 0:
        lib.lua_settop(state, -2)
        lua.__exit__(None, None, None)
        return False, "probe failed to load"
    if lib.lua_pcall(state, 0, 1, 0) != 0:
        size = ctypes.c_size_t()
        raw = lib.lua_tolstring(state, -1, ctypes.byref(size))
        msg = raw.decode() if raw else "?"
        lua.__exit__(None, None, None)
        return False, msg
    size = ctypes.c_size_t()
    raw = lib.lua_tolstring(state, -1, ctypes.byref(size))
    out = raw.decode() if raw else ""
    lua.__exit__(None, None, None)
    return "resolved=true" in out, out


def main() -> int:
    print("== P0-1: is WriteProcessMemory callable through the real kernel? ==")
    ok, detail = real_kernel_call_works()
    print(f"     {detail[:160]}")
    # The fix will make this True. Until then this check documents the defect.
    check("the real WriteProcessMemory path resolves", ok,
          "(expected to fail until the cdef is added)")

    print()
    print("== P0-2: are damage_index (type_id) and damage_position both correct? ==")
    blob = (ROOT / "data" / "raw" / "generated_damage_settings.dl_bin").read_bytes()
    start = __import__("parse_dlbin").anchor_start(blob)
    damages = build_map.parse_damages(blob)
    names = json.loads((ROOT / "data" / "weapon_names.json").read_text(encoding="utf-8"))

    # The map must expose BOTH numbers, and each must be right for its job:
    #   damage_position - indexes the row (this is what the writer addresses)
    #   damage_index    - the row's type_id (this is what identity compares)
    # The bug being regression-tested was that only one number existed and it was
    # used for both, which silently retargeted 50 weapons.
    mismatches = []
    out_of_range = []
    for w in names["weapons"]:
        pos = w.get("damage_position")
        tid = w.get("damage_index")
        if pos is None:
            out_of_range.append((w["page"], "no damage_position"))
            continue
        rec = damages.get(pos)
        if rec is None:
            out_of_range.append((w["page"], f"position {pos} not in table"))
            continue
        if rec.type_id != tid:
            mismatches.append((w["page"], pos, tid, rec.type_id))
        # The weapon's cached values must equal the row at that position.
        if (rec.damage, rec.durable_damage) != (w["damage"], w["durable"]):
            mismatches.append((w["page"], pos, (w["damage"], w["durable"]),
                               (rec.damage, rec.durable_damage)))

    print(f"     weapons: {len(names['weapons'])}")
    print(f"     entries with a missing/invalid position: {len(out_of_range)}")
    if out_of_range[:3]:
        print(f"       e.g. {out_of_range[:3]}")
    print(f"     entries where the two numbers disagree with the table: {len(mismatches)}")
    if mismatches[:3]:
        for row in mismatches[:3]:
            print(f"       {row}")

    check("damage_position indexes the row that holds the displayed values",
          len(mismatches) == 0,
          f"{len(mismatches)} entries inconsistent")
    check("damage_position is always valid", len(out_of_range) == 0,
          f"e.g. {out_of_range[:3]}")

    print()
    print("== the reviewer's specific counter-examples ==")
    # These are the weapons the reviewer used to prove the conflation, because
    # their type_id is not a valid position at all.
    for page in ("TD-220 Bastion MK XVI", "AC-8 Autocannon", "SG-225 Breaker"):
        w = next((x for x in names["weapons"] if x["page"] == page), None)
        if w is None:
            print(f"     {page}: not in the map (not matched)")
            continue
        pos, tid = w["damage_position"], w["damage_index"]
        rec = damages[pos]
        ok = (rec.damage, rec.durable_damage) == (w["damage"], w["durable"])
        print(f"     {page}: position={pos} type_id={tid} "
              f"shown={w['damage']}/{w['durable']} row={rec.damage}/{rec.durable_damage} "
              f"{'OK' if ok else 'MISMATCH'}")

    print()
    print(f"test_review_findings: {'PASS' if not failures else 'FAIL'} ({checks} checks)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
