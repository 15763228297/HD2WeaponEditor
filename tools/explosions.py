"""Parse the explosion table and link grenades/launchers to their damage rows.

Why this exists: many weapons deal their damage through an *explosion*, not the
projectile's own damage row. A grenade launcher's projectile carries a token
20/2 (the impact), while the real payload sits in `generated_explosion_settings`
and is referenced from the projectile record. Without this table, 20 weapons
(grenades, launchers, mines) come out with no usable damage entry or with a
misleading one.

Layout (stride 152), confirmed field by field against GL-21:

    +0    int32   ExplosionType        (the key projectiles reference)
    +4    int32   DamageInfoType       -> damage record TYPE ID
    +8    u64     UnkHash
    +16   float32 InnerRadius
    +20   float32 OuterRadius
    +24   float32 StaggerRadius
    +28   float32 ConeAngle
    +32   float32 CameraShakeRadius

The Go struct in filediver (`rawExplosionInfo`) lists `UnkBool uint8` + `_ [3]uint8`
before the radii, which would put InnerRadius at +20; the file disagrees.
Verified by value instead: GL-21's explosion carries 3.5 / 7.5 / 8 at
+16/+20/+24, which is exactly the wiki's "Inner Radius 3.5 / Outer Radius 7.5 /
Shockwave Radius 8". So the reliable rule is: trust the decoded values against a
known weapon, and treat the Go struct as a field *list* whose offsets may be
shifted by padding.

The link from a weapon to its explosion: the projectile record carries the
explosion type id at **+144 and +156** (both hold the same value on GL-21's
row). Verified: row 80 ('40mm HE Grenade') carries 353, and explosion type 353
is `400/400 AP[3,0,0,0]` - matching the wiki's GL-21 entry exactly.

`damage_index` is the raw `DamageInfoType` ENUM value, not a row position. It
reaches 639 while this table has 413 rows, which is how the id/position
mix-up was originally detected. Resolve it with `position_of_type_id`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import dlbin_tables

EXPLOSION_RECORD_SIZE = dlbin_tables.EXPLOSION_RECORD_SIZE
EXPLOSION_OFF_TYPE = 0
EXPLOSION_OFF_DAMAGE_TYPE = 4
EXPLOSION_OFF_INNER = 16
EXPLOSION_OFF_OUTER = 20
EXPLOSION_OFF_STAGGER = 24
EXPLOSION_OFF_CONE = 28
EXPLOSION_OFF_CAMERA_SHAKE = 32

# Container: u32 count, then a 24-byte LDLD header, then the payload whose first
# 16 bytes are the DLArray descriptor. Kept as a name because callers read the
# descriptor directly; `dlbin_tables` is the authority.
EXPLOSION_ARRAY_BASE = 0x1C

# Projectile -> explosion links. Both offsets carry the same id on the rows
# checked; +144 is used as primary and +156 as a cross-check.
PROJECTILE_OFF_EXPLOSION = 144
PROJECTILE_OFF_EXPLOSION_ALT = 156


@dataclass
class Explosion:
    index: int
    type_id: int
    damage_index: int
    inner_radius: float
    outer_radius: float
    stagger_radius: float
    cone_angle: float

    def as_dict(self) -> dict:
        return asdict(self)


def parse_explosions(blob: bytes) -> dict[int, Explosion]:
    """Parse the explosion table, keyed by ExplosionType id."""
    out: dict[int, Explosion] = {}
    for r in dlbin_tables.parse_explosions(blob):
        out[r["explosion_type"]] = Explosion(
            index=r["position"],
            type_id=r["explosion_type"],
            damage_index=r["damage_type"],
            inner_radius=r["inner_radius"],
            outer_radius=r["outer_radius"],
            stagger_radius=r["stagger_radius"],
            cone_angle=r["cone_angle"],
        )
    return out


def projectile_explosion(blob: bytes, row: int) -> int | None:
    """Return the explosion type a projectile row carries, or None.

    Zero means "no explosion", which is the normal case for a plain bullet.
    """
    rows = dlbin_tables.parse_projectiles(blob)
    if not 0 <= row < len(rows):
        raise IndexError(f"projectile row {row} out of range")
    value = rows[row]["explosion_type"] or rows[row]["explosion_type_alt"]
    return value or None


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "tools"))
    from build_map import parse_projectiles, parse_damages
    from parse_dlbin import position_of_type_id
    from names import projectile_name

    eblob = (root / "data/raw/generated_explosion_settings.dl_bin").read_bytes()
    pblob = (root / "data/raw/generated_projectile_settings.dl_bin").read_bytes()
    dblob = (root / "data/raw/generated_damage_settings.dl_bin").read_bytes()

    explosions = parse_explosions(eblob)
    damages = parse_damages(dblob)
    projectiles = parse_projectiles(pblob)
    strings = json.loads((root / "data/strings.json").read_text(encoding="utf-8"))
    eng = {
        k: (v.get("English (US)") or v.get("English (UK)") or next(iter(v.values())))
        for k, v in strings.items()
    }

    print(f"explosions: {len(explosions)}")

    # Verify against the wiki's GL-21 numbers: 3.5 / 7.5 / 8, 400/400 AP3.
    et = projectile_explosion(pblob, 80)
    print(f"\nrow 80 = {projectile_name(pblob, 80, eng)!r}")
    print(f"  explosion type: {et}")
    if et in explosions:
        e = explosions[et]
        pos = position_of_type_id(damages, e.damage_index)
        d = damages[pos]
        print(f"  explosion damage (type id {e.damage_index} at position {pos}): "
              f"{d.damage}/{d.durable_damage} AP{d.armor_penetration_per_angle}")
        print(f"  radii: inner={e.inner_radius:g} outer={e.outer_radius:g} "
              f"stagger={e.stagger_radius:g} cone={e.cone_angle:g}")
        ok = ((d.damage, d.durable_damage) == (400, 400)
              and d.armor_penetration_per_angle[0] == 3)
        print(f"  wiki cross-check (400/400 AP3): {'PASS' if ok else 'FAIL'}")

    out = root / "data" / "explosions.json"
    out.write_text(
        json.dumps({str(k): v.as_dict() for k, v in explosions.items()}, indent=1),
        encoding="utf-8",
    )
    print(f"\nwrote {out}")
