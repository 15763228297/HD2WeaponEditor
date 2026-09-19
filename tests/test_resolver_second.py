"""Resolve a second, unrelated weapon against the simulated image.

A test that only ever locates the one record whose values were used to build the
pattern proves the search can find what it was told to look for, and nothing
more. This runs the same resolver on a weapon from a different part of the table
with different values, and on a row whose id is NOT equal to its position - the
case that exposed the earlier row-number-plus-minus-one assumption.

Run:  python tests/test_resolver_second.py
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

import parse_dlbin as pd  # noqa: E402
from test_resolver import IMAGE_BASE, check  # noqa: E402
import test_resolver as base_mod  # noqa: E402


def build_probe(resolver_src: str, image: bytes, targets: list[dict]) -> str:
    """One probe that resolves every target in `targets`."""
    lua_image = base_mod.lua_bytes(image)
    cases = []
    for t in targets:
        cases.append(
            "{"
            f"name={t['name']!r}, row={t['row']}, type_id={t['type_id']}, "
            f"damage={t['damage']}, durable={t['durable']}, "
            f"ap={{{t['ap'][0]},{t['ap'][1]},{t['ap'][2]},{t['ap'][3]}}}, "
            f"before_id={t['before_id']}, after_id={t['after_id']}, "
            f"next_damage={t['next_damage']}, next_durable={t['next_durable']}"
            "}"
        )
    # Python repr of a str uses single quotes; Lua accepts both, so fine.
    cases_lua = "{" + ",".join(cases) + "}"

    return f"""
local ffi = require("ffi")
local image = {lua_image}
local IMAGE_BASE = {IMAGE_BASE}
local len = #image
local out = {{}}

local api = {{
  MEM_COMMIT = 0x1000, PAGE_GUARD = 0x100, WRITABLE = 0x04,
  WRITABLE2 = 0x40, WRITABLE3 = 0x08,
}}
function api.read(address, size)
  local off = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  if off < 0 or off + size > len then return nil end
  local buf = ffi.new("uint8_t[?]", size)
  ffi.copy(buf, image:sub(off + 1, off + size), size)
  return buf
end
function api.writable_regions(_a, start, limit)
  local s = tonumber(ffi.cast("uintptr_t", start))
  local l = tonumber(ffi.cast("uintptr_t", limit))
  if s >= IMAGE_BASE + len or l <= IMAGE_BASE then return {{}} end
  return {{ {{ base = ffi.cast("uint8_t *", IMAGE_BASE), size = len }} }}
end

rawset(_G, "__resolver_api", api)
local R = (function()
  local s = [====[
{resolver_src}
]====]
  return assert(loadstring(s, "resolver"))()
end)()

local cases = {cases_lua}
local parts = {{}}
for _, c in ipairs(cases) do
  -- type_id is the raw +0 value; the row's position is implicit in the scan.
  local record = {{ type_id = c.type_id, position = c.row,
                    damage = c.damage, durable = c.durable, ap = c.ap }}
  local expect = {{ before_id = c.before_id, after_id = c.after_id,
                    next = {{ damage = c.next_damage, durable = c.next_durable }} }}
  local ok, address, near = R.find_record(api, IMAGE_BASE, record, expect)
  local got = "nil"
  if ok then got = tostring(tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE) end
  parts[#parts + 1] = c.name .. "=" .. tostring(ok) .. "," .. got
end
return table.concat(parts, ";")
"""


def main() -> int:
    meta = json.loads((ROOT / "data" / "sim_memory.json").read_text())
    image = (ROOT / "data" / "sim_memory.bin").read_bytes()
    array_base = meta["array_base"]

    blob = (ROOT / "data" / "raw" / "generated_damage_settings.dl_bin").read_bytes()
    start = pd.anchor_start(blob)
    recs = pd.parse_damage_records(blob, start)

    # Pick targets: a normal one, and rows where id != row so the neighbour pin
    # is doing real work rather than coinciding with position.
    candidates = [132, 200, 300, 500, 633, 0, 4]
    targets = []
    for row in candidates:
        r = recs[row]
        if row + 1 >= len(recs):
            continue
        targets.append({
            "name": f"row{row}",
            "row": row,
            "type_id": struct.unpack_from("<i", blob, start + row * 76)[0],
            "damage": r.damage,
            "durable": r.durable_damage,
            "ap": r.armor_penetration_per_angle,
            "before_id": struct.unpack_from(
                "<i", blob, start + (row - 1) * 76)[0] if row > 0 else None,
            "after_id": struct.unpack_from("<i", blob, start + (row + 1) * 76)[0],
            "next_damage": recs[row + 1].damage,
            "next_durable": recs[row + 1].durable_damage,
        })

    resolver_src = (ROOT / "mod_template" / "src" / "10_resolver.lua").read_text(
        encoding="utf-8"
    )
    probe_lua = build_probe(resolver_src, image, targets)
    raw = base_mod.run_probe(probe_lua)

    print("== resolving several unrelated weapons ==")
    results = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        name, val = part.split("=", 1)
        ok, _, offset = val.partition(",")
        results[name] = (ok == "true", offset)

    for t in targets:
        ok, offset = results.get(t["name"], (False, "nil"))
        want = array_base + t["row"] * 76
        note = "" if t["type_id"] == t["row"] else f" (id {t['type_id']} != row {t['row']})"
        check(f"row {t['row']} found at 0x{want:x}{note}",
              ok and offset == str(want),
              f"got ok={ok} offset={offset}")

    odd = [t for t in targets if t["type_id"] != t["row"]]
    print()
    print(f"  ({len(odd)} of {len(targets)} targets have id != row, so the "
          "neighbour pin is load-bearing)")

    print()
    print(f"test_resolver_second: "
          f"{'PASS' if not base_mod.failures else 'FAIL'} "
          f"({base_mod.checks} checks)")
    return 1 if base_mod.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
