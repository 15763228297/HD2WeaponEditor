
import struct
from pathlib import Path
CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")

def dlarray(blob, base):
    off, cnt = struct.unpack_from("<QQ", blob, base)
    return base + off, cnt

for tag, root in (("OLD", OLD), ("CUR", CUR)):
    blob = (root/"generated_explosion_settings.dl_bin").read_bytes()
    magic = blob.find(b"LDLD")
    base = magic + 24
    arr, cnt = dlarray(blob, base)
    avail = len(blob) - arr
    print(f"{tag}: magic={magic} base={base} arr={arr} cnt={cnt} avail={avail} avail/cnt={avail/cnt:.4f}")
    for stride in (152, 160, 144, 168):
        n = avail // stride
        ids = [struct.unpack_from("<i", blob, arr+i*stride)[0] for i in range(min(n, cnt))]
        uniq = len(set(ids))
        inrange = sum(1 for x in ids if 0 < x < 5000)
        print(f"   stride={stride}: n={n} uniq={uniq} inrange={inrange}/{len(ids)} first8={ids[:8]}")
    print()
