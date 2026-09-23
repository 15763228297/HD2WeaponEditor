
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
    blob = p.read_bytes(); n = (len(blob)-START)//REC
    return [decode(blob, START+i*REC) for i in range(n)]

o = parse(OLD/"generated_damage_settings.dl_bin")
c = parse(CUR/"generated_damage_settings.dl_bin")

def key(r): return (r['damage'], r['durable'], tuple(r['ap']), r['dem'], r['force'], r['impulse'])

# Are the VALUE SEQUENCES the same (just shifted)?
ov = [key(r) for r in o]; cv = [key(r) for r in c]
print("value sequences equal as ordered lists:", ov == cv)
print("OLD len", len(ov), "CUR len", len(cv))
# longest common subsequence-ish: check if CUR contains OLD as a contiguous run
s = ','.join(map(str, ov))
# find OLD's first 20 in CUR
first = ov[:20]
for i in range(len(cv)-len(first)+1):
    if cv[i:i+len(first)] == first:
        print(f"OLD value-run [0:20] found in CUR at offset {i}")
        break
else:
    print("OLD value-run [0:20] NOT found contiguously in CUR")

# how many OLD value-tuples appear anywhere in CUR (multiset)
from collections import Counter
co, cc = Counter(ov), Counter(cv)
shared = sum((co & cc).values())
print(f"multiset overlap of value-tuples: {shared} / {len(ov)} OLD, {len(cv)} CUR")
print("only-in-OLD values:", list((co - cc).items())[:10])
print("only-in-CUR values:", list((cc - co).items())[:10])
