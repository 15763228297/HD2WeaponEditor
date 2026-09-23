import sys, json; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path
true_old = T.parse_damages(Path('data/raw/generated_damage_settings.dl_bin').read_bytes())
by_tid = {r['type_id']: r for r in true_old}
by_pos = {r['position']: r for r in true_old}
wn = json.load(open('data/weapon_names.json', encoding='utf-8'))

def ok(row, w):
    if row is None: return False
    return (row['damage']==w['wiki']['standard'] and
            row['durable_damage']==w['wiki']['durable'] and
            row['armor_penetration_per_angle'][0]==w['wiki']['ap_direct'])

stats = {'by_type':0,'by_pos':0,'neither':0,'no_wiki':0}
bad = []
for w in wn['weapons']:
    wk = w['wiki']
    if wk.get('standard') is None:
        stats['no_wiki'] += 1; continue
    t = ok(by_tid.get(w['damage_index']), w)
    p = ok(by_pos.get(w['damage_position']), w)
    if t: stats['by_type'] += 1
    if p: stats['by_pos'] += 1
    if not t and not p: stats['neither'] += 1
    if t != p:
        bad.append((w['page'], w['damage_index'], w['damage_position'], t, p))
print('weapons with wiki impact numbers:', sum(1 for w in wn['weapons'] if w['wiki'].get('standard') is not None))
print('  reproduces wiki when damage_index read as TYPE_ID :', stats['by_type'])
print('  reproduces wiki when damage_position read as INDEX:', stats['by_pos'])
print('  neither                                          :', stats['neither'])
print('  no wiki numbers                                  :', stats['no_wiki'])
print()
print('disagreements (page, damage_index, damage_position, type_ok, pos_ok):', len(bad))
for b in bad[:12]: print('   ', b)
print()
shifts = [w['damage_position'] - by_tid[w['damage_index']]['position']
          for w in wn['weapons'] if w['damage_index'] in by_tid]
print('damage_position - true_position  (min/max/distinct):', min(shifts), max(shifts), sorted(set(shifts)))