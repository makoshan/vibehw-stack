"""走线整理：把布线器留下的细碎台阶拉成 0/45/90° 的直线段，只改线、不动件和过孔。
用法：python tidy.py base.json out.json
做法：同网同层的线连成链，链在"分叉 / 过孔 / 焊盘内 / 线宽变化"处断开；
每条链从头往后贪心找最远的点，用一段直线或"一段斜 45° + 一段直线"连过去，
新线离别网铜、孔、板边、禁布区都满足间距（另加余量）才采用，否则退回原样。
最后由 KiCad 原生 DRC 和未连接数复核，脚本自己的判定只是初筛。"""
import json, sys, time, collections, os
DEBUG_NET = os.environ.get('DEBUG_NET')
ONLY_NET = os.environ.get('ONLY_NET')
WHY = collections.Counter()
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree
from shapely.prepared import prep
import reroute
REROUTE = os.environ.get('REROUTE', '0') == '1'   # 链内局部 A*：慢、收益小（C08A PWR_EN 22 s 只换 2 处），默认关

MM = 1_000_000
CLR = int(0.10 * MM)        # 铜到铜
HOLE_CLR = int(0.20 * MM)   # 铜到孔壁
EDGE_CLR = int(0.30 * MM)   # 铜到板边
MARGIN = int(0.01 * MM)     # 自加余量，防止贴着规则边
WINDOW = 400                # 每次往后最多看多少个点

d = json.load(open(sys.argv[1]))
tracks = d['tracks']
layers = sorted({t['layer'] for t in tracks})

# ---------- 障碍物 ----------
def polys(pl):
    return [Polygon(p) for p in pl if len(p) >= 3]

pad_geo = collections.defaultdict(list)   # layer -> [(net, poly)]
for p in d['pads']:
    for L, pl in p['polys'].items():
        for g in polys(pl):
            pad_geo[L].append((p['net'], g))
for c in d['fpcopper']:
    for g in polys(c['polys']):
        pad_geo[c['layer']].append(('', g))
via_geo = [(v['net'], Point(v['p']).buffer(v['d'] / 2, 16), v) for v in d['vias']]
holes = [Point(h['p']).buffer(h['r'], 16) for h in d['holes']] + [Point(v['p']).buffer(v['drill'] / 2, 16) for v in d['vias']]
hole_tree = STRtree(holes)
board = Polygon(d['edge'][0], d['edge'][1:])
keep = collections.defaultdict(list)
for z in d['keepouts']:
    if not z.get('no_tracks', True):
        continue
    for L in z['layers']:
        keep[L] += polys(z['polys'])

static = {}
for L in layers:
    items = [(n, g) for n, g in pad_geo[L]] + [(n, g) for n, g, _ in via_geo]
    static[L] = (STRtree([g for _, g in items]), [n for n, _ in items])
inner_cache = {}
def inner(w):
    if w not in inner_cache:
        inner_cache[w] = prep(board.buffer(-(EDGE_CLR + w / 2 + MARGIN)))
    return inner_cache[w]

# 走线障碍：按层建树，每处理完一个网重建一次（把新线放进去）
alive = {t['id']: t for t in tracks}
def build_track_trees():
    out = {}
    for L in layers:
        ts = [t for t in alive.values() if t['layer'] == L]
        out[L] = (STRtree([LineString([t['a'], t['b']]) if t['a'] != t['b'] else Point(t['a']) for t in ts]), ts)
    return out

# 孔：同网过孔的孔不算障碍（线本来就接在上面）；焊盘孔只有 10 个，一律按障碍
n_pad_holes = len(d['holes'])
def same_net_hole(i, net):
    return i >= n_pad_holes and d['vias'][i - n_pad_holes]['net'] == net

def seg_ok2(a, b, net, L, w, ttrees):
    if a == b:
        return True
    ln = LineString([a, b])
    for i in hole_tree.query(ln, predicate='dwithin', distance=w / 2 + HOLE_CLR + MARGIN):
        if not same_net_hole(i, net):
            WHY['孔'] += 1
            return False
    return seg_ok_nohole(ln, a, b, net, L, w, ttrees)

def seg_ok_nohole(ln, a, b, net, L, w, ttrees):
    if not inner(w).contains(ln):
        WHY['板边'] += 1
        return False
    tree, nets = static[L]
    for i in tree.query(ln, predicate='dwithin', distance=w / 2 + CLR + MARGIN):
        if nets[i] != net:
            WHY['焊盘/过孔'] += 1
            return False
    tt, ts = ttrees[L]
    for i in tt.query(ln, predicate='dwithin', distance=w / 2 + CLR + MARGIN + int(0.2 * MM)):
        t = ts[i]
        o = LineString([t['a'], t['b']]) if t['a'] != t['b'] else Point(t['a'])
        if t['net'] == net:
            # 同网别的线：碰上会多出交汇点（锐角/折回/重叠），只许在链两端碰
            if t['id'] in CUR['ids'] or ln.distance(o) >= w / 2 + t['w'] / 2:
                continue
            ov = ln.intersection(o.buffer(w / 2 + t['w'] / 2 + 1))
            if ov.difference(Point(CUR['ends'][0]).buffer(w * 1.5).union(Point(CUR['ends'][1]).buffer(w * 1.5))).is_empty:
                continue
            WHY['同网线'] += 1
            return False
        if ln.distance(o) < w / 2 + t['w'] / 2 + CLR + MARGIN:
            WHY['别网线'] += 1
            return False
    for k in keep[L]:
        if ln.distance(k) < w / 2 + MARGIN:
            return False
    return True

# ---------- 链 ----------
def sgn(x):
    return (x > 0) - (x < 0)

def octi(a, b):
    """a→b 的 0/45/90° 走法：一段直达，或两种"斜+直"/"直+斜"。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 or dy == 0 or abs(dx) == abs(dy):
        return [[a, b]]
    m = min(abs(dx), abs(dy))
    p1 = [a[0] + sgn(dx) * m, a[1] + sgn(dy) * m]
    p2 = [b[0] - sgn(dx) * m, b[1] - sgn(dy) * m]
    return [[a, p1, b], [a, p2, b]]

def plen(pts):
    return sum(((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) ** .5 for i in range(len(pts) - 1))

def collinear_merge(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if p == out[-1]:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (b[0] - a[0]) * (p[1] - b[1]) - (b[1] - a[1]) * (p[0] - b[0]) == 0 and \
               (b[0] - a[0]) * (p[0] - b[0]) + (b[1] - a[1]) * (p[1] - b[1]) > 0:
                out[-1] = p
                continue
        out.append(p)
    return out

by = collections.defaultdict(list)
for t in tracks:
    by[(t['net'], t['layer'])].append(t)

# T 形接法：同网同层别的线的端点落在某段线中间（不在两端）时，把这段线在该点切开，
# 否则拉直会把这段线挪走，接在它中间的线就悬空了。
SKIP_NETS = set(sys.argv[3].split(',')) if len(sys.argv) > 3 and sys.argv[3] else set()
n_split = 0
for key, ts in by.items():
    ends = {tuple(t['a']) for t in ts} | {tuple(t['b']) for t in ts}
    changed = True
    while changed:
        changed = False
        for t in list(ts):
            ln = LineString([t['a'], t['b']]) if t['a'] != t['b'] else None
            if ln is None:
                continue
            for e in ends:
                if e in (tuple(t['a']), tuple(t['b'])):
                    continue
                if ln.distance(Point(e)) <= 1000:
                    t1 = dict(t, b=list(e)); t2 = dict(t, a=list(e), id=t['id'] + '#2')
                    t1['id'] = t['id'] + '#1'
                    t1['orig'] = t2['orig'] = t.get('orig', t['id'])
                    ts.remove(t); ts += [t1, t2]; n_split += 1; changed = True
                    break
            if changed:
                break
print('T 形切开', n_split)
tracks = [t for ts in by.values() for t in ts]
alive = {t['id']: t for t in tracks}

def pad_contains(net, L):
    gs = [g for n, g in pad_geo[L] if n == net]
    return gs

def other_dirs(n, chain, adj):
    """该点上当前还在的同网同层别的线（含已被别的链改过的新线）的出线方向。"""
    ids = {c['id'] for c in chain}
    net, L = chain[0]['net'], chain[0]['layer']
    out = []
    for e in alive.values():
        if e['net'] != net or e['layer'] != L or e['id'] in ids:
            continue
        for a, b in ((e['a'], e['b']), (e['b'], e['a'])):
            if tuple(a) == n and tuple(b) != n:
                out.append((b[0] - n[0], b[1] - n[1]))
    return out

def acute_count(pts, ends_nb):
    """折点处两条出线夹角 <90°（点积 >0）就算一个锐角/折回。"""
    c = 0
    for i in range(1, len(pts) - 1):
        u = (pts[i - 1][0] - pts[i][0], pts[i - 1][1] - pts[i][1]); v = (pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        c += u[0] * v[0] + u[1] * v[1] > 0
    for i, j in ((0, 1), (len(pts) - 1, len(pts) - 2)):
        v = (pts[j][0] - pts[i][0], pts[j][1] - pts[i][1])
        c += sum(1 for u in ends_nb.get(i, []) if u[0] * v[0] + u[1] * v[1] > 0)
    return c

def seg_key(net, L, a, b):
    return (net, L) + tuple(sorted([tuple(a), tuple(b)]))

def existing_keys(net, L, chain):
    ids = {c['id'] for c in chain}
    return {seg_key(net, L, t['a'], t['b']) for t in alive.values() if t['net'] == net and t['layer'] == L and t['id'] not in ids}

CUR = {'ids': set(), 'ends': [(0, 0), (0, 0)]}
class Ctx:
    pass

def refine(out, net, L, w, ttrees):
    """窗口式局部重布：取 out[i..j] 这一小段，用少拐弯 A* 找 0/45/90° 新路，段数更少且逐段精确检查通过才换。"""
    ctx = Ctx()
    for k in ('static', 'hole_tree', 'keep', 'board', 'CLR', 'HOLE_CLR', 'EDGE_CLR', 'MARGIN', 'CUR', 'same_net_hole', 'octi', 'seg_ok2'):
        setattr(ctx, k, globals()[k])
    ctx.ttrees = ttrees
    res, i = [out[0]], 0
    while i < len(out) - 1:
        done = False
        for span in (8, 5, 3):
            j = min(i + span, len(out) - 1)
            if j - i < 2:
                break
            orig_len = plen(out[i:j + 1])
            path = reroute.astar(tuple(out[i]), tuple(out[j]), net, L, w, ctx)
            if path:
                path = collinear_merge(path)
                if len(path) - 1 < j - i and plen(path) <= orig_len + 200000 and \
                   all(seg_ok2(path[k], path[k + 1], net, L, w, ttrees) for k in range(len(path) - 1)):
                    res += path[1:]
                    i = j
                    done = True
                    stats['局部重布'] += 1
                    break
            if j == len(out) - 1:
                continue
        if not done:
            res.append(out[i + 1])
            i += 1
    return collinear_merge(res)

removed, added = [], []
stats = collections.Counter()
t0 = time.time()
ttrees = build_track_trees()
nets_order = sorted({k[0] for k in by})
for ni, net in enumerate(nets_order):
    if net in SKIP_NETS or (ONLY_NET and net != ONLY_NET):
        continue
    for L in layers:
        ts = by.get((net, L))
        if not ts:
            continue
        adj = collections.defaultdict(list)
        for t in ts:
            adj[tuple(t['a'])].append(t)
            adj[tuple(t['b'])].append(t)
        vpts = [(Point(v['p']), v['d'] / 2) for v in d['vias'] if v['net'] == net]
        pgs = pad_contains(net, L)
        def fixed(n):
            es = adj[n]
            if len(es) != 2 or es[0]['w'] != es[1]['w']:
                return True
            P = Point(n)
            if any(P.distance(c) <= r for c, r in vpts):
                return True
            return any(g.distance(P) <= 1000 for g in pgs)
        used = set()
        for start in list(adj):
            if not fixed(start):
                continue
            for e in adj[start]:
                if e['id'] in used:
                    continue
                chain, pts, cur, edge = [e], [list(start)], start, e
                while True:
                    used.add(edge['id'])
                    nxt = tuple(edge['b']) if tuple(edge['a']) == cur else tuple(edge['a'])
                    pts.append(list(nxt))
                    if fixed(nxt):
                        break
                    edge = [x for x in adj[nxt] if x['id'] != edge['id']][0]
                    if edge['id'] in used:
                        break
                    chain.append(edge)
                    cur = nxt
                if len(chain) < 2:
                    continue
                w = chain[0]['w']
                CUR['ids'] = {c['id'] for c in chain}; CUR['ends'] = [tuple(pts[0]), tuple(pts[-1])]
                base = collinear_merge(pts)
                # 贪心拉直
                out, i = [base[0]], 0
                while i < len(base) - 1:
                    done = False
                    for j in range(min(len(base) - 1, i + WINDOW), i + 1, -1):
                        orig = plen(base[i:j + 1])
                        for cand in octi(base[i], base[j]):
                            if plen(cand) > orig + 1000:
                                continue
                            if all(seg_ok2(cand[k], cand[k + 1], net, L, w, ttrees) for k in range(len(cand) - 1)):
                                out += cand[1:]
                                i, done = j, True
                                stats['拉直'] += 1
                                break
                        if done:
                            break
                    if not done:
                        out.append(base[i + 1])
                        i += 1
                out = collinear_merge(out)
                if REROUTE and len(out) >= 4:
                    out = refine(out, net, L, w, ttrees)
                if len(out) < 2 or tuple(out[0]) == tuple(out[-1]):
                    continue  # 首尾同点的环，不动
                # 不许新增锐角/折回：链内每个折点，以及链两端与该点上其它线的夹角都要 ≥90°；
                # 原链本来就有的锐角不算新增（这时整条链保持原样）
                ends_nb = {0: other_dirs(tuple(out[0]), chain, adj), len(out) - 1: other_dirs(tuple(out[-1]), chain, adj)}
                if DEBUG_NET == net:
                    print('链', L, len(chain), '段 →', len(out) - 1, [tuple(round(x / 1e6, 3) for x in pts[0]), tuple(round(x / 1e6, 3) for x in pts[-1])], dict(WHY)); WHY.clear()
                if acute_count(out, ends_nb) > acute_count(pts, {0: ends_nb[0], len(pts) - 1: ends_nb[len(out) - 1]}):
                    stats['因锐角保持原样'] += 1
                    continue
                if len(out) - 1 < len(chain):
                    removed += [c.get('orig', c['id']) for c in chain]
                    for c in chain:
                        alive.pop(c['id'], None)
                    for k in range(len(out) - 1):
                        if seg_key(net, L, out[k], out[k + 1]) in existing_keys(net, L, chain):
                            stats['跳过重复段'] += 1
                            continue
                        nt = {'id': f'new{len(added)}', 'net': net, 'layer': L, 'a': out[k], 'b': out[k + 1], 'w': w}
                        added.append(nt)
                        alive[nt['id']] = nt
                    stats['改动链'] += 1
                    stats['原段数'] += len(chain)
                    stats['新段数'] += len(out) - 1
    ttrees = build_track_trees()
    if ni % 20 == 0:
        print(f'{ni}/{len(nets_order)} {net} {dict(stats)} {time.time() - t0:.0f}s', flush=True)

removed = sorted(set(removed))
# 被切开的原线只要有一截改了，整条原线会删掉；没改的那几截要原样加回去
rs = set(removed)
for t in list(alive.values()):
    if t.get('orig') in rs and not t['id'].startswith('new'):
        added.append(dict(t, id=f'new{len(added)}'))
base_keys = collections.Counter(seg_key(t['net'], t['layer'], t['a'], t['b']) for t in tracks if t.get('orig', t['id']) not in rs)
seen, ded = set(), []
for t in added:
    kk = seg_key(t['net'], t['layer'], t['a'], t['b'])
    if kk in seen or kk in base_keys:
        stats['结尾去重'] += 1
        continue
    seen.add(kk); ded.append(t)
added = ded
json.dump({'removed': removed, 'added': added, 'stats': stats}, open(sys.argv[2], 'w'))
print('完成', dict(stats), '总段数', len(tracks), '→', len(alive), f'{time.time() - t0:.0f}s')
