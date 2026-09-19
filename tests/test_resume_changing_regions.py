"""Resume must survive a region list that changes between calls.

This is the third attempt at this bug and the first to be pinned by a real log.
In the field the game's region count moved 13320 -> 13394 across attempts while
the mod scanned, and the log showed why nothing was ever found:

    attempt 1:  scanned 134217728 bytes, 3 of 13320 region(s)
    attempt 2:  scanned  67108864 bytes, 2 of 13350 region(s)   <- restarted
    attempt 3:  scanned  67108864 bytes, 2 of 13370 region(s)   <- restarted

Each call re-read the same opening regions, so the walk never passed region 3
of thirteen thousand. Two earlier resume schemes keyed on things a live process
changes and a test harness never does:

  * the region table's identity - the list is rebuilt every call;
  * the region COUNT - a running game allocates while you scan.

Both passed every existing test, because those tests build the list once and
hand back the identical table every time. This suite varies it, which is the
whole point.

The cursor must therefore be an absolute ADDRESS: whatever the list does,
"resume at 0x1a2b3c" means the same thing.

Run:  python tests/test_resume_changing_regions.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402

REGION_SIZE = 8 * 1024 * 1024
REGION_COUNT = 12          # 96 MB - three calls at a 32 MB budget
REGIONS_BASE = 0x100000000


def main() -> int:
    check = base_mod.check
    resolver_src = (ROOT / "mod_template" / "src" / "10_resolver.lua").read_text(
        encoding="utf-8"
    )

    probe = f"""
local ffi = require("ffi")
local REGION_SIZE = {REGION_SIZE}
local REGION_COUNT = {REGION_COUNT}
local REGIONS_BASE = {REGIONS_BASE}

-- Override the budget so the walk takes a few calls over THIS geometry.
local read_calls = 0
local api = {{}}

-- Plant a realistic three-row table at the start of the LAST region, so a
-- walker that restarts cannot reach it.
local TABLE_AT = REGIONS_BASE + (REGION_COUNT - 1) * REGION_SIZE + 4096
local table_bytes = ffi.new("uint8_t[228]")
local tbase = ffi.cast("uint8_t *", table_bytes)
local function put(row, off, v)
  ffi.cast("int32_t *", tbase + row * 76 + off)[0] = v
end
put(0, 0, 136) put(0, 4, 165) put(0, 8, 45)
put(0, 12, 2) put(0, 16, 2) put(0, 20, 2) put(0, 24, 0)
put(1, 0, 137) put(1, 4, 220) put(1, 8, 45)
put(1, 12, 3) put(1, 16, 3) put(1, 20, 3) put(1, 24, 0)
put(2, 0, 138) put(2, 4, 200) put(2, 8, 50)
put(2, 12, 3) put(2, 16, 3) put(2, 20, 3) put(2, 24, 0)

function api.read(address, size)
  local addr = tonumber(ffi.cast("uintptr_t", address))
  read_calls = read_calls + 1
  local buf = ffi.new("uint8_t[?]", size)
  local lo, hi = TABLE_AT, TABLE_AT + 228
  if addr < hi and lo < addr + size then
    local from = math.max(addr, lo)
    local to = math.min(addr + size, hi)
    ffi.copy(ffi.cast("uint8_t *", buf) + (from - addr),
             tbase + (from - lo), to - from)
  end
  return buf
end

-- THE POINT OF THIS TEST: every call returns a DIFFERENT list. A live game
-- allocates while we scan, so the count drifts and extra regions appear - the
-- field log went 13320 -> 13394 across attempts.
local extra = 0
function api.writable_regions(_a, s, l)
  extra = extra + 1
  local list = {{}}
  for i = 0, REGION_COUNT - 1 do
    list[#list + 1] = {{ base = ffi.cast("uint8_t *", REGIONS_BASE + i * REGION_SIZE),
                        size = REGION_SIZE }}
  end
  -- Simulate growth: toss in a few fresh small regions each call, like the
  -- real process did.
  for j = 1, 3 + extra do
    list[#list + 1] = {{ base = ffi.cast("uint8_t *", 0x400000000 + j * 0x10000),
                        size = 0x10000 }}
  end
  return list
end
function api.module_base(n) return ffi.cast("uint8_t *", 0x7ff936410000) end
rawset(_G, "__resolver_api", api)

local R = (function()
  local s = [====[
{resolver_src}
]====]
  return assert(loadstring(s, "resolver"))()
end)()
-- Small budget so several calls are needed.
R.SCAN_BUDGET_BYTES = 32 * 1024 * 1024

local rec = {{ type_id = 137, position = 137, damage = 220, durable = 45,
              ap = {{3,3,3,0}} }}
local expect = {{ before_id = 136, after_id = 138 }}

local out = {{}}
local found = false
local call = 0
local msgs = {{}}
while not found and call < 12 do
  call = call + 1
  local ok, why = R.find_record(api, 0, rec, expect)
  if ok then found = true end
  msgs[#msgs + 1] = tostring(why)
end
out.found = found and "true" or "false"
out.calls = call

-- Second, stronger check: the total read must not exceed the region total by
-- much. A restarting walker reads the early regions once per call.
out.read_calls = read_calls
out.last = msgs[#msgs] or ""

-- And a directly observable property: successive messages must show a
-- monotonic cursor in ADDRESS space, not a reset to SCAN_FLOOR.
out.msgs = table.concat(msgs, " | ")

return table.concat({{
  "found=" .. out.found,
  "calls=" .. out.calls,
  "read_calls=" .. out.read_calls,
  "msgs=" .. out.msgs,
}}, ";")
"""

    raw = base_mod.run_probe(probe)
    parts = raw.split(";")
    res: dict = {}
    for part in parts:
        if "=" in part:
            k, v = part.split("=", 1)
            res[k] = v

    msgs = res.get("msgs", "")
    read_calls = int(res.get("read_calls", "0"))
    # 96 MB of regions at a 32 MB budget and 1 MB chunks = 96 reads if each
    # byte is read once. A restarter reads far more.
    max_reads = REGION_SIZE * REGION_COUNT // (1024 * 1024) + 20

    print("== the region list changes between calls (as a live game's does) ==")
    for line in msgs.split(" | ")[:6]:
        print(f"     {line[:100]}")
    check("the record was found", res.get("found") == "true",
          f"after {res.get('calls')} call(s)")
    print()
    print("== the walk made progress instead of restarting ==")
    print(f"     {read_calls} reads for {REGION_COUNT} x "
          f"{REGION_SIZE // (1024*1024)} MB of regions")
    check("no restart: reads stay close to one pass over the regions",
          read_calls <= max_reads, f"{read_calls} > {max_reads}")

    # A monotonic cursor is what actually distinguishes resume from restart.
    addrs = []
    for chunk in msgs.split(" | "):
        marker = "now at 0x"
        if marker in chunk:
            hexpart = chunk.split(marker, 1)[1].split(",")[0].strip()
            try:
                addrs.append(int(hexpart, 16))
            except ValueError:
                pass
    print()
    print("== the cursor advanced monotonically ==")
    print(f"     {[hex(a) for a in addrs]}")
    check("the cursor is reported as an absolute address", len(addrs) >= 2,
          f"{len(addrs)} progress messages")
    if len(addrs) >= 2:
        check("the cursor never went backwards", addrs == sorted(addrs),
              f"{[hex(a) for a in addrs]}")
        check("the cursor moved past the first region",
              addrs[-1] > REGIONS_BASE + REGION_SIZE,
              f"last={hex(addrs[-1])}, region 2 starts at "
              f"{hex(REGIONS_BASE + REGION_SIZE)}")

    print()
    print(f"test_resume_changing_regions: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} ({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
