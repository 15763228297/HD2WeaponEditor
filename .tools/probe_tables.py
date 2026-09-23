
import struct, json, sys
from pathlib import Path

CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")

def load(p):
    return Path(p).read_bytes()

def find_container_header(blob, magic_off):
    # LDLD + u32 version(1) + u32 typehash + u32 size  (per skill doc)
    base = magic_off
    ver, th, sz = struct.unpack_from("<III", blob, base + 4)
    return ver, th, sz

for name in ["generated_damage_settings.dl_bin", "generated_projectile_settings.dl_bin",
             "generated_explosion_settings.dl_bin", "generated_weapon_customization_settings.dl_bin"]:
    print("=" * 70)
    for tag, root in (("OLD", OLD), ("CUR", CUR)):
        p = root / name
        if not p.exists():
            print(f"{tag} {name}: MISSING"); continue
        blob = load(p)
        offs = []
        i = 0
        while True:
            j = blob.find(b"LDLD", i)
            if j < 0: break
            offs.append(j); i = j + 1
        print(f"{tag} {name}: len={len(blob)} LDLD@{offs[:6]} count={len(offs)}")
        for o in offs[:4]:
            ver, th, sz = struct.unpack_from("<III", blob, o + 4)
            print(f"    at {o}: version={ver} typehash=0x{th:08x} size={sz}")
