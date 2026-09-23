"""End-to-end test of the generated mod.

Everything before this tested the modules separately. This one runs the *actual
generated Lua* - the same source string that gets compiled and shipped - against
a simulated process image, and asserts the weapon's values change while its
neighbours do not.

That last part is the user's requirement ("only my weapon"), and it is the one
that cannot be verified by reading code: the whole point is that the mod writes
to a live process, and the only way to prove it wrote to the right place is to
look at what changed around it.

Run:  python tests/test_generated_mod.py
"""

from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402


def probe(generated_src: str, image: bytes, script: str) -> dict:
    """Run the generated mod's `main()` against a simulated process."""
    lua_image = base_mod.lua_bytes(image)
    prelude = f"""
local ffi = require("ffi")
local image = {lua_image}
local IMAGE_BASE = {base_mod.IMAGE_BASE}
local len = #image
local out = {{}}
local writes = 0
local overlays = {{}}

-- The simulated process. `image` is read-only from Lua, so writes are kept in
-- an overlay that reads consult; that makes verification meaningful (a read
-- after a write sees the new value only if the write actually stored it).
local api = {{
  MEM_COMMIT = 0x1000, PAGE_GUARD = 0x100, WRITABLE = 0x04,
  WRITABLE2 = 0x40, WRITABLE3 = 0x08,
}}
function api.writable_regions(_a, start, limit)
  local s = tonumber(ffi.cast("uintptr_t", start))
  local l = tonumber(ffi.cast("uintptr_t", limit))
  if s >= IMAGE_BASE + len or l <= IMAGE_BASE then return {{}} end
  return {{ {{ base = ffi.cast("uint8_t *", IMAGE_BASE), size = len }} }}
end
function api.is_writable(address) return true end
function api.write(address, buffer, size)
  writes = writes + 1
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  if off < 0 or off + size > len then return false, "out of range" end
  overlays[off] = ffi.string(buffer, size)
  return true
end

-- The resolver's read seam, overlay-aware.
function api.read(address, size)
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  if off < 0 or off + size > len then return nil end
  local buf = ffi.new("uint8_t[?]", size)
  ffi.copy(buf, image:sub(off + 1, off + size), size)
  for woff, wbytes in pairs(overlays) do
    if woff >= off and woff + #wbytes <= off + size then
      ffi.copy(ffi.cast("uint8_t *", buf) + (woff - off), wbytes, #wbytes)
    end
  end
  return buf
end

-- The log module writes to a file and falls back to print; capture instead.
local loglines = {{}}
rawset(_G, "print", function(...)
  local parts = {{}}
  for i = 1, select("#", ...) do parts[#parts + 1] = tostring(select(i, ...)) end
  loglines[#loglines + 1] = table.concat(parts, " ")
end)

-- The generated file loads its modules by calling load_10_resolver_lua() etc,
-- and gets the process API from the resolver. Inject ours.
rawset(_G, "__resolver_api", api)

-- The mod installs a frame hook by wrapping the global `update` and does its
-- work from there, because the game loads its tables after startup. Provide the
-- callback the game provides, then drive frames.
local frames = 0
rawset(_G, "update", function(dt) frames = frames + 1 end)

-- The generated mod guards on the game.dll base before doing anything, which is
-- correct for the real game but would make it bail here -- there is no game.dll
-- in this process. The resolver consults `api.module_base` when the API is
-- injected, so supply the simulated base through that seam.
function api.module_base(name)
  if name == "game.dll" then return ffi.cast("uint8_t *", IMAGE_BASE) end
  return nil
end
api.kernel = {{
  GetModuleHandleA = function(name)
    if name == "game.dll" then return ffi.cast("void *", IMAGE_BASE) end
    return nil
  end,
  GetCurrentProcess = function() return ffi.cast("void *", 1) end,
}}
api.process = ffi.cast("void *", 1)

-- Get the mod source in and call its main-like entry: it runs at load time.
local ok, err = pcall(function()
  local chunk = assert(loadstring([====[
{generated_src}
]====], "=generated"))
  chunk()
end)
out.loaded = ok and true or false
out.load_error = ok and "" or tostring(err)

-- Drive frames. The mod stays out of the way while the game starts up
-- (WARMUP_FRAMES) and then attempts about twice a second, so the first attempt
-- lands around frame 181. Drive well past that.
local hooked = _G.update
for i = 1, 400 do
  if hooked then hooked(0.016) end
end
out.frames = frames

{script}

out.writes = writes
local parts = {{}}
parts[#parts + 1] = "frames=" .. tostring(frames)
for k, v in pairs(out) do parts[#parts + 1] = k .. "=" .. tostring(v) end
parts[#parts + 1] = "logcount=" .. #loglines
parts[#parts + 1] = "log=" .. table.concat(loglines, " / ")
return table.concat(parts, ";")
"""
    raw = base_mod.run_probe(prelude)
    result: dict = {}
    # Split on ";" but keep the log field intact (it contains no ";" by design).
    for part in raw.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if v == "true":
            result[k] = True
        elif v == "false":
            result[k] = False
        elif v == "nil":
            result[k] = None
        elif v.isdigit() or (v.startswith("-") and v[1:].isdigit()):
            result[k] = int(v)
        else:
            result[k] = v
    return result


def read_field(sim_image: bytes, offset: int, field_offset: int) -> int:
    return struct.unpack_from("<i", sim_image, offset + field_offset)[0]


def main() -> int:
    check = base_mod.check
    meta = json.loads((ROOT / "data" / "sim_memory.json").read_text())
    image = (ROOT / "data" / "sim_memory.bin").read_bytes()
    array_base = meta["array_base"]

    # Generate this test's own mod rather than reading whatever build/generated.lua
    # happens to hold. Several suites write that one path, so reading it made this
    # test depend on which suite ran last: test_multi_weapon.py leaves a
    # three-weapon mod there, and the assertions below expect a single R-4 edit.
    gen_path = ROOT / "build" / "generated_single.lua"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_mod.py"),
         "--weapon", "R-4 Hyena", "--damage", "400",
         "--durable", "200", "--ap", "7",
         "--out", str(ROOT / "build"), "--emit-lua", str(gen_path)],
        check=True, capture_output=True, cwd=str(ROOT))
    generated = gen_path.read_text(encoding="utf-8")

    # Where to read back from. Taken from the generated mod's own PLANS block
    # rather than hardcoded: the row position changes with every balance patch
    # (R-4 moved from 137 to 147 in 1.8.45850), and a stale number here would
    # make the test read a different row than the mod wrote - reporting a
    # failure that is really the test being out of date, or worse, passing
    # because two wrong numbers cancel.
    m = re.search(r"position\s*=\s*(\d+)", generated)
    if not m:
        print("could not find the row position in the generated mod")
        return 2
    position = int(m.group(1))
    print(f"  (the generated mod targets row {position})")

    print("== the generated mod loads and runs ==")
    script = """
    -- Read back the record to report what the mod did.
    local rec = (function()
      local addr = IMAGE_BASE + ARRAY_BASE_PLACEHOLDER + POSITION_PLACEHOLDER * 76
      local buf = api.read(addr, 76)
      return {
        damage = buf[4] + buf[5]*256 + buf[6]*65536 + buf[7]*16777216,
        durable = buf[8] + buf[9]*256 + buf[10]*65536 + buf[11]*16777216,
        ap0 = buf[12] + buf[13]*256 + buf[14]*65536 + buf[15]*16777216,
        ap3 = buf[24] + buf[25]*256 + buf[26]*65536 + buf[27]*16777216,
      }
    end)()
    out.damage = rec.damage
    out.durable = rec.durable
    out.ap0 = rec.ap0
    out.ap3 = rec.ap3

    -- The neighbours must be untouched: this is the "only my weapon" check.
    local nb = (function()
      local addr = IMAGE_BASE + ARRAY_BASE_PLACEHOLDER + (POSITION_PLACEHOLDER - 1) * 76
      local buf = api.read(addr, 76)
      return buf[4] + buf[5]*256 + buf[6]*65536 + buf[7]*16777216
    end)()
    local na = (function()
      local addr = IMAGE_BASE + ARRAY_BASE_PLACEHOLDER + (POSITION_PLACEHOLDER + 1) * 76
      local buf = api.read(addr, 76)
      return buf[4] + buf[5]*256 + buf[6]*65536 + buf[7]*16777216
    end)()
    out.prev_damage = nb
    out.next_damage = na
    """.replace("ARRAY_BASE_PLACEHOLDER", str(array_base)).replace(
        "POSITION_PLACEHOLDER", str(position))

    out = probe(generated, image, script)

    check("generated source loads without error", out.get("loaded") is True,
          f"err={out.get('load_error')}")
    check("it wrote something", isinstance(out.get("writes"), int)
          and out["writes"] > 0, f"writes={out.get('writes')}")

    print()
    print("== the target weapon now holds the new values ==")
    check("damage is 400", out.get("damage") == 400, f"got {out.get('damage')}")
    check("durable is 200", out.get("durable") == 200, f"got {out.get('durable')}")
    check("AP 0 is 7", out.get("ap0") == 7, f"got {out.get('ap0')}")
    check("AP 3 is unchanged at 0", out.get("ap3") == 0, f"got {out.get('ap3')}")

    print()
    print("== neighbouring weapons are untouched ==")
    # damage[136] is 165/45 and damage[138] is 200/50 in the real table.
    check("previous row still 165", out.get("prev_damage") == 165,
          f"got {out.get('prev_damage')}")
    check("next row still 200", out.get("next_damage") == 200,
          f"got {out.get('next_damage')}")
    check("exactly 5 writes (one per differing field)", out.get("writes") == 5,
          f"got {out.get('writes')}")

    print()
    print("== the log says what happened ==")
    # The log module prefers a file under %LOCALAPPDATA% and only falls back to
    # print, so read what it actually wrote rather than a captured stdout.
    import os
    log_path = Path(os.environ.get("LOCALAPPDATA", "")) / "CowboyBingus" / \
        "Helldivers2" / "Logs" / "WeaponEditor.log"
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    check("a log file was written", log_path.exists(), str(log_path))
    check("log mentions the weapon", "R-4 Hyena" in log, f"log={log[:200]}")
    check("log reports verification", "verified" in log.lower(), f"log={log[:400]}")
    check("log reports no refusal", "REFUSED" not in log, f"log={log[:400]}")

    print()
    if log:
        print("  --- log file ---")
        for line in log.splitlines():
            if line.strip():
                print(f"   {line}")
        # Clear it so the next run's assertions cannot pass on stale content.
        log_path.write_text("", encoding="utf-8")

    print()
    print(f"test_generated_mod: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} "
          f"({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
