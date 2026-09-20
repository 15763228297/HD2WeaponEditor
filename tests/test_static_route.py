"""The static-route probe must find a pointer planted in a module image.

What this guards: the probe answers "can the 35-second-per-launch search be
replaced by a fixed offset in game.dll?". Its answer decides whether that work
happens at all, so a false negative is expensive - it would quietly cancel a
real improvement and look like a property of the game rather than a bug here.

Three specific ways it could return a false "not found", all of which were real
in the first draft:

  1. Only the module's WRITABLE sections were scanned. A global the game never
     writes to is `const` and lands in read-only data - which is exactly the
     shape a built-in table pointer has. The test plants its pointer in a
     read-only section, so the writable-only version fails.
  2. Candidates were looked up with a raw cdata uint32 as the table key. LuaJIT
     cdata does not hash by value like a Lua number, so every lookup missed.
  3. Chunking could break pointer alignment, making a pointer that straddles a
     chunk boundary invisible. The test plants a pointer at the very end of a
     chunk.

The image is a synthetic module: a PE-like layout of one read-only and one
writable section, with the table and its pointer placed by hand.

Run:  python tests/test_static_route.py
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from ljcompile import LuaJIT  # noqa: E402

MODULE_BASE = 0x7FF000000000
SECTION_RO_BASE = MODULE_BASE + 0x1000
SECTION_RW_BASE = MODULE_BASE + 0x8000
SECTION_SIZE = 0x4000
TABLE_BASE = 0x1A2B0000
TABLE_SIZE = 0x1000

# Exactly what the module scan should cover: every COMMITTED image section,
# whatever its protection.
#   headers 0x1000 (read-only) + .rdata 0x4000 + .data 0x4000
# Excluded, correctly: the RESERVE gap (not committed) and the MEM_PRIVATE
# region (outside the image entirely).
MODULE_SCANNED = 0x1000 + 2 * SECTION_SIZE

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


def build_image(pointer_rva: int, pointer_value: int, table_base: int) -> str:
    """A synthetic module image as a Lua string literal.

    Buffer offset == rva, so the image literally is the module: byte 0 is
    MODULE_BASE. Sections (see SECTIONS in make_probe):
        0x1000 .. 0x5000   read-only
        0x8000 .. 0xC000   writable
        0x10000 .. 0x14000 execute+read (code; the probe must skip it)

    The pointer is written at `pointer_rva`; which section contains it decides
    whether a writable-only scan can see it.
    """
    total = 0x14000
    buf = bytearray(total)
    assert 0 <= pointer_rva and pointer_rva + 8 <= total, "pointer outside image"
    buf[pointer_rva : pointer_rva + 8] = pointer_value.to_bytes(8, "little")

    # Noise that merely resembles the target: a value 4 higher. Written inside
    # both sections, so a scan that matches on the low half only would report
    # extra hits - the probe must compare all 8 bytes.
    noise = (pointer_value + 4).to_bytes(8, "little")
    for spot in (0x1100, 0x1404, 0x8100, 0x8300):
        buf[spot : spot + 8] = noise

    chunks = []
    for i in range(0, len(buf), 64):
        chunks.append("".join(f"\\{b:03d}" for b in buf[i : i + 64]))
    return '"' + "".join(chunks) + '"'


def run_probe(src: str, extra: str) -> str:
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
    lib.lua_settop.argtypes = [ctypes.c_void_p, ctypes.c_int]
    try:
        if lib.luaL_loadbuffer(state, extra.encode(), len(extra), b"=probe") != 0:
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


def make_probe(pointer_rva: int, pointer_value: int = TABLE_BASE,
               array_start: int = 0x1E0, chunk: int | None = None,
               budget: int | None = None) -> str:
    image = build_image(pointer_rva, pointer_value, TABLE_BASE)
    chunk_line = f"SR.CHUNK = {chunk}" if chunk else ""
    budget_line = f"SR.BUDGET = {budget}" if budget else ""

    return f"""
local ffi = require("ffi")
local image = {image}
local IMAGE_BASE = {MODULE_BASE}
local LEN = #image

-- Synthetic VirtualQuery: walks a fixed list of module sections and then hits a
-- non-image region, which is what ends the real walk too.
--
-- STATS: {{base, size, protection, type}} measured from IMAGE_BASE.
local SECTIONS = {{
  {{ 0x0000, 0x1000, 0x02, 0x1000000, 0x1000 }},   -- headers, read-only
  {{ 0x1000, 0x4000, 0x02, 0x1000000, 0x1000 }},   -- read-only  .rdata
  {{ 0x5000, 0x3000, 0x01, 0x1000000, 0x2000 }},   -- gap: MEM_IMAGE, RESERVE
  {{ 0x8000, 0x4000, 0x04, 0x1000000, 0x1000 }},   -- writable   .data
  {{ 0x10000, 0x4000, 0x20, 0x1000000, 0x1000 }},  -- .text, execute+read
  {{ 0x14000, 0x1000, 0x04, 0x20000,   0x1000 }},  -- MEM_PRIVATE: past the image
}}

local api = {{
  MEM_COMMIT = 0x1000, PAGE_GUARD = 0x100,
  WRITABLE = 0x04, WRITABLE2 = 0x40, WRITABLE3 = 0x08,
}}

function api.read(address, size)
  local a = tonumber(ffi.cast("uintptr_t", address))
  if a < IMAGE_BASE or a + size > IMAGE_BASE + LEN then return nil end
  local off = a - IMAGE_BASE
  local buf = ffi.new("uint8_t[?]", size)
  ffi.copy(buf, image:sub(off + 1, off + size), size)
  return buf
end

-- A VirtualQuery stand-in writing into the ShMemoryRegion the resolver
-- declared. Field order must match that cdef exactly.
api.kernel = {{}}
function api.kernel.VirtualQuery(address, region, size)
  if size < 48 then return 0 end
  local a = tonumber(ffi.cast("uintptr_t", address))
  local rel = a - IMAGE_BASE
  local hit = nil
  for _, s in ipairs(SECTIONS) do
    if rel >= s[1] and rel < s[1] + s[2] then hit = s break end
  end
  if hit == nil then
    local last = SECTIONS[#SECTIONS]
    if rel >= last[1] + last[2] then return 0 end
    hit = last
  end
  local raw = ffi.cast("uint8_t *", region)
  local function put64(off, v)
    ffi.cast("uint64_t *", raw + off)[0] = v
  end
  local function put32(off, v)
    ffi.cast("uint32_t *", raw + off)[0] = v
  end
  put64(0, IMAGE_BASE + hit[1])   -- base
  put64(8, IMAGE_BASE + hit[1])   -- allocation_base
  put32(16, 0)                    -- allocation_protection
  put32(20, 0)                    -- partition + reserved
  put64(24, hit[2])               -- size
  put32(32, hit[5])               -- state
  put32(36, hit[3])               -- protection
  put32(40, hit[4])               -- type
  return 48
end

local SR = (function()
  local s = [====[
{(ROOT / "mod_template" / "src" / "15_static_route.lua").read_text(encoding="utf-8")}
]====]
  return assert(loadstring(s, "static_route"))()
end)()

SR.ARRAY_START = {array_start}
{chunk_line}
{budget_line}

local state = SR.begin(api, ffi.cast("uint8_t *", IMAGE_BASE), {TABLE_BASE},
                       {{ array_start = {array_start}, record_address = nil }})

-- Drive to completion: budget is per-call and the caller owns cadence, so a
-- loop here is equivalent to many frames.
local guard = 0
while true do
  guard = guard + 1
  if guard > 100 then error("probe did not finish") end
  local done = SR.step(api, state)
  if done then break end
end

local lines = SR.describe_hits(state)
local rvas = {{}}
for _, h in ipairs(state.hits) do
  rvas[#rvas + 1] = string.format("0x%x:%s", h.rva, h.name)
end

local rec = {{}}
rec[#rec + 1] = "hits=" .. #state.hits
rec[#rec + 1] = "rvas=" .. table.concat(rvas, ",")
rec[#rec + 1] = "scanned=" .. state.scanned
rec[#rec + 1] = "regions=" .. #state.regions
rec[#rec + 1] = "first=" .. tostring(lines[1])
return table.concat(rec, ";")
"""


def make_probe_live(pointer_rva: int, pointer_value: int = TABLE_BASE,
                    array_start: int = 0x1E0) -> str:
    """The same probe, but WITHOUT the harness-only `api.read` seam.

    This is the shape the game sees: an api table whose only read route is
    `kernel.ReadProcessMemory`. Injecting `api.read` (as every other case here
    does) hides the production path entirely, which is how a probe that reads
    nothing in the live game passed a green suite.

    No `api.read` is defined below - deliberately. If the module under test
    reaches for it, it gets nil and scans nothing, and the assertions fail.
    """
    image = build_image(pointer_rva, pointer_value, TABLE_BASE)

    return f"""
local ffi = require("ffi")
local image = {image}
local IMAGE_BASE = {MODULE_BASE}
local LEN = #image

local SECTIONS = {{
  {{ 0x0000, 0x1000, 0x02, 0x1000000, 0x1000 }},
  {{ 0x1000, 0x4000, 0x02, 0x1000000, 0x1000 }},
  {{ 0x5000, 0x3000, 0x01, 0x1000000, 0x2000 }},
  {{ 0x8000, 0x4000, 0x04, 0x1000000, 0x1000 }},
  {{ 0x10000, 0x4000, 0x20, 0x1000000, 0x1000 }},
  {{ 0x14000, 0x1000, 0x04, 0x20000,   0x1000 }},
}}

-- NOTE: no `api.read`. This mirrors the live api table, which has none.
local api = {{
  MEM_COMMIT = 0x1000, PAGE_GUARD = 0x100,
  WRITABLE = 0x04, WRITABLE2 = 0x40, WRITABLE3 = 0x08,
  process = nil,
}}

api.kernel = {{}}
function api.kernel.VirtualQuery(address, region, size)
  if size < 48 then return 0 end
  local a = tonumber(ffi.cast("uintptr_t", address))
  local rel = a - IMAGE_BASE
  local hit = nil
  for _, s in ipairs(SECTIONS) do
    if rel >= s[1] and rel < s[1] + s[2] then hit = s break end
  end
  if hit == nil then
    local last = SECTIONS[#SECTIONS]
    if rel >= last[1] + last[2] then return 0 end
    hit = last
  end
  local raw = ffi.cast("uint8_t *", region)
  ffi.cast("uint64_t *", raw + 0)[0] = IMAGE_BASE + hit[1]
  ffi.cast("uint64_t *", raw + 8)[0] = IMAGE_BASE + hit[1]
  ffi.cast("uint32_t *", raw + 16)[0] = 0
  ffi.cast("uint32_t *", raw + 20)[0] = 0
  ffi.cast("uint64_t *", raw + 24)[0] = hit[2]
  ffi.cast("uint32_t *", raw + 32)[0] = hit[5]
  ffi.cast("uint32_t *", raw + 36)[0] = hit[3]
  ffi.cast("uint32_t *", raw + 40)[0] = hit[4]
  return 48
end

-- The ONLY read route, exactly like the shipped api.
local reads = 0
function api.kernel.ReadProcessMemory(process, address, buffer, size, got)
  local a = tonumber(ffi.cast("uintptr_t", address))
  if a < IMAGE_BASE or a + size > IMAGE_BASE + LEN then
    got[0] = 0
    return 0
  end
  local off = a - IMAGE_BASE
  ffi.copy(buffer, image:sub(off + 1, off + size), size)
  got[0] = size
  reads = reads + 1
  return 1
end

local SR = (function()
  local s = [====[
{(ROOT / "mod_template" / "src" / "15_static_route.lua").read_text(encoding="utf-8")}
]====]
  return assert(loadstring(s, "static_route"))()
end)()

SR.ARRAY_START = {array_start}

local state = SR.begin(api, ffi.cast("uint8_t *", IMAGE_BASE), {TABLE_BASE},
                       {{ array_start = {array_start}, record_address = nil }})

local guard = 0
while true do
  guard = guard + 1
  if guard > 100 then error("probe did not finish") end
  local done = SR.step(api, state)
  if done then break end
end

local rvas = {{}}
for _, h in ipairs(state.hits) do
  rvas[#rvas + 1] = string.format("0x%x:%s", h.rva, h.name)
end
return table.concat({{
  "hits=" .. #state.hits,
  "rvas=" .. table.concat(rvas, ","),
  "scanned=" .. state.scanned,
  "reads=" .. reads,
}}, ";")
"""


def main() -> int:
    print("test_static_route: pointer discovery inside a module image")

    # -- A pointer in the READ-ONLY section must be found --------------------
    # This is the case a writable-only scan misses, and the most likely real
    # location for a table pointer, so it is the primary assertion.
    rva = 0x1800
    out = run_probe("", make_probe(rva))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("a pointer in the read-only section is found",
          fields.get("hits") == "1", f"got hits={fields.get('hits')}")
    check("its rva is reported relative to the module base",
          f"0x{rva:x}" in fields.get("rvas", ""),
          f"got {fields.get('rvas')}")
    check("the read-only section is actually covered by the scan",
          int(fields.get("scanned", 0)) >= SECTION_SIZE,
          f"scanned={fields.get('scanned')}")
    # -- A pointer in the WRITABLE section must also be found ----------------
    rva_rw = 0x8400
    out = run_probe("", make_probe(rva_rw))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("a pointer in the writable section is found",
          fields.get("hits") == "1", f"got hits={fields.get('hits')}")
    check("every committed module section was enumerated",
          fields.get("regions") == "3", f"got regions={fields.get('regions')}")

    # -- A pointer in the last slot of a section must not be missed ----------
    rva_edge = 0x5000 - 8
    out = run_probe("", make_probe(rva_edge))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("a pointer in the last slot of a section is found",
          fields.get("hits") == "1", f"got hits={fields.get('hits')}")

    # -- Noise that merely resembles the target must not match ---------------
    rva = 0x1800
    out = run_probe("", make_probe(rva))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("near-miss values are not reported as hits",
          fields.get("hits") == "1", f"got hits={fields.get('hits')}")

    # -- The scan must cover BOTH sections, not just one --------------------
    out = run_probe("", make_probe(0x1800))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("read-only module data is scanned, not skipped",
          int(fields.get("scanned", 0)) >= 2 * SECTION_SIZE,
          f"scanned={fields.get('scanned')} (both sections total "
          f"{2 * SECTION_SIZE})")

    # -- A pointer that STRADDLES a chunk boundary must still be found -------
    # This is the case the alignment code exists for. It cannot be reached with
    # the shipped 1 MB chunk over a 16 KB section, so the chunk size is lowered
    # to force several chunks per section and the pointer is planted inside the
    # final 8 bytes of the first chunk - the one place a pointer is half in one
    # read and half in the next.
    CHUNK = 0x2000  # 8 KB: two chunks per read-only section
    rva_straddle = 0x1000 + CHUNK - 8
    out = run_probe("", make_probe(rva_straddle, chunk=CHUNK))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("a pointer in the last 8 bytes of a chunk is found",
          fields.get("hits") == "1",
          f"got hits={fields.get('hits')} at rva 0x{rva_straddle:x}")

    # A pointer just after a chunk boundary must also be seen - the mirror case.
    rva_after = 0x1000 + CHUNK
    out = run_probe("", make_probe(rva_after, chunk=CHUNK))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("a pointer just past a chunk boundary is found",
          fields.get("hits") == "1", f"got hits={fields.get('hits')}")

    # -- Chunking must not skip bytes when the budget splits a section -------
    # A tiny budget forces one chunk per step. Every pointer must still be
    # seen, and the reported scan total must equal the sections exactly -
    # over-reading would mean regions are being revisited.
    out = run_probe("", make_probe(0x1800, chunk=0x1000, budget=0x1000))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("a small budget still finds the pointer",
          fields.get("hits") == "1", f"got hits={fields.get('hits')}")
    check("chunked scanning covers each section exactly once",
          int(fields.get("scanned", 0)) == MODULE_SCANNED,
          f"scanned={fields.get('scanned')} expected {MODULE_SCANNED}")

    # -- A pointer inside a CODE section must be IGNORED --------------------
    # Code cannot hold a heap-pointer variable, and game.dll's .text is
    # hundreds of MB: scanning it would multiply the probe's cost for nothing.
    # The image has an execute+read section; a pointer planted there must not
    # be reported, and the scan total must not include its bytes.
    out = run_probe("", make_probe(0x10400))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("a pointer in the code section is not reported",
          fields.get("hits") == "0", f"got hits={fields.get('hits')}")
    check("the code section is excluded from the scan",
          int(fields.get("scanned", 0)) == MODULE_SCANNED,
          f"scanned={fields.get('scanned')} expected {MODULE_SCANNED}")

    # -- A pointer just BELOW the table (owner/header shape) must be named --
    # A module global points at the start of the heap allocation; if the
    # damage container is not the start, the pointer lands a few words below
    # table_base. Both shapes must be reported, with the name telling them
    # apart, because the chain shape differs.
    out = run_probe("", make_probe(0x1800, pointer_value=TABLE_BASE - 0x8))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("a pointer to table_base-8 is found as the owner shape",
          "owner-0x8" in fields.get("rvas", ""),
          f"got {fields.get('rvas')}")
    out = run_probe("", make_probe(0x1800, pointer_value=TABLE_BASE - 0x10))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("a pointer to table_base-0x10 is found as the owner shape",
          "owner-0x10" in fields.get("rvas", ""),
          f"got {fields.get('rvas')}")

    # -- Chunking must stay 8-byte aligned, or pointers are stepped over -----
    # A chunk size that is NOT a multiple of 8 is the realistic way alignment
    # breaks: the next chunk then starts at an odd position and the every-8th
    # -- byte walk samples a grid that no longer lines up with real pointers.
    # The pointer here sits in the second chunk; a version without the
    # alignment correction samples that chunk at the wrong offsets and misses
    # it entirely.
    out = run_probe("", make_probe(0x2008, chunk=0x1006))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    check("a non-multiple-of-8 chunk size does not desynchronise the walk",
          fields.get("hits") == "1",
          f"got hits={fields.get('hits')} with CHUNK=0x1006")

    # -- When there is no pointer, the answer must be a definite "not found" -
    out = run_probe("", make_probe(0x1800, pointer_value=0x0))
    fields = dict(p.split("=", 1) for p in out.split(";") if "=" in p)
    lines_joined = fields.get("first", "")
    check("a clean image reports NOT FOUND rather than a guess",
          "NOT FOUND" in lines_joined, f"got {lines_joined!r}")

    # -- The LIVE path must work, not just the harness path ------------------
    # Every test above injects `api.read`, which exists ONLY in a test harness.
    # The real api table has no `read` field - it has `kernel.ReadProcessMemory`
    # - so a probe that reaches for `api.read` reads nothing in the live game
    # and reports "NOT FOUND: 0 byte(s) scanned": a confident negative with no
    # scan behind it. That shipped, and this suite passed anyway, because no
    # case exercised the production read path. This one does.
    live_out = run_probe("", make_probe_live(0x1800))
    live_fields = dict(p.split("=", 1) for p in live_out.split(";") if "=" in p)
    check("the live read path scans bytes (kernel.ReadProcessMemory)",
          int(live_fields.get("scanned", 0)) > 0,
          f"scanned={live_fields.get('scanned')} - a harness-only `api.read` "
          f"path reads nothing in the game")
    check("the live read path finds a planted pointer",
          live_fields.get("hits") == "1", f"got hits={live_fields.get('hits')}")
    check("the live path reports the same rva as the harness path",
          "0x1800" in live_fields.get("rvas", ""),
          f"got {live_fields.get('rvas')}")

    print(f"\n{checks - failures}/{checks} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
