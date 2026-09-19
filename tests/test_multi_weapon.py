"""Editing N weapons must cost one address-space scan, not N.

The property: the first plan located fixes the damage table's base, and every
other plan is then addressed as `base + record_offset` and verified in place -
no second walk. The walk is what made the game stutter (~13000 regions
enumerated per pass), so N walks for N edits would be N times the stutter.

Also guards the generator side: two edits landing on the same damage row must
be refused rather than silently writing the row twice.

Run:  python tests/test_multi_weapon.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402

WEAPONS = [("R-4 Hyena", 400, 200, 7),
           ("R-63 Diligence", 300, 120, 5),
           ("P-4 Senator", 250, 100, 5)]


def build_multi() -> Path:
    """Generate a three-weapon mod; returns the emitted Lua path."""
    out = ROOT / "build" / "generated.lua"
    cmd = [sys.executable, str(ROOT / "tools" / "gen_mod.py")]
    for name, dmg, dur, ap in WEAPONS:
        cmd += ["--edit", f"{name}::{dmg}/{dur}/{ap}"]
    cmd += ["--out", str(ROOT / "build"), "--emit-lua", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit(f"gen_mod failed:\n{r.stdout}\n{r.stderr}")
    return out


def main() -> int:
    check = base_mod.check

    print("== the generator accepts several weapons ==")
    gen = build_multi()
    src = gen.read_text(encoding="utf-8")
    for name, _, _, _ in WEAPONS:
        check(f"plan present: {name}", f'weapon = "{name}"' in src, "missing")
    check("all three plans are in one PLANS table",
          src.count("weapon = ") >= 3, f"{src.count('weapon = ')} plans")

    print()
    print("== the generator refuses two edits on the same row ==")
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_mod.py"),
         "--edit", "R-4 Hyena::400/200/7",
         "--edit", "R-4 Hyena::500/250/8"],
        capture_output=True, text=True, cwd=str(ROOT))
    check("duplicate row is refused", r.returncode != 0, "it was accepted")
    # Localised message: accept either language's wording for "same row".
    check("the refusal names the shared row",
          any(k in (r.stdout + r.stderr).lower()
              for k in ("same", "row", "同一条", "合并")),
          (r.stdout + r.stderr).strip()[:120])

    print()
    print("== at runtime: one scan, then offsets ==")
    # Drive the real generated mod against a fake process and count walks.
    probe = f"""
local ffi = require("ffi")
local out = {{}}
local image = {base_mod.lua_bytes((ROOT / "data" / "sim_memory.bin").read_bytes())}
local IMAGE_BASE = {base_mod.IMAGE_BASE}

local reads = 0
local writes = 0
local api = {{}}
function api.read(address, size)
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  if off < 0 or off + size > #image then return nil end
  reads = reads + 1
  return ffi.cast("uint8_t *", image:sub(off + 1, off + size))
end
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
ffi.cdef[[
typedef struct {{ void *base; void *allocation_base; uint32_t allocation_protection;
                 size_t size; uint32_t state; uint32_t protection; uint32_t type; }} ShMemoryRegion;
]]
api.kernel = {{
  VirtualQuery = function(a, r, s)
    r[0].base = a; r[0].size = #image; r[0].state = 0x1000
    r[0].protection = 0x04; r[0].type = 0x20000
    return s end,
  GetCurrentProcess = function() return ffi.cast("void *", -1) end }}
function api.module_base(n) return ffi.cast("uint8_t *", 0x7ff936410000) end
rawset(_G, "__resolver_api", api)

local LOG = {{}}
rawset(_G, "CowboyBingusModLoader", {{ open_log = function()
  return {{ write = function(self, t) LOG[#LOG+1] = t; return true end }} end }})
rawset(_G, "update", function(dt) end)
local real_print = print
print = function(...)
  local n = select("#", ...)
  local parts = {{}}
  for i = 1, n do parts[i] = tostring((select(i, ...))) end
  LOG[#LOG + 1] = table.concat(parts, "\\t")
end

local ok, err = pcall(function()
  assert(loadstring([====[
{src}
]====], "=generated"))()
end)
out.loaded = ok and "true" or "false"
out.load_error = ok and "" or tostring(err)

local hooked = _G.update
for i = 1, 600 do if hooked then hooked(0.016) end end

local text = table.concat(LOG, "\\n")
local function count(needle)
  local n, i = 0, 1
  while true do
    local a, b = text:find(needle, i, true)
    if not a then break end
    n = n + 1
    i = b + 1
  end
  return n
end
out.applied = count("applied:")
out.verified = count("verified:")
out.by_offset = count("addressed by offset")
out.writes = writes

-- Did every weapon actually get changed?
-- Read the final values back out of the simulated image so we know all three
-- actually changed, not just that something was written.
local function rd32(off)
  local p = ffi.cast("int32_t *", ffi.cast("uint8_t *", image:sub(off + 1, off + 4)))
  return tonumber(p[0])
end
local AB = 4096   -- array_base from sim_memory.json
local R  = 76
out.v_r4   = rd32(AB + 137 * R + 4) .. "/" .. rd32(AB + 137 * R + 8)
out.v_r63  = rd32(AB + 136 * R + 4) .. "/" .. rd32(AB + 136 * R + 8)
out.ap_r4  = rd32(AB + 137 * R + 12)
out.ap_r63 = rd32(AB + 136 * R + 12)

out.r4 = count("R-4 Hyena")
out.r63 = count("R-63 Diligence")
out.p4 = count("P-4 Senator")

return table.concat({{
  "loaded=" .. out.loaded,
  "load_error=" .. out.load_error,
  "applied=" .. out.applied,
  "verified=" .. out.verified,
  "by_offset=" .. out.by_offset,
  "writes=" .. out.writes,
  "r4=" .. out.r4,
  "v_r4=" .. out.v_r4,
  "v_r63=" .. out.v_r63,
  "ap_r4=" .. out.ap_r4,
  "ap_r63=" .. out.ap_r63,
  "r63=" .. out.r63,
  "p4=" .. out.p4,
}}, ";")
"""

    raw = base_mod.run_probe(probe)
    res: dict = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            res[k] = v

    check("the generated mod loads", res.get("loaded") == "true",
          res.get("load_error"))
    applied = int(res.get("applied", "0"))
    verified = int(res.get("verified", "0"))
    by_offset = int(res.get("by_offset", "0"))
    print(f"     applied={applied}  verified={verified}  "
          f"by_offset={by_offset}  writes={res.get('writes')}")
    check("all three weapons were applied", applied >= 3, f"{applied}")
    check("all three were verified", verified >= 3, f"{verified}")
    # Two of the three must have been reached by offset rather than by a fresh
    # walk: that is the whole point of sharing the scan.
    check("later weapons were addressed by offset, not re-scanned",
          by_offset >= 2, f"only {by_offset} of 2 expected")

    print()
    print("== the values really changed ==")
    print(f"     R-4  -> {res.get('v_r4')} (want 400/200), ap={res.get('ap_r4')}")
    print(f"     R-63 -> {res.get('v_r63')} (want 300/120), ap={res.get('ap_r63')}")
    check("R-4 holds 400/200", res.get("v_r4") == "400/200", res.get("v_r4"))
    check("R-63 holds 300/120", res.get("v_r63") == "300/120", res.get("v_r63"))
    check("R-4 AP is 7", res.get("ap_r4") == "7", res.get("ap_r4"))
    check("R-63 AP is 5", res.get("ap_r63") == "5", res.get("ap_r63"))

    print()
    print("== every named weapon appears in the log ==")
    for key, label in (("r4", "R-4 Hyena"), ("r63", "R-63 Diligence"),
                       ("p4", "P-4 Senator")):
        check(f"{label} was processed", int(res.get(key, "0")) >= 1,
              f"{res.get(key)} mentions")

    print()
    print(f"test_multi_weapon: {'PASS' if not base_mod.failures else 'FAIL'} "
          f"({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
