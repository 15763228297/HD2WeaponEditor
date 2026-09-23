"""Tests for the .dl_bin parsers and the weapon mapping.

These run fully offline against the shipped data files. The point of the suite is
to pin the layout facts that were expensive to establish, so a future data
refresh fails loudly here instead of silently producing wrong numbers in the GUI.

Run: python tests/test_parse.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from parse_dlbin import (  # noqa: E402
    RECORD_SIZE,
    ANCHOR_INDEX,
    ANCHOR_TYPE_ID,
    ANCHOR_DAMAGE,
    ANCHOR_AP,
    ANCHOR_FORCES,
    anchor_start,
    parse_damage_settings_file,
)
from build_map import (  # noqa: E402
    PROJECTILE_RECORD_SIZE,
    assert_exclusive,
    build,
)

DATA = ROOT / "data" / "raw"
DMG = DATA / "generated_damage_settings.dl_bin"
PRJ = DATA / "generated_projectile_settings.dl_bin"

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} {detail}")
        failures.append(label)


def main() -> int:
    if not DMG.exists() or not PRJ.exists():
        print(f"missing data files under {DATA}; run the fetch step first")
        return 1

    dblob = DMG.read_bytes()
    pblob = PRJ.read_bytes()

    print("== record layout ==")
    check("damage record is 76 bytes", RECORD_SIZE == 76, f"got {RECORD_SIZE}")

    start = anchor_start(dblob)
    check(
        "array start divides the file exactly",
        (len(dblob) - start) % RECORD_SIZE == 0,
        f"start={start:#x} len={len(dblob)}",
    )

    print("== anchor row (R-4 Hyena) ==")
    recs = parse_damage_settings_file(DMG)
    check("parsed a plausible record count", len(recs) > 500, f"got {len(recs)}")
    # ANCHOR_INDEX/ANCHOR_DAMAGE/ANCHOR_AP come from data/weapon_names.json when
    # it is present, so this follows the game's renumbering instead of pinning
    # the values R-4 had in one build.
    r4 = recs[ANCHOR_INDEX]
    check(
        f"anchor damage == {ANCHOR_DAMAGE}",
        (r4.damage, r4.durable_damage) == ANCHOR_DAMAGE,
        f"got {r4.damage}/{r4.durable_damage}",
    )
    check(
        f"anchor armor penetration == {ANCHOR_AP}",
        r4.armor_penetration_per_angle == ANCHOR_AP,
        f"got {r4.armor_penetration_per_angle}",
    )
    check(
        f"anchor forces == {ANCHOR_FORCES}",
        (r4.demolition_strength, r4.force_strength, r4.force_impulse) == ANCHOR_FORCES,
        f"got {(r4.demolition_strength, r4.force_strength, r4.force_impulse)}",
    )
    check(
        f"anchor row carries type id {ANCHOR_TYPE_ID}",
        r4.type_id == ANCHOR_TYPE_ID,
        f"got {r4.type_id}",
    )

    print("== the array start is structural, not anchor-derived ==")
    # This used to assert that corrupting the anchor row made `anchor_start`
    # raise. That is no longer the behaviour on purpose: the start comes from
    # the container's own DLArray descriptor, so it does not care what any row
    # holds. Gating on a balance-dependent value is what broke the editor on the
    # 1.8.45850 update, so the property to assert is the opposite one - the
    # parse must survive a row being edited.
    mutated = bytearray(dblob)
    off = start + ANCHOR_INDEX * RECORD_SIZE
    struct.pack_into("<i", mutated, off + 4, 9999)
    try:
        again = anchor_start(bytes(mutated))
        check("a corrupted row does not move the array start", again == start,
              f"start moved from {start} to {again}")
    except ValueError as exc:
        check("a corrupted row does not move the array start", False, str(exc))

    print("== projectile table ==")
    damages, projectiles = build(DMG, PRJ)
    check("projectile count is 350", len(projectiles) == 350, f"got {len(projectiles)}")

    print("== R-4 exclusivity (the 'only my weapon' guarantee) ==")
    row = assert_exclusive(projectiles, ANCHOR_INDEX)
    check(f"exactly one projectile uses damage {ANCHOR_INDEX}", row == 248,
          f"got row {row}")
    p = projectiles[row]
    check("R-4 projectile speed == 950", p.speed == 950.0, f"got {p.speed}")
    check("R-4 projectile mass == 20", p.mass == 20.0, f"got {p.mass}")
    check("R-4 projectile calibre == 9", p.calibre == 9.0, f"got {p.calibre}")

    print("== neighbouring family rows must NOT be the same row ==")
    # The 9x70mm family sits on adjacent damage rows. If a future build merges
    # them, editing "R-4" would silently change other marksman rifles. The rows
    # are taken relative to R-4's own position so this follows a renumbering.
    base = ANCHOR_INDEX
    fam = {}
    for pr in projectiles:
        if pr.damage_position in range(base - 2, base + 3):
            fam[pr.damage_position] = (pr.speed, pr.mass)
    check("family rows are distinct entries", len(fam) >= 3, f"got {sorted(fam)}")
    check(
        "R-4 speed differs from its neighbours",
        fam.get(base, (None,))[0] not in (fam.get(base - 1, (None,))[0],
                                          fam.get(base + 1, (None,))[0]),
        f"{base}={fam.get(base)} {base-1}={fam.get(base-1)} {base+1}={fam.get(base+1)}",
    )

    print("== a shared row must be refused ==")
    # Build the duplicate from a real row and change only what matters, rather
    # than re-listing every field: Projectile gained fields over time and a
    # hardcoded constructor call here broke each time it did.
    import dataclasses
    fake = list(projectiles) + [
        dataclasses.replace(
            projectiles[row],
            row=999,
            sequence=999,
            damage_type=ANCHOR_INDEX,
            damage_position=ANCHOR_INDEX,
        )
    ]
    try:
        assert_exclusive(fake, ANCHOR_INDEX)
        check("shared row raises", False, "no exception")
    except ValueError:
        check("shared row raises", True)

    print()
    print(f"test_parse: {'PASS' if not failures else 'FAIL'} ({checks} checks)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
