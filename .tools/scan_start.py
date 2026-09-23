
import struct
from pathlib import Path

CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current\generated_damage_settings.dl_bin").read_bytes()
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw\generated_damage_settings.dl_bin").read_bytes()
REC = 76

def score(blob, start, rec=REC):
    if (len(blob) - start) % rec: return None
    n = (len(blob) - start) // rec
    ids, bad = [], 0
    for i in range(n):
        b = start + i*rec
        tid, dmg, dur = struct.unpack_from("<iii", blob, b)
        if not (0 < tid < 2000): bad += 1
        if not (0 <= dmg <= 100000): bad += 1
        if not (0 <= dur <= 100000): bad += 1
        ids.append(tid)
    uniq = len(set(ids))
    return n, uniq, bad

print("CUR: scanning candidate starts (must divide exactly, rec=76)")
for start in range(0, 400):
    r = score(CUR, start)
    if r: print(f"   start={start:4d} n={r[0]} uniq={r[1]} bad={r[2]}")
print()
print("CUR: try other record sizes")
for rec in range(60, 100):
    for start in range(0, 400):
        if (len(CUR) - start) % rec == 0:
            n = (len(CUR)-start)//rec
            if 600 <= n <= 700:
                print(f"   rec={rec} start={start} n={n}")
