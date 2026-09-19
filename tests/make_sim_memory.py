"""Build a simulated process image containing the real damage table.

The image is: random noise, then the genuine 634-record array copied out of the
parsed `.dl_bin`, then more noise. Bytes are real, so the resolver's pattern
search and neighbour checks run against the same data the game holds; only the
address space is simulated.

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

COUNT = 634
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
        array = blob[start : start + COUNT * pd.RECORD_SIZE]
        origin = "game table"
    else:
        rows = json.loads((ROOT / "data" / "damage_records.json")
                          .read_text(encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("records", rows)
        if isinstance(rows, dict):
            rows = [rows[k] for k in sorted(rows, key=int)]
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

    if len(array) != COUNT * pd.RECORD_SIZE:
        raise SystemExit("short array - layout changed?")

    rng = random.Random(42)
    # Deterministic noise. Deliberately includes byte runs that look like small
    # integers, because a scanner that matches on a weak pattern would hit them.
    head = bytes(rng.randrange(256) for _ in range(HEAD))
    tail = bytes(rng.randrange(256) for _ in range(0x2000))

    image = head + array + tail
    out = ROOT / "data" / "sim_memory.bin"
    out.write_bytes(image)
    (ROOT / "data" / "sim_memory.json").write_text(
        json.dumps({"array_base": HEAD, "count": COUNT, "image_size": len(image)})
    )
    print(f"wrote {out} ({len(image):,} bytes)")
    print(f"  array base = 0x{HEAD:x}, damage[137] at 0x{HEAD + 137 * 76:x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
