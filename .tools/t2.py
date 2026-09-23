import sys, json, struct; sys.path.insert(0,'tools')
import dlbin_tables as T
from pathlib import Path
true_old = T.parse_damages(Path('data/raw/generated_damage_settings.dl_bin').read_bytes())
derived  = json.load(open('data/damage_records.json', encoding='utf-8'))
print('true old rows:', len(true_old), ' derived rows:', len(derived))
shift_ok = all(
    derived[i]['type_id'] == true_old[i+5]['type_id'] and
    derived[i]['damage'] == true_old[i+5]['damage'] and
    derived[i]['durable_damage'] == true_old[i+5]['durable_damage'] and
    derived[i]['armor_penetration_per_angle'] == true_old[i+5]['armor_penetration_per_angle']
    for i in range(len(derived)))
print('derived[i] == true_old[i+5] for ALL rows:', shift_ok)
print('true_old[0:6]:', [(r['position'], r['type_id'], r['damage']) for r in true_old[:6]])
print('derived[0:6]  :', [(r['index'], r['type_id'], r['damage']) for r in derived[:6]])
print()
print('ARRAY_START in gen_mod = 0x1e0 =', 0x1e0)
print('true array start in file = 100  -> 480 - 100 =', 480-100, '= 5 * 76 =', 5*76)