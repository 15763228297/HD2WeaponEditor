"""The scan must be bounded per attempt, and must resume rather than restart.

The crash this guards against: an earlier version walked the entire address
space in 4 MB chunks with no byte budget, reading gigabytes of the game's memory
while it was still loading its startup assets. The game died before the intro
cinematic. Nothing in the suite objected, because every other test injects a
tiny region list - the cost was invisible by construction.

Two properties are asserted here, both of which were violated:

  1. a single `find_record` call reads at most SCAN_BUDGET_BYTES (+ at most one
     chunk of slack), and reports that it is incomplete rather than silently
     returning "not found";
  2. calling it again RESUMES - the total bytes read across the whole walk stay
     close to the region total, instead of repeating earlier reads.

Run:  python tests/test_scan_budget.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402

# Geometry: 64 regions of 8 MB = 512 MB, i.e. four budgeted calls. The real
# record is planted in the LAST region, so a scan that restarts instead of
# resuming can never reach it.
REGION_COUNT = 64
REGION_SIZE = 8 * 1024 * 1024
REGIONS_BASE = 0x100000000
PLANT_OFFSET = 4096


def main() -> int:
    check = base_mod.check
    resolver_src = (ROOT / "mod_template" / "src" / "10_resolver.lua").read_text(
        encoding="utf-8"
    )

    probe = f"""
local ffi = require("ffi")
local REGION_COUNT = {REGION_COUNT}
local REGION_SIZE = {REGION_SIZE}
local REGIONS_BASE = {REGIONS_BASE}
local PLANT_OFFSET = {PLANT_OFFSET}

-- The planted table: three consecutive 76-byte rows, because the resolver
-- cross-checks the neighbours (a lone record surrounded by zeros is rejected
-- as "probably not the table"). Only the middle row is our target.
--   row 136: type_id 136, 165/45   (R-63 Diligence)
--   row 137: type_id 137, 220/45   (R-4 Hyena)        <- the target
--   row 138: type_id 138, 200/50   (High Velocity)
local RECORD_AT = REGIONS_BASE + (REGION_COUNT - 1) * REGION_SIZE + PLANT_OFFSET
local TABLE_AT = RECORD_AT - 76          -- start of row 136

local table_bytes = ffi.new("uint8_t[228]")
local tbase = ffi.cast("uint8_t *", table_bytes)
local function put_i32(row, off, v)
  local p = ffi.cast("int32_t *", tbase + row * 76 + off)
  p[0] = v
end
-- row 136
put_i32(0, 0, 136) put_i32(0, 4, 165) put_i32(0, 8, 45)
put_i32(0, 12, 2)  put_i32(0, 16, 2)  put_i32(0, 20, 2) put_i32(0, 24, 0)
-- row 137 (the target)
put_i32(1, 0, 137) put_i32(1, 4, 220) put_i32(1, 8, 45)
put_i32(1, 12, 3)  put_i32(1, 16, 3)  put_i32(1, 20, 3) put_i32(1, 24, 0)
-- row 138
put_i32(2, 0, 138) put_i32(2, 4, 200) put_i32(2, 8, 50)
put_i32(2, 12, 3)  put_i32(2, 16, 3)  put_i32(2, 20, 3) put_i32(2, 24, 0)

local reads = 0
local bytes = 0
local api = {{}}
function api.read(address, size)
  local addr = tonumber(ffi.cast("uintptr_t", address))
  reads = reads + 1
  bytes = bytes + size
  local buf = ffi.new("uint8_t[?]", size)
  -- Copy the planted table in, by OVERLAP rather than containment. Both read
  -- patterns must work: the scanner reads 1 MB chunks that fully contain the
  -- table, while verify_record reads 28 bytes that start partway inside it.
  -- Requiring containment silently returned zeros for the verify read, so every
  -- candidate was rejected and the match looked like a layout problem.
  local read_lo, read_hi = addr, addr + size
  local tab_lo, tab_hi = TABLE_AT, TABLE_AT + 228
  if read_lo < tab_hi and tab_lo < read_hi then
    local from = math.max(read_lo, tab_lo)
    local to = math.min(read_hi, tab_hi)
    ffi.copy(ffi.cast("uint8_t *", buf) + (from - read_lo),
             ffi.cast("uint8_t *", table_bytes) + (from - tab_lo),
             to - from)
  end
  return buf
end
function api.writable_regions(_a, s, l)
  local list = {{}}
  for i = 0, REGION_COUNT - 1 do
    list[#list + 1] = {{ base = ffi.cast("uint8_t *", REGIONS_BASE + i * REGION_SIZE),
                        size = REGION_SIZE }}
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

local out = {{}}
out.budget = R.SCAN_BUDGET_BYTES
out.chunk = R.SCAN_CHUNK

local rec = {{ type_id = 137, position = 137, damage = 220, durable = 45,
              ap = {{3,3,3,0}} }}
-- The neighbouring rows are planted, so assert on them: the match must sit
-- between row 136 and row 138.
local expect = {{ before_id = 136, after_id = 138 }}

local r0, b0 = reads, bytes
local ok1, why1 = R.find_record(api, 0, rec, expect)
out.first_ok = ok1 and "true" or "false"
out.first_why = tostring(why1)
out.first_bytes = bytes - b0
out.first_reads = reads - r0

-- Continue until found (or 40 calls, which is far past what this geometry needs).
local found = ok1
local call = 1
local total_bytes = bytes
-- 512 MB of regions at 8 MB per call needs ~70 calls; allow generous headroom
-- so the assertion measures resume behaviour, not an arbitrary cutoff.
while not found and call < 200 do
  call = call + 1
  local ok = R.find_record(api, 0, rec, expect)
  if ok then found = true end
end
out.found = found and "true" or "false"
out.calls = call
out.total_bytes = bytes
out.region_total = REGION_COUNT * REGION_SIZE

return table.concat({{
  "budget=" .. out.budget,
  "chunk=" .. out.chunk,
  "first_ok=" .. out.first_ok,
  "first_bytes=" .. out.first_bytes,
  "first_reads=" .. out.first_reads,
  "found=" .. out.found,
  "calls=" .. out.calls,
  "total_bytes=" .. out.total_bytes,
  "region_total=" .. out.region_total,
  "first_why=" .. out.first_why,
}}, ";")
"""

    raw = base_mod.run_probe(probe)
    result: dict = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k] = v

    budget = int(float(result.get("budget", "0")))
    chunk = int(result.get("chunk", "0"))
    first_bytes = int(result.get("first_bytes", "0"))
    total_bytes = int(result.get("total_bytes", "0"))
    region_total = int(result.get("region_total", "1"))

    print("== the resolver declares a finite per-call budget ==")
    # This is the invariant whose absence crashed the game. A budget that is
    # infinite, or merely enormous, means one call can read the whole address
    # space - which is exactly what happened.
    SANE_CEILING = 512 * 1024 * 1024
    print(f"     budget {budget:,} B, chunk {chunk:,} B")
    check("the per-call budget is finite and sane",
          0 < budget <= SANE_CEILING, f"{budget:,} (ceiling {SANE_CEILING:,})")
    check("the chunk size is smaller than the budget", 0 < chunk <= budget,
          f"chunk {chunk:,}, budget {budget:,}")

    print()
    print("== a single call is bounded ==")
    print(f"     first call: {int(result.get('first_reads', 0))} read(s), "
          f"{first_bytes:,} B")
    check("the first call stays within the budget (one chunk of slack allowed)",
          first_bytes <= budget + chunk,
          f"{first_bytes:,} > {budget:,} + {chunk:,}")
    check("the first call reports 'not found YET', not a false failure",
          result.get("first_ok") == "false"
          and "not found yet" in result.get("first_why", ""),
          f"why={result.get('first_why', '')[:80]}")

    print()
    print("== the second call resumes rather than restarting ==")
    check("the record was eventually found", result.get("found") == "true",
          f"after {result.get('calls')} call(s)")
    # 512 MB of regions against a 128 MB budget means the walk CANNOT finish in
    # one call. If it does, the budget is not being enforced per call.
    check("one call could not cover all the regions",
          first_bytes < region_total,
          f"first call read {first_bytes:,} of {region_total:,} B")
    check("it took several calls to reach the last region",
          int(result.get("calls", "0")) > 1,
          f"calls={result.get('calls')}")

    print()
    print("== total cost across the whole walk ==")
    print(f"     {total_bytes:,} B read, {region_total:,} B of regions")
    # Resuming reads each byte about once; restating the search would read
    # several times the total. 1.5x leaves room for pattern overlap.
    check("the walk did not re-read earlier regions",
          total_bytes <= region_total * 1.5,
          f"{total_bytes:,} > {region_total:,} * 1.5")

    print()
    print(f"test_scan_budget: {'PASS' if not base_mod.failures else 'FAIL'} "
          f"({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
