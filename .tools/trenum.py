import sys, json; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path

o = T.parse_damages(Path('.tools/old/generated_damage_settings.dl_bin').read_bytes())
c = T.parse_damages(Path('data/raw/generated_damage_settings.dl_bin').read_bytes())
ob = {r['type_id']: r for r in o}
cb = {r['type_id']: r for r in c}

def key(r):
    return (r['damage'], r['durable_damage'], tuple(r['armor_penetration_per_angle']),
            r['demolition_strength'], r['force_strength'], r['force_impulse'], r['element_type'])

# for each CUR id, does its value tuple appear in OLD, and at which ids?
from collections import defaultdict
oldkey = defaultdict(list)
for r in o: oldkey[key(r)].append(r['type_id'])

print('=== CUR id -> OLD id(s) with identical values ===')
shifts = []
for r in c:
    cands = oldkey.get(key(r), [])
    delta = [r['type_id']-x for x in cands]
    shifts.append((r['type_id'], cands, delta))
uniq = [(cid, cands[0]) for cid, cands, d in shifts if len(cands)==1]
print('CUR ids with a UNIQUE OLD match:', len(uniq), '/', len(c))
deltas = defaultdict(int)
for cid, oid in uniq: deltas[cid-oid] += 1
print('delta histogram (CUR_id - OLD_id -> count):', dict(sorted(deltas.items())))
print()
print('=== the renumbering, as runs ===')
run = None
for cid, oid in uniq:
    d = cid - oid
    if run is None or run[2] != d:
        if run: print('   CUR id %d..%d  ==  OLD id %d..%d   (delta %+d)' % (run[0], prev, run[1], prev-d, run[2]))
        run = (cid, oid, d)
    prev = cid
if run: print('   CUR id %d..%d  ==  OLD id %d..%d   (delta %+d)' % (run[0], prev, run[1], prev-run[2], run[2]))
print()
print('=== OLD ids with NO counterpart in CUR (deleted rows) ===')
curkey = defaultdict(list)
for r in c: curkey[key(r)].append(r['type_id'])
gone = [r['type_id'] for r in o if not curkey.get(key(r))]
print('  count', len(gone), '->', gone)
for t in gone:
    r = ob[t]
    print('     OLD id %3d pos %3d  %d/%d AP%s' % (t, r['position'], r['damage'], r['durable_damage'], r['armor_penetration_per_angle'][:2]))