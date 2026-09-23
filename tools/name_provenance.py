"""Where does a projectile row's name come from, exactly?

For rows 81 and 82 in particular, because both were reported as "40mm HE Grenade"
and that is what blocks GL-15: if two rows share a name, name matching cannot tell
them apart.

The chain to verify, end to end, with no assumptions:

    projectile record  ->  name_upper / name_cased  (two u32 keys)
                       ->  data/strings.json          (key -> {lang: text})
                       ->  which raw .strings export the key came from

Two things to settle:
  * do the two rows share one key, or two different keys that happen to hold the
    same text? That changes whether the collision is a data problem or a
    coincidental duplicate string.
  * is the name really the row's own, or inherited from something else (a shared
    string entry, a fallback in the name lookup)?

Run:  python tools/name_provenance.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dlbin_tables as D  # noqa: E402

RAW = ROOT / "data" / "raw"
EXPORTS = ROOT / "data" / "strings_out"

ROWS = (81, 82, 80, 83, 86)


def main() -> int:
    strings = json.loads((ROOT / "data" / "strings.json").read_text(encoding="utf-8"))
    prj = D.parse_projectiles((RAW / "generated_projectile_settings.dl_bin").read_bytes())

    print("== the raw fields on the records ==")
    print(f"{'row':>4s} {'name_upper':>12s} {'name_cased':>12s}")
    for r in ROWS:
        p = prj[r]
        print(f"  {r:3d} {p['name_upper']:12d} {p['name_cased']:12d}")

    print()
    print("== those keys resolved against data/strings.json ==")
    for r in ROWS:
        p = prj[r]
        for field in ("name_upper", "name_cased"):
            k = p[field]
            if not k:
                print(f"  row {r:3d} {field:12s} {k:12d}  -> (zero: no key)")
                continue
            v = strings.get(str(k))
            if isinstance(v, dict):
                en = v.get("English (US)") or v.get("English (UK)")
                print(f"  row {r:3d} {field:12s} {k:12d}  -> {en!r}  "
                      f"({len(v)} languages)")
            else:
                print(f"  row {r:3d} {field:12s} {k:12d}  -> {v!r}")

    print()
    print("== are the two rows' keys the same key, or different keys? ==")
    a, b = prj[81], prj[82]
    for field in ("name_upper", "name_cased"):
        same = a[field] == b[field]
        print(f"  {field}: row81={a[field]}  row82={b[field]}  "
              f"{'SAME KEY' if same else 'different keys'}")

    print()
    print("== every projectile row sharing those keys ==")
    for field in ("name_upper", "name_cased"):
        key = a[field]
        if not key:
            continue
        sharers = [p["row"] for p in prj if p[field] == key]
        print(f"  {field}={key}: rows {sharers}")

    print()
    print("== and every row whose RESOLVED name is '40mm HE Grenade' ==")
    target = "40mm HE Grenade"
    by_name = defaultdict(list)
    for p in prj:
        for field in ("name_cased", "name_upper"):
            k = p[field]
            if not k:
                continue
            v = strings.get(str(k))
            if isinstance(v, dict):
                en = v.get("English (US)") or v.get("English (UK)")
                if en == target:
                    by_name[field].append((p["row"], k))
    for field, items in by_name.items():
        print(f"  via {field}:")
        for row, k in items:
            print(f"    row {row:3d} key {k}")

    print()
    print("== which raw .strings export carries those keys? ==")
    keys = {a["name_upper"], a["name_cased"], b["name_upper"], b["name_cased"]}
    keys.discard(0)
    found: dict[int, list] = defaultdict(list)
    for f in sorted(EXPORTS.glob("*.strings.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        lang = (doc.get("Language") or {}).get("KnownFriendlyName") or "?"
        for it in (doc.get("Items") or []):
            if it.get("Key") in keys:
                found[it["Key"]].append((f.name, lang, it.get("Value")))
    for k in sorted(keys):
        hits = found.get(k) or []
        print(f"  key {k}: {len(hits)} entries")
        for name, lang, value in hits[:4]:
            print(f"      [{lang:22s}] {value!r}   ({name})")

    print()
    print("== how does the code resolve a name? (the actual lookup) ==")
    sys.path.insert(0, str(ROOT / "tools"))
    import inspect
    import names as N
    src = inspect.getsource(N.projectile_name)
    print(src)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
