
import struct, json
from pathlib import Path
CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")
REC, PR, PA = 76, 272, 0x1C

def damage_array(blob):
    magic = blob.find(b"LDLD", 8)          # second block = DamageSettings
    base = magic + 24
    off, cnt = struct.unpack_from("<QQ", blob, base)
    arr = base + off
    assert arr + cnt*REC == len(blob), (arr, cnt, len(blob))
    recs = []
    for i in range(cnt):
        b = arr + i*REC
        tid, dmg, dur = struct.unpack_from("<iii", blob, b)
        ap = list(struct.unpack_from("<4I", blob, b+12))
        dem, fs, fi, el = struct.unpack_from("<4I", blob, b+28)
        recs.append(dict(pos=i, type_id=tid, damage=dmg, durable=dur, ap=ap,
                         dem=dem, force=fs, impulse=fi, element=el))
    return recs, arr, cnt

def projectiles(blob):
    off, cnt = struct.unpack_from("<QQ", blob, PA)
    arr = PA + off
    out = []
    for i in range(cnt):
        b = arr + i*PR
        out.append(dict(row=i,
            seq=struct.unpack_from("<i", blob, b)[0],
            name=struct.unpack_from("<i", blob, b+8)[0],
            cal=struct.unpack_from("<f", blob, b+24)[0],
            speed=struct.unpack_from("<f", blob, b+32)[0],
            mass=struct.unpack_from("<f", blob, b+36)[0],
            dmg_field=struct.unpack_from("<i", blob, b+60)[0]))
    return out

for tag, root in (("OLD", OLD), ("CUR", CUR)):
    db = (root/"generated_damage_settings.dl_bin").read_bytes()
    pb = (root/"generated_projectile_settings.dl_bin").read_bytes()
    recs, arr, cnt = damage_array(db)
    projs = projectiles(pb)
    by_pos = {r['pos']: r for r in recs}
    by_id = {}
    for r in recs: by_id.setdefault(r['type_id'], r)
    print(f"=== {tag} === damage arr={arr} count={cnt}; projectiles={len(projs)}")
    print("   pos0:", recs[0], " pos142:", recs[142])
    # discriminating test: field value V where by_pos[V].type_id != V
    disc = [p for p in projs if p['dmg_field'] in by_pos and by_pos[p['dmg_field']]['type_id'] != p['dmg_field']]
    print(f"   projectiles whose +60 value V has by_pos[V].type_id != V : {len(disc)} / {len(projs)}")
    same = [p for p in projs if p['dmg_field'] in by_pos and by_pos[p['dmg_field']]['type_id'] == p['dmg_field']]
    print(f"   projectiles where they coincide: {len(same)}")
    missing_pos = [p for p in projs if p['dmg_field'] not in by_pos]
    missing_id = [p for p in projs if p['dmg_field'] not in by_id]
    print(f"   +60 values with NO matching position: {len(missing_pos)} -> {sorted(set(p['dmg_field'] for p in missing_pos))[:12]}")
    print(f"   +60 values with NO matching type_id : {len(missing_id)} -> {sorted(set(p['dmg_field'] for p in missing_id))[:12]}")
    print()
