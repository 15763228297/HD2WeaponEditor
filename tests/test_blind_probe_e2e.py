"""Drive the blind probe against a simulated process, end to end.

The unit tests check `looks_like_table` on buffers. This checks the whole walk:
given a fake address space where the damage table sits somewhere, and a module
whose data holds a pointer to it, does `begin_blind` + `step_blind` find it and
report the right rva?

That is the property the probe is for, and the one a game launch would test -
except a game launch cannot be repeated cheaply, so it is worth proving here
first.

The layout mirrors the real one: module at ~0x7ff..., heap at ~0x1a..., table in
the heap, pointer in the module's writable data.
"""

from __future__ import annotations

import ctypes
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import dlbin_tables as D  # noqa: E402
from ljcompile import LuaJIT  # noqa: E402

MODULE_BASE = 0x7FF000000000
MODULE_SIZE = 0x400000          # 4 MB, enough for the walk
HEAP_BASE = 0x1A00000000
TABLE_RVA = 0x00123458          # where in the module the pointer lives
                                # (8-byte aligned - the walk only inspects
                                #  aligned slots, as a pointer must be)
TABLE_ADDR = HEAP_BASE + 0x1000  # where the table itself sits

blob = (ROOT / "data/raw/generated_damage_settings.dl_bin").read_bytes()
arr = D.dl_array(blob, D.find_block(blob, D.TYPE_DAMAGE)[1], 76)[0]
table_bytes = blob[arr:arr + 200 * 76]

# The simulated module: zeros, with the table pointer at TABLE_RVA.
module = bytearray(MODULE_SIZE)
struct.pack_into("<Q", module, TABLE_RVA, TABLE_ADDR)

# Decoys that must NOT be reported, in increasing order of subtlety:
#   1. a small integer            - rejected by the arithmetic pre-filter
#   2. a pointer into the module  - also rejected by the pre-filter
#   3. a pointer into the HEAP at memory that is NOT table-shaped - this one
#      passes the pre-filter, so only the identity check can reject it. Without
#      it the test would pass even if looks_like_table were deleted, which is
#      exactly what a mutation showed.
struct.pack_into("<Q", module, TABLE_RVA + 0x100, 12345)
struct.pack_into("<Q", module, TABLE_RVA + 0x200, MODULE_BASE + 0x800)
struct.pack_into("<Q", module, TABLE_RVA + 0x300, HEAP_BASE + 0x8000)

# The simulated heap: the table at TABLE_ADDR.
heap = bytearray(0x20000)
heap[0x1000:0x1000 + len(table_bytes)] = table_bytes
# A heap object at 0x8000 that is readable but not table-shaped: the decoy that
# the pre-filter cannot reject.
heap[0x8000:0x8100] = bytes(range(256))

module_src = (ROOT / "mod_template/src/15_static_route.lua").read_text(encoding="utf-8")


def lua_bytes(b: bytes) -> str:
    return '"' + "".join(f"\\{v:03d}" for v in b) + '"'


source = f"""
local ffi = require("ffi")
local MODULE_BASE = {MODULE_BASE}
local MODULE_SIZE = {MODULE_SIZE}
local HEAP_BASE = {HEAP_BASE}
local MODULE = {lua_bytes(bytes(module))}
local HEAP = {lua_bytes(bytes(heap))}
local out = {{}}

local SR = (function()
  local s = [====[
{module_src}
]====]
  return assert(loadstring(s, "static_route"))()
end)()

local api = {{}}
api.MEM_COMMIT = 0x1000

-- One readable region per area, so the walk has two to traverse.
function api.module_regions(_a, _mb)
  return {{
    {{ base = ffi.cast("uint8_t *", MODULE_BASE), size = MODULE_SIZE }},
  }}, 0, MODULE_SIZE
end
function api.region_span(r)
  return tonumber(ffi.cast("uintptr_t", r.base)), r.size
end

-- Reads serve from either the module or the heap.
local reads = 0
local function reader(address, size)
  reads = reads + 1
  local a = tonumber(ffi.cast("uintptr_t", address))
  local src
  if a >= MODULE_BASE and a + size <= MODULE_BASE + MODULE_SIZE then
    src = MODULE:sub(a - MODULE_BASE + 1, a - MODULE_BASE + size)
  elseif a >= HEAP_BASE and a + size <= HEAP_BASE + #HEAP then
    src = HEAP:sub(a - HEAP_BASE + 1, a - HEAP_BASE + size)
  else
    return nil
  end
  local buf = ffi.new("uint8_t[?]", size)
  ffi.copy(buf, src, size)
  return buf
end

-- read_range goes through kernel.ReadProcessMemory when api.read is absent;
-- provide it so the walk takes the live path, as it does in the game.
api.process = nil
api.kernel = {{}}
function api.kernel.ReadProcessMemory(process, address, buffer, size, got)
  local b = reader(address, size)
  if b == nil then got[0] = 0 return 0 end
  ffi.copy(buffer, b, size)
  got[0] = size
  return 1
end
function api.kernel.VirtualQuery(address, region, size)
  if size < 48 then return 0 end
  local a = tonumber(ffi.cast("uintptr_t", address))
  local base, rsize
  if a >= MODULE_BASE and a < MODULE_BASE + MODULE_SIZE then
    base, rsize = MODULE_BASE, MODULE_SIZE
  elseif a >= HEAP_BASE and a < HEAP_BASE + #HEAP then
    base, rsize = HEAP_BASE, #HEAP
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
  ffi.cast("uint32_t *", raw + 36)[0] = 0x04
  ffi.cast("uint32_t *", raw + 40)[0] = 0x20000
  return 48
end

local ok, state = pcall(SR.begin_blind, api, ffi.cast("uint8_t *", MODULE_BASE))
if not ok then
  return "start_error=" .. tostring(state)
end
out.total = state.total or 0

local guard = 0
while true do
  local sok, finished, msg = pcall(SR.step_blind, api, state)
  if not sok then return "step_error=" .. tostring(finished) end
  if finished then break end
  guard = guard + 1
  if guard > 2000 then return "did_not_finish" end
end

out.hits = #state.hits
local first = state.hits[1]
if first then
  out.rva = string.format("0x%x", first.rva)
  out.value = string.format("0x%x", first.value)
  out.detail = first.detail
end
out.reads = reads
out.scanned = state.scanned
out.tested = state.tested

local parts = {{
  "hits=" .. tostring(out.hits),
  "rva=" .. tostring(out.rva),
  "value=" .. tostring(out.value),
  "detail=" .. tostring(out.detail),
  "tested=" .. tostring(out.tested),
  "scanned=" .. tostring(out.scanned),
}}
return table.concat(parts, ";")
"""

probe = LuaJIT()
probe.__enter__()
lib, state = probe.lib, probe.state
lib.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                ctypes.c_size_t, ctypes.c_char_p]
lib.luaL_loadbuffer.restype = ctypes.c_int
lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
lib.lua_pcall.restype = ctypes.c_int
lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int,
                              ctypes.POINTER(ctypes.c_size_t)]
lib.lua_tolstring.restype = ctypes.c_char_p
try:
    if lib.luaL_loadbuffer(state, source.encode(), len(source), b"=blind") != 0:
        size = ctypes.c_size_t()
        print("LOAD ERROR:", lib.lua_tolstring(state, -1, ctypes.byref(size)).decode())
        raise SystemExit(1)
    if lib.lua_pcall(state, 0, 1, 0) != 0:
        size = ctypes.c_size_t()
        print("RUN ERROR:", lib.lua_tolstring(state, -1, ctypes.byref(size)).decode())
        raise SystemExit(1)
    size = ctypes.c_size_t()
    raw = lib.lua_tolstring(state, -1, ctypes.byref(size))
    out = raw.decode() if raw else ""
finally:
    probe.__exit__(None, None, None)

print("== blind probe against a simulated process ==")
fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
for k in ("hits", "rva", "value", "detail", "tested", "scanned"):
    print(f"  {k:9s}: {fields.get(k, '(missing)')}")

expect_rva = hex(TABLE_RVA)
expect_value = hex(TABLE_ADDR)
ok = True
if fields.get("hits") != "1":
    print(f"  ❌ expected exactly 1 hit, got {fields.get('hits')}")
    ok = False
elif fields.get("rva") != expect_rva:
    print(f"  ❌ expected rva {expect_rva}, got {fields.get('rva')}")
    ok = False
elif fields.get("value") != expect_value:
    print(f"  ❌ expected value {expect_value}, got {fields.get('value')}")
    ok = False
else:
    print(f"  ✅ found the table pointer at rva {expect_rva} -> {expect_value}")

# The pre-filter's value is performance, and without an assertion on it the
# filter can be deleted while the test still passes - measured: disabling it
# takes `tested` from 2 to 524,287 on this 4 MB module, i.e. every aligned slot
# becomes a memory read. On the real 26 MB of writable data that is the
# difference between a few hundred reads and 3.4 million.
tested = int(fields.get("tested", "0"))
if tested > 16:
    print(f"  ❌ the pre-filter is not doing its job: tested {tested} slot(s)")
    ok = False
else:
    print(f"  ✅ only {tested} slot(s) reached a memory read")

print()
if ok:
    print("test_blind_probe_e2e: PASS")
    raise SystemExit(0)
print("test_blind_probe_e2e: FAIL")
raise SystemExit(1)
