
import struct, json
from pathlib import Path

CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")
REC = 76
START = 480

def decode(blob, base, i):
    tid, dmg, dur = struct.unpack_from("<iii", blob, base)
    ap = list(struct.unpack_from("<4I", blob, base + 12))
    dem, fs, fi, el = struct.unpack_from("<4I", blob, base + 28)
    return dict(index=i, type_id=tid, damage=dmg, durable=dur, ap=ap,
                dem=dem, force=fs, impulse=fi, element=el)

def parse(blob):
    n = (len(blob) - START) // REC
    return [decode(blob, START + i*REC, i) for i in range(n)], n

old = (OLD/"generated_damage_settings.dl_bin").read_bytes()
cur = (CUR/"generated_damage_settings.dl_bin").read_bytes()
o, no = parse(old)
c, nc = parse(cur)
print(f"OLD records={no}  CUR records={nc}  delta={nc-no}")

# align by type_id: build id->record maps
om = {r["type_id"]: r for r in o}
cm = {r["type_id"]: r for r in c}
print("OLD unique ids:", len(om), " CUR unique ids:", len(cm))
new_ids = sorted(set(cm) - set(om))
gone_ids = sorted(set(om) - set(cm))
print("NEW type_ids (only in CUR):", new_ids)
print("GONE type_ids (only in OLD):", gone_ids)

# For ids present in both: did the position change?
moved = [(t, om[t]["index"], cm[t]["index"]) for t in om if t in cm and om[t]["index"] != cm[t]["index"]]
print(f"ids whose ARRAY POSITION changed: {len(moved)}")
print("  first 30:", moved[:30])

# Value changes for same id
changed = []
for t in om:
    if t not in cm: continue
    a, b = om[t], cm[t]
    if (a["damage"], a["durable"], a["ap"], a["dem"], a["force"], a["impulse"]) != \
       (b["damage"], b["durable"], b["ap"], b["dem"], b["force"], b["impulse"]):
        changed.append((t, a["index"], b["index"], (a["damage"],a["durable"]), (b["damage"],b["durable"])))
print(f"ids whose VALUES changed: {len(changed)}")
for row in changed[:40]: print("   ", row)

# Show CUR record at position 137 (was R-4)
print()
print("CUR position 137:", c[137])
print("OLD position 137:", o[137])
