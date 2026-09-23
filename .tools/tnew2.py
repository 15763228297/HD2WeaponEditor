import sys, json; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path

o = T.parse_damages(Path('data/raw/generated_damage_settings.dl_bin').read_bytes())
c = T.parse_damages(Path('.tools/current/generated_damage_settings.dl_bin').read_bytes())
op = T.parse_projectiles(Path('data/raw/generated_projectile_settings.dl_bin').read_bytes())
cp = T.parse_projectiles(Path('.tools/current/generated_projectile_settings.dl_bin').read_bytes())
oe = T.parse_explosions(Path('data/raw/generated_explosion_settings.dl_bin').read_bytes())
ce = T.parse_explosions(Path('.tools/current/generated_explosion_settings.dl_bin').read_bytes())

old_ids = {r['type_id'] for r in o}
print('=== NEW damage type ids (in CUR, not OLD) ===')
for r in c:
    if r['type_id'] not in old_ids:
        users = [q['row'] for q in cp if q['damage_type']==r['type_id']]
        exps  = [(e['position'],e['explosion_type']) for e in ce if e['damage_type']==r['type_id']]
        print('  pos %3d id %3d  %d/%d AP%s  forces %d/%d/%d  proj=%s expl=%s' % (
            r['position'], r['type_id'], r['damage'], r['durable_damage'],
            r['armor_penetration_per_angle'], r['demolition_strength'],
            r['force_strength'], r['force_impulse'], users, exps))
print()
print('=== NEW explosion rows (ExplosionType in CUR, not OLD) ===')
oid = {e['explosion_type'] for e in oe}
for e in ce:
    if e['explosion_type'] not in oid:
        users = [q['row'] for q in cp if q['explosion_type']==e['explosion_type'] or q['explosion_type_alt']==e['explosion_type']]
        print('  pos %3d type %3d  dmgtype %3d  radii %.2f/%.2f/%.2f  proj=%s' % (
            e['position'], e['explosion_type'], e['damage_type'],
            e['inner_radius'], e['outer_radius'], e['stagger_radius'], users))
print()
print('=== CUR projectile rows 230-240 and 320-330 ===')
for p in cp:
    if 230 <= p['row'] <= 240 or 320 <= p['row'] <= 330:
        old = op[p['row']] if p['row'] < len(op) else None
        print('  row %3d seq=%-4d name=0x%08X spd=%-7.1f mass=%-6.1f cal=%-5.1f dmg=%-4d expl=%-4d | OLD seq=%s name=0x%08X' % (
            p['row'], p['sequence'], p['name_upper'], p['speed'], p['mass'], p['calibre'], p['damage_type'], p['explosion_type'],
            old['sequence'] if old else '-', old['name_upper'] if old else 0))