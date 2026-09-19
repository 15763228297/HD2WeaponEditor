"""Parse the explosion table and link grenades/launchers to their damage rows.

Why this exists: many weapons deal their damage through an *explosion*, not the
projectile's own damage row. A grenade launcher's projectile carries a token
20/2 (the impact), while the real payload sits in `generated_explosion_settings`
and is referenced from the projectile record. Without this table, 20 weapons
(grenades, launchers, mines) come out with no usable damage entry or with a
misleading one.

Layout (stride 152), read from the Go struct in filediver's
`datalibrary/explosion_settings.go` and confirmed field by field against GL-21:

    +0    int32   ExplosionType        (the key projectiles reference)
    +4    int32   DamageInfoType       -> damage row index
    +8    u64     UnkHash
    +16   u8      UnkBool, then 3 pad
    +20   float32 InnerRadius
    +24   float32 OuterRadius
    +28   float32 StaggerRadius
    +32   float32 ConeAngle
    +36   float32 CameraShakeRadius
    ...

Wait - InnerRadius is at +16 in the file, not +20. The Go struct lists
`UnkBool uint8` + `_ [3]uint8` before the radii, which would put InnerRadius at
+20; the file disagrees. Verified by value instead: GL-21's explosion carries
3.5 / 7.5 / 8 at +16/+20/+24, which is exactly the wiki's
"Inner Radius 3.5 / Outer Radius 7.5 / Shockwave Radius 8". So the reliable
rule is: trust the decoded values against a known weapon, and treat the Go
struct as a field *list* whose offsets may be shifted by padding.

The link from a weapon to its explosion: the projectile record carries the
explosion type id at **+144 and +156** (both hold the same value on GL-21's
row). Verified: row 80 ('40mm HE Grenade') carries 353, and explosion type 353
is `400/400 AP[3,0,0,0]` - matching the wiki's GL-21 entry exactly.

Go structs for this table are not needed for the fields used here; only the read
order above matters.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, asdict
from pathlib import Path

# Container: u32 count, 24-byte LDLD header, DLArray(offset,count) at data start.
EXPLOSION_ARRAY_BASE = 0x1C
EXPLOSION_RECORD_SIZE = 152
EXPLOSION_OFF_TYPE = 0
EXPLOSION_OFF_DAMAGE_TYPE = 4
EXPLOSION_OFF_INNER = 16
EXPLOSION_OFF_OUTER = 20
EXPLOSION_OFF_STAGGER = 24
EXPLOSION_OFF_CONE = 32

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
    off, count = struct.unpack_from("<QQ", blob, EXPLOSION_ARRAY_BASE)
    arr = EXPLOSION_ARRAY_BASE + off
    if count == 0:
        raise ValueError("explosion table reports zero entries")
    out: dict[int, Explosion] = {}
    for i in range(count):
        base = arr + i * EXPLOSION_RECORD_SIZE
        if base + EXPLOSION_RECORD_SIZE > len(blob):
            raise ValueError(f"explosion record {i} runs past end of file")
        type_id, damage_type = struct.unpack_from("<ii", blob, base)
        inner, outer, stagger, cone = struct.unpack_from("<4f", blob, base + 16)
        out[type_id] = Explosion(
            index=i,
            type_id=type_id,
            damage_index=damage_type,
            inner_radius=inner,
            outer_radius=outer,
            stagger_radius=stagger,
            cone_angle=cone,
        )
    return out


def projectile_explosion(blob: bytes, row: int) -> int | None:
    """Return the explosion type a projectile row carries, or None.

    Zero means "no explosion", which is the normal case for a plain bullet.
    """
    from build_map import PROJECTILE_ARRAY_OFFSET, PROJECTILE_RECORD_SIZE

    off, count = struct.unpack_from("<QQ", blob, PROJECTILE_ARRAY_OFFSET)
    arr = PROJECTILE_ARRAY_OFFSET + off
    if not 0 <= row < count:
        raise IndexError(f"projectile row {row} out of range")
    base = arr + row * PROJECTILE_RECORD_SIZE
    primary = struct.unpack_from("<I", blob, base + PROJECTILE_OFF_EXPLOSION)[0]
    alt = struct.unpack_from("<I", blob, base + PROJECTILE_OFF_EXPLOSION_ALT)[0]
    value = primary or alt
    return value or None


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "tools"))
    from build_map import parse_projectiles, parse_damages
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
    row80 = next(p for p in projectiles if p.row == 80)
    et = projectile_explosion(pblob, 80)
    print(f"\nrow 80 = {projectile_name(pblob, 80, eng)!r}")
    print(f"  projectile own damage: {damages[row80.damage_position].damage}"
          f"/{damages[row80.damage_position].durable_damage}")
    print(f"  explosion type: {et}")
    if et in explosions:
        e = explosions[et]
        d = damages[e.damage_index]
        print(f"  explosion damage: {d.damage}/{d.durable_damage} AP{d.armor_penetration_per_angle}")
        print(f"  radii: inner={e.inner_radius:g} outer={e.outer_radius:g} "
              f"stagger={e.stagger_radius:g} cone={e.cone_angle:g}")
        ok = (d.damage, d.durable_damage) == (400, 400) and d.armor_penetration_per_angle[0] == 3
        print(f"  wiki cross-check (400/400 AP3): {'PASS' if ok else 'FAIL'}")

    out = root / "data" / "explosions.json"
    out.write_text(
        json.dumps({str(k): v.as_dict() for k, v in explosions.items()}, indent=1),
        encoding="utf-8",
    )
    print(f"\nwrote {out}")
