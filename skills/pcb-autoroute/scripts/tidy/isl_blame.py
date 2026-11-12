"""找新增孤岛旁边被改过的线：新板孤岛减去源板孤岛（同网同层）>0.3 mm² 的区域，0.35 mm 内有改动过的线（同层，任意网）就记 (网, 层)。
用法：isl_blame.py 源板.json 源孤岛.json 新板.json 新孤岛.json → 打印候选 (网,层)，写 blame.json"""
import sys, json, collections, os
MIN = float(os.environ.get('MIN_MM2', '0.3'))
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union
S, SI, N, NI = [json.load(open(p)) for p in sys.argv[1:5]]
def isl(d):
    g = collections.defaultdict(list)
    for x in d['islands']:
        if len(x['pts']) >= 3: g[(x['net'], x['layer'])].append(Polygon(x['pts']).buffer(0))
    return {k: unary_union(v) for k, v in g.items()}
si, ni = isl(SI), isl(NI)
key = lambda t: (t['net'], t['layer'], tuple(sorted([tuple(t['a']), tuple(t['b'])])))
old = {key(t) for t in S['tracks']}
changed = [t for t in N['tracks'] if key(t) not in old]
blame, regions = set(), []
for k_, g in ni.items():
    new = g.difference(si.get(k_, Polygon()).buffer(20000))
    for r in getattr(new, 'geoms', [new]):
        if r.is_empty or r.area < MIN * 1e12: continue
        regions.append((k_, round(r.area / 1e12, 2), round(r.centroid.x / 1e6, 1), round(r.centroid.y / 1e6, 1)))
        for t in changed:
            if t['layer'] == k_[1] and LineString([t['a'], t['b']]).distance(r) < 350000:
                blame.add((t['net'], t['layer']))
print('新增孤岛区域', regions)
print('候选', sorted(blame))
prev = {tuple(x) for x in json.load(open('blame.json'))} if os.path.exists('blame.json') and os.environ.get('ACC') else set()
blame |= prev
print('累计', len(blame))
json.dump(sorted(blame), open('blame.json', 'w'))
