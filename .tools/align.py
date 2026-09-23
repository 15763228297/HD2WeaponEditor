
import struct, json, difflib
from pathlib import Path

CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")
REC = 76

def decode(blob, base):
    tid, dmg, dur = struct.unpack_from("<iii", blob, base)
    ap = list(struct.unpack_from("<4I", blob, base + 12))
    dem, fs, fi, el = struct.unpack_from("<4I", blob, base + 28)
    return dict(type_id=tid, damage=dmg, durable=dur, ap=ap, dem=dem, force=fs, impulse=fi, element=el)

def parse(blob, start):
    n = (len(blob) - start) // REC
    return [decode(blob, start + i*REC) for i in range(n)], n

old_blob = (OLD/"generated_damage_settings.dl_bin").read_bytes()
cur_blob = (CUR/"generated_damage_settings.dl_bin").read_bytes()

# container at 60, payload at 76.  Try the "array = magic + 420" hypothesis (480)
for start in (480,):
    o, no = parse(old_blob, start)
    c, nc = parse(cur_blob, start)
    print(f"start={start}: OLD n={no} CUR n={nc}")
    print("  OLD[0:6] type_ids:", [r['type_id'] for r in o[:6]])
    print("  CUR[0:6] type_ids:", [r['type_id'] for r in c[:6]])
    print("  OLD[-3:] type_ids:", [r['type_id'] for r in o[-3:]])
    print("  CUR[-3:] type_ids:", [r['type_id'] for r in c[-3:]])

    oid = [r['type_id'] for r in o]
    cid = [r['type_id'] for r in c]
    print("  OLD unique:", len(set(oid)), "CUR unique:", len(set(cid)))
    print("  ids in CUR not OLD:", sorted(set(cid)-set(oid)))
    print("  ids in OLD not CUR:", sorted(set(oid)-set(cid)))
    print()
    sm = difflib.SequenceMatcher(None, oid, cid, autojunk=False)
    ops = sm.get_opcodes()
    print(f"  opcodes: {len(ops)}")
    for tag, i1, i2, j1, j2 in ops[:25]:
        print(f"    {tag:8s} OLD[{i1}:{i2}]={oid[i1:i2][:8]} CUR[{j1}:{j2}]={cid[j1:j2][:8]}")
    Path(r"E:\game\HD2WeaponEditor\.tools\damage_OLD.json").write_text(json.dumps(o, indent=1), encoding='utf-8')
    Path(r"E:\game\HD2WeaponEditor\.tools\damage_CUR.json").write_text(json.dumps(c, indent=1), encoding='utf-8')
