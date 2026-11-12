"""耦合关：某网在新板上与别的信号网（不含 GND/3V3）同层平行、边距 <0.2 mm 的累计长度，按网对比旧板。
用法：python par.py 旧.json 新.json 网   退出码 1 = 出现新的 >3 mm 平行对，或已有的对增加 >1 mm"""
import sys, json, math, collections
from shapely.geometry import LineString
MM = 1e6; GAP = 0.2 * MM; MIN = 3 * MM
def pairs(d, net):
    mine = [t for t in d['tracks'] if t['net'] == net and t['a'] != t['b']]
    oth = [t for t in d['tracks'] if t['net'] not in (net, 'GND', '3V3') and t['a'] != t['b']]
    acc = collections.Counter()
    for a in mine:
        la = LineString([a['a'], a['b']]); ua = ((a['b'][0]-a['a'][0])/la.length, (a['b'][1]-a['a'][1])/la.length)
        for b in oth:
            if b['layer'] != a['layer']: continue
            lb = LineString([b['a'], b['b']])
            if la.distance(lb) - a['w']/2 - b['w']/2 >= GAP: continue
            ub = ((b['b'][0]-b['a'][0])/lb.length, (b['b'][1]-b['a'][1])/lb.length)
            if abs(ua[0]*ub[1]-ua[1]*ub[0]) > 0.02: continue      # 不平行
            # 重叠长度：b 两端投影到 a 上
            t0 = sorted(((p[0]-a['a'][0])*ua[0] + (p[1]-a['a'][1])*ua[1]) for p in (b['a'], b['b']))
            ov = max(0, min(la.length, t0[1]) - max(0, t0[0]))
            acc[(b['net'], a['layer'])] += ov
    return {k: v for k, v in acc.items()}
old, new = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])); net = sys.argv[3]
po, pn = pairs(old, net), pairs(new, net)
bad = [(k, round(po.get(k, 0)/MM, 1), round(v/MM, 1)) for k, v in pn.items() if v > MIN and v > po.get(k, 0) + 1*MM]
print('耦合', net, '新增/变长的平行对', bad)
sys.exit(1 if bad else 0)
