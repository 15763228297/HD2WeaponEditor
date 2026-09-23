"""Which projectile records are genuinely new in this build, and can any of them
be the new weapons?

The earlier index-based diff (row >= 343) assumed the table only grows at the
end. This build inserts rows, so that assumption can point at the wrong record.
Diff by record *content* instead, ignoring the sequence/row fields, and report
only records that exist now and did not before.

For each genuinely new record, print everything: its name if it resolves, its
damage row, its explosion, velocity, mass, calibre. Then check it against what
the wiki states for each new weapon, and against the user's hypothesis
(490 = 40 direct + 450 explosion).

Run:  python tools/new_records.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dlbin_tables as D  # noqa: E402

RAW = ROOT / "data" / "raw"
OLD = ROOT / ".tools" / "old"


def sig(p: dict) -> tuple:
    """A record's identity, ignoring its position in the table."""
    return (p["name_upper"], p["name_cased"], p["projectile_type"],
            round(p["calibre"], 3), round(p["speed"], 3), round(p["mass"], 3),
            round(p["drag"], 4), round(p["gravity"], 4),
            p["damage_type"], p["explosion_type"], p["explosion_type_alt"])


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
    old_dmg = D.parse_damages((OLD / "generated_damage_settings.dl_bin").read_bytes())
    exp = D.parse_explosions((RAW / "generated_explosion_settings.dl_bin").read_bytes())
    prj = D.parse_projectiles((RAW / "generated_projectile_settings.dl_bin").read_bytes())
    old_prj = D.parse_projectiles((OLD / "generated_projectile_settings.dl_bin").read_bytes())

    by_id = {r["type_id"]: r for r in dmg}
    old_by_id = {r["type_id"]: r for r in old_dmg}
    exp_by_pos = {e["position"]: e for e in exp}

    def fmt(r):
        if not r:
            return "no damage row"
        return f"{r['damage']}/{r['durable_damage']} AP{r['armor_penetration_per_angle'][0]}"

    def detail(p):
        d = by_id.get(p["damage_type"])
        line = (f"direct {fmt(d)}")
        for key in ("explosion_type", "explosion_type_alt"):
            pos = p[key]
            if not pos:
                continue
            e = exp_by_pos.get(pos)
            if not e:
                continue
            ed = by_id.get(e["damage_type"])
            line += (f" | {key}={pos} -> explosion {fmt(ed)} "
                     f"r{e['inner_radius']:.2f}/{e['outer_radius']:.2f}")
            break
        return line

    # ---- genuinely new records -------------------------------------------
    old_sigs = {}
    for p in old_prj:
        old_sigs.setdefault(sig(p), []).append(p)

    new_records = []
    for p in prj:
        if sig(p) not in old_sigs:
            new_records.append(p)

    print(f"== projectile records with no counterpart in the old table: "
          f"{len(new_records)} ==")
    for p in new_records:
        label = nm(p["name_cased"]) or nm(p["name_upper"]) or "(name does not resolve)"
        print(f"\n  row {p['row']:3d}  {label}")
        print(f"      speed {p['speed']:.0f}  mass {p['mass']:.1f}  cal {p['calibre']:.1f}  "
              f"seq {p['sequence']}")
        print(f"      {detail(p)}")

    # ---- records that vanished -------------------------------------------
    new_sigs = {sig(p) for p in prj}
    gone = [p for s, ps in old_sigs.items() if s not in new_sigs for p in ps]
    print()
    print(f"== projectile records that existed before and do not now: {len(gone)} ==")
    for p in gone[:20]:
        label = nm(p["name_cased"]) or nm(p["name_upper"]) or "(unnamed)"
        print(f"  old row {p['row']:3d}  {label}  direct {fmt(old_by_id.get(p['damage_type']))}")
    if len(gone) > 20:
        print(f"  ... and {len(gone) - 20} more")

    # ---- damage rows that are new ----------------------------------------
    old_ids = {r["type_id"] for r in old_dmg}
    new_dmg = [r for r in dmg if r["type_id"] not in old_ids]
    print()
    print(f"== damage rows new in this build: {len(new_dmg)} ==")
    for r in new_dmg:
        owners = [p for p in prj if p["damage_type"] == r["type_id"]]
        who = ", ".join(nm(p["name_cased"]) or nm(p["name_upper"]) or f"row {p['row']}"
                        for p in owners) or "nothing references it"
        print(f"  pos {r['position']:3d} id {r['type_id']:4d} "
              f"{r['damage']:6d}/{r['durable_damage']:6d} AP{r['armor_penetration_per_angle'][0]:2d}"
              f"  <- {who}")

    # ---- the user's hypothesis -------------------------------------------
    print()
    print("== user's hypothesis: 490 = 40 direct + 450 explosion ==")
    n = 0
    for p in prj:
        d = by_id.get(p["damage_type"])
        if not d:
            continue
        for key in ("explosion_type", "explosion_type_alt"):
            pos = p[key]
            if not pos:
                continue
            e = exp_by_pos.get(pos)
            if not e:
                continue
            ed = by_id.get(e["damage_type"])
            if not ed:
                continue
            if d["damage"] == 40 and ed["damage"] == 450:
                n += 1
                print(f"  MATCH row {p['row']}: 40 + 450 = 490")
            elif d["damage"] + ed["damage"] == 490:
                n += 1
                print(f"  MATCH row {p['row']}: {d['damage']} + {ed['damage']} = 490")
    print(f"  ({n} matches)")

    # ---- every explosion whose damage is 450 ------------------------------
    print()
    print("== every 450 explosion, and the projectile rows that carry it ==")
    for e in exp:
        d = by_id.get(e["damage_type"])
        if not d or d["damage"] != 450:
            continue
        carriers = []
        for p in prj:
            if p["explosion_type"] == e["position"] or p["explosion_type_alt"] == e["position"]:
                carriers.append(f"row {p['row']}")
        print(f"  explosion {e['position']:3d}  {fmt(d)}  "
              f"r{e['inner_radius']:.2f}/{e['outer_radius']:.2f}  "
              f"carried by: {', '.join(carriers) or 'nothing'}")

    # ---- the wiki's stated values for the new weapons --------------------
    print()
    print("== wiki-stated (damage, AP) for the new weapons, against the tables ==")
    stated = {
        "GL-15 Evictor": (490, 3),
        "P-34 Breacher": (2000, 7),
        "G-8 Immolation": (20, None),
        "AR-11 Arbitrator": (None, None),
    }
    for w, (dval, ap) in stated.items():
        print(f"  {w}: wiki damage={dval} AP={ap}")
        if dval is None:
            print("      no figure on the page")
            continue
        rows = [r for r in dmg if r["damage"] == dval]
        if not rows:
            print(f"      no damage row holds {dval}")
        for r in rows:
            owners = [p for p in prj if p["damage_type"] == r["type_id"]]
            who = ", ".join(nm(p["name_cased"]) or nm(p["name_upper"]) or f"row {p['row']}"
                            for p in owners) or "nothing"
            print(f"      pos {r['position']:3d} id {r['type_id']:4d} "
                  f"{r['damage']}/{r['durable_damage']} "
                  f"AP{r['armor_penetration_per_angle'][0]}  <- {who}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
