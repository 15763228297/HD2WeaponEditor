import sys, json, re; sys.path.insert(0,'tools')
from pathlib import Path
import wiki_names as W
p = Path('data/wiki/pages/GL-15_Evictor.json')
d = json.loads(p.read_text(encoding='utf-8'))
html = d['html']
print('title:', d['title'], 'html len', len(html))
print()
print('--- parse_page ---')
pp = W.parse_page(html)
print(json.dumps(pp, ensure_ascii=False, indent=1)[:2500] if pp else 'None')
print()
print('--- which attack tables exist? ---')
for m in set(re.findall(r'attack-data-table-([a-z_]+)', html)):
    print('   ', m)
print()
print('--- arc ---'); print(W.parse_arc_section(html))
print('--- explosion_only ---'); print(W.parse_explosion_only(html))
print('--- explosion_section ---'); print(W.parse_explosion_section(html))