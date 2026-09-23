
import struct
from pathlib import Path
CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current\generated_damage_settings.dl_bin").read_bytes()
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw\generated_damage_settings.dl_bin").read_bytes()

pat = struct.pack("<iii", 137, 220, 45) + struct.pack("<4I", 3,3,3,0) + struct.pack("<3I", 10,20,14)
for tag, blob in (("OLD", OLD), ("CUR", CUR)):
    hits = []
    i = 0
    while True:
        j = blob.find(pat, i)
        if j < 0: break
        hits.append(j); i = j+1
    print(tag, "R-4 28B pattern hits:", hits, "len", len(blob))

# All type_id==137 occurrences (4-byte aligned)
for tag, blob in (("OLD", OLD), ("CUR", CUR)):
    hits = [o for o in range(0, len(blob)-4) if struct.unpack_from("<i", blob, o)[0] == 137]
    print(tag, "int32==137 at:", hits[:40], "count", len(hits))

print()
print("=== OLD bytes 60..500 ===")
print(OLD[60:500].hex(" "))
print()
print("=== CUR bytes 60..500 ===")
print(CUR[60:500].hex(" "))
