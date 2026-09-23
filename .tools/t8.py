import sys; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path
for tag, root in (('OLD','data/raw'),('CUR','.tools/current')):
    d=T.parse_damages(Path(root+'/generated_damage_settings.dl_bin').read_bytes())
    p=T.parse_projectiles(Path(root+'/generated_projectile_settings.dl_bin').read_bytes())
    e=T.parse_explosions(Path(root+'/generated_explosion_settings.dl_bin').read_bytes())
    print(tag,'damages',len(d),'projectiles',len(p),'explosions',len(e))
    r4=[x for x in d if x['damage']==220 and x['durable_damage']==45]
    print('   220/45 rows:',[(x['position'],x['type_id']) for x in r4])
    pos=r4[0]['position']
    users=[q['row'] for q in p if q['damage_type']==r4[0]['type_id']]
    print('   projectiles referencing type_id',r4[0]['type_id'],'->',users)
    print('   that projectile:',{k:p[users[0]][k] for k in ('speed','mass','calibre','drag','gravity')} if users else None)
    print('   position_of_type_id(137)=',T.position_of_type_id(d,137))
    print('   type_id_at(142)=',T.type_id_at(d,142))
print()
print('djb2 checks:', hex(T.djb2('DamageSettings')), hex(T.TYPE_DAMAGE))