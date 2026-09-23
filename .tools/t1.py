import sys; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path
for tag, root in (('OLD','data/raw'),('CUR','.tools/current')):
    d=T.parse_damages(Path(root+'/generated_damage_settings.dl_bin').read_bytes())
    p=T.parse_projectiles(Path(root+'/generated_projectile_settings.dl_bin').read_bytes())
    e=T.parse_explosions(Path(root+'/generated_explosion_settings.dl_bin').read_bytes())
    print(tag,'damages',len(d),'projectiles',len(p),'explosions',len(e))
    print('   pos142:',d[142]['type_id'],d[142]['damage'],d[142]['durable_damage'])