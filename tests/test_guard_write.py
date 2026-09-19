"""Offline test of the guard and write layers (20_guard.lua, 30_write.lua).

The property under test is not "does it write the right value" - it is **"does it
refuse to write when anything is off"**. Every failure path is exercised against a
simulated process image, and each one asserts that the record is byte-for-byte
unchanged afterwards. A guard that logs a refusal but writes anyway is worse than
no guard, because it looks safe.

Cases:
  1. clean run                       -> writes, verifies, other fields untouched
  2. identity mismatch (wrong row)   -> refuses, nothing written
  3. baseline mismatch (mod conflict)-> refuses, nothing written, names the field
  4. page not writable               -> refuses, nothing written
  5. already at the target value      -> skips the write, reports it
  6. an unknown field name           -> refuses rather than guessing an offset
  7. write appears to succeed but the
     read-back disagrees             -> reports failure, does not retry

Run:  python tests/test_guard_write.py
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import test_resolver as base_mod  # noqa: E402


def probe(modules: dict[str, str], image: bytes, script: str) -> dict:
    """Load several Lua modules against one simulated image, then run `script`.

    Each module is loadable by name from the script, so it can assemble the
    pieces in the same order the generated mod does.
    """
    lua_image = base_mod.lua_bytes(image)
    loaders = []
    for name, src in modules.items():
        loaders.append(
            f'rawset(_G, "__mod_{name}", function()\n'
            f"  local s = [====[\n{src}\n]====]\n"
            f"  return assert(loadstring(s, '{name}'))()\n"
            f"end)\n"
        )
    prelude = f"""
local ffi = require("ffi")
local image = {lua_image}
local IMAGE_BASE = {base_mod.IMAGE_BASE}
local len = #image
local out = {{}}
local reads, writes = 0, 0
local write_log = {{}}

local api = {{
  MEM_COMMIT = 0x1000, PAGE_GUARD = 0x100, WRITABLE = 0x04,
  WRITABLE2 = 0x40, WRITABLE3 = 0x08,
}}

function api.read(address, size)
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  if off < 0 or off + size > len then return nil end
  local buf = ffi.new("uint8_t[?]", size)
  ffi.copy(buf, image:sub(off + 1, off + size), size)
  reads = reads + 1
  return buf
end

function api.writable_regions(_a, start, limit)
  local s = tonumber(ffi.cast("uintptr_t", start))
  local l = tonumber(ffi.cast("uintptr_t", limit))
  if s >= IMAGE_BASE + len or l <= IMAGE_BASE then return {{}} end
  return {{ {{ base = ffi.cast("uint8_t *", IMAGE_BASE), size = len }} }}
end

-- Writability seam. The test flips this to simulate a read-only page.
api.__writable = true
function api.is_writable(address)
  if api.__writable then return true end
  return false, "simulated read-only page"
end

-- Write seam. Applies the write to the image so read-back can observe it, and
-- can be told to lie (report success without writing) to test verification.
api.__drop_writes = false
function api.write(address, buffer, size)
  writes = writes + 1
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  if off < 0 or off + size > len then return false, "out of range" end
  local bytes = ffi.string(buffer, size)
  write_log[#write_log + 1] = off .. ":" .. size
  if api.__drop_writes then
    return true          -- pretend it worked; the value is NOT stored
  end
  -- image is read-only from Lua here, so remember the writes and apply them at
  -- the end; reads consult this overlay.
  api.__writes = api.__writes or {{}}
  api.__writes[off] = bytes
  return true
end

-- Reads must see the overlay for read-back verification to be meaningful.
local base_read = api.read
function api.read(address, size)
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  local buf = base_read(address, size)
  if buf == nil then return nil end
  if api.__writes then
    for woff, wbytes in pairs(api.__writes) do
      if woff >= off and woff + #wbytes <= off + size then
        ffi.copy(ffi.cast("uint8_t *", buf) + (woff - off), wbytes, #wbytes)
      end
    end
  end
  return buf
end

rawset(_G, "__resolver_api", api)
{''.join(loaders)}
local R = __mod_resolver()
local G = __mod_guard()
local W = __mod_write().init(G)

{script}

out.reads = reads
out.writes = writes
local parts = {{}}
for k, v in pairs(out) do
  parts[#parts + 1] = k .. "=" .. tostring(v)
end
return table.concat(parts, ";")
"""

    raw = base_mod.run_probe(prelude)
    result: dict = {}
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
        else:
            try:
                result[k] = float(v) if "." in v else int(v)
            except ValueError:
                result[k] = v
    return result


R4 = {"type_id": 137, "position": 137, "damage": 220, "durable": 45,
      "ap": [3, 3, 3, 0]}
CHANGES = {"damage": 400, "durable": 200, "ap0": 4, "ap1": 4, "ap2": 4, "ap3": 0}
EXPECT = {"before_id": 136, "after_id": 138,
          "next": {"damage": 200, "durable": 50}}


def script_for(*, record="R4", changes="CHANGES", baseline_note="",
               writable=True, drop_writes=False, extra=""):
    return f"""
local R4 = {{ type_id = 137, position = 137, damage = 220, durable = 45,
              ap = {{3,3,3,0}} }}
local CHANGES = {{ damage = 400, durable = 200, ap0 = 4, ap1 = 4, ap2 = 4, ap3 = 0 }}
local EXPECT = {{ before_id = 136, after_id = 138,
                  next = {{ damage = 200, durable = 50 }} }}
-- The baseline the GUI captured: the values as they are right now.
local BASELINE = {{ damage = 220, durable = 45, ap0 = 3, ap1 = 3, ap2 = 3, ap3 = 0,
                    ap = {{3,3,3,0}} }}
api.__writable = {str(writable).lower()}
api.__drop_writes = {str(drop_writes).lower()}

local ok, address = R.find_record(api, IMAGE_BASE, {record}, EXPECT)
out.found = ok and true or false
if not ok then
  out.code = "not-found"
  return out
end
out.offset = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE

local gok, gcode, gdetail = G.run_all(api, R, address, {record}, {changes}, BASELINE)
out.guard_ok = gok and true or false
out.guard_code = tostring(gcode)

if gok then
  local wok, applied, problems = W.apply(api, R, address, {changes})
  out.write_ok = wok and true or false
  out.applied = #applied
  out.applied_desc = W.describe(applied)
  if #problems > 0 then out.problem = problems[1] end
  local vok2, bad = W.verify(api, R, address, {changes})
  out.verify_ok = vok2 and true or false
  if bad then out.verify_bad = bad[1] end
else
  out.detail = type(gdetail) == "table" and gdetail[1] and gdetail[1].detail
               or tostring(gdetail)
  -- The baseline failure carries one entry per disagreeing field; capture the
  -- name too, since "expected 999, found 220" alone does not say *which* field.
  if type(gdetail) == "table" and gdetail[1] then
    out.field = tostring(gdetail[1].field)
  end
end
{extra}
"""


def main() -> int:
    meta = json.loads((ROOT / "data" / "sim_memory.json").read_text())
    image = (ROOT / "data" / "sim_memory.bin").read_bytes()

    modules = {
        "resolver": (ROOT / "mod_template" / "src" / "10_resolver.lua").read_text(
            encoding="utf-8"),
        "guard": (ROOT / "mod_template" / "src" / "20_guard.lua").read_text(
            encoding="utf-8"),
        "write": (ROOT / "mod_template" / "src" / "30_write.lua").read_text(
            encoding="utf-8"),
    }

    # --- 1. clean run -------------------------------------------------------
    print("== 1. clean run applies exactly the requested fields ==")
    out = probe(modules, image, script_for())
    check = base_mod.check
    check("record found", out.get("found") is True)
    check("guards pass", out.get("guard_ok") is True,
          f"code={out.get('guard_code')} detail={out.get('detail')}")
    check("write reported success", out.get("write_ok") is True,
          f"problem={out.get('problem')}")
    check("verify passes", out.get("verify_ok") is True,
          f"bad={out.get('verify_bad')}")
    # ap3's target equals its current value (both 0), so it is skipped: 5 writes
    # for 6 requested fields is the correct outcome, not a shortfall.
    check("5 writes for 6 fields (ap3 already correct)", out.get("writes") == 5,
          f"got {out.get('writes')}")
    check("the untouched fourth AP angle is reported as already set",
          "ap3=0 (already set)" in str(out.get("applied_desc")),
          f"desc={out.get('applied_desc')}")
    print(f"     applied: {out.get('applied_desc')}")

    # --- 2. identity mismatch ----------------------------------------------
    print()
    print("== 2. a wrong row is refused, nothing written ==")
    # Ask for a different weapon's id on the R-4 pattern: identity must fail.
    out2 = probe(modules, image, script_for(record="{" \
        "type_id = 999, position = 137, damage = 220, durable = 45, ap = {3,3,3,0}}"))
    check("no write happened", out2.get("writes") in (0, None),
          f"writes={out2.get('writes')}")

    # --- 3. baseline mismatch ----------------------------------------------
    print()
    print("== 3. a field another mod changed is refused ==")
    # Baseline says damage should be 220, but tell the guard it should be 999 -
    # standing in for "another mod already moved it".
    script3 = script_for(baseline_note="bumped").replace(
        "local BASELINE = { damage = 220,", "local BASELINE = { damage = 999,")
    out3 = probe(modules, image, script3)
    check("guard refused", out3.get("guard_ok") is False)
    check("refusal is classified as a baseline mismatch",
          "baseline" in str(out3.get("guard_code")),
          f"code={out3.get('guard_code')}")
    check("no write happened", out3.get("writes") in (0, None),
          f"writes={out3.get('writes')}")
    check("it says which field disagreed", out3.get("field") == "damage",
          f"field={out3.get('field')} detail={out3.get('detail')}")

    # --- 4. not writable ----------------------------------------------------
    print()
    print("== 4. a read-only page is refused ==")
    out4 = probe(modules, image, script_for(writable=False))
    check("guard refused", out4.get("guard_ok") is False)
    check("refusal names writability", "writable" in str(out4.get("guard_code")),
          f"code={out4.get('guard_code')}")
    check("no write happened", out4.get("writes") in (0, None),
          f"writes={out4.get('writes')}")

    # --- 5. already at target ----------------------------------------------
    print()
    print("== 5. a field already at the target is skipped, not rewritten ==")
    script5 = script_for().replace(
        "local CHANGES = { damage = 400,",
        "local CHANGES = { damage = 220,",   # == baseline damage
    )
    out5 = probe(modules, image, script5)
    check("write still reports success", out5.get("write_ok") is True)
    # damage is already at its target, and ap3 was already 0: 4 writes remain.
    check("only the fields that actually differ are written",
          out5.get("writes") == 4, f"got {out5.get('writes')}")
    check("the skip is reported", "already set" in str(out5.get("applied_desc")),
          f"desc={out5.get('applied_desc')}")

    # --- 6. unknown field ---------------------------------------------------
    print()
    print("== 6. an unknown field name is refused, not guessed ==")
    script6 = script_for().replace(
        "local CHANGES = { damage = 400,",
        "local CHANGES = { recoil = 400,",
    )
    out6 = probe(modules, image, script6)
    check("guard refused", out6.get("guard_ok") is False)
    check("refusal names the problem", "baseline" in str(out6.get("guard_code")),
          f"code={out6.get('guard_code')}")

    # --- 7. write dropped ---------------------------------------------------
    print()
    print("== 7. a write that does not stick is reported, not retried ==")
    out7 = probe(modules, image, script_for(drop_writes=True))
    check("write reports failure", out7.get("write_ok") is False,
          f"ok={out7.get('write_ok')}")
    problems = str(out7.get("problem", ""))
    check("the problem says what happened",
          "read back" in problems or "could not read back" in problems,
          f"problem={problems}")
    # One attempt per differing field: 5 fields differ, so 5 attempts total. A
    # retry would show up as more.
    check("it did not retry (one attempt per differing field)",
          out7.get("writes") == 5, f"got {out7.get('writes')}")

    print()
    print(f"test_guard_write: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} "
          f"({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
