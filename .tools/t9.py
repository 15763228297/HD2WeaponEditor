import sys; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path
for tag, root in (('OLD','data/raw'),('CUR','.tools/current')):
    d=T.parse_damages(Path(root+'/generated_damage_settings.dl_bin').read_bytes())
    p=T.parse_projectiles(Path(root+'/generated_projectile_settings.dl_bin').read_bytes())
    tids={r['type_id'] for r in d}
    print('===',tag,'damage rows',len(d),'type_id range',min(tids),max(tids))
    print('   type ids 1,2,3 present?', {k:(k in tids) for k in (1,2,3)})
    for k in (1,2,3):
        rows=[(r['position'],r['damage'],r['durable_damage'],r['armor_penetration_per_angle'][:1]) for r in d if r['type_id']==k]
        users=[q['row'] for q in p if q['damage_type']==k]
        print(f'   type_id {k}: damage rows {rows}  referenced by projectile rows {users}')
    # all distinct +60 values, and whether each resolves
    vals=sorted(set(q['damage_type'] for q in p))
    print('   distinct +60 values:',len(vals),'range',vals[0],vals[-1])
    print('   +60 values with NO type_id match:',[v for v in vals if v not in tids])
    print('   +60 values < 4:',[v for v in vals if v<4])
    # zero values
    print('   +60 == 0 rows:',[q['row'] for q in p if q['damage_type']==0][:10])