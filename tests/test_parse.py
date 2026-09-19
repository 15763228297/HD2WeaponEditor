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
    r4 = recs[ANCHOR_INDEX]
    check(
        f"anchor damage == {ANCHOR_DAMAGE}",
        (r4.damage, r4.durable_damage) == ANCHOR_DAMAGE,
        f"got {r4.damage}/{r4.durable_damage}",
    )
    check(
        "anchor armor penetration == [3,3,3,0]",
        r4.armor_penetration_per_angle == ANCHOR_AP,
        f"got {r4.armor_penetration_per_angle}",
    )
    check(
        "anchor forces == (10,20,14)",
        (r4.demolition_strength, r4.force_strength, r4.force_impulse) == ANCHOR_FORCES,
        f"got {(r4.demolition_strength, r4.force_strength, r4.force_impulse)}",
    )

    print("== anchor mismatch must fail, not silently drift ==")
    mutated = bytearray(dblob)
    off = start + ANCHOR_INDEX * RECORD_SIZE
    struct.pack_into("<i", mutated, off + 4, 9999)
    try:
        anchor_start(bytes(mutated))
        check("mutated anchor raises", False, "no exception")
    except ValueError:
        check("mutated anchor raises", True)

    print("== projectile table ==")
    damages, projectiles = build(DMG, PRJ)
    check("projectile count is 343", len(projectiles) == 343, f"got {len(projectiles)}")

    print("== R-4 exclusivity (the 'only my weapon' guarantee) ==")
    row = assert_exclusive(projectiles, ANCHOR_INDEX)
    check("exactly one projectile uses damage 137", row == 245, f"got row {row}")
    p = projectiles[row]
    check("R-4 projectile speed == 950", p.speed == 950.0, f"got {p.speed}")
    check("R-4 projectile mass == 20", p.mass == 20.0, f"got {p.mass}")
    check("R-4 projectile calibre == 9", p.calibre == 9.0, f"got {p.calibre}")

    print("== neighbouring family rows must NOT be the same row ==")
    # fmj (136) and hv (138) sit adjacent to R-4's 137. If a future build merges
    # them, editing "R-4" would silently change other marksman rifles.
    fam = {}
    for pr in projectiles:
        if pr.damage_position in (136, 137, 138, 139, 140):
            fam[pr.damage_position] = (pr.speed, pr.mass)
    check("family rows are distinct entries", len(fam) >= 4, f"got {sorted(fam)}")
    check(
        "R-4 speed differs from fmj/hv",
        fam.get(137, (None,))[0] not in (fam.get(136, (None,))[0], fam.get(138, (None,))[0]),
        f"137={fam.get(137)} 136={fam.get(136)} 138={fam.get(138)}",
    )

    print("== a shared row must be refused ==")
    fake = list(projectiles) + [
        type(projectiles[0])(
            row=999,
            sequence=999,
            calibre=9.0,
            speed=950.0,
            mass=20.0,
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
