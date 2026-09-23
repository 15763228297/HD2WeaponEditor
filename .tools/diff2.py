
import struct, json
from pathlib import Path

CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")
REC, START = 76, 480

def decode(blob, base):
    tid, dmg, dur = struct.unpack_from("<iii", blob, base)
    ap = list(struct.unpack_from("<4I", blob, base + 12))
    dem, fs, fi, el = struct.unpack_from("<4I", blob, base + 28)
    return dict(type_id=tid, damage=dmg, durable=dur, ap=ap, dem=dem, force=fs, impulse=fi, element=el)

def parse(p):
    blob = p.read_bytes()
    n = (len(blob) - START) // REC
    return [decode(blob, START + i*REC) for i in range(n)], blob

o, ob = parse(OLD/"generated_damage_settings.dl_bin")
c, cb = parse(CUR/"generated_damage_settings.dl_bin")
print(f"OLD n={len(o)} CUR n={len(c)}")

def sig(r): return (r['damage'], r['durable'], tuple(r['ap']), r['dem'], r['force'], r['impulse'], r['element'])

# position-wise comparison over the common prefix
same = sum(1 for i in range(min(len(o), len(c))) if sig(o[i]) == sig(c[i]))
print(f"positions with IDENTICAL values: {same} / {min(len(o),len(c))}")

# id-wise comparison
om = {r['type_id']: r for r in o}; cm = {r['type_id']: r for r in c}
common = set(om) & set(cm)
idsame = sum(1 for t in common if sig(om[t]) == sig(cm[t]))
print(f"type_ids present in both: {len(common)}; of those, IDENTICAL values: {idsame}")

# find the damage record R-4 uses: check the projectile table
print()
print("=== R-4 neighbourhood (by position) ===")
for i in range(133, 143):
    print(f"  OLD[{i}] tid={o[i]['type_id']:4d} dmg={o[i]['damage']:6d} dur={o[i]['durable']:6d} ap={o[i]['ap']}")
print()
for i in range(133, 143):
    print(f"  CUR[{i}] tid={c[i]['type_id']:4d} dmg={c[i]['damage']:6d} dur={c[i]['durable']:6d} ap={c[i]['ap']}")
print()
# where is type_id 137 in CUR?
for i, r in enumerate(c):
    if r['type_id'] == 137:
        print("CUR type_id 137 at position", i, r)
for i, r in enumerate(o):
    if r['type_id'] == 137:
        print("OLD type_id 137 at position", i, r)
