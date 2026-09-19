"""Reproduce the in-game crash: region bases typed `void *`.

Every other test injects `api.writable_regions`, returning a hand-built table
whose `base` is already a `uint8_t *`. The real code builds that table from a
`VirtualQuery` struct field, which is typed `void *` - and LuaJIT refuses to add
a number to a void pointer:

    attempt to perform arithmetic on 'void *' and 'number'

That raised inside the game and killed the mod, while the whole suite stayed
green, because no test ran the real enumerator and the injected one used a
friendlier type than reality.

This test runs the SHIPPED `writable_regions` (not an injected stand-in) against
a fake `VirtualQuery`, so the region table is built exactly as in game, then
scans the result. The assertion that matters is "scanning does not raise".

Run:  python tests/test_real_regions.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402

IMAGE_BASE = 0x10000000


def main() -> int:
    check = base_mod.check
    meta = json.loads((ROOT / "data" / "sim_memory.json").read_text())
    image = (ROOT / "data" / "sim_memory.bin").read_bytes()
    resolver_src = (ROOT / "mod_template" / "src" / "10_resolver.lua").read_text(
        encoding="utf-8"
    )

    # The fake VirtualQuery is written in Lua, and - crucially - it stores the
    # region base with the SAME type the real struct field has. Declaring the
    # struct here and filling `region[0].base` reproduces the real typing.
    probe = f"""
local ffi = require("ffi")
local image = {base_mod.lua_bytes(image)}
local len = #image
local IMAGE_BASE = {IMAGE_BASE}
local out = {{}}

ffi.cdef[[
    typedef struct {{
        void *base; void *allocation_base; uint32_t allocation_protection;
        uint16_t partition; uint16_t reserved; size_t size;
        uint32_t state; uint32_t protection; uint32_t type;
    }} ShMemoryRegion;
]]

local guard = {{ GetModuleHandleA = function(n) return ffi.cast("void *", IMAGE_BASE) end,
                GetCurrentProcess = function() return ffi.cast("void *", 1) end }}

-- Three fake regions: one writable+private (the one we want), one read-only,
-- one writable but PAGE_GUARD. Only the first must survive the filter.
local READWRITE = IMAGE_BASE
local READONLY  = IMAGE_BASE + 0x20000
local GUARDED   = IMAGE_BASE + 0x40000
local REGION_SIZE = 0x10000

local queries = 0
function guard.VirtualQuery(address, region, size)
  queries = queries + 1
  local addr = tonumber(ffi.cast("uintptr_t", address))
  local r = region[0]
  -- Report the region CONTAINING addr. Comparing against fixed boundaries
  -- instead made the walk hand back the first region forever, because after
  -- stepping past it every remaining address still satisfied `< READONLY`.
  local function inside(b) return addr >= b and addr < b + REGION_SIZE end
  if inside(READWRITE) then
    -- `base` is a void * field, exactly as in game.
    r.base = ffi.cast("void *", READWRITE)
    r.size = REGION_SIZE; r.state = 0x1000; r.protection = 0x04; r.type = 0x20000
  elseif inside(READONLY) then
    r.base = ffi.cast("void *", READONLY)
    r.size = REGION_SIZE; r.state = 0x1000; r.protection = 0x02; r.type = 0x20000
  elseif inside(GUARDED) then
    r.base = ffi.cast("void *", GUARDED)
    -- 0x104 = PAGE_READWRITE | PAGE_GUARD
    r.size = REGION_SIZE; r.state = 0x1000; r.protection = 0x104; r.type = 0x20000
  else
    return 0
  end
  return ffi.sizeof(region)
end

function guard.ReadProcessMemory(process, address, buffer, size, got)
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  if off < 0 or off + size > len then got[0] = 0; return 0 end
  ffi.copy(buffer, image:sub(off + 1, off + size), size)
  got[0] = size
  return 1
end

local api = {{
  kernel = guard,
  process = ffi.cast("void *", 1),
  MEM_COMMIT = 0x1000, PAGE_GUARD = 0x100,
  WRITABLE = 0x04, WRITABLE2 = 0x40, WRITABLE3 = 0x08,
}}
rawset(_G, "__resolver_api", api)

local R = (function()
  local s = [====[
{resolver_src}
]====]
  return assert(loadstring(s, "resolver"))()
end)()

-- Run the SHIPPED enumerator.
local regions = R.writable_regions(api, IMAGE_BASE, R.MAX_ADDRESS)
out.count = #regions
if regions[1] then
  out.base_type = tostring(ffi.typeof(regions[1].base))
  out.base_value = tonumber(ffi.cast("uintptr_t", regions[1].base))
  out.size = regions[1].size
end
out.queries = queries

-- Now scan it. This is what raised in game.
local ok, err = pcall(function()
  local rec = {{ type_id = 137, position = 137, damage = 220, durable = 45,
                ap = {{3,3,3,0}} }}
  return R.find_record(api, IMAGE_BASE, rec,
    {{ before_id = 136, after_id = 138, next = {{ damage = 200, durable = 50 }} }})
end)
out.scan_ok = ok
out.scan_err = tostring(err)

return table.concat({{
  "count=" .. tostring(out.count),
  "base_type=" .. tostring(out.base_type),
  "base_value=" .. tostring(out.base_value),
  "size=" .. tostring(out.size),
  "scan_ok=" .. tostring(out.scan_ok),
  "scan_err=" .. out.scan_err,
}}, ";")
"""

    raw = base_mod.run_probe(probe)
    result: dict = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k] = v

    print("== the shipped enumerator, on a real-shaped VirtualQuery ==")
    check("exactly one region survives the filter (read-only and guard excluded)",
          result.get("count") == "1", f"got {result.get('count')}")
    # LuaJIT prints uint8_t* as "unsigned char *"; either spelling means the
    # type is an arithmetic-friendly pointer rather than void *.
    # tostring(ffi.typeof(x)) yields "ctype<unsigned char *>"; check for the
    # element type rather than the wrapper spelling.
    bt = result.get("base_type", "")
    check("its base is typed so arithmetic works",
          "unsigned char" in bt or "uint8_t" in bt,
          f"got {bt!r}")
    check("its base is the writable region",
          result.get("base_value") == str(IMAGE_BASE),
          f"got {result.get('base_value')}")
    check("its size is right", result.get("size") == "65536",
          f"got {result.get('size')}")

    print()
    print("== scanning what the real enumerator produced does not raise ==")
    check("find_record ran without error", result.get("scan_ok") == "true",
          f"err={result.get('scan_err')}")

    print()
    print(f"test_real_regions: {'PASS' if not base_mod.failures else 'FAIL'} "
          f"({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
