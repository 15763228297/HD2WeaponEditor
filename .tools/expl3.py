
import struct
from pathlib import Path
CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")

for tag, root in (("OLD", OLD), ("CUR", CUR)):
    blob = (root/"generated_explosion_settings.dl_bin").read_bytes()
    magic = blob.find(b"LDLD")
    off, cnt = struct.unpack_from("<QQ", blob, magic+24)
    arr = magic + 24 + off
    stride = 152
    print(f"=== {tag} explosion: count={cnt} arr={arr}")
    vals = []
    for i in range(cnt):
        b = arr + i*stride
        t, dt = struct.unpack_from("<ii", blob, b)
        vals.append((t, dt))
    print("   distinct DamageType values:", len(set(d for _, d in vals)))
    mx = max(d for _, d in vals)
    print("   MAX DamageType value:", mx, " (array positions are 0..%d)" % (cnt-1))
    print("   DamageType >= count (cannot be a position):", sorted(set(d for _, d in vals if d >= cnt)))
    print("   first 5 (type, damageType):", vals[:5])
