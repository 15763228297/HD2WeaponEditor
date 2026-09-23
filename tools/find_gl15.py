"""Where is GL-15 Evictor in the game tables?

The user's hypothesis: the wiki's 490 is direct + explosion, maybe 40 + 450.

This script tests that properly. The earlier search was polluted by a sentinel:
`explosion_type == 0` means "this projectile has no explosion", but the explosion
table's row 0 is a real entry (300/300 AP4, r 4.0/10.0), so every no-explosion
row appeared to carry a 300 explosion. Any sum computed from that is wrong.

It also compares the old and new projectile tables *by name* rather than by row
index: this build inserted rows, so old row N and new row N are different
records, and an index comparison reports almost everything as changed.

Run:  python tools/find_gl15.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dlbin_tables as D  # noqa: E402


def main() -> int:
    strings = json.loads((ROOT / "data" / "strings.json").read_text(encoding="utf-8"))

    def nm(h: int) -> str | None:
        if not h:
            return None
        v = strings.get(str(h))
        if isinstance(v, dict):
            return v.get("English (US)") or v.get("English (UK)")
        return v

    dmg = D.parse_damages(
        (ROOT / "data" / "raw" / "generated_damage_settings.dl_bin").read_bytes())
    exp = D.parse_explosions(
        (ROOT / "data" / "raw" / "generated_explosion_settings.dl_bin").read_bytes())
    prj = D.parse_projectiles(
        (ROOT / "data" / "raw" / "generated_projectile_settings.dl_bin").read_bytes())
    old_prj = D.parse_projectiles(
        (ROOT / ".tools" / "old" / "generated_projectile_settings.dl_bin").read_bytes())

    by_id = {r["type_id"]: r for r in dmg}
    exp_by_pos = {e["position"]: e for e in exp}

    def dmg_of(type_id: int):
        return by_id.get(type_id)

    def fmt(r) -> str:
        if not r:
            return "no row"
        return (f"{r['damage']}/{r['durable_damage']} "
                f"AP{r['armor_penetration_per_angle'][0]}")

    def explosion_of(p):
        """(row, damage row) for a projectile's real explosion, or (None, None).

        Both fields are checked and the sentinel 0 is treated as absent. Which
        field is authoritative is not assumed - the pair is returned so the
        caller can see which one answered.
        """
        for key in ("explosion_type", "explosion_type_alt"):
            pos = p[key]
            if not pos:
                continue
            e = exp_by_pos.get(pos)
            if e:
                d = dmg_of(e["damage_type"])
                if d:
                    return key, e, d
        return None, None, None

    # ---- 1. Truly new projectile rows, matched by name --------------------
    old_by_name = {}
    for p in old_prj:
        if p["name_upper"] or p["name_cased"]:
            old_by_name[(p["name_upper"], p["name_cased"])] = p

    print("== projectile rows present now that were not before (by name) ==")
    new_named = []
    for p in prj:
        key = (p["name_upper"], p["name_cased"])
        if key in old_by_name:
            continue
        if not (p["name_upper"] or p["name_cased"]):
            continue
        new_named.append(p)
        k, e, d = explosion_of(p)
        ex = f" + explosion {fmt(d)} r{e['inner_radius']:.1f}/{e['outer_radius']:.1f}" if d else ""
        print(f"  row {p['row']:3d}  {nm(p['name_cased']) or nm(p['name_upper'])}"
              f"   direct {fmt(dmg_of(p['damage_type']))}{ex}")
    print(f"  ({len(new_named)} named rows)")

    print()
    print("== projectile rows with no name at all (cannot be reached by name) ==")
    old_unnamed = [p for p in old_prj if not (p["name_upper"] or p["name_cased"])]
    print(f"  old table had {len(old_unnamed)} unnamed rows; new table has "
          f"{len([p for p in prj if not (p['name_upper'] or p['name_cased'])])}")

    # ---- 2. Every row whose direct + explosion lands near 490 -------------
    print()
    print("== rows whose direct + explosion is between 450 and 530 ==")
    hits = []
    for p in prj:
        d = dmg_of(p["damage_type"])
        if not d:
            continue
        k, e, ed = explosion_of(p)
        if not ed:
            continue
        tot = d["damage"] + ed["damage"]
        if 450 <= tot <= 530:
            hits.append((tot, p, d, k, e, ed))
    for tot, p, d, k, e, ed in sorted(hits, key=lambda x: abs(x[0] - 490)):
        label = nm(p["name_cased"]) or nm(p["name_upper"]) or "(unnamed)"
        print(f"  total {tot:4d}  row {p['row']:3d} {label:28s} "
              f"direct {fmt(d)} + explosion {fmt(ed)} "
              f"r{e['inner_radius']:.1f}/{e['outer_radius']:.1f} via {k}")
    if not hits:
        print("  none")

    # ---- 3. Direct damage of exactly 40, with any real explosion ----------
    print()
    print("== rows with direct damage 40 and a real explosion ==")
    n = 0
    for p in prj:
        d = dmg_of(p["damage_type"])
        if not d or d["damage"] != 40:
            continue
        k, e, ed = explosion_of(p)
        if not ed:
            continue
        n += 1
        label = nm(p["name_cased"]) or nm(p["name_upper"]) or "(unnamed)"
        print(f"  row {p['row']:3d} {label:28s} direct 40/{d['durable_damage']} "
              f"AP{d['armor_penetration_per_angle'][0]} + explosion {fmt(ed)} "
              f"= {40 + ed['damage']}")
    if not n:
        print("  none")

    # ---- 4. Who carries the 450 explosion --------------------------------
    print()
    print("== explosion rows whose damage is 450 ==")
    for e in exp:
        d = dmg_of(e["damage_type"])
        if d and d["damage"] == 450:
            carriers = []
            for p in prj:
                if p["explosion_type"] == e["position"] or p["explosion_type_alt"] == e["position"]:
                    label = nm(p["name_cased"]) or nm(p["name_upper"]) or "(unnamed)"
                    carriers.append(f"row {p['row']} {label}")
            print(f"  explosion {e['position']:3d} r{e['inner_radius']:.1f}/"
                  f"{e['outer_radius']:.1f} -> {fmt(d)}   carried by: "
                  f"{', '.join(carriers) or 'nothing'}")

    # ---- 5. Is the EVICTOR string hash referenced anywhere in the tables? --
    print()
    print("== do the weapon name hashes appear in the projectile table? ==")
    for label, h in (("EVICTOR", 1759411076), ("Evictor", 4275496005),
                     ("ARBITRATOR", 286599935), ("BREACHER", 3961796442)):
        found = [p["row"] for p in prj
                 if p["name_upper"] == h or p["name_cased"] == h]
        print(f"  {label} (0x{h:08x}): projectile rows {found or 'none'}")

    # ---- 6. Unnamed rows that changed ------------------------------------
    print()
    print("== unnamed rows in the new table, in full ==")
    for p in prj:
        if p["name_upper"] or p["name_cased"]:
            continue
        k, e, ed = explosion_of(p)
        ex = f" + explosion {fmt(ed)} r{e['inner_radius']:.1f}/{e['outer_radius']:.1f}" if ed else ""
        print(f"  row {p['row']:3d} speed {p['speed']:6.0f} mass {p['mass']:9.1f} "
              f"cal {p['calibre']:6.1f}  direct {fmt(dmg_of(p['damage_type']))}{ex}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
