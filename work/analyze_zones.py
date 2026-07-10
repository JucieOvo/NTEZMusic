"""分析 YAML 输出的音区分布"""
import re, yaml, sys
from collections import Counter

with open(sys.argv[1], encoding='utf-8') as f:
    data = yaml.safe_load(f)

zone_counts = {'high': 0, 'middle': 0, 'low': 0}
note_by_zone = {'high': [], 'middle': [], 'low': []}

for evt in data['score']:
    notes = evt['notes']
    tokens = re.findall(r'[-+&#]?\d+|b\d+|#\d+', notes)
    for t in tokens:
        if t.startswith('+'):
            zone_counts['high'] += 1
            note_by_zone['high'].append(t)
        elif t.startswith('-'):
            zone_counts['low'] += 1
            note_by_zone['low'].append(t)
        else:
            zone_counts['middle'] += 1
            note_by_zone['middle'].append(t)

print("Zone distribution:")
for z in ['high', 'middle', 'low']:
    c = Counter(note_by_zone[z])
    top = c.most_common(5)
    print(f"  {z}: {zone_counts[z]} tokens, top: {top}")

print(f"\nTotal score events: {len(data['score'])}")
print(f"Total tokens: {sum(zone_counts.values())}")
