"""A single scan attempt must stay small enough not to stall a frame.

The stutter this guards against: a 64 MB budget meant 64 synchronous
ReadProcessMemory calls back-to-back inside one frame callback. Total bandwidth
was modest (~128 MB/s) and irrelevant - what the user felt was the per-frame
stall, and the game was visibly stuttery for exactly as long as the scan ran
(~40 seconds).

So the invariant is per-frame work, not throughput: one attempt must read a
bounded number of bytes, and the caller must space attempts so that this is
also a bound on any single frame.

Run:  python tests/test_per_frame_cost.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402

# A frame budget a game can absorb without a visible hitch.
MAX_BYTES_PER_ATTEMPT = 16 * 1024 * 1024
MAX_READS_PER_ATTEMPT = 24


def main() -> int:
    check = base_mod.check
    resolver_src = (ROOT / "mod_template" / "src" / "10_resolver.lua").read_text(
        encoding="utf-8"
    )
    generated = (ROOT / "build" / "generated.lua")
    gen_src = generated.read_text(encoding="utf-8") if generated.exists() else ""

    probe = f"""
local ffi = require("ffi")
local REGION_COUNT = 64
local REGION_SIZE = 8 * 1024 * 1024
local REGIONS_BASE = 0x100000000

local reads = 0
local bytes = 0
local api = {{}}
function api.read(address, size)
  reads = reads + 1
  bytes = bytes + size
  return ffi.new("uint8_t[?]", size)
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
local rec = {{ type_id = 137, position = 137, damage = 220, durable = 45, ap = {{3,3,3,0}} }}

local r0, b0 = reads, bytes
R.find_record(api, 0, rec, {{}})
return table.concat({{
  "budget=" .. R.SCAN_BUDGET_BYTES,
  "chunk=" .. R.SCAN_CHUNK,
  "reads=" .. (reads - r0),
  "bytes=" .. (bytes - b0),
}}, ";")
"""

    raw = base_mod.run_probe(probe)
    res: dict = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            res[k] = v

    budget = int(float(res.get("budget", "0")))
    reads = int(res.get("reads", "0"))
    nbytes = int(res.get("bytes", "0"))

    print("== one attempt must fit in a frame ==")
    print(f"     budget {budget:,} B, chunk {int(res.get('chunk','0')):,} B")
    print(f"     measured: {reads} read(s), {nbytes:,} B")
    check("one attempt reads at most MAX_BYTES_PER_ATTEMPT",
          nbytes <= MAX_BYTES_PER_ATTEMPT,
          f"{nbytes:,} > {MAX_BYTES_PER_ATTEMPT:,}")
    check("one attempt makes a bounded number of read calls",
          reads <= MAX_READS_PER_ATTEMPT, f"{reads} > {MAX_READS_PER_ATTEMPT}")

    print()
    print("== the frame hook must space attempts out ==")
    # Cadence is in the generated mod, not the resolver.
    import re
    every = re.search(r"ATTEMPT_EVERY\s*=\s*(\d+)", gen_src)
    every_n = int(every.group(1)) if every else 0
    print(f"     ATTEMPT_EVERY = {every_n} frames")
    check("attempts are spaced across frames, not every frame",
          every_n >= 2, f"ATTEMPT_EVERY={every_n}")
    # Worst case per frame = one attempt, since only one runs per frame.
    check("the declared budget is small enough for one frame",
          budget <= MAX_BYTES_PER_ATTEMPT, f"{budget:,}")

    print()
    print(f"test_per_frame_cost: {'PASS' if not base_mod.failures else 'FAIL'} "
          f"({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
