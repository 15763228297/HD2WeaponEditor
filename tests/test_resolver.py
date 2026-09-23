"""Offline test of 10_resolver.lua against a simulated process image.

Why a simulated image: the resolver locates the damage-record array in live
process memory, and the failure it must avoid is writing to a wrong address. It
cannot be iterated on against the running game. Here the shipped Lua file runs
unchanged; only its two seams are answered from a buffer built out of the real
parsed table:

  * `api.read(address, size)`                 - copies from the image
  * `api.writable_regions(api, start, limit)` - one region covering the image

The image is passed into Lua as a string and the two seams are written in plain
Lua. An earlier version of this test routed reads through a ctypes callback so
the bytes crossed the C boundary; that exercised Python's FFI plumbing rather
than the resolver, and when it reported "not found" it was the plumbing at fault,
not the code under test. Going through Lua only keeps the test pointed at the
logic that ships.

Run:  python tests/test_resolver.py
"""

from __future__ import annotations

import ctypes
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))

from ljcompile import LuaJIT  # noqa: E402

import parse_dlbin as pd  # noqa: E402
import r4_fixture  # noqa: E402

IMAGE_BASE = 0x10000000

failures = 0
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures, checks
    checks += 1
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} {detail}")
        failures += 1


def lua_bytes(data: bytes) -> str:
    """A Lua string literal holding `data`, as decimal escapes.

    Chunked per line: one 60 KB literal on a single line makes a 250 KB source
    line, which is slow to parse and awkward in any error message.
    """
    out = []
    for i in range(0, len(data), 64):
        out.append("".join(f"\\{b:03d}" for b in data[i : i + 64]))
    return '"' + "".join(out) + '"'


def run_probe(probe_lua: str) -> str:
    """Execute `probe_lua` and return its single string result."""
    lua = LuaJIT()
    lua.__enter__()
    lib, state = lua.lib, lua.state
    lib.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                    ctypes.c_size_t, ctypes.c_char_p]
    lib.luaL_loadbuffer.restype = ctypes.c_int
    lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int]
    lib.lua_pcall.restype = ctypes.c_int
    lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                  ctypes.POINTER(ctypes.c_size_t)]
    lib.lua_tolstring.restype = ctypes.c_char_p
    lib.lua_settop.argtypes = [ctypes.c_void_p, ctypes.c_int]
    try:
        status = lib.luaL_loadbuffer(state, probe_lua.encode(), len(probe_lua),
                                     b"=probe")
        if status != 0:
            size = ctypes.c_size_t()
            raw = lib.lua_tolstring(state, -1, ctypes.byref(size))
            raise SyntaxError(raw.decode() if raw else "load failed")
        if lib.lua_pcall(state, 0, 1, 0) != 0:
            size = ctypes.c_size_t()
            raw = lib.lua_tolstring(state, -1, ctypes.byref(size))
            raise RuntimeError(raw.decode() if raw else "run failed")
        size = ctypes.c_size_t()
        raw = lib.lua_tolstring(state, -1, ctypes.byref(size))
        return raw.decode() if raw else ""
    finally:
        lua.__exit__(None, None, None)


def probe(resolver_src: str, image: bytes, extra: str) -> dict:
    """Run the resolver against the image; `extra` runs with `R` = resolver."""
    lua_image = lua_bytes(image)
    source = f"""
local ffi = require("ffi")
local image = {lua_image}
local IMAGE_BASE = {IMAGE_BASE}
local len = #image
local out = {{}}
local reads, bytes_read = 0, 0

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
  bytes_read = bytes_read + size
  return buf
end
function api.writable_regions(_a, start, limit)
  local s = tonumber(ffi.cast("uintptr_t", start))
  local l = tonumber(ffi.cast("uintptr_t", limit))
  if s >= IMAGE_BASE + len or l <= IMAGE_BASE then return {{}} end
  return {{ {{ base = ffi.cast("uint8_t *", IMAGE_BASE), size = len }} }}
end

-- Publish before loading: the resolver's bind() runs at load time.
rawset(_G, "__resolver_api", api)
local R = (function()
  local s = [====[
{resolver_src}
]====]
  return assert(loadstring(s, "resolver"))()
end)()

{extra}

out.reads = reads
out.bytes_read = bytes_read
local parts = {{}}
for _, k in ipairs({{"found","offset","before_id","after_id","after_damage","reason",
                     "wrong_neighbours_found","absent_found","wrong_next_found"}}) do
  parts[#parts + 1] = k .. "=" .. tostring(out[k])
end
parts[#parts + 1] = "reads=" .. reads
parts[#parts + 1] = "bytes=" .. bytes_read
parts[#parts + 1] = "maxscan=" .. tostring(R.MAX_SCAN_BYTES)
return table.concat(parts, ";")
"""

    raw = run_probe(source)
    result: dict = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if v in ("true", "false"):
            result[k] = v == "true"
        elif v == "nil":
            result[k] = None
        else:
            try:
                result[k] = float(v) if "." in v else int(v)
            except ValueError:
                result[k] = v
    return result


EXTRACT = r"""
-- R-4's row, taken from the shipped data rather than written out here. Its
-- position and id move with every balance patch (137/137 in 1.8.45317,
-- 147/142 in 1.8.45850), and a stale copy makes this test look for a row the
-- mod never writes. See tests/r4_fixture.py.
local record = RECORD_PLACEHOLDER
local expect = EXPECT_PLACEHOLDER
local ok, address, near = R.find_record(api, IMAGE_BASE, record, expect)
out.found = ok and true or false
if ok then
  out.offset = tonumber(ffi.cast("uintptr_t", address)) - IMAGE_BASE
  out.before_id = near.before.index
  out.after_id = near.after.index
  out.after_damage = near.after.damage
else
  out.reason = tostring(address)
end
local ok2 = R.find_record(api, IMAGE_BASE, record,
  { before_id = 999, after_id = 999, next = { damage = NEXT_DAMAGE_PLACEHOLDER, durable = NEXT_DURABLE_PLACEHOLDER } })
out.wrong_neighbours_found = ok2 and true or false
local ok3 = R.find_record(api, IMAGE_BASE,
  { type_id = RECORD_ID_PLACEHOLDER, position = RECORD_POS_PLACEHOLDER, damage = 99999, durable = 1, ap = {9,9,9,9} }, expect)
out.absent_found = ok3 and true or false
local ok4 = R.find_record(api, IMAGE_BASE, record,
  { before_id = BEFORE_ID_PLACEHOLDER, after_id = AFTER_ID_PLACEHOLDER, next = { damage = 11111, durable = 22222 } })
out.wrong_next_found = ok4 and true or false
"""


def main() -> int:
    sim_path = ROOT / "data" / "sim_memory.bin"
    meta_path = ROOT / "data" / "sim_memory.json"
    if not sim_path.exists() or not meta_path.exists():
        print("simulated image missing - run: python tests/make_sim_memory.py")
        return 2

    meta = json.loads(meta_path.read_text())
    image = sim_path.read_bytes()
    base = meta["array_base"]

    # R-4's row as the shipped data describes it, so this test and the mod agree
    # on which row is under test even after a rebalance.
    row = r4_fixture.r4_row()
    pos, tid = row["position"], row["type_id"]

    resolver_src = (ROOT / "mod_template" / "src" / "10_resolver.lua").read_text(
        encoding="utf-8"
    )

    print("== the simulated image holds the real table ==")
    rec = image[base + pos * 76 : base + (pos + 1) * 76]
    idx, dmg, dur = struct.unpack_from("<iii", rec, 0)
    ap = list(struct.unpack_from("<4I", rec, 12))
    check(f"record {pos} is {row['damage']}/{row['durable']} AP{row['ap']}",
          (idx, dmg, dur) == (tid, row["damage"], row["durable"]) and ap == row["ap"],
          f"idx={idx} {dmg}/{dur} ap={ap}")

    print()
    print("== driving the shipped resolver against the simulated process ==")
    extract = (EXTRACT
               .replace("RECORD_PLACEHOLDER", r4_fixture.lua_record(row))
               .replace("EXPECT_PLACEHOLDER", r4_fixture.lua_expect(row))
               .replace("RECORD_ID_PLACEHOLDER", str(tid))
               .replace("RECORD_POS_PLACEHOLDER", str(pos))
               .replace("NEXT_DAMAGE_PLACEHOLDER", str(row["next_damage"]))
               .replace("NEXT_DURABLE_PLACEHOLDER", str(row["next_durable"]))
               .replace("BEFORE_ID_PLACEHOLDER", str(row["before_id"]))
               .replace("AFTER_ID_PLACEHOLDER", str(row["after_id"])))
    out = probe(resolver_src, image, extract)

    check("resolver found the record", out.get("found") is True,
          f"reason={out.get('reason')}")
    expected = base + pos * 76
    check(f"at the correct offset (0x{expected:x})",
          out.get("offset") == expected, f"got {out.get('offset')}")
    check(f"neighbour before has id {row['before_id']}",
          out.get("before_id") == row["before_id"], f"got {out.get('before_id')}")
    check(f"neighbour after has id {row['after_id']}",
          out.get("after_id") == row["after_id"], f"got {out.get('after_id')}")
    check(f"neighbour after reads {row['next_damage']}",
          out.get("after_damage") == row["next_damage"],
          f"got {out.get('after_damage')}")

    print()
    print("== the guards reject wrong hits (the point of the exercise) ==")
    check("wrong neighbour ids are rejected",
          out.get("wrong_neighbours_found") is False)
    check("a record not in the table is not found",
          out.get("absent_found") is False)
    check("a contradicting next row is rejected",
          out.get("wrong_next_found") is False)

    print()
    print("== scan cost ==")
    print(f"  reads {out.get('reads')}, bytes {int(out.get('bytes') or 0):,}")
    check("scan stayed bounded", int(out.get("bytes") or 0) < 4_000_000,
          f"{out.get('bytes')} bytes")

    print()
    print(f"test_resolver: {'PASS' if not failures else 'FAIL'} ({checks} checks)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
