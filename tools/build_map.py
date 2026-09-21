"""Build the weapon -> projectile -> damage mapping from the decrypted tables.

The chain, verified end to end against the shipped files:

    damage record (index 137)
        ^  referenced by a projectile's `damage_info_type` field
    projectile record (row 245)   <- speed 950, mass 20 g, calibre 9
        ^  referenced by a weapon's ammo variant
    weapon ammo variant

Why this matters for "only my weapon changes": the R-4 does **not** share its
damage row with the rest of the 9x70mm family. Sample of the family as it
actually sits in the file:

    damage[136] <- projectile 239   fmj   165/45  AP2   speed 850
    damage[137] <- projectile 245   R-4   220/45  AP3   speed 950   <-- unique
    damage[138] <- projectile 240   hv    200/50  AP3   speed 940

Each damage row here is referenced by exactly one projectile, so editing row
137 cannot leak into another weapon. **That property is checked, not assumed** -
`assert_exclusive` fails loudly if a future build shares the row, because then
the edit would silently change weapons the user did not select.

Field offsets were derived by probing, not from a published struct (filediver
exposes the record as an opaque blob for this table):

    projectile record, stride 272:
        +0   int32   sequence number (1..N, unique, not the damage index)
        +24  float   calibre
        +32  float   speed (m/s)
        +36  float   mass (g)
        +60  int32   damage_info_type -> damage record's `index` field

    damage record, stride 76:
        +0   int32   index
        +4   int32   damage (standard)
        +8   int32   durable damage
        +12  uint32  armor_penetration_per_angle[4]

The +60 offset was identified by requiring that most projectiles resolve to a
damage row whose standard damage is in a sane range (325/343 resolved; the
alternatives scored 25, 3 and 0). +32/+36 were identified by matching the known
9x70mm speeds (850/940/950/1050) and mass (20 g).
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, asdict
from pathlib import Path

from parse_dlbin import (
    RECORD_SIZE as DAMAGE_RECORD_SIZE,
    DamageInfo,
    anchor_start,
)

PROJECTILE_RECORD_SIZE = 272
PROJECTILE_OFF_CALIBRE = 24
PROJECTILE_OFF_SPEED = 32
PROJECTILE_OFF_MASS = 36
PROJECTILE_OFF_DAMAGE_INFO_TYPE = 60

# Container: u32 count, then per item a 24-byte LDLD header, then the payload.
# For generated_projectile_settings the payload is a DLArray (offset u64 + count u64).
PROJECTILE_ARRAY_OFFSET = 0x1C  # data start of item 0
PROJECTILE_ARRAY_COUNT = 343

# Values at +60 that cannot be a damage-type id, because no row carries them.
#
# The damage table's ids start at 4 (rows 0..3 hold ids 4..19), so 1/2/3 are not
# ids at all. They appear on six projectile rows, all of them flame weapons
# (Flamethrower, Torcher, Crisper, Cremator, Stoker, Dog Breath). Flame weapons
# do not deal their damage through a damage row the way a bullet does - they
# apply a burning STATUS, and the status carries the damage. So these rows
# reference a status id in the same slot rather than a damage id.
#
# They are recorded as `damage_type = None` rather than guessed at: a wrong
# damage row is worse than no row, because the editor would offer to change
# numbers that have nothing to do with the weapon.
PROJECTILE_STATUS_SENTINELS = {1, 2, 3}


@dataclass
class Projectile:
    """One projectile record.

    `damage_type` is the **damage-type id** stored at +60 - the same enum value
    the damage table keeps at each row's +0 - and `damage_position` is that row's
    array index, or None when the id resolves to no row.

    Both are carried because they mean different things and the distinction has
    already caused one shipped bug. The field is literally named
    `damage_info_type`: it is an id, not an index. Reading it as an index picks a
    row whose id happens to equal the number - which is a different weapon's
    damage whenever id != position, and 519 of 634 rows are in that state.
    """

    row: int
    sequence: int
    calibre: float
    speed: float
    mass: float
    damage_type: int
    damage_position: int | None

    def as_dict(self) -> dict:
        return asdict(self)


def parse_projectiles(blob: bytes) -> list[Projectile]:
    """Decode the projectile table.

    `damage_position` is resolved by the caller, which is the only place that
    knows the damage table; this function records the raw id it reads.
    """
    off, count = struct.unpack_from("<QQ", blob, PROJECTILE_ARRAY_OFFSET)
    arr = PROJECTILE_ARRAY_OFFSET + off
    avail = len(blob) - arr
    if count != PROJECTILE_ARRAY_COUNT or avail % PROJECTILE_RECORD_SIZE:
        raise ValueError(
            f"unexpected projectile layout: count={count} avail={avail} "
            f"(stride {PROJECTILE_RECORD_SIZE} leaves {avail % PROJECTILE_RECORD_SIZE} over)"
        )
    out = []
    for i in range(count):
        base = arr + i * PROJECTILE_RECORD_SIZE
        raw_type = struct.unpack_from(
            "<i", blob, base + PROJECTILE_OFF_DAMAGE_INFO_TYPE
        )[0]
        out.append(
            Projectile(
                row=i,
                sequence=struct.unpack_from("<i", blob, base)[0],
                calibre=struct.unpack_from("<f", blob, base + PROJECTILE_OFF_CALIBRE)[0],
                speed=struct.unpack_from("<f", blob, base + PROJECTILE_OFF_SPEED)[0],
                mass=struct.unpack_from("<f", blob, base + PROJECTILE_OFF_MASS)[0],
                damage_type=raw_type,
                # Filled in by resolve_damage_positions once the damage table is
                # available. None means "this row does not reference a damage
                # row" - either a status sentinel (flame weapons) or an id the
                # table does not contain.
                damage_position=None,
            )
        )
    return out


def resolve_damage_positions(
    projectiles: list[Projectile], damages: dict[int, "DamageInfo"]
) -> list[str]:
    """Turn each projectile's damage-type id into the row it addresses.

    The projectile field is an ID; the damage table is indexed by POSITION. The
    two are different numbering schemes - ids run 4..639, positions 0..633, and
    519 of 634 rows have id != position - so a lookup that skips this step picks
    whichever row happens to sit at that index, i.e. a different weapon's damage.

    Returns a list of human-readable notes for the rows that could not be
    resolved, so a caller can report them instead of silently dropping them.
    """
    by_type: dict[int, int] = {}
    for position, damage in damages.items():
        by_type.setdefault(damage.type_id, position)

    notes: list[str] = []
    for p in projectiles:
        if p.damage_type in PROJECTILE_STATUS_SENTINELS:
            # Not an id: flame weapons apply a burning status and the status
            # carries the damage, so this slot holds a status reference. Left as
            # None on purpose - pointing it at "the row numbered 2" would be a
            # fabricated mapping.
            notes.append(
                f"projectile {p.row}: +60={p.damage_type} is a status reference "
                f"(flame weapon), not a damage row"
            )
            continue
        position = by_type.get(p.damage_type)
        if position is None:
            notes.append(
                f"projectile {p.row}: damage type {p.damage_type} has no row"
            )
            continue
        p.damage_position = position
    return notes


def parse_damages(blob: bytes) -> dict[int, DamageInfo]:
    """Decode the damage table, keyed by **position** (0..633).

    Two ways to address a damage row exist and the game mixes them, so callers
    must be explicit about which they hold:

    * **position** - the row's index in the array. The projectile table's
      `damage_info_type` field (offset +60) references *positions*: all 229 of
      its distinct values are < 634, and four of them have no matching type_id.
    * **type_id** - the value stored at a record's +0. The explosion table's
      DamageInfoType (offset +4) references *type_ids*: three of its values are
      635/636/639, which cannot be positions in a 634-row array.

    This function keys by position because that is what addresses the row for a
    write. To go from a type_id (what `weapon_names.json.damage_index` holds, and
    what the runtime pattern is built from) use `position_of_type_id`.
    """
    start = anchor_start(blob)
    n = (len(blob) - start) // DAMAGE_RECORD_SIZE
    out: dict[int, DamageInfo] = {}
    for i in range(n):
        base = start + i * DAMAGE_RECORD_SIZE
        type_id = struct.unpack_from("<i", blob, base)[0]
        dmg, dur = struct.unpack_from("<ii", blob, base + 4)
        ap = list(struct.unpack_from("<4I", blob, base + 12))
        dem, fs, fi, elem = struct.unpack_from("<4I", blob, base + 28)
        effects = [
            struct.unpack_from("<if", blob, base + 44 + k * 8) for k in range(4)
        ]
        out[i] = DamageInfo(
            index=i,
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
    return out


def position_of_type_id(damages: dict[int, DamageInfo], type_id: int) -> int | None:
    """Map a type_id to its row position, or None when unknown.

    Kept as a search rather than a cached dict on purpose: a caller that gets
    None back must handle it (a stale id means the table moved), and a lookup
    helper that silently returned a wrong position would reintroduce exactly the
    bug this exists to prevent.
    """
    for position, record in damages.items():
        if record.type_id == type_id:
            return position
    return None


def assert_exclusive(projectiles: list[Projectile], damage_index: int) -> int:
    """Assert exactly one projectile references this damage row.

    This is the guard behind the user's requirement "edit only my weapon". If a
    row is shared, an edit aimed at one weapon also changes the others - which
    would be a silent, hard-to-notice correctness failure. Fail loudly instead.
    """
    users = [p for p in projectiles if p.damage_position == damage_index]
    if len(users) != 1:
        raise ValueError(
            f"damage row {damage_index} is referenced by {len(users)} projectiles "
            f"(rows {[p.row for p in users]}) - editing it would affect more than one "
            "weapon; refusing"
        )
    return users[0].row


def build(
    damage_path: str | Path, projectile_path: str | Path
) -> tuple[dict[int, DamageInfo], list[Projectile]]:
    """Parse both tables and resolve each projectile's damage-type id to a row.

    The resolution step belongs here rather than in `parse_projectiles`, because
    it needs the damage table to exist first - and a projectile parsed on its own
    can only report the id it read, not the row that id addresses.
    """
    dblob = Path(damage_path).read_bytes()
    pblob = Path(projectile_path).read_bytes()
    damages = parse_damages(dblob)
    projectiles = parse_projectiles(pblob)
    resolve_damage_positions(projectiles, damages)
    return damages, projectiles


if __name__ == "__main__":
    import sys

    dpath = sys.argv[1] if len(sys.argv) > 1 else "data/raw/generated_damage_settings.dl_bin"
    ppath = sys.argv[2] if len(sys.argv) > 2 else "data/raw/generated_projectile_settings.dl_bin"

    damages, projectiles = build(dpath, ppath)
    print(f"damage rows: {len(damages)}   projectile rows: {len(projectiles)}")

    # The verified R-4 chain.
    R4_DAMAGE = 137
    row = assert_exclusive(projectiles, R4_DAMAGE)
    p = next(x for x in projectiles if x.row == row)
    d = damages[R4_DAMAGE]
    print()
    print(f"R-4 chain (exclusivity asserted):")
    print(f"  damage[{R4_DAMAGE}]  damage={d.damage}/{d.durable_damage} "
          f"ap={d.armor_penetration_per_angle}")
    print(f"  projectile row {row}: speed={p.speed} mass={p.mass} calibre={p.calibre}")
    ok = (
        (d.damage, d.durable_damage) == (220, 45)
        and d.armor_penetration_per_angle == [3, 3, 3, 0]
        and p.speed == 950.0
        and p.mass == 20.0
        and p.calibre == 9.0
    )
    print(f"  cross-check vs wiki (220/45, AP3, 950 m/s, 20 g, 9 mm): "
          f"{'PASS' if ok else 'FAIL'}")

    out = Path("data/mapping.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "damages": {str(k): v.as_dict() for k, v in damages.items()},
                "projectiles": [p.as_dict() for p in projectiles],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out}")
