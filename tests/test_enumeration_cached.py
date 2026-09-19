"""Region enumeration must be cached, not repeated per scan attempt.

The stutter this guards against: `find_record` re-enumerated the whole address
space on every call. A live game has ~13000 regions, so each attempt paid
thousands of VirtualQuery calls before reading a single byte. With attempts
every 8 frames that is near-continuous enumeration, and it dominated the cost
- which is why cutting the READ budget from 64 MB to 8 MB barely helped.

Enumeration is amortised: walk the address space once, cache the list, and
filter out regions already passed by the cursor on later attempts.

Run:  python tests/test_enumeration_cached.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402

# Enough attempts that per-call enumeration would be unmistakable.
ATTEMPTS = 40
# Enumeration is O(regions); allow a couple of refreshes, not one per attempt.
MAX_ENUMERATIONS = 3


def main() -> int:
    check = base_mod.check
    resolver_src = (ROOT / "mod_template" / "src" / "10_resolver.lua").read_text(
        encoding="utf-8"
    )

    probe = f"""
local ffi = require("ffi")
local REGION_COUNT = 48
local REGION_SIZE = 8 * 1024 * 1024
local REGIONS_BASE = 0x100000000

local enumerations = 0
local reads = 0
local api = {{}}
function api.read(address, size)
  reads = reads + 1
  return ffi.new("uint8_t[?]", size)
end
function api.writable_regions(_a, s, l)
  enumerations = enumerations + 1
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
R.SCAN_BUDGET_BYTES = 8 * 1024 * 1024

local rec = {{ type_id = 137, position = 137, damage = 220, durable = 45,
              ap = {{3,3,3,0}} }}
for i = 1, {ATTEMPTS} do
  R.find_record(api, 0, rec, {{}})
end

return table.concat({{
  "enumerations=" .. enumerations,
  "reads=" .. reads,
}}, ";")
"""

    raw = base_mod.run_probe(probe)
    res: dict = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            res[k] = v

    enums = int(res.get("enumerations", "0"))
    reads = int(res.get("reads", "0"))

    print(f"== {ATTEMPTS} scan attempts ==")
    print(f"     region enumerations: {enums}")
    print(f"     reads:               {reads}")
    check("the address space is enumerated once, not per attempt",
          enums <= MAX_ENUMERATIONS,
          f"{enums} enumerations for {ATTEMPTS} attempts "
          f"(max {MAX_ENUMERATIONS})")

    print()
    print("== and the scan still makes progress ==")
    # 48 x 8 MB = 384 MB at 8 MB per attempt: roughly 48 reads if it advances.
    check("the walk advanced across attempts (more than one read)",
          reads > 1, f"{reads} reads")
    check("the walk did not re-read the whole space every attempt",
          reads <= (REGION_MB := 384) + 20, f"{reads} reads")

    print()
    print(f"test_enumeration_cached: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} ({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
