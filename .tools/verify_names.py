import json, pathlib
root = pathlib.Path('.')
recs = json.loads((root/'data/damage_records.json').read_text(encoding='utf-8'))
by_idx = {r['index']: r for r in recs}
by_tid = {r['type_id']: r for r in recs}
print('rows', len(recs))
w = json.loads((root/'data/weapon_names.json').read_text(encoding='utf-8'))
ws = w['weapons']
ok = bad = missing = 0
badlist = []
for e in ws:
    p, t = e.get('damage_position'), e.get('damage_index')
    if p is None or t is None: continue
    r = by_idx.get(p)
    if r is None: missing += 1; continue
    if r['type_id'] == t: ok += 1
    else:
        bad += 1
        if len(badlist) < 8: badlist.append((e['page'], 'idx', p, 'want tid', t, 'got', r['type_id']))
print('index->type_id == damage_index :', ok, '| bad', bad, '| missing', missing)
for b in badlist: print('   BAD', b)
for key in ('R-4 Hyena','R-63 Silenced Diligence','GL-15 Evictor'):
    hit = [e for e in ws if e['page'] == key]
    print(key, '->', [(h.get('damage_index'), h.get('damage_position'), h.get('impact_damage_index'), h.get('impact_damage_position')) for h in hit])
um = w.get('unmatched', [])
print('unmatched', len(um))
print('  evictor?', [u for u in um if 'Evictor' in json.dumps(u)][:2])
