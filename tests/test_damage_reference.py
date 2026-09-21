"""The projectile's damage reference is an ID, and must be resolved to a row.

`generated_projectile_settings` stores a damage reference at +60. The field is
named `damage_info_type`, and it holds a damage-type ID - the same value the
damage table keeps at each row's +0 - not an array index.

The damage table has two numbering schemes and they disagree on most rows: ids
run 4..639, positions 0..633, and 519 of 634 rows have id != position. Reading
the id as an index therefore selects whichever row happens to sit at that
offset, i.e. a different weapon's damage.

This shipped. It survived because the weapon the project was built around, R-4
Hyena, is one of the rows where the two numbers coincide (both 137) - so the
in-game test that confirmed the tool "works" could not distinguish the two
readings. The user who reported it pointed at EAT-17, whose direct hit came out
as 525 instead of 2000.

What is asserted here:

  1. resolution happens, and a raw id is never silently used as an index;
  2. the resolved row is the one the wiki documents, on weapons where the two
     readings differ (the only informative cases);
  3. an id that no row carries is reported rather than mapped to a wrong row;
  4. the flame-weapon sentinels are not treated as row numbers.

Run:  python tests/test_damage_reference.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_map  # noqa: E402

DATA = ROOT / "data"

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


def load_rows() -> list[dict]:
    raw = json.loads((DATA / "damage_records.json").read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else raw.get("records", raw)
    if isinstance(rows, dict):
        rows = [rows[k] for k in sorted(rows, key=int)]
    return rows


def load_weapons() -> list[dict]:
    raw = json.loads((DATA / "weapon_names.json").read_text(encoding="utf-8"))
    ws = raw if isinstance(raw, list) else raw.get("weapons", raw)
    if isinstance(ws, dict):
        ws = list(ws.values())
    return ws


def main() -> int:
    print("test_damage_reference: +60 is a damage-type id, not a row index")

    rows = load_rows()
    dblob = (DATA / "raw" / "generated_damage_settings.dl_bin").read_bytes()
    pblob = (DATA / "raw" / "generated_projectile_settings.dl_bin").read_bytes()
    damages = build_map.parse_damages(dblob)

    # -- 1. parsing alone must NOT produce a usable row index ----------------
    projectiles = build_map.parse_projectiles(pblob)
    unresolved = [p for p in projectiles if p.damage_position is None]
    check("parse_projectiles leaves the row unresolved",
          len(unresolved) == len(projectiles),
          f"{len(projectiles) - len(unresolved)} of {len(projectiles)} were "
          f"resolved before the damage table existed")
    check("the raw id is still available",
          all(isinstance(p.damage_type, int) for p in projectiles))

    # -- 2. the two numbering schemes really do differ -----------------------
    # If this ever stops being true the whole test loses its power, so it is
    # asserted rather than assumed.
    differing = sum(1 for i, r in enumerate(rows) if r["type_id"] != i)
    check("the two numbering schemes differ on most rows",
          differing > len(rows) // 2,
          f"only {differing} of {len(rows)} rows differ - the test would be "
          f"blind on a table where id == position")

    # -- 3. resolution maps the id to the row that carries it ---------------
    notes = build_map.resolve_damage_positions(projectiles, damages)
    by_type = {r["type_id"]: i for i, r in enumerate(rows)}
    wrong = [
        p for p in projectiles
        if p.damage_position is not None
        and rows[p.damage_position]["type_id"] != p.damage_type
    ]
    check("every resolved row carries the id that pointed at it",
          not wrong,
          f"{len(wrong)} rows hold a different id, e.g. projectile "
          f"{wrong[0].row} -> row {wrong[0].damage_position}"
          if wrong else "")
    check("the id is what was matched, not the number itself",
          all(p.damage_position == by_type[p.damage_type]
              for p in projectiles if p.damage_position is not None))

    # -- 4. the wiki must agree, on the rows where the readings differ -------
    # This is the assertion that would have caught the original bug: R-4 is a
    # row where both readings agree, so only the other weapons can tell them
    # apart.
    #
    # In practice the discriminating weapons are exactly the EXPLOSIVE ones:
    # for a non-explosive weapon the stored `impact_damage_position` is the
    # resolved row itself, so `index` and `position` coincide and the row tells
    # us nothing. An explosive weapon goes through the explosion conversion, so
    # its impact segment keeps both numbers and they differ - 18 weapons, and
    # EAT-17 (the one the user reported) is among them.
    weapons = load_weapons()
    informative = 0
    agree = 0
    disagree = []
    for w in weapons:
        if not w.get("verified"):
            continue
        pos = w.get("impact_damage_position")
        want = (w.get("wiki") or {}).get("standard")
        if pos is None or want is None:
            continue
        if pos == w.get("impact_damage_index"):
            continue                      # uninformative: both readings agree
        informative += 1
        got = rows[pos]["damage"]
        if got == want:
            agree += 1
        else:
            disagree.append((w.get("page"), got, want))

    check("there are weapons where the two readings differ",
          informative > 0, f"found {informative}")
    # EAT-17 is the reported case; assert it is in the checked set so this test
    # cannot quietly stop covering the weapon that exposed the bug.
    check("the reported weapon is among the checked ones",
          any(w.get("page", "").startswith("EAT-17 Expendable")
              for w in weapons
              if w.get("impact_damage_position") != w.get("impact_damage_index")),
          "EAT-17 is no longer discriminating - the check has gone blind")

    # Known exceptions, listed explicitly rather than tolerated by a threshold.
    #
    # These four are a DIFFERENT defect: the matcher picked the wrong projectile
    # row for them (it fell back to matching the explosion and did not constrain
    # velocity), so the impact segment belongs to another weapon entirely. That
    # is not the id/position conversion this test is about, and letting it fail
    # here would hide whether the conversion itself is right.
    #
    # Named individually so a NEW disagreement fails the suite - a bare count
    # would silently absorb one.
    KNOWN_WRONG_PROJECTILE = {
        "AR/GL-21 One-Two",
        "GL-21 Grenade Launcher",
        "GP-31 Grenade Pistol",
        # GR-8 shares projectile row 91 with EAT-17 (its wiki lists 250 m/s,
        # the row is 200 m/s), so its impact segment is EAT-17's row. Same
        # wrong-projectile defect, different weapon.
        "GR-8 Recoilless Rifle",
    }
    unexpected = [d for d in disagree if d[0] not in KNOWN_WRONG_PROJECTILE]
    check("no NEW weapon disagrees with the wiki",
          not unexpected,
          f"{unexpected[:3]}")
    print(f"       ({agree} of {informative} informative weapon(s) agree; "
          f"{len(disagree)} known-wrong-projectile exception(s) excluded)")

    # -- 5. an id no row carries must be reported, not guessed at ------------
    check("unresolvable ids are reported",
          any("no row" in n for n in notes) or not notes,
          "silently dropped")

    # -- 6. the flame sentinels are not row numbers --------------------------
    sentinels = [p for p in projectiles if p.damage_type in build_map.PROJECTILE_STATUS_SENTINELS]
    check("flame-weapon status references are recognised",
          len(sentinels) > 0, "none found - the sentinel set may be stale")
    check("they are not mapped to a damage row",
          all(p.damage_position is None for p in sentinels),
          "a status reference was used as a row number")
    check("they are reported as status references",
          any("status reference" in n for n in notes))

    # -- 7. EAT-17 specifically: the case the user reported ------------------
    eat = next((w for w in weapons
                if w.get("page", "").startswith("EAT-17 Expendable")), None)
    if eat is not None:
        pos = eat.get("impact_damage_position")
        check("EAT-17's direct hit resolves to the 2000 row",
              pos is not None and rows[pos]["damage"] == 2000,
              f"row {pos} holds {rows[pos]['damage'] if pos is not None else '—'}")

    print(f"\n{checks - failures}/{checks} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
