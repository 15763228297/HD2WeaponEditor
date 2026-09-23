import json, pathlib
root = pathlib.Path('.')
recs = json.loads((root/'data/damage_records.json').read_text(encoding='utf-8'))
print('top type', type(recs).__name__)
if isinstance(recs, dict):
    print('keys', list(recs)[:8])
    first = recs[list(recs)[0]]
    print('value type', type(first).__name__)
    print('first value', json.dumps(first)[:300])
else:
    print('len', len(recs))
    print('first', json.dumps(recs[0])[:300])
