"""A Located-and-applied plan must settle.

Guards: the failure where `apply_one` locates the record and then throws on a
later line (a nil variable, a nil field). The throw is swallowed by the frame
hook's pcall, `finished` stays false, and the mod re-scans and re-locates on
every subsequent attempt - forever. The log looks healthy because the
"located at" line is written just before the throw, so the run reads like
progress while nothing is ever applied.

One symptom this catches: a log where "located at" repeats with no "applied"
and no "verified" line anywhere.

Run:  python tests/test_settles_after_success.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402


def main() -> int:
    check = base_mod.check

    # The real generated source, loaded into the standard fake-api harness.
    gen = (ROOT / "build" / "generated.lua")
    if not gen.exists():
        print("build/generated.lua missing - run tools/gen_mod.py first")
        return 1
    #Regenerate a single-weapon mod here rather than trusting whatever was built
    #last: the multi-weapon test leaves a 3-plan generated.lua behind, and this
    #test asserts per-plan settling counts.
    sys.path.insert(0, str(ROOT / "tools"))
    import gen_mod
    import subprocess
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_mod.py"),
         "--weapon", "R-4 Hyena", "--damage", "400",
         "--durable", "200", "--ap", "7",
         "--out", str(ROOT / "build"),
         "--emit-lua", str(gen)],
        check=True, capture_output=True, cwd=str(ROOT))
    generated_src = gen.read_text(encoding="utf-8")

    probe = f"""
local ffi = require("ffi")
local out = {{}}

local image = {base_mod.lua_bytes((ROOT / "data" / "sim_memory.bin").read_bytes())}
local IMAGE_BASE = {base_mod.IMAGE_BASE}

local writes = 0
local api = {{}}
function api.read(address, size)
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  if off < 0 or off + size > #image then return nil end
  return ffi.cast("uint8_t *", image:sub(off + 1, off + size))
end
-- api.write is (address, buffer, size) and must actually mutate, because the
-- writer reads the values back to verify them.
function api.write(address, buffer, size)
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  if off < 0 or off + size > #image then return false end
  writes = writes + 1
  image = image:sub(1, off)
      .. ffi.string(ffi.cast("uint8_t *", buffer), size)
      .. image:sub(off + size + 1)
  return true
end
function api.writable_regions(_a, s, l)
  return {{ {{ base = ffi.cast("uint8_t *", IMAGE_BASE), size = #image }} }}
end
-- The writability guard calls VirtualQuery; provide one that reports the
-- simulated image as committed + writable + private. The struct is declared by
-- the resolver module, so declare the same shape here for the fake.
ffi.cdef[[
typedef struct {{
    void *base;
    void *allocation_base;
    uint32_t allocation_protection;
    size_t size;
    uint32_t state;
    uint32_t protection;
    uint32_t type;
}} ShMemoryRegion;
]]
function api.virtual_query(address, region, size)
  region[0].base = address
  region[0].size = #image
  region[0].state = 0x1000        -- MEM_COMMIT
  region[0].protection = 0x04     -- PAGE_READWRITE
  region[0].type = 0x20000        -- MEM_PRIVATE
  return size
end
api.kernel = setmetatable({{
  VirtualQuery = function(address, region, size)
    return api.virtual_query(address, region, size)
  end,
  GetCurrentProcess = function() return ffi.cast("void *", -1) end,
}}, {{}})
function api.module_base(name)
  return ffi.cast("uint8_t *", 0x7ff936410000)
end
rawset(_G, "__resolver_api", api)

-- Minimal loader so the log writes somewhere we can read.
local LOG = {{ "LOG-START" }}
local handle = {{
  write = function(self, text) LOG[#LOG + 1] = text; return true end,
}}
rawset(_G, "CowboyBingusModLoader", {{
  open_log = function(_name) return handle end,
}})
-- Provide the frame callback the mod hooks.
rawset(_G, "update", function(dt) end)

-- The mod also echoes every line to print() for the game console, so capture
-- print as well - otherwise the log we inspect is missing most of the lines.
local real_print = print
print = function(...)
  local n = select("#", ...)
  local parts = {{}}
  for i = 1, n do parts[i] = tostring((select(i, ...))) end
  LOG[#LOG + 1] = table.concat(parts, "	")
end

local ok, err = pcall(function()
  assert(loadstring([====[
{generated_src}
]====], "=generated"))()
end)
out.loaded = ok and "true" or "false"
out.load_error = ok and "" or tostring(err)

-- Drive past the warmup and through several attempts.
local hooked = _G.update
for i = 1, 400 do if hooked then hooked(0.016) end end

out.writes = writes
local text = table.concat(LOG, "\\n")
out.log = text

local function count(needle)
  local n = 0
  local i = 1
  while true do
    local a, b = text:find(needle, i, true)
    if not a then break end
    n = n + 1
    i = b + 1
  end
  return n
end
out.located = count("located at")
out.applied = count("applied:")
out.verified = count("verified:")

return table.concat({{
  "loaded=" .. out.loaded,
  "load_error=" .. out.load_error,
  "writes=" .. out.writes,
  "located=" .. out.located,
  "applied=" .. out.applied,
  "verified=" .. out.verified,
}}, ";")
"""

    raw = base_mod.run_probe(probe)
    result: dict = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k] = v

    print("== the mod settles after it succeeds ==")
    check("the generated source loads", result.get("loaded") == "true",
          result.get("load_error"))
    located = int(result.get("located", "0"))
    applied = int(result.get("applied", "0"))
    verified = int(result.get("verified", "0"))
    print(f"     located={located}  applied={applied}  verified={verified}  "
          f"writes={result.get('writes')}")
    check("the record was applied", applied >= 1, f"applied={applied}")
    check("the change was verified", verified >= 1, f"verified={verified}")
    # This is the loop guard: once applied, the plan must settle, so the mod
    # must not keep re-locating. Allow a small margin for one in-flight attempt.
    check("it stopped re-locating after succeeding", located <= 2,
          f"located {located}x - apply_one is throwing after the locate")

    print()
    print(f"test_settles_after_success: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} ({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
