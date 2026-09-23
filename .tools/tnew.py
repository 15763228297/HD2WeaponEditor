import sys, json; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path

o = T.parse_projectiles(Path('data/raw/generated_projectile_settings.dl_bin').read_bytes())
c = T.parse_projectiles(Path('.tools/current/generated_projectile_settings.dl_bin').read_bytes())
print('OLD proj', len(o), 'CUR proj', len(c))
oh = [p['name_upper'] for p in o]
ch = [p['name_upper'] for p in c]
print('OLD distinct name_upper:', len(set(oh)), ' CUR:', len(set(ch)))
new = [p for p in c if p['name_upper'] not in set(oh)]
gone = [p for p in o if p['name_upper'] not in set(ch)]
print()
print('CUR projectile rows whose name_upper is NOT in OLD:', len(new))
for p in new:
    print('   row %3d  name_upper=0x%08X  seq=%-4d spd=%-7.1f mass=%-6.1f cal=%-5.1f dmg_type=%-4d expl=%d' % (
        p['row'], p['name_upper'], p['sequence'], p['speed'], p['mass'], p['calibre'], p['damage_type'], p['explosion_type']))
print()
print('OLD rows whose name_upper is NOT in CUR:', len(gone))
for p in gone:
    print('   row %3d  name_upper=0x%08X  seq=%-4d spd=%-7.1f' % (p['row'], p['name_upper'], p['sequence'], p['speed']))
print()
print('--- duplicate name_upper in CUR (same ammo, several rows) ---')
from collections import Counter
cc = Counter(ch)
dups = {h:n for h,n in cc.items() if n>1}
print('   count of duplicated hashes:', len(dups), ' total extra rows:', sum(dups.values())-len(dups))