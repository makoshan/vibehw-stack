"""DRC 错误里涉及的走线 → (网,层)，并入 blame.json。用法：drc_blame.py drc.json"""
import sys, json, re
d = json.load(open(sys.argv[1])); bl = {tuple(x) for x in json.load(open('blame.json'))}; add = set()
LN = {'F.Cu': 'F.Cu', 'B.Cu': 'B.Cu', 'In1.Cu': 'In1.Cu', 'In2.Cu': 'In2.Cu'}
for v in d['violations']:
    if v['severity'] != 'error': continue
    for it in v['items']:
        m = re.match(r'走线 \[([^\]]+)\] \(([^)]+)\)', it['description'])
        if m and (m.group(1), m.group(2)) not in bl:
            add.add((m.group(1), m.group(2)))
print('新增', sorted(add)); json.dump(sorted(bl | add), open('blame.json', 'w'))
