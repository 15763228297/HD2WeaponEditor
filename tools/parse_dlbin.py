"""Parse Helldivers 2 `.dl_bin` data tables.

Layout authority: `xypwn/filediver` Go sources (`datalibrary/damage_settings.go`)
and the container format itself, verified byte-for-byte on two game builds.
Little-endian throughout.

Why this module exists: the game ships its tables as encrypted `.dl_bin`, so
they cannot be read or edited in place. `filediver` republishes the same tables
decrypted, so parsing them yields the pristine weapon values offline - no game
run and no memory access. The GUI displays these values; the generated mod
writes the user's replacements into the live process.

Record layout (`rawDamageInfo`), 76 bytes:

    +0   int32   type id (DamageInfoType enum value)
    +4   int32   damage (standard)
    +8   int32   durable damage
    +12  uint32  armor_penetration_per_angle[0..3]
    +28  uint32  demolition_strength
    +32  uint32  force_strength
    +36  uint32  force_impulse
    +40  uint32  element_type
    +44  status_effects[4] of {int32 type, float32 value}     -> 32 bytes
    ---- 4 + 4 + 4 + 16 + 4 + 4 + 4 + 4 + 32 = 76

TWO EARLIER READINGS WERE WRONG. They are recorded because the second one
shipped, and it is the reason the mod broke on the 1.8.45850 update.

* **72 bytes** - from eyeballing adjacent rows. It "worked" because most rows
  leave the trailing status-effect slots zeroed, so a 72-byte read of a
  76-byte array still showed a plausible next row. The Go struct settled it.

* **Content anchor** - locating the array by searching for the R-4 row's
  fingerprint (137, 220, 45, AP[3,3,3,0], 10, 20, 14) and subtracting
  `137 * 76`. It failed twice over. It used a *balance-dependent* value as a
  structural anchor, so the renumbered build stopped matching it. And even in
  the build it was written for it subtracted the wrong row number: the anchor
  row sat at position 142, not 137, so the derived start was 480 instead of
  100 and the parse silently dropped the first five rows. Every offset
  downstream inherited that 380-byte shift - and the mod's `ARRAY_START`
  constant had been tuned to cancel it, which hid the bug until the build
  changed. See `dlbin_tables` for the container walk that replaces it.

THE ID / POSITION DISTINCTION (the other half of the same bug)

`type_id` (record +0) is a `DamageInfoType` enum value. Projectile records
reference it at +60 and explosion records at +4. It is NOT the row position:
in the current build 614 of 639 rows have `type_id != position`. Resolve one to
the other with `position_of_type_id`; never index the array with an enum value.
"""

from __future__ import annotations

import gzip
import json
import struct
from dataclasses import dataclass, asdict
from pathlib import Path

import dlbin_tables

RECORD_SIZE = dlbin_tables.DAMAGE_RECORD_SIZE

# The reference row used by the diagnostics and by the tests: the R-4 Hyena
# damage row, identified by its *enum id* rather than by its array position.
#
# This is deliberately no longer a gate on parsing. It used to be one, and
# gating on a balance-dependent value is exactly what broke on the update: a
# buff to R-4 would have made the whole editor refuse to start. Parsing is now
# structural (see `array_start`); this row is only cross-checked and reported.
#
# The values move with the game: 1.8.45317 had id 137 / position 137, and
# 1.8.45850 has id 142 / position 147 (damage, AP and forces are unchanged).
# They are read from `data/weapon_names.json` when it is available so the
# diagnostic tracks the shipped data; the literals below are only a fallback for
# a checkout with no derived data. A mismatch is reported, never fatal.
def _reference_from_map() -> dict | None:
    try:
        p = Path(__file__).resolve().parent.parent / "data" / "weapon_names.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        w = next(x for x in doc["weapons"] if x["page"] == "R-4 Hyena")
        return {
            "type_id": w["damage_index"],
            "position": w["damage_position"],
            "damage": (w["damage"], w["durable"]),
            "ap": list(w["ap"]),
        }
    except Exception:
        return None


_ref = _reference_from_map()
ANCHOR_TYPE_ID = _ref["type_id"] if _ref else 142
ANCHOR_INDEX = _ref["position"] if _ref else 147
ANCHOR_DAMAGE = _ref["damage"] if _ref else (220, 45)
ANCHOR_AP = _ref["ap"] if _ref else [3, 3, 3, 0]
# Forces are not in the map, so they stay literal and are reported only.
ANCHOR_FORCES = (10, 20, 14)


@dataclass
class DamageInfo:
    """One `DamageInfoType_*` entry: the stats a projectile deals on hit.

    Two numbers identify a record and they are NOT interchangeable:

    * `index` - the row's position in the array (0..638). Use it to *address*
      a row in memory: the runtime record address is
      `array_base + index * 76`.
    * `type_id` - the raw value stored at record offset 0. Use it to *identify*
      a row. This is the number projectiles and explosions reference, and the
      number that stays stable across builds when rows are reordered.

    The runtime resolver builds its search pattern from `type_id` and pins the
    neighbours by their `type_id`s. Using `index` there made most rows
    unfindable while still passing a test on a row where the two coincide.
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
        """Armor penetration on a direct hit - what the wiki calls "pen"."""
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


def array_start(blob: bytes) -> int:
    """Offset where damage record 0 begins, read from the container.

    Structural and build-independent: it reads the `DamageSettings` LDLD block
    and its DLArray descriptor. No record content is consulted, so a rebalance
    or a renumbering cannot move it.
    """
    _, start, end = dlbin_tables.find_block(blob, dlbin_tables.TYPE_DAMAGE)
    arr, count = dlbin_tables.dl_array(blob, start, RECORD_SIZE, limit=end)
    if arr + count * RECORD_SIZE != end:
        raise ValueError(
            f"the damage array does not fill its LDLD block: records "
            f"{arr}..{arr + count * RECORD_SIZE} but the block ends at {end}; "
            "the container layout changed - re-derive before trusting output"
        )
    return arr


def anchor_start(blob: bytes) -> int:
    """Deprecated alias for `array_start`, kept so callers keep working.

    It no longer searches for the R-4 fingerprint. See the module docstring for
    why that search was removed.
    """
    return array_start(blob)


def parse_damage_records(blob: bytes, start: int = 0) -> list[DamageInfo]:
    """Decode a damage-record array that begins at `start`."""
    if (len(blob) - start) % RECORD_SIZE != 0:
        raise ValueError(
            f"{(len(blob) - start)} bytes from offset {start} is not a multiple "
            f"of the {RECORD_SIZE}-byte record size - wrong start offset"
        )
    n = (len(blob) - start) // RECORD_SIZE
    return [_decode_record(blob, start + i * RECORD_SIZE, i) for i in range(n)]


def parse_damage_settings_file(path: str | Path) -> list[DamageInfo]:
    """Parse a full `generated_damage_settings.dl_bin`."""
    blob = load_any(path)
    return parse_damage_records(blob, array_start(blob))


def position_of_type_id(records, type_id: int) -> int | None:
    """Map a `DamageInfoType` enum value to its row position, or None.

    Accepts either a list of records or a `{position: record}` mapping, because
    the two call shapes exist in this codebase and both mean the same thing.
    """
    if isinstance(records, dict):
        records = records.values()
    for rec in records:
        if rec.type_id == type_id:
            return rec.index
    return None


def reference_row(records: list[DamageInfo]) -> DamageInfo | None:
    """The R-4 reference row, found by enum id. None when it is absent."""
    pos = position_of_type_id(records, ANCHOR_TYPE_ID)
    return None if pos is None else records[pos]


def reference_row_differs(records: list[DamageInfo]) -> str | None:
    """Describe how the reference row differs from its recorded values.

    Returns None when it matches, or a human-readable difference. This is a
    *diagnostic*, not a gate: a balance change legitimately moves these
    numbers, and refusing to run over one is what made the editor brittle.
    """
    row = reference_row(records)
    if row is None:
        return f"no row carries type id {ANCHOR_TYPE_ID}"
    problems = []
    if (row.damage, row.durable_damage) != ANCHOR_DAMAGE:
        problems.append(
            f"damage is {row.damage}/{row.durable_damage}, recorded "
            f"{ANCHOR_DAMAGE[0]}/{ANCHOR_DAMAGE[1]}"
        )
    if row.armor_penetration_per_angle != ANCHOR_AP:
        problems.append(
            f"armor penetration is {row.armor_penetration_per_angle}, recorded "
            f"{ANCHOR_AP}"
        )
    forces = (row.demolition_strength, row.force_strength, row.force_impulse)
    if forces != ANCHOR_FORCES:
        problems.append(f"forces are {forces}, recorded {ANCHOR_FORCES}")
    return "; ".join(problems) if problems else None


def load_any(path: str | Path) -> bytes:
    """Read a `.dl_bin`, transparently gunzipping a `.dl_bin.gz`."""
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    return raw


if __name__ == "__main__":
    import sys

    target = (sys.argv[1] if len(sys.argv) > 1
              else "data/raw/generated_damage_settings.dl_bin")
    blob = load_any(target)
    start = array_start(blob)
    recs = parse_damage_records(blob, start)
    print(f"parsed {len(recs)} damage records from {target}")
    print(f"  array starts at {start} (0x{start:x})")

    diff = reference_row_differs(recs)
    row = reference_row(recs)
    if row is None:
        print(f"  reference row (type id {ANCHOR_TYPE_ID}): MISSING")
    else:
        print(
            f"  reference row: position {row.index}, type id {row.type_id}, "
            f"damage {row.damage}/{row.durable_damage}, "
            f"ap {row.armor_penetration_per_angle}, "
            f"forces {row.demolition_strength}/{row.force_strength}/"
            f"{row.force_impulse}"
        )
        print(f"  cross-check vs recorded values: {diff or 'MATCH'}")

    out = Path("data/damage_records.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([r.as_dict() for r in recs], indent=1),
                   encoding="utf-8")
    print(f"wrote {out}")
