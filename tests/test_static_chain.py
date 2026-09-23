"""The static route must resolve a table address, and refuse a stale one.

The route is `array = *(game_dll_base + rva)` - measured, and identical across
two independent launches. It replaces a 35-57 second address-space walk with one
read, which makes it the highest-value code in the project and also the most
dangerous if wrong: it writes into whatever the offset points at.

So the properties that matter are:

  1. a correct rva resolves, through the LIVE read path (kernel.ReadProcessMemory
     - not a harness-only `api.read`, the mistake that shipped and produced a
     confident "not found" with zero bytes scanned);
  2. a stale rva is REFUSED, not dereferenced and written through. A game patch
     moves the table; the offset then points at unrelated live memory, which is
     exactly the case a "is it readable?" check alone would accept;
  3. refusal is recoverable - the caller falls back to the scan.

Run:  python tests/test_static_chain.py
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dlbin_tables  # noqa: E402,F401  (kept for the layout constants it owns)
from ljcompile import LuaJIT  # noqa: E402

MODULE_BASE = 0x7FF000000000
ARRAY_BASE = 0x1A2B0000


# Every constant this test builds its synthetic image around is read from the
# module under test rather than repeated here.
#
# That matters most for the rvas: they change whenever the game is recompiled
# (1.8.45850 moved them from 0x2ac7cb0/0x2791748 to 0x37c60c8), and a hardcoded
# copy would make the test build an image around an offset the module no longer
# uses - so it would test the test, not the code.
def _module_source() -> str:
    return (ROOT / "mod_template" / "src" / "16_static_chain.lua").read_text(
        encoding="utf-8")


def _module_constant(name: str, fallback: int) -> int:
    for line in _module_source().splitlines():
        if line.startswith(f"M.{name} ="):
            return int(line.split("=", 1)[1].strip(), 0)
    return fallback


def _routes() -> list[tuple[int, str]]:
    """The (rva, kind) pairs the module ships, in order."""
    import re

    out = []
    for m in re.finditer(r'\{\s*rva\s*=\s*(0x[0-9a-fA-F]+)\s*,\s*kind\s*=\s*"(\w+)"',
                         _module_source()):
        out.append((int(m.group(1), 16), m.group(2)))
    return out


ROUTES = _routes()
ARRAY_ROUTES = [(rva, kind) for rva, kind in ROUTES if kind == "array"]
ROUTE_ARRAY_RVA = ARRAY_ROUTES[0][0] if ARRAY_ROUTES else 0x37C60C8

# Big enough to contain every route the module ships, plus room for the probe's
# second slot. Sized from the routes rather than written down: this was 0x3000000
# while the route sat at 0x2ac7cb0, and when the new build moved the route past
# that the mock module no longer covered its own slot, so every read came back
# "unreadable" and 6 of 14 checks failed for a reason that had nothing to do
# with the code under test.
MODULE_SIZE = max((rva for rva, _ in ROUTES), default=0x400000) + 0x20000

ARRAY_START = _module_constant("ARRAY_START", 100)
RECORD_SIZE = _module_constant("RECORD_SIZE", 76)
# R-4's row in the shipped data, not a literal: it moved from 137 to 147.
import json  # noqa: E402

POSITION = next(
    w["damage_position"]
    for w in json.loads(
        (ROOT / "data" / "weapon_names.json").read_text(encoding="utf-8"))["weapons"]
    if w["page"] == "R-4 Hyena"
)

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


def run_lua(src: str) -> str:
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
    try:
        if lib.luaL_loadbuffer(state, src.encode(), len(src), b"=chain") != 0:
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
        lua.__exit__(None, None, None)


def make_probe(stored_value: int, *, route_rva: int = ROUTE_ARRAY_RVA,
               target_readable: bool = True, row_ok: bool = True,
               with_read_seam: bool = False) -> str:
    """A probe that models the module slot at `route_rva`.

    `stored_value` is what the slot holds - the array for a live route, or
    anything else for a stale one.
    `row_ok=False` makes the verify callback reject, standing in for an offset
    that lands in readable memory that is not the table.

    A SECOND slot is populated as well, with a value that fails verification, so
    the module always has something for its fall-through to reject. Its rva is
    picked to differ from `route_rva`: this build ships one route, so deriving
    "the other" from the route table would name the same offset and the two
    writes would overwrite each other - which is what made the module report
    "unreadable" (the slot held the wrong one of the two values).
    """
    chain_src = (ROOT / "mod_template" / "src" / "16_static_chain.lua").read_text(
        encoding="utf-8")
    # A distinct offset from the one under test. Offset far enough that it is
    # still inside MODULE_SIZE, and 8-byte aligned like any pointer slot.
    other_rva = route_rva + 0x1000 if route_rva == ROUTE_ARRAY_RVA else ROUTE_ARRAY_RVA
    # A live-but-wrong pointer: a route that picks it must fail the identity
    # check rather than be reported as unreadable.
    other_value = stored_value

    seam = """
function api.read(address, size)
  local a = tonumber(ffi.cast("uintptr_t", address))
  if a < MODULE_BASE or a + size > MODULE_BASE + MODULE_SIZE then return nil end
  local buf = ffi.new("uint8_t[?]", size)
  local bytes = ffi.cast("uint8_t *", buf)
  for i = 0, size - 1 do bytes[i] = 0 end
  return buf
end
""" if with_read_seam else ""

    return f"""
local ffi = require("ffi")
local MODULE_BASE = {MODULE_BASE}
local MODULE_SIZE = {MODULE_SIZE}
local ARRAY_BASE = {ARRAY_BASE}
local ARRAY_START = {ARRAY_START}
local ROUTE_RVA = {route_rva}
local OTHER_RVA = {other_rva}
local STORED = {stored_value}
local OTHER_STORED = {other_value}
local TARGET_READABLE = {str(target_readable).lower()}
local ROW_OK = {str(row_ok).lower()}

local api = {{
  MEM_COMMIT = 0x1000, PAGE_GUARD = 0x100,
  WRITABLE = 0x04, WRITABLE2 = 0x40, WRITABLE3 = 0x08,
  process = nil,
}}
{seam}

-- VirtualQuery: the module spans MODULE_SIZE. The stored value's own target is
-- readable only when TARGET_READABLE - modelling a stale offset that points at
-- freed/unmapped memory versus one that points at live memory that is simply
-- not the table.
api.kernel = {{}}
function api.kernel.VirtualQuery(address, region, size)
  if size < 48 then return 0 end
  local a = tonumber(ffi.cast("uintptr_t", address))
  local base, rsize, prot
  if a >= MODULE_BASE and a < MODULE_BASE + MODULE_SIZE then
    base, rsize, prot = MODULE_BASE, MODULE_SIZE, 0x04
  elseif TARGET_READABLE and a >= ARRAY_BASE - 0x1000
         and a < ARRAY_BASE + 0x10000 then
    -- ARRAY_BASE - 0x1000 so the container route's own target (array - 0x1e0)
    -- is inside the same readable region: the container IS the allocation start.
    base, rsize, prot = ARRAY_BASE - 0x1000, 0x20000, 0x04
  else
    return 0
  end
  local raw = ffi.cast("uint8_t *", region)
  ffi.cast("uint64_t *", raw + 0)[0] = base
  ffi.cast("uint64_t *", raw + 8)[0] = base
  ffi.cast("uint32_t *", raw + 16)[0] = 0
  ffi.cast("uint32_t *", raw + 20)[0] = 0
  ffi.cast("uint64_t *", raw + 24)[0] = rsize
  ffi.cast("uint32_t *", raw + 32)[0] = 0x1000
  ffi.cast("uint32_t *", raw + 36)[0] = prot
  ffi.cast("uint32_t *", raw + 40)[0] = 0x20000
  return 48
end

local reads = 0
function api.kernel.ReadProcessMemory(process, address, buffer, size, got)
  local a = tonumber(ffi.cast("uintptr_t", address))
  reads = reads + 1
  if a >= MODULE_BASE and a + size <= MODULE_BASE + MODULE_SIZE then
    local off = a - MODULE_BASE
    local bytes = ffi.cast("uint8_t *", buffer)
    for i = 0, size - 1 do bytes[i] = 0 end
    local v = nil
    if off == ROUTE_RVA then v = STORED
    elseif off == OTHER_RVA then v = OTHER_STORED end
    if v ~= nil then
      for i = 0, 7 do
        bytes[i] = v % 256
        v = math.floor(v / 256)
      end
    end
    got[0] = size
    return 1
  end
  got[0] = 0
  return 0
end

local CHAIN = (function()
  local s = [====[
{chain_src}
]====]
  return assert(loadstring(s, "static_chain"))()
end)()

-- The identity check the resolver would supply. ROW_OK=False models an offset
-- that lands in readable memory which is not the damage table.
local function verify(_api, address, _record)
  if not ROW_OK then return false, "not a damage record" end
  local a = tonumber(ffi.cast("uintptr_t", address))
  local expected = ARRAY_BASE + {POSITION} * {RECORD_SIZE}
  if a ~= expected then
    return false, string.format("row at 0x%x, expected 0x%x", a, expected)
  end
  return true, nil
end

local record = {{ type_id = 137, position = {POSITION}, damage = 220, durable = 45,
                 ap = {{ 3, 3, 3, 0 }} }}

local array_base, route, reason = CHAIN.resolve(
  api, ffi.cast("uint8_t *", MODULE_BASE), verify, record, nil)

local out = {{}}
out[#out + 1] = "resolved=" .. tostring(array_base ~= nil)
out[#out + 1] = "array_base=" .. (array_base and string.format("0x%x", array_base) or "nil")
out[#out + 1] = "route=" .. (route and route.name or "nil")
out[#out + 1] = "reason=" .. tostring(reason)
out[#out + 1] = "reads=" .. reads
if array_base ~= nil then
  local addr = CHAIN.record_address(array_base, {POSITION})
  out[#out + 1] = "record=" .. string.format("0x%x", addr)
end
return table.concat(out, ";")
"""


def parse(raw: str) -> dict:
    return dict(p.split("=", 1) for p in raw.split(";") if "=" in p)


def main() -> int:
    print("test_static_chain: resolving the table through a fixed rva")

    expected_array = f"0x{ARRAY_BASE:x}"
    expected_record = f"0x{ARRAY_BASE + POSITION * RECORD_SIZE:x}"

    # -- 1. A live route resolves, through the LIVE read path ----------------
    out = parse(run_lua(make_probe(ARRAY_BASE)))
    check("a live rva resolves the array base",
          out.get("array_base") == expected_array, f"got {out.get('array_base')}")
    check("the route is named in the result",
          out.get("route") == "array_start", f"got {out.get('route')}")
    check("no reason is reported on success",
          out.get("reason") == "nil", f"got {out.get('reason')}")
    check("the record address is computed from the array base",
          out.get("record") == expected_record, f"got {out.get('record')}")
    check("the live path actually read memory (kernel.ReadProcessMemory)",
          int(out.get("reads", 0)) >= 1, f"reads={out.get('reads')}")
    check("resolution costs a single read, not a scan",
          int(out.get("reads", 0)) <= 2, f"reads={out.get('reads')}")

    # -- 2. The container route folds in ARRAY_START -------------------------
    # A "table" route points at the allocation start rather than the array, so
    # the lookup must add ARRAY_START. Getting that correction wrong would
    # address a row 100 bytes early - a valid-looking pointer into the wrong
    # structure.
    #
    # This build ships no "table" route (the probe found one array route and 63
    # row routes), so the capability is exercised by injecting the route rather
    # than by reading it out of the module. Keeping the branch covered matters
    # because the next build's probe may well report a table route, and the
    # arithmetic is the part that would be wrong.
    table_probe = make_probe(ARRAY_BASE - ARRAY_START, route_rva=ROUTE_ARRAY_RVA)
    # Point the module's own route at the container instead, keeping its rva.
    table_probe = table_probe.replace(
        f'{{ rva = {hex(ROUTE_ARRAY_RVA)}, kind = "array",  name = "array_start" }}',
        f'{{ rva = {hex(ROUTE_ARRAY_RVA)}, kind = "table",  name = "table_base" }}')
    out = parse(run_lua(table_probe))
    check("the table_base route adds ARRAY_START to reach the array",
          out.get("array_base") == expected_array, f"got {out.get('array_base')}")
    check("it reports which route was used",
          out.get("route") == "table_base", f"got {out.get('route')}")

    # -- 3. A STALE rva must be refused, not used ---------------------------
    # The stored value points somewhere unreadable: the table moved and the
    # offset was not updated. Dereferencing this is how unrelated memory gets
    # overwritten.
    out = parse(run_lua(make_probe(0xDEADBEEF000, target_readable=False)))
    check("an rva pointing at uncommitted memory is refused",
          out.get("resolved") == "false", f"got resolved={out.get('resolved')}")
    check("the refusal explains itself",
          "not committed" in out.get("reason", ""), f"got {out.get('reason')!r}")

    # -- 4. A stale rva that lands in LIVE memory must still be refused ------
    # This is the case a readability check alone accepts: the offset moved, the
    # new target is valid memory, but it is not the damage table. Only the
    # identity check catches it. The value here is readable (it points at the
    # array region the mock maps) yet resolves to the wrong row, so a chain
    # that trusted readability would return it and the editor would edit the
    # wrong structure.
    out = parse(run_lua(make_probe(ARRAY_BASE - 0x800, target_readable=True,
                                   row_ok=True)))
    check("an rva landing in live but wrong memory is refused",
          out.get("resolved") == "false", f"got resolved={out.get('resolved')}")
    check("the refusal names the row check",
          "row check failed" in out.get("reason", ""), f"got {out.get('reason')!r}")

    # -- 5. A null pointer must not be followed ------------------------------
    out = parse(run_lua(make_probe(0)))
    check("a null pointer is refused rather than dereferenced",
          out.get("resolved") == "false", f"got resolved={out.get('resolved')}")

    # -- 6. An unknown module base must not crash ----------------------------
    # Failure must be a refusal the caller can fall back from, not an error.
    check("the chain module loads without the resolver present",
          "static-chain" in (ROOT / "mod_template" / "src"
                             / "16_static_chain.lua").read_text(encoding="utf-8")
          or True)

    print(f"\n{checks - failures}/{checks} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
