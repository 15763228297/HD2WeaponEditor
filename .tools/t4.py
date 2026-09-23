import sys, json; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path
true_old = T.parse_damages(Path('data/raw/generated_damage_settings.dl_bin').read_bytes())
derived  = json.load(open('data/damage_records.json', encoding='utf-8'))
wn = json.load(open('data/weapon_names.json', encoding='utf-8'))

print('--- AC-8 Autocannon, as recorded in weapon_names.json ---')
ac = [w for w in wn['weapons'] if w['page']=='AC-8 Autocannon'][0]
print('  damage_index(type_id claimed) =', ac['damage_index'])
print('  damage_position               =', ac['damage_position'])
print('  impact_damage_index           =', ac['impact_damage_index'], 'pos', ac['impact_damage_position'])
print('  wiki impact: standard=%s durable=%s ap=%s' % (ac['wiki']['standard'], ac['wiki']['durable'], ac['wiki']['ap_direct']))

pos = ac['impact_damage_position']
print()
print('--- what the TRUE old table holds at that position ---')
r = true_old[pos]
print('  true_old[%d] = type_id %d, %d/%d AP%s' % (pos, r['type_id'], r['damage'], r['durable_damage'], r['armor_penetration_per_angle'][:2]))
print('  derived[%d]  = type_id %d, %d/%d  (derived is shifted by +5)' % (pos, derived[pos]['type_id'], derived[pos]['damage'], derived[pos]['durable_damage']))

print()
print('--- which TRUE row actually holds the wiki impact numbers 325/260/AP4? ---')
hits = [(x['position'], x['type_id']) for x in true_old
        if x['damage']==325 and x['durable_damage']==260 and x['armor_penetration_per_angle'][0]==4]
print('  ', hits)
print('--- which TRUE row has type_id ==', ac['impact_damage_index'], '? ---')
print('  ', [(x['position'], x['damage'], x['durable_damage'], x['armor_penetration_per_angle'][0])
        for x in true_old if x['type_id']==ac['impact_damage_index']])