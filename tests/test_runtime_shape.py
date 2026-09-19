"""Probe what the runtime damage table might actually look like.

The file-side pattern matches the parsed .dl_bin exactly, but the in-game scan
over the whole writable address space found nothing. Two explanations remain:

  (a) the scan covered almost nothing (region filter too tight), or
  (b) the runtime representation differs from the file layout.

This checks (b) against every plausible difference that a loader could introduce,
using the file as the source of truth. If a variant is plausible, the search
should use that variant instead.

Run:  python tests/test_runtime_shape.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import parse_dlbin as pd  # noqa: E402

failures = 0
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures, checks
    checks += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + ("" if cond else f"  {detail}"))
    if not cond:
        failures += 1


def main() -> int:
    blob = (ROOT / "data" / "raw" / "generated_damage_settings.dl_bin").read_bytes()
    start = pd.anchor_start(blob)
    recs = pd.parse_damage_records(blob, start)
    r = recs[137]

    print("== the file layout, as the generator encodes it ==")
    as_int = (struct.pack("<iii", r.type_id, r.damage, r.durable_damage)
              + struct.pack("<4I", *r.armor_penetration_per_angle))
    print(f"     ints: {as_int.hex(' ')}")
    check("the int pattern occurs in the file", blob.find(as_int) != -1)

    print()
    print("== variant: damage/durable stored as floats ==")
    # A loader that deserialises into a struct with float fields would store
    # these as f32. 220.0f and 45.0f have completely different bytes.
    as_float = (struct.pack("<i", r.type_id)
                + struct.pack("<ff", float(r.damage), float(r.durable_damage))
                + struct.pack("<4I", *r.armor_penetration_per_angle))
    print(f"     floats: {as_float.hex(' ')}")
    print(f"     (damage as f32 = {struct.pack('<f', 220.0).hex(' ')}, "
          f"as i32 = {struct.pack('<i', 220).hex(' ')})")
    check("float and int encodings differ (so this is a real fork)",
          struct.pack("<i", 220) != struct.pack("<f", 220.0))

    print()
    print("== variant: the type_id field is absent at runtime ==")
    # If the loader keys records by array index rather than storing the id, the
    # record starts at damage and is 72 bytes with no id field.
    no_id = (struct.pack("<ii", r.damage, r.durable_damage)
             + struct.pack("<4I", *r.armor_penetration_per_angle))
    print(f"     no-id prefix ({len(no_id)} bytes): {no_id.hex(' ')}")
    hits = []
    i = blob.find(no_id)
    while i != -1:
        hits.append(i)
        i = blob.find(no_id, i + 1)
    check("the no-id prefix is rare in the file (so it would be a usable pattern)",
          len(hits) < 50, f"{len(hits)} hits")

    print()
    print("== variant: fields are 64-bit ==")
    wide = b"".join(struct.pack("<Q", v) for v in
                    (r.type_id, r.damage, r.durable_damage))
    print(f"     wide: {wide.hex(' ')}")

    print()
    print("== how common is each variant's byte string in the file? ==")
    for label, pat in (("int 28B", as_int), ("float 28B", as_float),
                       ("no-id 24B", no_id), ("wide 24B", wide)):
        n = blob.count(pat)
        print(f"     {label:12s} {n} occurrence(s)")

    print()
    print("== conclusion about which single record to key on ==")
    # damage+durable as ints, with no id, is the most likely runtime prefix if the
    # loader does not carry the id. Check how unique it is table-wide.
    pairs = {}
    for rec in recs:
        key = (rec.damage, rec.durable_damage)
        pairs.setdefault(key, []).append(rec.index)
    dupes = {k: v for k, v in pairs.items() if len(v) > 1}
    print(f"     distinct (damage, durable) pairs: {len(pairs)} of {len(recs)} rows")
    print(f"     pairs shared by more than one row: {len(dupes)}")
    print(f"     R-4's pair 220/45 is shared by: "
          f"{pairs.get((220, 45), [])}")
    check("R-4's damage/durable pair is unique across the table",
          len(pairs.get((220, 45), [])) == 1,
          f"shared by {pairs.get((220, 45))}")

    print()
    print(f"test_runtime_shape: {'PASS' if not failures else 'FAIL'} ({checks} checks)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
