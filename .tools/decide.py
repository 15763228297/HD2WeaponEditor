
import struct, json
from pathlib import Path
CUR = Path(r"E:\game\HD2WeaponEditor\.tools\current")
OLD = Path(r"E:\game\HD2WeaponEditor\data\raw")
REC, PR, PA = 76, 272, 0x1C

def damage(blob):
    magic = blob.find(b"LDLD", 8)
    off, cnt = struct.unpack_from("<QQ", blob, magic+24)
    arr = magic + 24 + off
    out = []
    for i in range(cnt):
        b = arr + i*REC
        tid, dmg, dur = struct.unpack_from("<iii", blob, b)
        out.append(dict(pos=i, type_id=tid, damage=dmg, durable=dur,
                        ap=list(struct.unpack_from("<4I", blob, b+12))))
    return out

def projs(blob):
    off, cnt = struct.unpack_from("<QQ", blob, PA)
    arr = PA + off
    out = []
    for i in range(cnt):
        b = arr + i*PR
        out.append(dict(row=i, seq=struct.unpack_from("<i", blob, b)[0],
                        name=struct.unpack_from("<i", blob, b+8)[0],
                        cal=struct.unpack_from("<f", blob, b+24)[0],
                        speed=struct.unpack_from("<f", blob, b+32)[0],
                        mass=struct.unpack_from("<f", blob, b+36)[0],
                        f60=struct.unpack_from("<i", blob, b+60)[0]))
    return out

for tag, root in (("OLD", OLD), ("CUR", CUR)):
    d = damage((root/"generated_damage_settings.dl_bin").read_bytes())
    p = projs((root/"generated_projectile_settings.dl_bin").read_bytes())
    by_type = {r['type_id']: r for r in d}
    by_pos  = {r['pos']: r for r in d}
    print("="*70); print(tag)
    print("  true table rows:", len(d))
    print("  positions 133..146:")
    for r in d[133:147]:
        print(f"     pos={r['pos']:3d} type_id={r['type_id']:3d} dmg={r['damage']:5d}/{r['durable']:4d} ap={r['ap']}")
    print("  9x70mm-ish projectiles (cal 9, mass 20):")
    for q in p:
        if abs(q['cal']-9.0) < 0.01 and abs(q['mass']-20.0) < 0.01:
            bt = by_type.get(q['f60']); bp = by_pos.get(q['f60'])
            print(f"     row={q['row']:3d} speed={q['speed']:6.0f} +60={q['f60']:4d} "
                  f"| by_type -> {('pos %d dmg %d/%d' % (bt['pos'], bt['damage'], bt['durable'])) if bt else 'NONE'} "
                  f"| by_pos -> {('tid %d dmg %d/%d' % (bp['type_id'], bp['damage'], bp['durable'])) if bp else 'NONE'}")
