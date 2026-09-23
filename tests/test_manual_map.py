"""A hand-registered fingerprint must resolve to exactly one row, or refuse.

Why this needs tests rather than a comment: a manual entry is the one part of the
mapping that is not derived from the wiki, so nothing else in the pipeline can
catch it being wrong. If the fingerprint is not specific, or is typo'd, the
result is a weapon pointing at another weapon's damage row - and unlike a stale
wiki figure, there is no second source to disagree with it.

The safety property is uniqueness. These tests hold the entry to it:

  * the registered fingerprint matches exactly one projectile row
  * a fingerprint with one field loosened matches MORE than one, which is what
    makes the full tuple load-bearing rather than decorative
  * a fingerprint whose values are absent resolves to nothing, so a rebalance
    produces a refusal instead of a wrong row
  * the resolved entry carries the same fields an automatic match does, so
    nothing downstream has to special-case it
  * the entry does not steal a row from a weapon that already had one

Run:  python tests/test_manual_map.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dlbin_tables as D  # noqa: E402
import manual_map  # noqa: E402
import wiki_names as W  # noqa: E402
from build_map import (parse_damages, parse_projectiles,
                       resolve_damage_positions)  # noqa: E402
from explosions import parse_explosions, projectile_explosion  # noqa: E402

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  -- {detail}" if detail else ""))


def load():
    dblob = (ROOT / "data/raw/generated_damage_settings.dl_bin").read_bytes()
    pblob = (ROOT / "data/raw/generated_projectile_settings.dl_bin").read_bytes()
    damages = parse_damages(dblob)
    projectiles = parse_projectiles(pblob)
    # `build()` resolves each projectile's damage-type id into the position it
    # addresses before matching; without it `p.damage_position` is None and the
    # manual resolver has no impact row to record. A test that skipped this
    # would be exercising a state the real pipeline never produces.
    resolve_damage_positions(projectiles, damages)
    strings = json.loads((ROOT / "data/strings.json").read_text(encoding="utf-8"))
    return damages, projectiles, pblob, strings


def count_fits(fp, damages, projectiles, pblob) -> int:
    """How many projectile rows satisfy this fingerprint (uniqueness probe)."""
    from build_map import position_of_type_id

    exp_table = parse_explosions(
        (ROOT / "data/raw/generated_explosion_settings.dl_bin").read_bytes())
    by_id = {d.type_id: d for d in damages.values()}
    n = 0
    for p in projectiles:
        d = by_id.get(p.damage_type)
        if d is None:
            continue
        if (d.damage, d.durable_damage,
                d.armor_penetration_per_angle[0]) != fp.direct:
            continue
        if fp.explosion is None and fp.radii is None:
            n += 1
            continue
        et = projectile_explosion(pblob, p.row)
        e = exp_table.get(et) if et is not None else None
        if e is None:
            continue
        if fp.radii is not None and (
                abs(e.inner_radius - fp.radii[0]) > 0.01
                or abs(e.outer_radius - fp.radii[1]) > 0.01):
            continue
        if fp.explosion is not None:
            ed = damages.get(position_of_type_id(damages, e.damage_index))
            if ed is None or (ed.damage, ed.armor_penetration_per_angle[0]) != fp.explosion:
                continue
        n += 1
    return n


def main() -> int:
    damages, projectiles, pblob, strings = load()
    entries = manual_map.by_page()

    check("at least one entry is registered", len(entries) >= 1, str(list(entries)))
    check("every entry names a page", all(e.page for e in manual_map.MANUAL))
    check("every entry records where its figures came from",
          all(e.fingerprint.source.strip() for e in manual_map.MANUAL))
    # A measurement of a two-part weapon includes the blast radius, and the
    # radius is what separates explosions that share a damage value. An entry
    # that drops it is less specific than the evidence it is based on, so the
    # policy is: if the explosion is constrained, the radii are too.
    check("every entry with an explosion also constrains the radii",
          all(e.fingerprint.radii is not None
              for e in manual_map.MANUAL if e.fingerprint.explosion is not None))
    check("every entry constrains the direct hit",
          all(e.fingerprint.direct and e.fingerprint.direct != (0, 0, 0)
              for e in manual_map.MANUAL))

    # -- an ambiguous fingerprint must be REFUSED ------------------------
    # This is the property that keeps a hand-entered value set from becoming a
    # guess. A loose fingerprint that fits several rows has to resolve to None,
    # because taking the first fit is exactly how GR-8 once ended up pointing at
    # EAT-17's row.
    # (20, 2, 0) is shared by 15 projectile rows - verified in the table, not
    # assumed, so this probe stays valid if the specific count changes.
    loose = manual_map.Fingerprint(direct=(20, 2, 0))
    n_loose = count_fits(loose, damages, projectiles, pblob)
    check("a deliberately loose fingerprint fits more than one row",
          n_loose > 1, f"{n_loose} rows")
    check("an ambiguous fingerprint is refused, not resolved to the first fit",
          W._match_manual(manual_map.ManualEntry(page="(probe)", fingerprint=loose),
                          projectiles, damages, strings) is None)

    for entry in manual_map.MANUAL:
        print(f"\n-- {entry.page} --")
        fp = entry.fingerprint

        # -- the fingerprint is specific --------------------------------
        n = count_fits(fp, damages, projectiles, pblob)
        check(f"{entry.page}: the full fingerprint matches exactly one row",
              n == 1, f"matched {n} rows")

        # -- every field is ENFORCED, through the real resolver ----------
        # The property that matters is not "loosening widens the match" - for
        # GL-15 each part is individually unique, so dropping one changes
        # nothing. It is that each field is actually checked by the RESOLVER.
        #
        # These call `_match_manual`, not a local re-implementation. An earlier
        # version of this test counted matches with its own copy of the logic,
        # so deleting the radius check from the resolver did not fail anything -
        # the test was validating its own duplicate.
        pert = {
            "direct damage": manual_map.Fingerprint(
                direct=(fp.direct[0] + 1, fp.direct[1], fp.direct[2]),
                explosion=fp.explosion, radii=fp.radii),
            "direct durable": manual_map.Fingerprint(
                direct=(fp.direct[0], fp.direct[1] + 1, fp.direct[2]),
                explosion=fp.explosion, radii=fp.radii),
            "direct AP": manual_map.Fingerprint(
                direct=(fp.direct[0], fp.direct[1], fp.direct[2] + 1),
                explosion=fp.explosion, radii=fp.radii),
        }
        if fp.explosion is not None:
            pert["explosion damage"] = manual_map.Fingerprint(
                direct=fp.direct, explosion=(fp.explosion[0] + 1, fp.explosion[1]),
                radii=fp.radii)
            pert["explosion AP"] = manual_map.Fingerprint(
                direct=fp.direct, explosion=(fp.explosion[0], fp.explosion[1] + 1),
                radii=fp.radii)
        if fp.radii is not None:
            pert["inner radius"] = manual_map.Fingerprint(
                direct=fp.direct, explosion=fp.explosion,
                radii=(fp.radii[0] + 0.5, fp.radii[1]))
            pert["outer radius"] = manual_map.Fingerprint(
                direct=fp.direct, explosion=fp.explosion,
                radii=(fp.radii[0], fp.radii[1] + 0.5))
        for name, alt in pert.items():
            probe = manual_map.ManualEntry(page="(probe)", fingerprint=alt)
            resolved = W._match_manual(probe, projectiles, damages, strings)
            check(f"{entry.page}: a wrong {name} makes the resolver refuse",
                  resolved is None,
                  f"resolved to {resolved['damage_position'] if resolved else None}"
                  f" - the field is not enforced")

        # -- the tuple is specific: exactly one row, not merely one of few ---
        check(f"{entry.page}: the fingerprint is specific enough to be safe",
              n == 1, f"{n} rows fit")

        # -- a wrong fingerprint refuses rather than guessing ------------
        absent = manual_map.Fingerprint(direct=(fp.direct[0] + 9999, 0, 0))
        check(f"{entry.page}: absent values match nothing (refusal, not a guess)",
              count_fits(absent, damages, projectiles, pblob) == 0)

        # -- it resolves, and to the right shape ------------------------
        row = W._match_manual(entry, projectiles, damages, strings)
        check(f"{entry.page}: resolves", row is not None)
        if row is None:
            continue
        for field in ("page", "damage_index", "damage_position",
                      "impact_damage_index", "impact_damage_position", "payload",
                      "projectile_row", "speed", "damage", "durable", "ap",
                      "checks", "matched_by", "verified"):
            check(f"{entry.page}: carries `{field}`", field in row, str(sorted(row)))
        check(f"{entry.page}: says it was manual, not derived",
              row["matched_by"] == "manual fingerprint", row["matched_by"])
        check(f"{entry.page}: records its source",
              bool(row.get("manual_source")))
        check(f"{entry.page}: the row's source is the entry's own text",
              row.get("manual_source") == entry.fingerprint.source,
              "the source is not carried through from the entry")
        check(f"{entry.page}: is verified", row["verified"] is True)
        check(f"{entry.page}: no wiki figures are claimed",
              row["wiki"] == {}, str(row["wiki"]))

        # -- the row it points at really holds the measured values ------
        # `damages` is keyed by POSITION and `damage_index` is a TYPE ID; the two
        # differ on most rows, so the lookup must use the position.
        d = damages.get(row["damage_position"])
        check(f"{entry.page}: damage_position holds the explosion values",
              d is not None and (d.damage, d.armor_penetration_per_angle[0])
              == fp.explosion,
              f"row has {d.damage if d else None}")
        check(f"{entry.page}: damage_index is the type id of that row",
              d is not None and row["damage_index"] == d.type_id,
              f"index {row['damage_index']} vs row type_id {d.type_id if d else None}")
        imp = damages.get(row["impact_damage_position"])
        check(f"{entry.page}: impact_damage_position holds the direct values",
              imp is not None
              and (imp.damage, imp.durable_damage,
                   imp.armor_penetration_per_angle[0]) == fp.direct,
              f"row has {imp.damage if imp else None}")
        check(f"{entry.page}: impact_damage_index is the type id of that row",
              imp is not None and row["impact_damage_index"] == imp.type_id,
              f"index {row['impact_damage_index']} vs "
              f"row type_id {imp.type_id if imp else None}")

        # -- it did not steal a row ------------------------------------
        wn = json.loads((ROOT / "data/weapon_names.json").read_text(encoding="utf-8"))
        others = [w for w in wn["weapons"]
                  if w["page"] != entry.page
                  and w["damage_position"] == row["damage_position"]]
        check(f"{entry.page}: no other weapon already owned that row",
              not others, f"also used by {[w['page'] for w in others]}")

    # -- the matcher actually USES the entries ---------------------------
    # The tests above exercise `_match_manual` directly, and the shipped-map
    # checks read the artifact. Neither proves the matcher calls the resolver:
    # if the manual branch were dropped from `match_all`, the shipped map would
    # still contain GL-15 from whenever it was last generated, and every check
    # above would still pass. So run the matcher itself.
    print()
    matched = W.match_all(pblob, damages, projectiles)
    unmatched_pages = {t for t, _ in matched.get("__unmatched", [])}
    for entry in manual_map.MANUAL:
        got = matched.get(entry.page)
        check(f"{entry.page}: the matcher resolves it (not just the resolver)",
              got is not None,
              "absent from match_all's output - the manual branch may be unwired")
        if got is None:
            continue
        check(f"{entry.page}: the matcher did not fall back to 'unmatched'",
              entry.page not in unmatched_pages)
        check(f"{entry.page}: the matcher's row is the resolver's row",
              got["matched_by"] == "manual fingerprint"
              and got["damage_position"] == W._match_manual(
                  entry, projectiles, damages, strings)["damage_position"],
              f"got {got['matched_by']} at {got['damage_position']}")

    # -- the shipped map agrees with the live computation ---------------
    print()
    wn = json.loads((ROOT / "data/weapon_names.json").read_text(encoding="utf-8"))
    by_page = {w["page"]: w for w in wn["weapons"]}
    for entry in manual_map.MANUAL:
        shipped = by_page.get(entry.page)
        check(f"{entry.page}: present in the shipped map", shipped is not None)
        if not shipped:
            continue
        live = W._match_manual(entry, projectiles, damages, strings)
        check(f"{entry.page}: shipped row matches a fresh resolution",
              live is not None
              and shipped["damage_position"] == live["damage_position"]
              and shipped["damage_index"] == live["damage_index"],
              f"shipped {shipped['damage_position']} vs live "
              f"{live['damage_position'] if live else None}")
        check(f"{entry.page}: not listed as unmatched",
              not any(entry.page == t for t, _ in wn.get("unmatched", [])))

    print(f"\n{PASS} PASS, {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
