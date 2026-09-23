
import struct, json
from pathlib import Path
CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")
PR, PA = 272, 0x1C

def proj(p):
    blob = p.read_bytes()
    off, count = struct.unpack_from("<QQ", blob, PA)
    arr = PA + off
    avail = len(blob) - arr
    out = []
    for i in range(count):
        b = arr + i*PR
        out.append(dict(row=i,
            seq=struct.unpack_from("<i", blob, b)[0],
            name=struct.unpack_from("<i", blob, b+8)[0],
            cal=struct.unpack_from("<f", blob, b+24)[0],
            speed=struct.unpack_from("<f", blob, b+32)[0],
            mass=struct.unpack_from("<f", blob, b+36)[0],
            drag=struct.unpack_from("<f", blob, b+40)[0],
            grav=struct.unpack_from("<f", blob, b+44)[0],
            dmg=struct.unpack_from("<i", blob, b+60)[0]))
    return out, count, avail, len(blob)

o, oc, oa, ol = proj(OLD/"generated_projectile_settings.dl_bin")
c, cc, ca, cl = proj(CUR/"generated_projectile_settings.dl_bin")
print(f"OLD: file={ol} count={oc} avail={oa} (avail%272={oa%272})")
print(f"CUR: file={cl} count={cc} avail={ca} (avail%272={ca%272})")
print("  CUR stride if 272:", ca/272)
# try to infer CUR stride
for stride in range(260, 300):
    if ca % stride == 0:
        print(f"   stride {stride} -> {ca//stride} records")
print()
print("R-4 candidates: search projectiles by speed ~950 and mass ~20")
for tag, rows in (("OLD", o), ("CUR", c)):
    print(f"--- {tag} ---")
    for r in rows:
        if 900 <= r['speed'] <= 1000 and 10 <= r['mass'] <= 30:
            print("   ", r)
