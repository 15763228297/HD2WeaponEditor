
import struct, json
from pathlib import Path
CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")
REC = 76

def damage_array(blob):
    magic = blob.find(b"LDLD", 8)
    off, cnt = struct.unpack_from("<QQ", blob, magic+24)
    arr = magic + 24 + off
    end = arr + cnt*REC
    return arr, cnt, end, len(blob)

for tag, root in (("OLD", OLD), ("CUR", CUR)):
    blob = (root/"generated_damage_settings.dl_bin").read_bytes()
    arr, cnt, end, ln = damage_array(blob)
    print(f"{tag}: magic={blob.find(b'LDLD',8)} arr={arr} count={cnt} end={end} filelen={ln} exact={end==ln}")

def parse(blob):
    arr, cnt, end, ln = damage_array(blob)
    out = []
    for i in range(cnt):
        b = arr + i*REC
        tid, dmg, dur = struct.unpack_from("<iii", blob, b)
        ap = list(struct.unpack_from("<4I", blob, b+12))
        dem, fs, fi, el = struct.unpack_from("<4I", blob, b+28)
        out.append(dict(pos=i, type_id=tid, damage=dmg, durable=dur, ap=ap, dem=dem, force=fs, impulse=fi, element=el))
    return out

o = parse((OLD/"generated_damage_settings.dl_bin").read_bytes())
c = parse((CUR/"generated_damage_settings.dl_bin").read_bytes())
print()
print(f"OLD rows={len(o)}  CUR rows={len(c)}")
print("OLD pos0..3:", [(r['pos'],r['type_id'],r['damage']) for r in o[:4]])
print("CUR pos0..3:", [(r['pos'],r['type_id'],r['damage']) for r in c[:4]])
print("OLD last3:", [(r['pos'],r['type_id']) for r in o[-3:]])
print("CUR last3:", [(r['pos'],r['type_id']) for r in c[-3:]])

def sig(r): return (r['damage'], r['durable'], tuple(r['ap']), r['dem'], r['force'], r['impulse'])

# 1) Is CUR's prefix identical to OLD (pure append)?
n = len(o)
print()
print("CUR[0:%d] identical to OLD by signature: %s" % (n, all(sig(o[i])==sig(c[i]) for i in range(n))))
print("CUR[0:%d] identical by type_id: %s" % (n, all(o[i]['type_id']==c[i]['type_id'] for i in range(n))))

# 2) by type_id
om = {r['type_id']: r for r in o}; cm = {r['type_id']: r for r in c}
print("unique type_ids OLD=%d CUR=%d" % (len(om), len(cm)))
print("new ids:", sorted(set(cm)-set(om)))
print("gone ids:", sorted(set(om)-set(cm)))
common = set(om)&set(cm)
print("common ids: %d ; values identical: %d" % (len(common), sum(1 for t in common if sig(om[t])==sig(cm[t]))))
print("common ids where POSITION changed:", sum(1 for t in common if om[t]['pos']!=cm[t]['pos']))
moved = [(t, om[t]['pos'], cm[t]['pos']) for t in common if om[t]['pos']!=cm[t]['pos']]
print("  examples:", moved[:10])
Path(r"E:\game\HD2WeaponEditor\.tools\damage_OLD.json").write_text(json.dumps(o, indent=1), encoding='utf-8')
Path(r"E:\game\HD2WeaponEditor\.tools\damage_CUR.json").write_text(json.dumps(c, indent=1), encoding='utf-8')
