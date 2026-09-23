"""Parse Helldivers 2 `.dl_bin` data tables from the container itself.

WHY THIS REPLACES THE ANCHOR SEARCH

An earlier parser located the damage array by searching for the R-4 row's
fingerprint (137, 220, 45, AP[3,3,3,0], 10, 20, 14) and subtracting
`137 * 76`. Two things were wrong with that:

1. It used a *balance-dependent* value as a structural anchor. The 1.8.45850
   build renumbered the type ids, so the fingerprint stopped identifying R-4.
2. Even in the old build it subtracted the wrong row number. The anchor row
   sits at position 142, not 137, so the computed start was 480 instead of 100
   and the parse silently dropped the first five rows. Every downstream offset
   inherited that 380-byte shift, and the ARRAY_START constant in the mod was
   tuned to cancel it - so the bug was invisible until the build changed.

THE CONTAINER (verified byte-for-byte on both builds)

    +0    u32                        item count
    then per item:
      +0   "LDLD" magic
      +4   u32  version (1)
      +8   u32  type hash  (djb2 of the type name)
      +12  u32  size       (payload byte count)
      +16  8 bytes of flags/padding
      +24  payload, `size` bytes long

    so item N+1 begins at item N + 24 + size. Both builds satisfy
    `magic + 24 + size == len(blob)` exactly for the last item.

Inside a settings payload the first thing is a DLArray descriptor:

    +0   u64 offset   (relative to the payload start, i.e. to this descriptor)
    +8   u64 count

`offset` is 16 in every settings table here, so the record array starts 16
bytes after the descriptor. This is exact and build-independent - it cannot be
knocked out by a rebalance, which is the property the anchor search lacked.

IN-MEMORY FORM DIFFERS (see the runtime resolver): in the process, the DLArray
`offset` field holds an absolute pointer rather than a relative offset. Both
interpretations are tried at runtime and decided by whether the decoded record
identity matches; they must not be conflated.
"""
from __future__ import annotations

import struct

DAMAGE_RECORD_SIZE = 76
PROJECTILE_RECORD_SIZE = 272
EXPLOSION_RECORD_SIZE = 152

LDLD_HEADER = 24

# djb2 type hashes of the settings type names. These are a *stable* contract:
# the hash is derived from the type name, so it survives rebalances and even
# recompiles, unlike record counts or row order.
TYPE_DAMAGE = 0xE0A72CF0        # DamageSettings
TYPE_PROJECTILE = 0xBD4042C2    # ProjectileSettings
TYPE_EXPLOSION = 0x2AEA2592     # ExplosionSettings
TYPE_WEAPON_CUSTOM = 0x1E604234 # WeaponCustomizationSettings


def djb2(name: str) -> int:
    """The engine's type-name hash: djb2 with seed 5381, minus the seed."""
    r = 5381
    for ch in name:
        r = (r * 33 + ord(ch)) & 0xFFFFFFFF
    return (r - 5381) & 0xFFFFFFFF


def iter_blocks(blob: bytes):
    """Yield (magic, version, type_hash, size, payload_start, payload_end)."""
    if len(blob) < 4:
        return
    count = struct.unpack_from("<I", blob, 0)[0]
    pos = 4
    for _ in range(count):
        if pos + LDLD_HEADER > len(blob) or blob[pos:pos + 4] != b"LDLD":
            return
        ver, typ, size = struct.unpack_from("<III", blob, pos + 4)
        start = pos + LDLD_HEADER
        end = start + size
        if end > len(blob):
            raise ValueError(
                f"LDLD block at {pos} claims {size} payload bytes but only "
                f"{len(blob) - start} remain"
            )
        yield pos, ver, typ, size, start, end
        pos = end


def find_block(blob: bytes, type_hash: int):
    """Return (magic, payload_start, payload_end) for a type, or raise."""
    for magic, ver, typ, size, start, end in iter_blocks(blob):
        if typ == type_hash:
            return magic, start, end
    raise ValueError(
        f"no LDLD block with type hash 0x{type_hash:08X} in this file "
        f"(found {[hex(t) for _, _, t, _, _, _ in iter_blocks(blob)]})"
    )


def dl_array(blob: bytes, payload_start: int, stride: int, limit: int | None = None):
    """Resolve the DLArray at `payload_start` to (absolute_offset, count)."""
    off, count = struct.unpack_from("<QQ", blob, payload_start)
    arr = payload_start + off
    if limit is not None and arr + count * stride > limit:
        raise ValueError(
            f"DLArray at {payload_start} spans {arr}..{arr + count * stride} "
            f"which overruns the block end {limit}"
        )
    return arr, count


def _damage_record(blob: bytes, base: int, position: int) -> dict:
    type_id, dmg, dur = struct.unpack_from("<iii", blob, base)
    ap = list(struct.unpack_from("<4I", blob, base + 12))
    dem, fs, fi, elem = struct.unpack_from("<4I", blob, base + 28)
    effects = [list(struct.unpack_from("<if", blob, base + 44 + k * 8))
               for k in range(4)]
    return dict(
        position=position, type_id=type_id, damage=dmg, durable_damage=dur,
        armor_penetration_per_angle=ap, demolition_strength=dem,
        force_strength=fs, force_impulse=fi, element_type=elem,
        status_effects=effects,
    )


def parse_damages(blob: bytes) -> list[dict]:
    """Decode the damage table, indexed by ARRAY POSITION.

    `type_id` (record +0) is a `DamageInfoType` enum value. Projectiles and
    explosions reference *that*, not the row position - the two numbering
    schemes differ on 614 of 639 rows in the current build. Use
    `position_of_type_id` to go from one to the other.
    """
    _, start, end = find_block(blob, TYPE_DAMAGE)
    arr, count = dl_array(blob, start, DAMAGE_RECORD_SIZE, limit=end)
    if arr + count * DAMAGE_RECORD_SIZE != end:
        raise ValueError(
            f"damage array does not fill its block: arr={arr} count={count} "
            f"end={arr + count * DAMAGE_RECORD_SIZE} block_end={end}"
        )
    return [_damage_record(blob, arr + i * DAMAGE_RECORD_SIZE, i)
            for i in range(count)]


def position_of_type_id(damages, type_id: int) -> int | None:
    """Map a DamageInfoType enum value to its row position, or None."""
    for rec in damages:
        if rec["type_id"] == type_id:
            return rec["position"]
    return None


def type_id_at(damages, position: int) -> int | None:
    """The enum value stored at a row position, or None if out of range."""
    for rec in damages:
        if rec["position"] == position:
            return rec["type_id"]
    return None


def parse_projectiles(blob: bytes) -> list[dict]:
    """Decode the projectile table. `damage_type` is a DamageInfoType enum."""
    _, start, end = find_block(blob, TYPE_PROJECTILE)
    arr, count = dl_array(blob, start, PROJECTILE_RECORD_SIZE, limit=end)
    out = []
    for i in range(count):
        b = arr + i * PROJECTILE_RECORD_SIZE
        out.append(dict(
            row=i,
            sequence=struct.unpack_from("<i", blob, b)[0],
            name_upper=struct.unpack_from("<I", blob, b + 4)[0],
            name_cased=struct.unpack_from("<I", blob, b + 8)[0],
            projectile_type=struct.unpack_from("<i", blob, b + 16)[0],
            calibre=struct.unpack_from("<f", blob, b + 24)[0],
            speed=struct.unpack_from("<f", blob, b + 32)[0],
            mass=struct.unpack_from("<f", blob, b + 36)[0],
            drag=struct.unpack_from("<f", blob, b + 40)[0],
            gravity=struct.unpack_from("<f", blob, b + 44)[0],
            damage_type=struct.unpack_from("<i", blob, b + 60)[0],
            explosion_type=struct.unpack_from("<I", blob, b + 144)[0],
            explosion_type_alt=struct.unpack_from("<I", blob, b + 156)[0],
        ))
    return out


def parse_explosions(blob: bytes) -> list[dict]:
    """Decode the explosion table. `damage_type` is a DamageInfoType enum."""
    _, start, end = find_block(blob, TYPE_EXPLOSION)
    arr, count = dl_array(blob, start, EXPLOSION_RECORD_SIZE, limit=end)
    out = []
    for i in range(count):
        b = arr + i * EXPLOSION_RECORD_SIZE
        etype, dtype = struct.unpack_from("<ii", blob, b)
        inner, outer, stagger, cone = struct.unpack_from("<4f", blob, b + 16)
        out.append(dict(
            position=i, explosion_type=etype, damage_type=dtype,
            inner_radius=inner, outer_radius=outer,
            stagger_radius=stagger, cone_angle=cone,
        ))
    return out
