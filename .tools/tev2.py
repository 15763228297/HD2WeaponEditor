import json, re
from pathlib import Path
html = json.loads(Path('data/wiki/pages/GL-15_Evictor.json').read_text(encoding='utf-8'))['html']
# strip tags for a readable view
txt = re.sub(r'<[^>]+>', ' ', html)
txt = re.sub(r'\s+', ' ', txt)
print(txt[:2600])
print()
print('=== table classes present ===')
for m in sorted(set(re.findall(r'class="([^"]*table[^"]*)"', html))):
    print('   ', m)
print()
print('=== any data-* attrs ===')
for m in sorted(set(re.findall(r'(data-[a-z-]+)=', html)))[:40]:
    print('   ', m)