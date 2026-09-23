
import struct, json
from pathlib import Path
CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")

def load(p): return p.read_bytes()
def hdr(blob, off):
    ver, th, sz = struct.unpack_from("<III", blob, off+4)
    return ver, th, sz

for tag, root in (("OLD", OLD), ("CUR", CUR)):
    blob = load(root/"generated_explosion_settings.dl_bin")
    off = blob.find(b"LDLD")
    ver, th, sz = hdr(blob, off)
    print(f"{tag} explosion: len={len(blob)} magic@{off} size={sz}")
    # DLArray at magic+24: u64 offset, u64 count
    o, cnt = struct.unpack_from("<QQ", blob, off+24)
    print(f"    DLArray offset={o} count={cnt}")
    arr = off + 24 + o
    avail = len(blob) - arr
    print(f"    arr={arr} avail={avail} avail/cnt={avail/cnt if cnt else 0}")

print()
# compare explosion type_ids
for tag, root in (("OLD", OLD), ("CUR", CUR)):
    blob = load(root/"generated_explosion_settings.dl_bin")
    off = blob.find(b"LDLD")
    o, cnt = struct.unpack_from("<QQ", blob, off+24)
    arr = off + 24 + o
    stride = (len(blob)-arr)//cnt
    ids = [struct.unpack_from("<i", blob, arr+i*stride)[0] for i in range(cnt)]
    print(f"{tag}: count={cnt} stride={stride} first10={ids[:10]} last5={ids[-5:]}")
