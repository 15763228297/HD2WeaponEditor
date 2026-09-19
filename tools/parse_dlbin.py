"""Parse Helldivers 2 `.dl_bin` data tables.

Layout authority: `xypwn/filediver` Go sources (`datalibrary/damage_settings.go`,
`datalibrary/datalib_instance.go`). The binary is little-endian throughout.

Why this module exists: the game ships its tables as encrypted `.dl_bin`
(entropy ~7.996/8, so not editable in place). `filediver` republishes the same
tables decrypted and gzipped, so parsing them yields the pristine weapon values
offline - no game run and no memory access. The GUI displays these values; the
generated mod writes the user's replacements into the live process.

Record layout (filediver's `rawDamageInfo`), 76 bytes, verified against the file:

    +0   int32   type index
    +4   int32   damage (standard)
    +8   int32   durable damage
    +12  uint32  armor_penetration_per_angle[0..3]
    +28  uint32  demolition_strength
    +32  uint32  force_strength
    +36  uint32  force_impulse
    +40  uint32  element_type
    +44  status_effects[4] of {int32 type, float32 value}     -> 32 bytes
    ---- 4 + 4 + 4 + 16 + 4 + 4 + 4 + 4 + 32 = 76

Two earlier readings were wrong and are recorded so they are not repeated:

* **72 bytes** - from eyeballing adjacent rows. It "worked" because most rows
  leave the trailing status-effect slots zeroed, so a 72-byte read of a 76-byte
  array still showed a plausible next row. The Go struct settled it.
* **Container walk** - guessing header sizes. The authoritative way to locate
  the array is to use one independently verified record (index 137 @ 0x2a8c)
  as an anchor and subtract `index * RECORD_SIZE`; that lands on a start offset
  which divides the remaining file exactly and whose last record has a sane
  index. Prefer that over reimplementing the container reader.
"""

from __future__ import annotations

import gzip
import json
import struct
from dataclasses import dataclass, asdict
from pathlib import Path

RECORD_SIZE = 76

# Anchor: the R-4 Hyena damage row. Offset inside the shipped
# `generated_damage_settings.dl_bin`. Verified by decoding the fields and
# matching the wiki's 220/45/AP3 + 10/20/14 exactly. Also serves as a
# regression check: if this row stops matching, the table layout moved.
ANCHOR_INDEX = 137
ANCHOR_DAMAGE = (220, 45)
ANCHOR_AP = [3, 3, 3, 0]
ANCHOR_FORCES = (10, 20, 14)


@dataclass
class DamageInfo:
    """One `DamageInfoType_*` entry: the stats a projectile deals on hit.

    Two numbers identify a record and they are NOT interchangeable:

    * `index` - the row's position in the array (0..633). This is the number the
      projectile table's `damage_info_type` field references, and the number the
      GUI shows. Use it to *address* a row.
    * `type_id` - the raw value stored at record offset 0 (4..639, unique). Use
      it to *identify* a row: 519 of 634 rows have `type_id != index`, and the
      array's own order is unrelated to either (ids run 4, 5, 18, 19, 337, ...).

    The runtime resolver builds its search pattern from `type_id` and pins the
    neighbours by their `type_id`s. Using `index` there made most rows
    unfindable while still passing a test on R-4, where the two coincide.
    """

    index: int
    type_id: int
    damage: int
    durable_damage: int
    armor_penetration_per_angle: list[int]
    demolition_strength: int
    force_strength: int
    force_impulse: int
    element_type: int
    status_effects: list[tuple[int, float]]

    @property
    def ap_direct(self) -> int:
        """Armor penetration on a direct hit - the number the wiki calls 'pen'."""
        return self.armor_penetration_per_angle[0]

    def as_dict(self) -> dict:
        return asdict(self)


def _decode_record(blob: bytes, base: int, index: int) -> DamageInfo:
    type_id, dmg, dur = struct.unpack_from("<iii", blob, base)
    ap = list(struct.unpack_from("<4I", blob, base + 12))
    dem, fs, fi, elem = struct.unpack_from("<4I", blob, base + 28)
    effects = []
    for k in range(4):
        etype, evalue = struct.unpack_from("<if", blob, base + 44 + k * 8)
        effects.append((etype, evalue))
    return DamageInfo(
        index=index,
        type_id=type_id,
        damage=dmg,
        durable_damage=dur,
        armor_penetration_per_angle=ap,
        demolition_strength=dem,
        force_strength=fs,
        force_impulse=fi,
        element_type=elem,
        status_effects=effects,
    )


def parse_damage_records(blob: bytes, start: int = 0) -> list[DamageInfo]:
    """Decode a damage-record array that begins at `start`."""
    if (len(blob) - start) % RECORD_SIZE != 0:
        raise ValueError(
            f"{(len(blob) - start)} bytes from offset {start} is not a multiple of "
            f"the {RECORD_SIZE}-byte record size - wrong start offset or layout"
        )
    n = (len(blob) - start) // RECORD_SIZE
    return [_decode_record(blob, start + i * RECORD_SIZE, i) for i in range(n)]


def anchor_start(blob: bytes, index: int = ANCHOR_INDEX) -> int:
    """Locate the record array using the verified anchor row.

    Returns the offset where index 0 begins. Raises if the anchor row is not
    where the known layout predicts, which is the signal that the table changed.
    """
    want = struct.pack("<iii", index, *ANCHOR_DAMAGE)
    # Search the whole container region, not a fixed window: the array sits after
    # the header and the angle table, which put the anchor past 8 KiB in the
    # shipped file. A too-small window fails as "anchor not found", which reads
    # like a layout change - so scan the file.
    for candidate in range(0, len(blob) - RECORD_SIZE):
        if blob[candidate : candidate + len(want)] != want:
            continue
        start = candidate - index * RECORD_SIZE
        if start < 0:
            continue
        if (len(blob) - start) % RECORD_SIZE:
            continue
        ap = list(struct.unpack_from("<4I", blob, candidate + 12))
        if ap != ANCHOR_AP:
            continue
        forces = struct.unpack_from("<3I", blob, candidate + 28)
        if list(forces) != list(ANCHOR_FORCES):
            continue
        return start
    raise ValueError(
        f"anchor row (index {index} = {ANCHOR_DAMAGE} ap {ANCHOR_AP}) not found - "
        "the damage table layout or content changed; re-derive before trusting output"
    )


def parse_damage_settings_file(path: str | Path) -> list[DamageInfo]:
    """Parse a full `generated_damage_settings.dl_bin` (gunzipped)."""
    blob = Path(path).read_bytes()
    return parse_damage_records(blob, anchor_start(blob))


def load_any(path: str | Path) -> bytes:
    """Read a `.dl_bin`, transparently gunzipping a `.dl_bin.gz`."""
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    return raw


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "data/raw/generated_damage_settings.dl_bin"
    recs = parse_damage_settings_file(target)
    print(f"parsed {len(recs)} damage records from {target}")

    r4 = recs[ANCHOR_INDEX]
    ok = (
        (r4.damage, r4.durable_damage) == ANCHOR_DAMAGE
        and r4.armor_penetration_per_angle == ANCHOR_AP
        and (r4.demolition_strength, r4.force_strength, r4.force_impulse)
        == ANCHOR_FORCES
    )
    print(
        f"  anchor[{ANCHOR_INDEX}] damage={r4.damage}/{r4.durable_damage} "
        f"ap={r4.armor_penetration_per_angle} "
        f"dem/force/imp={r4.demolition_strength}/{r4.force_strength}/{r4.force_impulse}"
    )
    print(f"  anchor check: {'PASS' if ok else 'FAIL'}")

    out = Path("data/damage_records.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([r.as_dict() for r in recs], indent=1), encoding="utf-8")
    print(f"wrote {out}")
