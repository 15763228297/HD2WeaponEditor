import sys; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path
o = T.parse_damages(Path('data/raw/generated_damage_settings.dl_bin').read_bytes())
c = T.parse_damages(Path('.tools/current/generated_damage_settings.dl_bin').read_bytes())
print('OLD rows', len(o), 'CUR rows', len(c))

print()
print('--- type_id == position+1 ? ---')
for tag, t in (('OLD',o),('CUR',c)):
    eq = sum(1 for r in t if r['type_id']==r['position']+1)
    print(f'  {tag}: {eq}/{len(t)} rows have type_id == position+1')

print()
print('--- OLD ids / CUR ids ---')
oid=set(r['type_id'] for r in o); cid=set(r['type_id'] for r in c)
print('  OLD id range', min(oid), max(oid), 'n', len(oid))
print('  CUR id range', min(cid), max(cid), 'n', len(cid))
print('  ids only in CUR:', sorted(cid-oid))
print('  ids only in OLD:', sorted(oid-cid))

print()
print('--- does CUR[position+5] == OLD[position] (id and values)? ---')
same=0; diffs=[]
for i in range(min(len(o), len(c)-5)):
    a=o[i]; b=c[i+5]
    if a['type_id']==b['type_id'] and a['damage']==b['damage'] and a['durable_damage']==b['durable_damage']:
        same+=1
    elif len(diffs)<8:
        diffs.append((i, a['type_id'],a['damage'],a['durable_damage'], b['type_id'],b['damage'],b['durable_damage']))
print('  identical at +5 offset:', same, '/', min(len(o), len(c)-5))
print('  first mismatches (i, OLD tid/d/dur, CUR[i+5] tid/d/dur):')
for d in diffs: print('    ', d)