import json
d = json.load(open('data/weapon_names.json', encoding='utf-8'))
for page in ('R-4 Hyena','AC-8 Autocannon','EAT-17 Expendable Anti-Tank','GL-21 Grenade Launcher'):
    m = [w for w in d['weapons'] if w['page']==page]
    if not m: print(page, '-> NOT MATCHED'); continue
    w = m[0]
    print('%-34s type_id=%-4d pos=%-4d  %d/%d AP%s  wiki=%s/%s AP%s  verified=%s' % (
        page, w['damage_index'], w['damage_position'], w['damage'], w['durable'], w['ap'][0],
        w['wiki'].get('standard'), w['wiki'].get('durable'), w['wiki'].get('ap_direct'), w['verified']))
print()
print('total matched:', len(d['weapons']), 'all verified:', all(w['verified'] for w in d['weapons']))
print('unmatched:', len(d['unmatched']))