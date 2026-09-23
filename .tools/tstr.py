import json, re
from pathlib import Path
s = json.load(open('data/strings.json', encoding='utf-8'))
print('strings.json keys:', len(s))
vals = {}
for k, langs in s.items():
    for lang, v in langs.items():
        vals.setdefault(v, k)
print('distinct values:', len(vals))
for probe in ('Evictor','EVICTOR','Ironclad','Meltagun','Verdict','Suppressor','Pacifier','Coyote','One-Two'):
    hits = [v for v in vals if probe.lower() in v.lower()]
    print(f'  {probe!r}: {len(hits)} -> {hits[:6]}')