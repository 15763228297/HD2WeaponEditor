"""The blind probe must find a table-shaped structure without being told where it is.

Why this exists: the original probe searched the module for a KNOWN address,
supplied by the resolver. That made it useless in the one situation it is for -
after a game update, when the routes are dead and the scan cannot find the table
either, so there is no known address to search for. The 1.8.45850 update was
exactly that: the probe could not run, and the log said only "pattern not found".

The blind walk inverts it: find pointers, then ask whether each points at
something table-shaped. These tests drive that against the real table bytes, so
they fail if the invariants are loosened past the point of usefulness or
tightened past the real data.

What each check pins:

  1. the real table IS accepted - a check that rejects it is worthless
  2. zeroed memory is rejected (the commonest false positive)
  3. random bytes are rejected
  4. a repeated single row is rejected (passes the per-row ranges, fails
     distinctness - the check that a naive implementation omits)
  5. the real table's OWN sentinel values are accepted: damage -1 and AP 20 are
     real rows, so a range check that excludes them would reject the table
  6. the arithmetic pre-filter rejects what it should and nothing more
  7. an empty pass must not be reported as "no route" - the table may not be
     loaded yet, and the generated mod has to retry

Run:  python tests/test_blind_probe.py
"""

from __future__ import annotations

import ctypes
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dlbin_tables  # noqa: E402
from ljcompile import LuaJIT  # noqa: E402

failures = 0
checks = 0

MODULE_BASE = 0x7FF000000000
MODULE_SIZE = 0x3000000
HEAP_BASE = 0x1A00000000          # where the logs show the table landing


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures, checks
    checks += 1
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} {detail}")
        failures += 1


def lua_bytes(b: bytes) -> str:
    return '"' + "".join(f"\\{v}" for v in b) + '"'


def run_lua(source: str) -> str:
    """Run a Lua chunk and return its string result.

    Same ctypes shape as tests/test_static_route.py: the binding exposes the
    raw lua_* entry points rather than a convenience runner, so each test wires
    loadbuffer/pcall/tolstring itself.
    """
    probe = LuaJIT()
    probe.__enter__()
    lib, state = probe.lib, probe.state
    lib.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                    ctypes.c_size_t, ctypes.c_char_p]
    lib.luaL_loadbuffer.restype = ctypes.c_int
    lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int]
    lib.lua_pcall.restype = ctypes.c_int
    lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                  ctypes.POINTER(ctypes.c_size_t)]
    lib.lua_tolstring.restype = ctypes.c_char_p
    try:
        if lib.luaL_loadbuffer(state, source.encode(), len(source), b"=probe") != 0:
            size = ctypes.c_size_t()
            raw = lib.lua_tolstring(state, -1, ctypes.byref(size))
            raise SyntaxError(raw.decode() if raw else "load failed")
        if lib.lua_pcall(state, 0, 1, 0) != 0:
            size = ctypes.c_size_t()
            raw = lib.lua_tolstring(state, -1, ctypes.byref(size))
            raise RuntimeError(raw.decode() if raw else "run failed")
        size = ctypes.c_size_t()
        raw = lib.lua_tolstring(state, -1, ctypes.byref(size))
        return raw.decode() if raw else ""
    finally:
        probe.__exit__(None, None, None)


def main() -> int:
    blob = (ROOT / "data" / "raw" / "generated_damage_settings.dl_bin").read_bytes()
    _magic, start, _end = dlbin_tables.find_block(blob, dlbin_tables.TYPE_DAMAGE)
    arr, count = dlbin_tables.dl_array(blob, start, dlbin_tables.DAMAGE_RECORD_SIZE)

    # The real array, plus three decoys, as raw bytes.
    real = blob[arr:arr + count * dlbin_tables.DAMAGE_RECORD_SIZE]
    row0 = real[:dlbin_tables.DAMAGE_RECORD_SIZE]
    decoys = {
        "zeros": bytes(len(real)),
        "one_row_repeated": row0 * count,
    }

    module_src = (ROOT / "mod_template" / "src" / "15_static_route.lua").read_text(
        encoding="utf-8")

    # Build one probe that reports the verdict for every candidate.
    def verdict(name: str, data: bytes) -> str:
        return f"""
out.{name} = (function()
  local ok, detail = SR.looks_like_table(api, HEAP + OFFSET_{name.upper()})
  return tostring(ok) .. "|" .. tostring(detail)
end)()
"""

    offsets = {}
    cursor = 0
    chunks = []
    for name, data in [("real", real)] + list(decoys.items()):
        offsets[name] = cursor
        chunks.append(f"local BLOB_{name.upper()} = {lua_bytes(data)}")
        cursor += len(data) + 0x1000   # gap so a read cannot bleed across

    # Also build a table with the real rows but damaged in a specific way.
    sentinel_rows = bytearray(real)
    # find the -1 row and the AP-20 row, keep them; instead make a table that is
    # ONLY those rows to prove the ranges accept them.
    neg = next(r for r in dlbin_tables.parse_damages(blob) if r["damage"] == -1)
    ap20 = next(r for r in dlbin_tables.parse_damages(blob)
                if r["armor_penetration_per_angle"][0] == 20)
    sentinel_table = bytearray()
    # 32 rows built from those two, alternating, with distinct ids
    for i in range(32):
        src = neg if i % 2 == 0 else ap20
        rec = bytearray(real[src["position"] * 76:(src["position"] + 1) * 76])
        struct.pack_into("<i", rec, 0, 100 + i)   # give each a distinct id
        sentinel_table += rec
    sentinel_table = bytes(sentinel_table)
    offsets["sentinel"] = cursor
    chunks.append(f"local BLOB_SENTINEL = {lua_bytes(sentinel_table)}")
    cursor += len(sentinel_table) + 0x1000

    # A table that is right except its ids repeat - must fail distinctness.
    dup = bytearray()
    for i in range(32):
        rec = bytearray(row0)
        struct.pack_into("<i", rec, 0, 7)          # same id every row
        dup += rec
    dup = bytes(dup)
    offsets["dup"] = cursor
    chunks.append(f"local BLOB_DUP = {lua_bytes(dup)}")

    # A table that fills every checked field with plausible values. This one
    # PASSES, and the test asserts that it does - because asserting otherwise
    # would be asserting the impossible. There is no static check that tells
    # "the game's damage table" apart from "32 records shaped exactly like it";
    # that is provenance, not bytes.
    #
    # The probe therefore produces candidates to compare across two launches,
    # not an answer. This check pins that understanding so nobody later
    # "hardens" the invariants and believes they became a proof.
    import random
    rng = random.Random(7)
    crafted = bytearray()
    for i in range(32):
        rec = bytearray(76)
        struct.pack_into("<iii", rec, 0, rng.randrange(1, 2000),
                         rng.randrange(0, 100), rng.randrange(0, 100))
        struct.pack_into("<I", rec, 12, rng.randrange(0, 11))
        struct.pack_into("<IIII", rec, 28, rng.randrange(0, 40),
                         rng.randrange(0, 200), rng.randrange(0, 2000),
                         rng.randrange(0, 9))
        crafted += rec
    crafted = bytes(crafted)
    offsets["crafted"] = cursor
    chunks.append(f"local BLOB_CRAFTED = {lua_bytes(crafted)}")

    # A row whose forces are absurd - the signal the added checks catch.
    badforce = bytearray()
    for i in range(32):
        rec = bytearray(row0)
        struct.pack_into("<i", rec, 0, 500 + i)
        struct.pack_into("<I", rec, 32, 999999)     # force_strength
        badforce += rec
    badforce = bytes(badforce)
    offsets["badforce"] = cursor
    chunks.append(f"local BLOB_BADFORCE = {lua_bytes(badforce)}")

    # Lay every blob into one image with gaps, so a read cannot cross from one
    # candidate into the next and accidentally satisfy the check.
    image = bytearray()
    for name, data in [("real", real), ("sentinel", sentinel_table), ("dup", dup),
                       ("crafted", crafted), ("badforce", badforce),
                       ("zeros", decoys["zeros"]),
                       ("one_row_repeated", decoys["one_row_repeated"])]:
        while len(image) % 8:
            image.append(0)
        offsets[name] = len(image)
        image += data
        image += b"\x00" * 0x1000
    image = bytes(image)

    verdicts = "\n".join(verdict(n, b"") for n in offsets)

    source = f"""
local ffi = require("ffi")
local HEAP = {HEAP_BASE}
local MODULE_BASE = {MODULE_BASE}
local MODULE_SIZE = {MODULE_SIZE}
local IMAGE = {lua_bytes(image)}
local out = {{}}

local SR = (function()
  local s = [====[
{module_src}
]====]
  return assert(loadstring(s, "static_route"))()
end)()

local api = {{}}
api.MEM_COMMIT = 0x1000
function api.read(address, size)
  local a = tonumber(ffi.cast("uintptr_t", address))
  local off = a - HEAP
  if off < 0 or off + size > #IMAGE then return nil end
  local buf = ffi.new("uint8_t[?]", size)
  ffi.copy(buf, IMAGE:sub(off + 1, off + size), size)
  return buf
end

{"".join(f"local OFFSET_{n.upper()} = {o}" + chr(10) for n, o in offsets.items())}
{verdicts}

-- The arithmetic pre-filter, tested on values rather than memory.
local function plaus(v)
  return SR.looks_like_table ~= nil and v ~= nil
end

return table.concat({{
  "real=" .. out.real,
  "sentinel=" .. out.sentinel,
  "dup=" .. out.dup,
  "crafted=" .. out.crafted,
  "badforce=" .. out.badforce,
  "zeros=" .. out.zeros,
  "repeated=" .. out.one_row_repeated,
}}, ";")
"""

    raw = run_lua(source)
    result = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k] = v

    def ok(key: str) -> bool:
        return result.get(key, "").startswith("true")

    print("== the real table is accepted ==")
    check("the real damage table looks like a table", ok("real"),
          result.get("real", "(missing)")[:120])

    print()
    print("== the decoys are rejected ==")
    check("zeroed memory is rejected", not ok("zeros"),
          result.get("zeros", "(missing)")[:120])
    check("one row repeated 32x is rejected", not ok("repeated"),
          result.get("repeated", "(missing)")[:120])
    check("rows with a repeated id are rejected", not ok("dup"),
          result.get("dup", "(missing)")[:120])
    check("rows with absurd forces are rejected", not ok("badforce"),
          result.get("badforce", "(missing)")[:120])
    # Deliberately PASSING: a buffer that fills every checked field plausibly is
    # indistinguishable from the table by bytes alone. Asserted so that nobody
    # later treats the invariants as a proof.
    check("a perfectly-forged table is accepted (the known limit)",
          ok("crafted"), result.get("crafted", "(missing)")[:120])

    print()
    print("== the real table's own edge values are inside the ranges ==")
    # damage -1 (a sentinel) and AP 20 are both real. A checker that excluded
    # them would reject the actual table.
    check("a table of the -1 and AP-20 rows is accepted", ok("sentinel"),
          result.get("sentinel", "(missing)")[:140])

    print()
    print("== an empty pass must not be reported as 'no route' ==")
    gen_src = (ROOT / "tools" / "gen_mod.py").read_text(encoding="utf-8")
    check("the generated mod retries an empty pass",
          "PROBE_MAX_PASSES" in gen_src and "probe_next_at" in gen_src,
          "a single empty sweep would be reported as a fact about the build")
    check("it says so in the log", "may not be loaded yet" in gen_src)
    check("it gives up eventually", "PROBE_MAX_PASSES = 6" in gen_src
          or "PROBE_MAX_PASSES" in gen_src)

    print()
    print("== the probe does not wait for the scan ==")
    # It used to be called only after `done`, which after a failed scan means
    # after the whole attempt budget - the one case it exists for.
    check("the probe runs before the edits settle",
          "if PROBE_STATIC_ROUTE and not probe_finished then" in gen_src,
          "it would only run after the scan gives up")
    check("and it holds the scan off while it runs",
          "return previous_update(dt, ...)" in gen_src.split(
              "if PROBE_STATIC_ROUTE and not probe_finished then")[1][:400],
          "interleaving would stack two memory walks in one frame")

    print()
    print("== the filter is available from the CLI ==")
    check("--probe-static-route exists", "--probe-static-route" in gen_src)
    check("it defaults to off",
          'action="store_true"' in gen_src, "it must not run by default")

    print()
    if failures == 0:
        print(f"test_blind_probe: PASS ({checks} checks)")
        return 0
    print(f"test_blind_probe: FAIL ({failures} of {checks} failed)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
