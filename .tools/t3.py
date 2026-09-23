import sys, json; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path

def load(tag, root):
    r = Path(root)
    return (tag,
        T.parse_damages((r/'generated_damage_settings.dl_bin').read_bytes()),
        T.parse_projectiles((r/'generated_projectile_settings.dl_bin').read_bytes()))

tables = dict((t[0], t[1:]) for t in (load('OLD','data/raw'), load('CUR','.tools/current')))

for tag in ('OLD','CUR'):
    dmg, proj = tables[tag]
    print('===', tag, 'damages', len(dmg), 'projectiles', len(proj))
    # R-4 Hyena is the 220/45 row. Find every row with damage==220 and durable==45.
    cands = [(r['position'], r['type_id'], r['armor_penetration_per_angle'][0])
             for r in dmg if r['damage']==220 and r['durable_damage']==45]
    print('   rows with 220/45 -> (pos, type_id, ap0):', cands)
    # projectiles whose +60 equals each candidate's position and type_id
    for pos, tid, ap in cands:
        by_pos = [p['row'] for p in proj if p['damage_type']==pos]
        by_tid = [p['row'] for p in proj if p['damage_type']==tid]
        print(f'     220/45 row pos={pos} tid={tid}: projectiles +60==pos -> {by_pos} ; +60==tid -> {by_tid}')
    # How many projectile +60 values exceed the damage count (=> cannot be positions)?
    over = sorted(set(p['damage_type'] for p in proj if p['damage_type'] >= len(dmg)))
    print('   projectile +60 values >= damage count (cannot be a position):', over)
    missing_as_pos = sorted(set(p['damage_type'] for p in proj if p['damage_type'] > 0))
    tids = set(r['type_id'] for r in dmg)
    unresolved = [v for v in missing_as_pos if v not in tids]
    print('   projectile +60 values with NO matching type_id:', unresolved)
    print('   ... and how many rows would that leave unmatched by position?: n/a')