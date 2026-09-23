"""The scan must actually reach the heap, not just run without error.

Two in-game failures came from the scan window being wrong:

  1. bounded to the module (95 MB from its base) - found nothing;
  2. opened AT the module base and walked UP - also found nothing, because a
     Windows x64 process maps modules high (game.dll at 0x7ff936410000) and puts
     heap allocations far below them.

Both logged "pattern not found", which reads like the table moved. It had not:
the search was looking at the wrong end of the address space.

This test asserts the window's geometry, which is the property that was wrong
both times and which no other test checks - the other resolver tests inject
`writable_regions` and so never see the window at all.

Run:  python tests/test_scan_window.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import r4_fixture  # noqa: E402
import test_resolver as base_mod  # noqa: E402


def main() -> int:
    check = base_mod.check
    meta = json.loads((ROOT / "data" / "sim_memory.json").read_text())
    image = (ROOT / "data" / "sim_memory.bin").read_bytes()
    # R-4's row from the shipped data: its position and id move with every
    # balance patch, and a hardcoded copy would search for a row that is not
    # there any more. See tests/r4_fixture.py.
    row = r4_fixture.r4_row()
    resolver_src = (ROOT / "mod_template" / "src" / "10_resolver.lua").read_text(
        encoding="utf-8"
    )

    # Record what window find_record asks for, then answer with a region list.
    probe = f"""
local ffi = require("ffi")
local image = {base_mod.lua_bytes(image)}
local len = #image
local out = {{}}

-- A module address high in the x64 space, the way Windows maps one. The heap in
-- a real process sits far BELOW this.
local MODULE_BASE = 0x7ff936410000
local IMAGE_BASE = {base_mod.IMAGE_BASE}

local asked_from, asked_to = nil, nil

local api = {{}}
function api.read(address, size)
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  if off < 0 or off + size > len then return nil end
  local buf = ffi.new("uint8_t[?]", size)
  ffi.copy(buf, image:sub(off + 1, off + size), size)
  return buf
end
function api.writable_regions(_a, start, limit)
  asked_from = tonumber(ffi.cast("uintptr_t", start))
  asked_to = tonumber(ffi.cast("uintptr_t", limit))
  -- The image lives at IMAGE_BASE, which stands in for the heap: low, and well
  -- below the module. If the walk starts at the module base it never reaches it.
  if IMAGE_BASE >= asked_from and IMAGE_BASE < asked_to then
    return {{ {{ base = ffi.cast("uint8_t *", IMAGE_BASE), size = len }} }}
  end
  return {{}}
end
rawset(_G, "__resolver_api", api)

local R = (function()
  local s = [====[
{resolver_src}
]====]
  return assert(loadstring(s, "resolver"))()
end)()

local rec = RECORD_PLACEHOLDER
local ok, err = R.find_record(api, MODULE_BASE, rec, EXPECT_PLACEHOLDER)

-- Format as hex: tostring() renders values above 2^53 in scientific notation,
-- which cannot be parsed back as an integer.
return table.concat({{
  "ok=" .. tostring(ok),
  "asked_from=" .. string.format("%x", asked_from),
  "asked_to=" .. string.format("%x", asked_to),
  "module_base=" .. string.format("%x", MODULE_BASE),
  "image_base=" .. string.format("%x", IMAGE_BASE),
  "err=" .. tostring(err),
}}, ";")
"""

    raw = base_mod.run_probe(probe
                             .replace("RECORD_PLACEHOLDER", r4_fixture.lua_record(row))
                             .replace("EXPECT_PLACEHOLDER", r4_fixture.lua_expect(row)))
    result: dict = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k] = v

    module_base = int(result.get("module_base", "0"), 16)
    asked_from = int(result.get("asked_from", "0"), 16)
    asked_to = int(result.get("asked_to", "0"), 16)
    image_base = int(result.get("image_base", "0"), 16)

    print("== the scan window must cover the heap ==")
    print(f"     module base (high)  = 0x{module_base:x}")
    print(f"     heap stand-in (low) = 0x{image_base:x}")
    print(f"     window asked for    = [0x{asked_from:x}, 0x{asked_to:x}]")

    check("the window starts below the module base", asked_from < module_base,
          f"starts at 0x{asked_from:x}, module at 0x{module_base:x}")
    check("the window contains the heap stand-in",
          asked_from <= image_base < asked_to,
          f"heap 0x{image_base:x} not inside [0x{asked_from:x}, 0x{asked_to:x}]")
    check("the window starts low, not at the module base",
          asked_from < 0x100000000,
          f"starts at 0x{asked_from:x} - heap allocations are typically far below 0x100000000")

    print()
    print("== and the record is actually found through that window ==")
    check("find_record succeeded", result.get("ok") == "true",
          f"err={result.get('err')}")

    print()
    print(f"test_scan_window: {'PASS' if not base_mod.failures else 'FAIL'} "
          f"({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
