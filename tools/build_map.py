"""Build the weapon -> projectile -> damage mapping from the decrypted tables.

The chain, verified end to end against the shipped files:

    damage record (type id 137, position 142)
        ^  referenced by a projectile's `damage_info_type` field (+60),
           which holds the TYPE ID, not the row position
    projectile record (row 245)   <- speed 950, mass 20 g, calibre 9
        ^  referenced by a weapon's ammo variant
    weapon ammo variant

Why this matters for "only my weapon changes": the R-4 does **not** share its
damage row with the rest of the 9x70mm family. Sample of the family as it
actually sits in the file:

    type id 136 <- projectile 239   fmj   165/45  AP2   speed 850
    type id 137 <- projectile 245   R-4   220/45  AP3   speed 950   <-- unique
    type id 138 <- projectile 240   hv    200/50  AP3   speed 940

Each damage row here is referenced by exactly one projectile, so editing that
row cannot leak into another weapon. **That property is checked, not assumed** -
`assert_exclusive` fails loudly if a future build shares the row, because then
the edit would silently change weapons the user did not select.

FIELD OFFSETS

    projectile record, stride 272:
        +0   int32   sequence number
        +4   uint32  name_upper    (localisation key hash)
        +8   uint32  name_cased
        +16  int32   projectile type (enum)
        +24  float   calibre
        +32  float   speed (m/s)
        +36  float   mass (g)
        +40  float   drag
        +44  float   gravity multiplier
        +60  int32   damage_info_type  -> damage record's TYPE ID
        +144 uint32  explosion type    (primary)
        +156 uint32  explosion type    (cross-check)

    damage record, stride 76:
        +0   int32   type id (DamageInfoType enum)
        +4   int32   damage (standard)
        +8   int32   durable damage
        +12  uint32  armor_penetration_per_angle[4]

The +32/+36 offsets were identified by matching the known 9x70mm speeds
(850/940/950/1050) and mass (20 g). The Go struct in filediver
(`rawProjectileInfo`, generated from the game's own typelib) confirms the field
order, so these are no longer guesswork.

THE BUG THIS FILE USED TO HAVE

It read +60 as an ARRAY POSITION. It looked right because on the row it was
tested against the id and the position happened to coincide. They do not in
general - in the current build 614 of 639 rows differ - so most projectiles
resolved to a different weapon's damage. The justification written here was
"all 229 of its distinct values are < 634", which is true of the ids as well and
proves nothing. The explosion table settled it: its `DamageInfoType` values
reach 639 while the table has 413 rows, so they cannot be positions. The wiki
cross-check agrees: of 91 weapons with published impact numbers, 65 match when
read as ids and 0 match when read as positions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import dlbin_tables
from parse_dlbin import (
    RECORD_SIZE as DAMAGE_RECORD_SIZE,
    DamageInfo,
    position_of_type_id,
)

PROJECTILE_RECORD_SIZE = dlbin_tables.PROJECTILE_RECORD_SIZE
PROJECTILE_OFF_CALIBRE = 24
PROJECTILE_OFF_SPEED = 32
PROJECTILE_OFF_MASS = 36
PROJECTILE_OFF_DAMAGE_INFO_TYPE = 60

# Container: u32 count, then a 24-byte LDLD header, then the payload. The
# projectile file has a single block, so its payload starts at 0x1C and the
# DLArray descriptor there resolves the array. Kept as a name because callers
# read the descriptor directly; `dlbin_tables` is the authority.
PROJECTILE_ARRAY_OFFSET = 0x1C


@dataclass
class Projectile:
    """One projectile record.

    `damage_type` is the **DamageInfoType enum value** stored at +60 - the same
    number the damage table keeps at each row's +0 - and `damage_position` is
    that row's array index, or None when the id resolves to no row.

    Both are carried because they mean different things and the distinction has
    already caused one shipped bug. The field is literally named
    `damage_info_type`: it is an id, not an index.
    """

    row: int
    sequence: int
    calibre: float
    speed: float
    mass: float
    drag: float
    gravity: float
    damage_type: int
    damage_position: int | None
    explosion_type: int

    def as_dict(self) -> dict:
        return asdict(self)


def parse_projectiles(blob: bytes) -> list[Projectile]:
    """Decode the projectile table.

    `damage_position` is resolved by the caller, which is the only place that
    knows the damage table; this function records the raw enum value it reads.
    """
    rows = dlbin_tables.parse_projectiles(blob)
    return [
        Projectile(
            row=r["row"],
            sequence=r["sequence"],
            calibre=r["calibre"],
            speed=r["speed"],
            mass=r["mass"],
            drag=r["drag"],
            gravity=r["gravity"],
            damage_type=r["damage_type"],
            # Filled in by resolve_damage_positions once the damage table is
            # available. None means "this row references no damage row".
            damage_position=None,
            explosion_type=r["explosion_type"] or r["explosion_type_alt"],
        )
        for r in rows
    ]


def resolve_damage_positions(
    projectiles: list[Projectile], damages: dict[int, DamageInfo]
) -> list[str]:
    """Turn each projectile's damage-type id into the row it addresses.

    The projectile field is an enum id; the array is indexed by POSITION. The
    two are different numbering schemes, so a lookup that skips this step picks
    whichever row happens to sit at that index - a different weapon's damage.

    Returns human-readable notes for the rows that could not be resolved, so a
    caller can report them instead of silently dropping them.
    """
    notes: list[str] = []
    for p in projectiles:
        if p.damage_type == 0:
            # 0 means "no damage row". It is the only value in either table that
            # does not resolve, and it is a real absence rather than an id.
            continue
        position = position_of_type_id(damages, p.damage_type)
        if position is None:
            notes.append(
                f"projectile {p.row}: damage type id {p.damage_type} has no row"
            )
            continue
        p.damage_position = position
    return notes


def parse_damages(blob: bytes) -> dict[int, DamageInfo]:
    """Decode the damage table, keyed by **position** (0..638).

    Keyed by position because that is what addresses a row for a write: the
    runtime record address is `array_base + position * 76`. To go from an enum
    id - what `weapon_names.json.damage_index` holds, and what the runtime
    pattern is built from - use `position_of_type_id`.
    """
    out: dict[int, DamageInfo] = {}
    for r in dlbin_tables.parse_damages(blob):
        out[r["position"]] = DamageInfo(
            index=r["position"],
            type_id=r["type_id"],
            damage=r["damage"],
            durable_damage=r["durable_damage"],
            armor_penetration_per_angle=list(r["armor_penetration_per_angle"]),
            demolition_strength=r["demolition_strength"],
            force_strength=r["force_strength"],
            force_impulse=r["force_impulse"],
            element_type=r["element_type"],
            status_effects=[tuple(e) for e in r["status_effects"]],
        )
    return out


def position_of_type_id(damages: dict[int, DamageInfo], type_id: int) -> int | None:
    """Map a `DamageInfoType` enum value to its row position, or None.

    Kept as a search rather than a cached dict on purpose: a caller that gets
    None back must handle it (a stale id means the table moved), and a lookup
    helper that silently returned a wrong position would reintroduce exactly the
    bug this exists to prevent.
    """
    for position, record in damages.items():
        if record.type_id == type_id:
            return position
    return None


def assert_exclusive(projectiles: list[Projectile], damage_position: int) -> int:
    """Assert exactly one projectile references this damage row.

    This is the guard behind the user's requirement "edit only my weapon". If a
    row is shared, an edit aimed at one weapon also changes the others - which
    would be a silent, hard-to-notice correctness failure. Fail loudly instead.
    """
    users = [p for p in projectiles if p.damage_position == damage_position]
    if len(users) != 1:
        raise ValueError(
            f"damage row {damage_position} is referenced by {len(users)} "
            f"projectiles (rows {[p.row for p in users]}) - editing it would "
            "affect more than one weapon; refusing"
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

    dpath = (sys.argv[1] if len(sys.argv) > 1
             else "data/raw/generated_damage_settings.dl_bin")
    ppath = (sys.argv[2] if len(sys.argv) > 2
             else "data/raw/generated_projectile_settings.dl_bin")

    damages, projectiles = build(dpath, ppath)
    print(f"damage rows: {len(damages)}   projectile rows: {len(projectiles)}")

    # The verified R-4 chain, addressed by enum id.
    R4_TYPE_ID = 137
    r4_position = position_of_type_id(damages, R4_TYPE_ID)
    if r4_position is None:
        print(f"R-4 (type id {R4_TYPE_ID}) is not in this table")
        raise SystemExit(1)
    row = assert_exclusive(projectiles, r4_position)
    p = next(x for x in projectiles if x.row == row)
    d = damages[r4_position]
    print()
    print("R-4 chain (exclusivity asserted):")
    print(f"  damage type id {d.type_id} at position {d.index}: "
          f"{d.damage}/{d.durable_damage} ap={d.armor_penetration_per_angle}")
    print(f"  projectile row {row}: speed={p.speed} mass={p.mass} "
          f"calibre={p.calibre}")
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
