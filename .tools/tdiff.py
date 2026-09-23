import sys, json; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path

o = T.parse_damages(Path('data/raw/generated_damage_settings.dl_bin').read_bytes())
c = T.parse_damages(Path('.tools/current/generated_damage_settings.dl_bin').read_bytes())
ob = {r['type_id']: r for r in o}
cb = {r['type_id']: r for r in c}
common = sorted(set(ob) & set(cb))
print('common type ids:', len(common))
def same(a,b):
    return (a['damage']==b['damage'] and a['durable_damage']==b['durable_damage']
            and a['armor_penetration_per_angle']==b['armor_penetration_per_angle'])
ident = [t for t in common if same(ob[t],cb[t])]
print('common ids with IDENTICAL damage/durable/AP:', len(ident), '/', len(common))
moved = [t for t in common if ob[t]['position']!=cb[t]['position']]
print('common ids whose array POSITION changed:', len(moved))
print()
print('--- sample of moved ids ---')
for t in moved[:10]:
    print('   id %3d: pos %3d -> %3d   %d/%d -> %d/%d' % (t, ob[t]['position'], cb[t]['position'],
        ob[t]['damage'], ob[t]['durable_damage'], cb[t]['damage'], cb[t]['durable_damage']))
print()
print('--- ids whose VALUES changed ---')
chg = [t for t in common if not same(ob[t],cb[t])]
for t in chg[:15]:
    print('   id %3d: %d/%d AP%s -> %d/%d AP%s' % (t, ob[t]['damage'], ob[t]['durable_damage'],
        ob[t]['armor_penetration_per_angle'][:2], cb[t]['damage'], cb[t]['durable_damage'],
        cb[t]['armor_penetration_per_angle'][:2]))
print('   total changed:', len(chg))