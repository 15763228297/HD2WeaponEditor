"""ARRAY_START must equal the offset the table's own container declares.

Why this test exists, and why it is separate from test_static_chain:

test_static_chain builds a synthetic memory image and checks the chain's
arithmetic - given some ARRAY_START, does it reach `array + position * 76`, and
does it refuse a stale route. It reads the constant from the module so the two
stay consistent, which means it cannot catch a *wrong* constant: change the
module to 0x1e0 and the test reads 0x1e0 too and still passes.

The value itself is a fact about the game's data, not about our code, so it has
to be pinned against an outside source. That source is the decrypted table: the
DLArray descriptor at the payload start holds the distance to the record array,
and the array must land exactly where the container says.

This is the assertion that would have caught the bug that shipped in the
1.8.45850 update, where ARRAY_START was 380 bytes too far and every edit would
have landed five rows past its target.

Run:  python tests/test_array_start_matches_container.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dlbin_tables  # noqa: E402

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


def module_constant(path: Path, name: str) -> int | None:
    """Read `M.<name> = <literal>` out of a Lua module."""
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(rf"^M\.{name}\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*$", line)
        if m:
            return int(m.group(1), 0)
    return None


def container_array_offset(blob: bytes, type_hash: int, stride: int) -> tuple[int, int]:
    """(array_offset_from_blob_start, count) per the DLArray descriptor."""
    _magic, start, end = dlbin_tables.find_block(blob, type_hash)
    arr, count = dlbin_tables.dl_array(blob, start, stride, limit=end)
    return arr, count


def main() -> int:
    print("== the value the mods ship ==")
    sources = {
        "tools/gen_mod.py": None,
        "mod_template/src/10_resolver.lua": None,
        "mod_template/src/15_static_route.lua": None,
        "mod_template/src/16_static_chain.lua": None,
    }
    for rel in sources:
        p = ROOT / rel
        if rel.endswith(".py"):
            m = re.search(r"^ARRAY_START = (0x[0-9a-fA-F]+|\d+)", p.read_text(encoding="utf-8"), re.M)
            v = int(m.group(1), 0) if m else None
        else:
            v = module_constant(p, "ARRAY_START")
        sources[rel] = v
        check(f"{rel} declares ARRAY_START", v is not None, "no declaration found")

    values = {v for v in sources.values() if v is not None}
    check("every copy agrees on one value", len(values) == 1,
          f"found {sorted(hex(v) for v in values)}")
    declared = values.pop() if len(values) == 1 else None

    print()
    print("== what the game's table says ==")
    # ARRAY_START describes the DAMAGE table only: it is the offset folded into
    # a route when addressing a damage record, and the projectile/explosion
    # tables are reached by their own paths (their own descriptors sit at a
    # different distance from their blob start - 44, not 100). Comparing
    # against them would be comparing the wrong thing, so only the damage
    # table is checked here.
    tables = [
        ("damage", dlbin_tables.TYPE_DAMAGE, dlbin_tables.DAMAGE_RECORD_SIZE),
    ]
    offsets = {}
    for name, thash, stride in tables:
        p = ROOT / "data" / "raw" / f"generated_{name}_settings.dl_bin"
        if not p.exists():
            print(f"  [SKIP] {name}: {p.name} not present")
            continue
        arr, count = container_array_offset(p.read_bytes(), thash, stride)
        offsets[name] = arr
        print(f"  {name:11s} array at {arr}  ({count} rows x {stride})")

    # Report the other tables' offsets for context without asserting on them -
    # a future reader comparing the numbers deserves to know why they differ.
    for name, thash, stride in (
        ("projectile", dlbin_tables.TYPE_PROJECTILE, dlbin_tables.PROJECTILE_RECORD_SIZE),
        ("explosion", dlbin_tables.TYPE_EXPLOSION, dlbin_tables.EXPLOSION_RECORD_SIZE),
    ):
        p = ROOT / "data" / "raw" / f"generated_{name}_settings.dl_bin"
        if p.exists():
            arr, count = container_array_offset(p.read_bytes(), thash, stride)
            print(f"  ({name} table: array at {arr} - not covered by ARRAY_START)")

    if not offsets:
        print()
        print("no damage table present; cannot verify")
        return 2

    print()
    print("== the comparison ==")
    if declared is None:
        check("ARRAY_START matches the container", False, "no single declared value")
    else:
        for name, arr in offsets.items():
            check(f"ARRAY_START == {name} table's array offset",
                  declared == arr, f"declared {declared}, container says {arr}")

    print()
    print("== the record address a weapon would get ==")
    # End-to-end: take a real weapon from the map, compute its record offset the
    # way the generator does, and confirm that offset lands on a row whose values
    # match what the map promised. This is the check that fails when the constant
    # and the row numbering disagree - exactly the 1.8.45850 failure mode.
    import json
    wn_path = ROOT / "data" / "weapon_names.json"
    rec_path = ROOT / "data" / "damage_records.json"
    if wn_path.exists() and rec_path.exists() and declared is not None:
        wn = json.loads(wn_path.read_text(encoding="utf-8"))
        records = json.loads(rec_path.read_text(encoding="utf-8"))
        by_index = {r["index"]: r for r in records}
        # Two weapons with different positions, so a constant that is wrong by a
        # whole number of rows cannot coincidentally agree for one of them.
        sample = [w for w in wn["weapons"] if w.get("payload") == "projectile"][:2]
        for w in sample:
            pos = w.get("damage_position")
            if pos is None:
                continue
            # The record the generator will address.
            addressed = declared + pos * dlbin_tables.DAMAGE_RECORD_SIZE
            # Which row does that offset correspond to, if the array starts at
            # the container's own offset?
            arr = offsets.get("damage", 100)
            row = (addressed - arr) // dlbin_tables.DAMAGE_RECORD_SIZE
            check(f"{w['page']}: offset lands on the mapped row",
                  row == pos, f"addresses row {row}, map says {pos}")
            rec = by_index.get(pos)
            if rec:
                check(f"{w['page']}: that row holds the mapped values",
                      (rec["damage"], rec["durable_damage"]) == (w["damage"], w["durable"]),
                      f"row has {rec['damage']}/{rec['durable_damage']}, "
                      f"map says {w['damage']}/{w['durable']}")
    else:
        print("  [SKIP] weapon map or damage records not present")

    print()
    if failures == 0:
        print(f"test_array_start_matches_container: PASS ({checks} checks)")
        return 0
    print(f"test_array_start_matches_container: FAIL ({failures} of {checks} failed)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
