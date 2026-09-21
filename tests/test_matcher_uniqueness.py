"""A weapon must be matched to the projectile row that actually belongs to it.

Three defects lived in the matcher, all with the same shape: a criterion that
was too weak to be unique, resolved by taking the first row that fitted.

  1. The explosion route matched on radii + explosion damage + AP and never
     looked at velocity. EAT-17 and GR-8 Recoilless Rifle have IDENTICAL
     explosion radii (1.5 / 3.0 / 6.0) and differ only in muzzle velocity (200
     vs 250 m/s) and damage (2000 vs 3200), so whichever page was processed
     first claimed the row and the other was mapped to it too. LAS-99 Quasar
     Cannon landed there the same way (1300 m/s). The user-visible symptom was
     a GUI warning that editing GR-8 would also change EAT-17 - when in fact
     the tool was pointing at the wrong weapon's data.

  2. The fingerprint routes took the first fitting row without checking whether
     a second row fitted too. Where two rows both fit and reach DIFFERENT
     damage rows, the choice is a guess.

  3. A page with no damage fields (P-11 Stim Pistol: a healing weapon) reduced
     the fingerprint to velocity alone, and 16 projectile rows share 300 m/s.

WHY THIS RUNS THE MATCHER INSTEAD OF READING THE DATA FILE

The first version of this test read `data/weapon_names.json` and asserted the
mapped rows looked right. It passed even with the velocity check removed from
the matcher, because the committed JSON had already been regenerated with the
fix - so the test was checking a build artifact, not the code that produces it.
Re-running the matcher is slower (it parses every wiki page) but it is the only
way a regression in the matching logic can fail this suite.

Run:  python tests/test_matcher_uniqueness.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_map  # noqa: E402
import wiki_names  # noqa: E402

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


def main() -> int:
    print("test_matcher_uniqueness: the assigned row must belong to the weapon")

    pblob = (DATA / "raw" / "generated_projectile_settings.dl_bin").read_bytes()
    dblob = (DATA / "raw" / "generated_damage_settings.dl_bin").read_bytes()
    damages = build_map.parse_damages(dblob)
    projectiles = build_map.parse_projectiles(pblob)
    build_map.resolve_damage_positions(projectiles, damages)

    # Run the matcher over the cached wiki pages. This is the code under test;
    # reading the committed JSON would only prove the artifact was regenerated.
    print("  (running the matcher over the cached wiki pages...)")
    matched = wiki_names.match_all(pblob, damages, projectiles)
    # Two keys carry build diagnostics rather than weapons; they are lists, and
    # iterating them as weapons is how this test first crashed.
    weapons = {k: v for k, v in matched.items() if not k.startswith("__")}
    print(f"       ({len(weapons)} weapon(s) matched, "
          f"{len(matched.get('__unmatched', []))} unmatched, "
          f"{len(matched.get('__ambiguous', []))} ambiguous)")

    # -- 1. the decisive external check -------------------------------------
    # The row a weapon was matched to must have the muzzle velocity the wiki
    # documents for that weapon. This is what caught the EAT-17/GR-8 collision:
    # GR-8's row read 200 m/s while its wiki page says 250.
    print("\n== assigned row's speed vs the wiki's documented velocity ==")
    bad = []
    checked = 0
    for page, w in weapons.items():
        if not w.get("verified"):
            continue
        row = w.get("projectile_row")
        want = (w.get("wiki") or {}).get("velocity")
        if not isinstance(row, int) or row < 0 or want is None:
            continue
        checked += 1
        got = projectiles[row].speed
        if abs(got - want) >= 0.5:
            bad.append((page, row, got, want))

    check("every matched weapon sits on a row with its own velocity",
          not bad,
          f"{len(bad)} mismatch, e.g. {bad[:3]}")
    print(f"       ({checked} weapon(s) checked against the wiki)")

    # -- 2. no two weapons with DIFFERENT velocities share a row -------------
    print("\n== weapons sharing a projectile row must agree on velocity ==")
    by_row: dict[int, list] = {}
    for page, w in weapons.items():
        if not w.get("verified"):
            continue
        row = w.get("projectile_row")
        if isinstance(row, int) and row >= 0:
            by_row.setdefault(row, []).append(page)

    clashes = []
    for row, pages in by_row.items():
        if len(pages) < 2:
            continue
        speeds = {(weapons[p].get("wiki") or {}).get("velocity") for p in pages}
        speeds.discard(None)
        if len(speeds) > 1:
            clashes.append((row, pages, sorted(speeds)))

    check("no shared row holds weapons with different velocities",
          not clashes, f"{clashes[:2]}")

    # -- 3. the specific collision that was reported ------------------------
    print("\n== the reported collision is resolved ==")
    eat = weapons.get("EAT-17 Expendable Anti-Tank")
    gr8 = weapons.get("GR-8 Recoilless Rifle")
    if eat and gr8:
        check("EAT-17 and GR-8 are on different projectile rows",
              eat.get("projectile_row") != gr8.get("projectile_row"),
              f"both on row {eat.get('projectile_row')}")
        eat_row = projectiles[eat["projectile_row"]]
        gr8_row = projectiles[gr8["projectile_row"]]
        check("EAT-17 keeps its 200 m/s row", abs(eat_row.speed - 200) < 0.5,
              f"speed {eat_row.speed}")
        check("GR-8 gets its 250 m/s row", abs(gr8_row.speed - 250) < 0.5,
              f"speed {gr8_row.speed}")
        # The whole point: GR-8's row must hold GR-8's damage (3200), not
        # EAT-17's (2000).
        gr8_dmg = damages[gr8_row.damage_position].damage
        check("GR-8's row holds GR-8's damage, not EAT-17's",
              gr8_dmg == 3200, f"row holds {gr8_dmg}, wiki says 3200")
    else:
        check("EAT-17 and GR-8 are both mapped", False,
              "one of them is missing from the map")

    # -- 4. a page with no damage fields must not be matched on velocity alone
    # P-11 Stim Pistol is a healing weapon: its page has a velocity and no
    # damage numbers. Sixteen rows share 300 m/s, so any "match" would be a
    # fabricated mapping onto a random weapon's damage row.
    #
    # Asserted on the matcher's own report, not on the resulting map: a page
    # matched to an arbitrary row still fails its wiki cross-check and so would
    # not be `verified` either way, which makes the map unable to tell "refused
    # to match" from "matched and then rejected". Only the report distinguishes
    # them.
    print("\n== a page with no damage data is not matched ==")
    unmatched_names = [str(t) for t, _ in matched.get("__unmatched", [])]
    check("P-11 Stim Pistol is reported unmatched",
          any("P-11 Stim Pistol" in n for n in unmatched_names),
          f"unmatched list: {unmatched_names[:5]}")
    stim = weapons.get("P-11 Stim Pistol")
    check("P-11 Stim Pistol is not mapped to a damage row",
          stim is None or not stim.get("verified"),
          f"it was mapped to row {stim.get('projectile_row') if stim else '?'}")

    # -- 5. genuine ambiguity is REPORTED, never silently resolved ----------
    # P-113 Verdict and SMG-32 Reprimand both fit projectile rows 180 and 187,
    # which read identically (140/32 AP3 at 285 m/s) but reach damage rows 87
    # and 92. There is no way to tell which the weapon uses, so the honest
    # outcome is to leave them unmapped.
    #
    # Asserted on `__ambiguous` rather than on the map, for the same reason as
    # above: picking either row produces a mapping that looks plausible, and the
    # defect is precisely that the tool made a choice it could not justify.
    print("\n== genuinely ambiguous weapons are reported, not resolved ==")
    ambiguous_names = [t for t, _, _ in matched.get("__ambiguous", [])]
    check("P-113 Verdict is reported ambiguous",
          any("P-113 Verdict" in n for n in ambiguous_names),
          f"ambiguous list: {ambiguous_names[:6]}")
    check("SMG-32 Reprimand is reported ambiguous",
          any("SMG-32 Reprimand" in n for n in ambiguous_names),
          f"ambiguous list: {ambiguous_names[:6]}")
    for name in ("P-113 Verdict", "SMG-32 Reprimand"):
        w = weapons.get(name)
        check(f"{name} is not mapped to a damage row",
              w is None or not w.get("verified"),
              f"it was mapped to row {w.get('damage_position') if w else '?'}")

    # -- 6. identical stat pages must not land on different rows ------------
    print("\n== identical stat pages must not land on different rows ==")
    by_stats: dict[tuple, list] = {}
    for page, w in weapons.items():
        if not w.get("verified"):
            continue
        wiki = w.get("wiki") or {}
        expl = wiki.get("explosion") or {}
        key = (wiki.get("velocity"),
               wiki.get("standard") or None,
               wiki.get("durable") or None,
               wiki.get("ap_direct") or None,
               expl.get("inner_radius"),
               expl.get("outer_radius"),
               expl.get("shockwave_radius"))
        # A zero means "not documented", and a page whose only content is a
        # velocity cannot identify a row (see P-11), so those are excluded
        # rather than grouped.
        if not any((wiki.get("standard"), wiki.get("durable"), wiki.get("ap_direct"),
                    expl.get("explosion_damage"))):
            continue
        by_stats.setdefault(key, []).append((page, w.get("damage_position")))

    split = []
    for key, entries in by_stats.items():
        positions = {p for _, p in entries if p is not None}
        if len(entries) > 1 and len(positions) > 1:
            split.append((key, entries))

    check("no group of identical wiki pages is split across damage rows",
          not split,
          f"{split[:1]}")

    print(f"\n{checks - failures}/{checks} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
