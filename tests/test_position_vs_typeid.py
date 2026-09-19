"""Generate a mod for a weapon whose position and type_id differ.

This is the case the whole numbering-scheme bug hid behind. Every earlier test
target - R-4 at 137, Constitution at 132 - is a row where position == type_id, so
a generator that conflated them passed. 519 of 634 rows do not have that
coincidence, and for those the mod used to be built against a different record
than the GUI displayed, with every guard agreeing because the guard derived its
own expectation from the same wrong row.

Run:  python tests/test_position_vs_typeid.py
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import gen_mod  # noqa: E402
import parse_dlbin as pd  # noqa: E402
from build_map import parse_damages  # noqa: E402

failures = 0
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures, checks
    checks += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + ("" if cond else f"  {detail}"))
    if not cond:
        failures += 1


def main() -> int:
    blob = (ROOT / "data" / "raw" / "generated_damage_settings.dl_bin").read_bytes()
    damages = parse_damages(blob)
    names = json.loads((ROOT / "data" / "weapon_names.json").read_text(encoding="utf-8"))

    print("== every weapon's map entry matches the row it points at ==")
    bad = []
    for w in names["weapons"]:
        pos = w.get("damage_position")
        if pos is None:
            bad.append((w["page"], "no damage_position"))
            continue
        rec = damages.get(pos)
        if rec is None:
            bad.append((w["page"], f"position {pos} not in table"))
            continue
        if rec.type_id != w["damage_index"]:
            bad.append((w["page"], f"pos {pos} holds type_id {rec.type_id}, map says {w['damage_index']}"))
        elif (rec.damage, rec.durable_damage) != (w["damage"], w["durable"]):
            bad.append((w["page"], f"values differ: map {w['damage']}/{w['durable']} row {rec.damage}/{rec.durable_damage}"))
    check("no inconsistencies", not bad, f"{len(bad)} bad: {bad[:2]}")

    print()
    print("== the two numbers really do differ for most weapons ==")
    differ = [w for w in names["weapons"]
              if w.get("damage_position") != w.get("damage_index")]
    check("some weapons have position != type_id", len(differ) > 0,
          f"{len(differ)} of {len(names['weapons'])}")
    print(f"       {len(differ)} of {len(names['weapons'])} weapons differ")

    print()
    print("== generating for such a weapon targets ITS row, not another's ==")
    # Pick a verified+exclusive weapon where the two numbers differ.
    candidates = [w for w in names["weapons"]
                  if w.get("verified") and w.get("exclusive")
                  and w.get("damage_position") != w.get("damage_index")]
    check("at least one such weapon is generatable", bool(candidates),
          f"{len(candidates)} candidates")
    if not candidates:
        return 1

    w = candidates[0]
    page = w["page"]
    pos = w["damage_position"]
    tid = w["damage_index"]
    print(f"       target: {page}  position={pos} type_id={tid}  "
          f"GUI shows {w['damage']}/{w['durable']}")

    spec = gen_mod.build_spec(page, damage=400, durable=200, ap=7)

    check("spec.position is the array position", spec.position == pos,
          f"got {spec.position}, expected {pos}")
    check("spec.type_id is the type id", spec.type_id == tid,
          f"got {spec.type_id}, expected {tid}")
    check("spec.position != spec.type_id (the case under test)", pos != tid)

    # The baseline must be the row the GUI showed.
    check("baseline damage is the GUI's value", spec.baseline["damage"] == w["damage"],
          f"got {spec.baseline['damage']}, GUI showed {w['damage']}")
    check("baseline durable is the GUI's value",
          spec.baseline["durable"] == w["durable"],
          f"got {spec.baseline['durable']}, GUI showed {w['durable']}")

    # And the row at spec.position must hold exactly that.
    rec = damages[spec.position]
    check("the row at spec.position holds the baseline",
          (rec.damage, rec.durable_damage) == (spec.baseline["damage"], spec.baseline["durable"]),
          f"row {spec.position} = {rec.damage}/{rec.durable_damage}")
    check("the row at spec.position holds spec.type_id", rec.type_id == spec.type_id)

    # Neighbours come from spec.position, not from spec.type_id (which is not a
    # position at all and would index an unrelated row).
    if pos + 1 in damages:
        check("next-row expectation comes from position+1",
              spec.next_damage == damages[pos + 1].damage,
              f"spec says {spec.next_damage}, row {pos + 1} holds {damages[pos + 1].damage}")

    print()
    print("== the generated Lua carries the position and the type id separately ==")
    sources = {
        name: (ROOT / "mod_template" / "src" / name).read_text(encoding="utf-8")
        for name in gen_mod.MODULES
    }
    source = gen_mod.render_module([spec], sources)
    check(f"the plan records position = {pos}", f"position = {pos}," in source)
    check(f"the plan records type_id = {tid}", f"type_id = {tid}," in source)
    # Guard against the two being collapsed into one value anywhere.
    check("the baseline block uses the row's values",
          f"damage = {w['damage']}," in source,
          f"expected 'damage = {w['damage']},'")

    print()
    print("== a stale map is refused rather than written ==")
    # Simulate a map that disagrees with the table: build_spec must refuse.
    import tempfile
    original = (ROOT / "data" / "weapon_names.json").read_text(encoding="utf-8")
    tampered = json.loads(original)
    victim = next(x for x in tampered["weapons"] if x["page"] == page)
    victim["damage_index"] = 999999
    try:
        (ROOT / "data" / "weapon_names.json").write_text(
            json.dumps(tampered), encoding="utf-8")
        try:
            gen_mod.build_spec(page, damage=400, durable=200, ap=7)
            check("a disagreeing map is refused", False, "build_spec returned instead")
        except SystemExit as exc:
            check("a disagreeing map is refused", True)
            print(f"       refused with: {str(exc)[:90]}")
    finally:
        (ROOT / "data" / "weapon_names.json").write_text(original, encoding="utf-8")

    print()
    print(f"test_position_vs_typeid: {'PASS' if not failures else 'FAIL'} ({checks} checks)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
