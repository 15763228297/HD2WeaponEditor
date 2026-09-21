"""Prove the flush bug is fixed by reproducing the exact game condition.

The previous in-game run failed with an empty log. The cause was found by
reading the shared loader's bytecode: its `open_log` returns a handle with no
`flush` method, and the log module wrapped `write` and `flush` in one pcall, so
the missing method aborted the write too.

This test loads the *whole generated mod* with a handle shaped exactly like the
loader's (write present, flush absent) and asserts the mod still runs to
completion and still changes the record. That is the regression that matters:
the previous bug did not merely lose the log, it stopped the entire mod before
any memory was touched.

Run:  python tests/test_loader_handle_shape.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402


def probe(generated_src: str, image: bytes, array_base: int, handle_kind: str) -> dict:
    lua_image = base_mod.lua_bytes(image)
    script = f"""
local ffi = require("ffi")
local image = {lua_image}
local IMAGE_BASE = {base_mod.IMAGE_BASE}
local len = #image
local out = {{}}
local writes = 0
local overlays = {{}}
local log_lines = {{}}

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
function api.module_base(name) return ffi.cast("uint8_t *", IMAGE_BASE) end
api.kernel = {{ GetModuleHandleA = function(n) return ffi.cast("void *", IMAGE_BASE) end,
                GetCurrentProcess = function() return ffi.cast("void *", 1) end }}
api.process = ffi.cast("void *", 1)
function api.write(address, buffer, size)
  writes = writes + 1
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  if off < 0 or off + size > len then return false, "out of range" end
  overlays[off] = ffi.string(buffer, size)
  return true
end
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
rawset(_G, "__resolver_api", api)

-- The loader's handle shape. This is the thing under test.
local function make_handle(kind)
  local h = {{}}
  function h.write(self, text)
    log_lines[#log_lines + 1] = text
    return self
  end
  if kind == "with_flush" then
    function h.flush(self) end
  end
  return h
end
local HANDLE = make_handle({handle_kind!r})

-- The mod works from a frame hook now, because the tables load after startup.
rawset(_G, "update", function(dt) end)

rawset(_G, "CowboyBingusModLoader", {{
  version = 15, api = 1, modules = {{}},
  open_log = function(name) return HANDLE end,
}})

-- Silence the console copy so the test output stays readable, but keep it.
local console = {{}}
local real_print = print
rawset(_G, "print", function(...)
  local p = {{}}
  for i = 1, select("#", ...) do p[#p + 1] = tostring(select(i, ...)) end
  console[#console + 1] = table.concat(p, " ")
end)

local ok, err = pcall(function()
  assert(loadstring([====[
{generated_src}
]====], "=generated"))()
end)

-- Drive frames. The mod waits out its startup warmup and then attempts about
-- twice a second, so drive well past the first attempt.
local hooked = _G.update
for i = 1, 400 do if hooked then hooked(0.016) end end

-- Read back the record.
local buf = api.read(IMAGE_BASE + {array_base} + 137 * 76, 76)
local function u32at(b, o)
  return b[o] + b[o+1]*256 + b[o+2]*65536 + b[o+3]*16777216
end
out.loaded = ok and true or false
out.err = ok and "" or tostring(err)
out.damage = u32at(buf, 4)
out.durable = u32at(buf, 8)
out.ap0 = u32at(buf, 12)
out.writes = writes
out.log = table.concat(log_lines, "|")
out.console_count = #console
return table.concat({{
  "loaded=" .. tostring(out.loaded),
  "damage=" .. tostring(out.damage),
  "durable=" .. tostring(out.durable),
  "ap0=" .. tostring(out.ap0),
  "writes=" .. tostring(out.writes),
  "console_count=" .. tostring(out.console_count),
  "has_weapon=" .. tostring(out.log:find("R-4 Hyena") ~= nil),
  "has_verified=" .. tostring(out.log:lower():find("verified") ~= nil),
  "has_refused=" .. tostring(out.log:find("REFUSED") ~= nil),
  "err=" .. out.err,
}}, ";")
"""
    raw = base_mod.run_probe(script)
    result: dict = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if v == "true":
            result[k] = True
        elif v == "false":
            result[k] = False
        elif v.lstrip("-").isdigit():
            result[k] = int(v)
        else:
            result[k] = v
    return result


def main() -> int:
    check = base_mod.check
    meta = json.loads((ROOT / "data" / "sim_memory.json").read_text())
    image = (ROOT / "data" / "sim_memory.bin").read_bytes()
    # Own the input: several suites write build/generated.lua, so reading it here
    # made this test depend on which one ran last. Generate a single R-4 edit
    # under a private name, which is what the assertions below expect.
    gen_path = ROOT / "build" / "generated_single.lua"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_mod.py"),
         "--weapon", "R-4 Hyena", "--damage", "400",
         "--durable", "200", "--ap", "7",
         "--out", str(ROOT / "build"), "--emit-lua", str(gen_path)],
        check=True, capture_output=True, cwd=str(ROOT))
    generated = gen_path.read_text(encoding="utf-8")

    print("== the handle shape the shared loader actually returns (no flush) ==")
    out = probe(generated, image, meta["array_base"], "no_flush")
    check("the mod ran to completion", out.get("loaded") is True,
          f"err={out.get('err')}")
    check("damage is 400", out.get("damage") == 400, f"got {out.get('damage')}")
    check("durable is 200", out.get("durable") == 200, f"got {out.get('durable')}")
    check("AP is 7", out.get("ap0") == 7, f"got {out.get('ap0')}")
    check("it wrote to memory", isinstance(out.get("writes"), int)
          and out["writes"] > 0, f"writes={out.get('writes')}")
    check("the log captured the weapon", out.get("has_weapon") is True)
    check("the log captured verification", out.get("has_verified") is True)
    check("no refusal was logged", out.get("has_refused") is False)

    print()
    print("== a handle that also has flush ==")
    out2 = probe(generated, image, meta["array_base"], "with_flush")
    check("the mod ran to completion", out2.get("loaded") is True,
          f"err={out2.get('err')}")
    check("damage is 400", out2.get("damage") == 400, f"got {out2.get('damage')}")

    print()
    print(f"test_loader_handle_shape: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} "
          f"({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
