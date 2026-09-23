"""Is a projectile row a usable match key? Unique is not the same as stable.

The proposal: match weapons to projectile rows by row number instead of by name,
because the row number is unique. It is - trivially, it is an array index. The
question that decides whether it works is different:

    does the SAME weapon keep the SAME row number across a game update?

If it does not, a stored row number points at a different projectile next patch,
and writing to it edits the wrong weapon - the failure this project exists to
prevent. The mod already broke once on exactly this class of drift (R-4 moved
from row 137 to 147 in 1.8.45850).

So this measures, across the two builds on hand:

  1. uniqueness of every 4-byte field in the projectile record, to find any
     candidate stable identifier (the record carries a `sequence` field that has
     not been examined for this purpose)
  2. stability of `row` and of each unique field, measured on rows that can be
     matched between the builds by name - the only rows where "the same weapon"
     is knowable without assuming the answer
  3. the specific case in question: which row carried damage 33 in each build

Run:  python tools/match_key_stability.py
"""

from __future__ import annotations

import json
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dlbin_tables as D  # noqa: E402

RAW = ROOT / "data" / "raw"
OLD = ROOT / ".tools" / "old"
REC = D.PROJECTILE_RECORD_SIZE


def words(blob: bytes, arr: int, count: int) -> list[list[int]]:
    out = []
    for i in range(count):
        b = arr + i * REC
        out.append(list(struct.unpack_from(f"<{REC // 4}I", blob, b)))
    return out


def main() -> int:
    strings = json.loads((ROOT / "data" / "strings.json").read_text(encoding="utf-8"))

    def nm(h: int):
        if not h:
            return None
        v = strings.get(str(h))
        if isinstance(v, dict):
            return v.get("English (US)") or v.get("English (UK)")
        return v

    new_blob = (RAW / "generated_projectile_settings.dl_bin").read_bytes()
    old_blob = (OLD / "generated_projectile_settings.dl_bin").read_bytes()
    new = D.parse_projectiles(new_blob)
    old = D.parse_projectiles(old_blob)
    _, ns, ne = D.find_block(new_blob, D.TYPE_PROJECTILE)
    narr, ncount = D.dl_array(new_blob, ns, REC, limit=ne)
    _, os_, oe = D.find_block(old_blob, D.TYPE_PROJECTILE)
    oarr, ocount = D.dl_array(old_blob, os_, REC, limit=oe)
    nw = words(new_blob, narr, ncount)
    ow = words(old_blob, oarr, ocount)

    print(f"new build: {ncount} projectile rows")
    print(f"old build: {ocount} projectile rows")
    print()

    # ---- 1. which fields are unique? ------------------------------------
    print("== fields whose non-zero values are all distinct (candidate ids) ==")
    print(f"{'offset':>7s} {'non-zero':>9s} {'distinct':>9s}  note")
    candidates = []
    for off in range(0, REC // 4):
        vals = [w[off] for w in nw]
        nz = [v for v in vals if v]
        if not nz:
            continue
        distinct = len(set(nz))
        unique = distinct == len(nz)
        if unique:
            note = ""
            if off == 0:
                note = "  <-- `sequence`, the record's first field"
            print(f"  +{off * 4:4d} {len(nz):9d} {distinct:9d}{note}")
            candidates.append(off)
    print(f"  ({len(candidates)} candidate fields)")

    # ---- 2. stability, measured on rows matchable by name ---------------
    print()
    print("== stability across the update, on rows matchable by NAME ==")
    print("   (name is the only link that does not assume the answer)")
    old_by_name = {}
    for p in old:
        key = (p["name_upper"], p["name_cased"])
        if key != (0, 0):
            old_by_name.setdefault(key, []).append(p)

    pairs = []
    for p in new:
        key = (p["name_upper"], p["name_cased"])
        if key == (0, 0):
            continue
        matches = old_by_name.get(key)
        if matches and len(matches) == 1:
            pairs.append((p, matches[0]))
    print(f"   {len(pairs)} rows matched uniquely by name")

    def stability(label: str, get):
        same = sum(1 for a, b in pairs if get(a) == get(b))
        print(f"   {label:26s} same in both builds: {same:4d} / {len(pairs)}"
              f"   ({100.0 * same / max(1, len(pairs)):5.1f}%)")
        return same

    print()
    stability("row number", lambda p: p["row"])
    stability("sequence (+0)", lambda p: p["sequence"])
    for off in candidates:
        if off == 0:
            continue
        stability(f"offset +{off * 4}", lambda p, o=off: p.get("_w", [None] * 68)[o]
                  if p.get("_w") else None)
    print()
    print("   NOTE: only `row` and `sequence` are decoded by the parser; the other")
    print("   offsets need raw word access, done below.")

    # ---- raw-word stability for every unique field ----------------------
    print()
    print("== raw-word stability for each candidate field ==")
    new_by_name = {(p["name_upper"], p["name_cased"]): p for p in new
                   if (p["name_upper"], p["name_cased"]) != (0, 0)}
    print(f"{'offset':>7s} {'same':>6s} {'of':>5s} {'pct':>7s}")
    stable_fields = []
    for off in candidates:
        same = total = 0
        for (nu, nc), p in new_by_name.items():
            m = old_by_name.get((nu, nc))
            if not m or len(m) != 1:
                continue
            total += 1
            if nw[p["row"]][off] == ow[m[0]["row"]][off]:
                same += 1
        pct = 100.0 * same / max(1, total)
        mark = ""
        if total and pct == 100.0:
            mark = "  <-- STABLE"
            stable_fields.append(off)
        print(f"  +{off * 4:4d} {same:6d} {total:5d} {pct:6.1f}%{mark}")

    # ---- 3. the case in question ----------------------------------------
    print()
    print("== the weapon carrying damage 33 (the measured GL-15 direct) ==")
    new_dmg = D.parse_damages((RAW / "generated_damage_settings.dl_bin").read_bytes())
    old_dmg = D.parse_damages((OLD / "generated_damage_settings.dl_bin").read_bytes())
    for label, rows in (("new", new), ("old", old)):
        hits = [p for p in rows if p["damage_type"] == 33]
        for p in hits:
            name = nm(p["name_cased"]) or "(unnamed)"
            print(f"  {label}: row {p['row']:3d}  seq {p['sequence']:3d}  "
                  f"speed {p['speed']:.0f} mass {p['mass']:.0f}  {name}")
        if not hits:
            print(f"  {label}: no row uses damage 33")

    print()
    print("  what is at row 82 in each build:")
    for label, rows in (("new", new), ("old", old)):
        if 82 < len(rows):
            p = rows[82]
            name = nm(p["name_cased"]) or "(unnamed)"
            print(f"    {label}: seq {p['sequence']:3d} speed {p['speed']:6.0f} "
                  f"damage_type {p['damage_type']:3d}  {name}")

    # ---- 4. how many rows shifted? --------------------------------------
    print()
    print("== how far did rows move? ==")
    deltas = Counter()
    for (nu, nc), p in new_by_name.items():
        m = old_by_name.get((nu, nc))
        if not m or len(m) != 1:
            continue
        deltas[p["row"] - m[0]["row"]] += 1
    for delta, n in sorted(deltas.items()):
        print(f"  row changed by {delta:+4d}: {n} rows")
    moved = sum(n for d, n in deltas.items() if d != 0)
    print(f"  {moved} of {len(pairs)} name-matched rows changed row number")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
