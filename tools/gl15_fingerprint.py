"""Would a value fingerprint identify GL-15 uniquely, and how robust is it?

Row matching is out (4.3% stable across one update). The alternative is to match
on the weapon's measured VALUES, which is what the tool already does for every
other weapon - just via the wiki instead of a hand measurement.

For that to work the fingerprint has to be unique in the table. This checks the
full tuple, and also checks how much of it is load-bearing: if the fingerprint is
unique only because of one field, that field is a single point of failure.

Run:  python tools/gl15_fingerprint.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dlbin_tables as D  # noqa: E402
from explosions import parse_explosions, projectile_explosion  # noqa: E402

RAW = ROOT / "data" / "raw"

# The user's measurements.
WANT_DIRECT = (50, 2, 3)          # damage, durable, AP
WANT_EXPL = (440, 3)              # damage, AP
WANT_RADII = (2.25, 5.50)


def main() -> int:
    strings = json.loads((ROOT / "data" / "strings.json").read_text(encoding="utf-8"))

    def nm(h: int):
        if not h:
            return None
        v = strings.get(str(h))
        if isinstance(v, dict):
            return v.get("English (US)") or v.get("English (UK)")
        return v

    dmg = D.parse_damages((RAW / "generated_damage_settings.dl_bin").read_bytes())
    pblob = (RAW / "generated_projectile_settings.dl_bin").read_bytes()
    prj = D.parse_projectiles(pblob)
    by_id = {r["type_id"]: r for r in dmg}
    table = parse_explosions((RAW / "generated_explosion_settings.dl_bin").read_bytes())

    def fingerprint(p):
        """(direct tuple, explosion tuple, radii) for a projectile row."""
        d = by_id.get(p["damage_type"])
        if not d:
            return None
        direct = (d["damage"], d["durable_damage"],
                  d["armor_penetration_per_angle"][0])
        et = p["explosion_type"] or p["explosion_type_alt"]
        if not et or et not in table:
            return (direct, None, None)
        e = table[et]
        ed = by_id.get(e.damage_index)
        if not ed:
            return (direct, None, (e.inner_radius, e.outer_radius))
        expl = (ed["damage"], ed["armor_penetration_per_angle"][0])
        return (direct, expl, (e.inner_radius, e.outer_radius))

    # ---- 1. full fingerprint uniqueness ---------------------------------
    print("== does the full fingerprint pick out exactly one row? ==")
    want = (WANT_DIRECT, WANT_EXPL, WANT_RADII)
    hits = []
    for p in prj:
        fp = fingerprint(p)
        if fp is None:
            continue
        if (fp[0] == want[0] and fp[1] == want[1]
                and fp[2] and abs(fp[2][0] - want[2][0]) < 0.01
                and abs(fp[2][1] - want[2][1]) < 0.01):
            hits.append(p)
    print(f"  rows matching the full tuple {want}:")
    for p in hits:
        print(f"    row {p['row']:3d} seq {p['sequence']:3d} speed {p['speed']:.0f} "
              f"mass {p['mass']:.0f} cal {p['calibre']:.0f} "
              f"{nm(p['name_cased']) or '(unnamed)'}")
    print(f"  -> {len(hits)} row(s)")
    print()

    # ---- 2. which parts are load-bearing? -------------------------------
    print("== how unique is each part on its own? ==")
    for label, fn, target in (
        ("direct only", lambda fp: fp[0], WANT_DIRECT),
        ("explosion only", lambda fp: fp[1], WANT_EXPL),
        ("radii only", lambda fp: fp[2], WANT_RADII),
    ):
        n = 0
        for p in prj:
            fp = fingerprint(p)
            if fp is None:
                continue
            v = fn(fp)
            if v is None:
                continue
            if label == "radii only":
                ok = abs(v[0] - target[0]) < 0.01 and abs(v[1] - target[1]) < 0.01
            else:
                ok = v == target
            if ok:
                n += 1
        print(f"  {label:16s} matches {n} row(s)")

    print()
    print("  (a part that matches many rows is not load-bearing; a part that")
    print("   matches one is the part doing the work, and the part that fails")
    print("   first if the game rebalances that segment)")

    # ---- 3. what would break the fingerprint? ---------------------------
    print()
    print("== what invalidates it ==")
    print("  * the direct hit rebalanced away from 50/2/AP3")
    print("  * the explosion rebalanced away from 440/AP3")
    print("  * either radius changed from 2.25 / 5.50")
    print("  any one of these makes the tool refuse rather than guess, which is")
    print("  the intended failure mode")

    # ---- 4. is there a name-based route at all? -------------------------
    print()
    print("== could the wiki name ever work? ==")
    key = prj[82]["name_cased"]
    sharers = [p["row"] for p in prj if p["name_cased"] == key]
    print(f"  row 82's name key {key} is shared by rows {sharers}")
    print(f"  the game gives GL-21 and GL-15 the same ammo string, so no")
    print(f"  name-based rule can separate them - not a tool limitation")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
