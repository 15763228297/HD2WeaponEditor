
import struct, json
from pathlib import Path

CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")
REC = 76

def anchor_start(blob, index, damage, ap, forces):
    want = struct.pack("<iii", index, *damage)
    for cand in range(0, len(blob) - REC):
        if blob[cand:cand+len(want)] != want: continue
        start = cand - index * REC
        if start < 0: continue
        if (len(blob) - start) % REC: continue
        if list(struct.unpack_from("<4I", blob, cand + 12)) != ap: continue
        if list(struct.unpack_from("<3I", blob, cand + 28)) != list(forces): continue
        return start
    return None

def decode(blob, base, i):
    tid, dmg, dur = struct.unpack_from("<iii", blob, base)
    ap = list(struct.unpack_from("<4I", blob, base + 12))
    dem, fs, fi, el = struct.unpack_from("<4I", blob, base + 28)
    return dict(index=i, type_id=tid, damage=dmg, durable=dur, ap=ap,
                dem=dem, force=fs, impulse=fi, element=el)

for tag, root in (("OLD", OLD), ("CUR", CUR)):
    blob = (root / "generated_damage_settings.dl_bin").read_bytes()
    start = anchor_start(blob, 137, (220, 45), [3,3,3,0], (10,20,14))
    print(f"{tag}: len={len(blob)} anchor_start={start}")
    if start is None:
        # try without forcing AP/forces
        start2 = anchor_start(blob, 137, (220,45), None, None)
        print("   relaxed:", start2)
        continue
    n = (len(blob) - start) // REC
    print(f"   records={n}  (from {start} to {start + n*REC} of {len(blob)})")
    recs = [decode(blob, start + i*REC, i) for i in range(n)]
    print("   rec0:", recs[0])
    print("   rec137:", recs[137])
    print("   last:", recs[-1])
    out = Path(r"E:\game\HD2WeaponEditor\.tools") / f"damage_{tag}.json"
    out.write_text(json.dumps(recs, indent=1), encoding="utf-8")
    print("   wrote", out)
