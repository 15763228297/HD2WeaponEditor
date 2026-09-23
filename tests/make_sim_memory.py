"""Build a simulated process image containing the real damage table.

The image is: random noise, then the genuine record array copied out of the
parsed `.dl_bin`, then more noise. Bytes are real, so the resolver's pattern
search and neighbour checks run against the same data the game holds; only the
address space is simulated.

The row count is read from the table rather than hardcoded: it changes with the
game's balance patches (639 in 1.8.45317, 649 in 1.8.45850), and a stale count
would make the fixture disagree with the data the rest of the suite parses - the
kind of mismatch that makes a test pass while the mod is wrong.

Run:  python tests/make_sim_memory.py
"""

from __future__ import annotations

import json
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import parse_dlbin as pd  # noqa: E402

HEAD = 0x1000


def main() -> int:
    # Preferred source: the game's own table, when a local install has been
    # unpacked. That keeps the fixture byte-exact, including the trailing
    # pointer/hash fields the derived JSON cannot represent.
    #
    # Fallback: rebuild from the derived JSON. Rows then differ in the bytes
    # after +44, which no test reads - the resolver matches on the first 28
    # bytes and the writer touches +4/+8/+12..+24. The committed fixture is
    # produced by the preferred path; this fallback exists so a clone of the
    # public repo (which ships no game data) can still regenerate something
    # usable.
    src = ROOT / "data" / "raw" / "generated_damage_settings.dl_bin"
    if src.exists():
        blob = src.read_bytes()
        start = pd.anchor_start(blob)
        # Read the count from the table itself. `array_start` points at the
        # records, so everything from there to the end of the array is rows.
        rows_json = ROOT / "data" / "damage_records.json"
        if rows_json.exists():
            count = len(json.loads(rows_json.read_text(encoding="utf-8")))
        else:
            raise SystemExit(
                "data/damage_records.json is required to size the fixture; "
                "run tools/build_map.py first"
            )
        array = blob[start : start + count * pd.RECORD_SIZE]
        origin = "game table"
    else:
        rows = json.loads((ROOT / "data" / "damage_records.json")
                          .read_text(encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("records", rows)
        if isinstance(rows, dict):
            rows = [rows[k] for k in sorted(rows, key=int)]
        count = len(rows)
        array = bytearray()
        for row in rows:
            ap = list(row["armor_penetration_per_angle"])
            # Layout from parse_dlbin: +0 type_id, +4 damage, +8 durable,
            # +12 ap[4], +28 forces. The remaining bytes are opaque; zero them.
            rec = struct.pack(
                "<iii4i", row["type_id"], row["damage"],
                row["durable_damage"],
                ap[0], ap[1], ap[2], ap[3],
            )
            rec += struct.pack(
                "<iiii", row.get("demolition_strength", 0),
                row.get("force_strength", 0), row.get("force_impulse", 0),
                row.get("element_type", 0),
            )
            array += rec[:pd.RECORD_SIZE].ljust(pd.RECORD_SIZE, b"\0")
        array = bytes(array)
        origin = "derived JSON"

    if len(array) != count * pd.RECORD_SIZE:
        raise SystemExit(
            f"short array: wanted {count * pd.RECORD_SIZE} bytes, "
            f"got {len(array)} - layout changed?"
        )

    rng = random.Random(42)
    # Deterministic noise. Deliberately includes byte runs that look like small
    # integers, because a scanner that matches on a weak pattern would hit them.
    head = bytes(rng.randrange(256) for _ in range(HEAD))
    tail = bytes(rng.randrange(256) for _ in range(0x2000))

    image = head + array + tail
    out = ROOT / "data" / "sim_memory.bin"
    out.write_bytes(image)
    (ROOT / "data" / "sim_memory.json").write_text(
        json.dumps({"array_base": HEAD, "count": count, "image_size": len(image)})
    )
    print(f"wrote {out} ({len(image):,} bytes) from {origin}")
    print(f"  array base = 0x{HEAD:x}, {count} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
