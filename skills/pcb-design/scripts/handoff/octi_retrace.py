"""整段重画走线：沿原走廊把每段线（焊盘/过孔/分叉之间）重写成只含 0/45/90° 的线，拐弯只许 45°。
用法：python octi_retrace.py 板.json 输出.json [跳过网,逗号分隔]   （板.json 由 extract.py 导出，单位 nm）
约束：中途只经过原线上的点（不换走廊、不动过孔和件）；出长方形焊盘沿长轴或垂直；一串一个线宽（取最宽）；
放不下才按原线宽分段（只宽不窄）。间距由这里初筛，最后以 KiCad 原生 DRC 为准。"""
import json, math, os, sys, collections
import numpy as np, shapely
from shapely.geometry import LineString, Point, Polygon
from shapely import STRtree

MM = 1_000_000
CLR, HOLE, EDGE = int(.1 * MM), int(float(os.environ.get('HOLE', '.15')) * MM), int(float(os.environ.get('EDGE', '.21')) * MM)
MARGIN = int(.001 * MM)
TOUCH = int(.06 * MM)                                      # 线头离孔/焊盘不到半线宽，铜已相碰，算接上
STEP = int(float(os.environ.get('STEP', '.12')) * MM)     # 沿原线取点间隔
BEND = float(os.environ.get('BEND', '.6')) * MM           # 每个拐弯折算的长度
SHORT = int(.12 * MM)                                      # 比这短的中间段要罚
WIN = int(os.environ.get('WIN', '90'))
PASSES = int(os.environ.get('PASSES', '3'))
d = json.load(open(sys.argv[1])); out_path = sys.argv[2]
SKIP = set(filter(None, (sys.argv[3] if len(sys.argv) > 3 else '').split(',')))
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
ang = lambda v: math.degrees(math.atan2(v[1], v[0])) % 360
def dir8(v):
    return round(ang(v) / 45) % 8
def turn(a, b):
    t = abs(a - b) % 8
    return min(t, 8 - t)
dist = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])

# ---------- 障碍 ----------
layers = sorted({t['layer'] for t in d['tracks']})
static = {L: [] for L in layers}                     # (几何, 网)
LONG = {}                                            # 细长焊盘中心 → 长轴两个方向
pads_on = collections.defaultdict(list)              # (网, 层) → (多边形, 中心, 允许方向或 None)
for p in d['pads']:
    for L, ps in p['polys'].items():
        for pl in ps:
            g = Polygon(pl).buffer(0)
            if L in static:
                static[L].append((g, p['net']))
            allow = None
            r = g.minimum_rotated_rectangle
            c = list(r.exterior.coords); e1 = (c[1][0] - c[0][0], c[1][1] - c[0][1]); e2 = (c[2][0] - c[1][0], c[2][1] - c[1][1])
            l1, l2 = math.hypot(*e1), math.hypot(*e2)
            if abs(l1 - l2) >= .05 * MM:
                ax = ang(e1 if l1 > l2 else e2) % 180
                if abs((ax + 22.5) % 45 - 22.5) < .6:
                    k = round(ax / 45) % 8
                    allow = {k % 8, (k + 2) % 8, (k + 4) % 8, (k + 6) % 8}
            ct = g.centroid
            if allow and max(l1, l2) >= 1.5 * min(l1, l2):
                LONG[(int(ct.x), int(ct.y))] = {k % 8, (k + 4) % 8}
            pads_on[(p['net'], L)].append((g, (int(ct.x), int(ct.y)), allow, f"{p['ref']}.{p['num']}"))
for v in d['vias']:
    r = max(v['d'] // 2, v['drill'] // 2 + HOLE - CLR)
    for L in layers:
        static[L].append((Point(*v['p']).buffer(r, 16), v['net']))
for h in d['holes']:
    for L in layers:
        static[L].append((Point(*h['p']).buffer(h['r'] + HOLE - CLR, 16), ''))
for e in d['edge']:
    ring = LineString(e + e[:1]).buffer(EDGE - CLR)
    for L in layers:
        static[L].append((ring, ''))
for z in d['keepouts']:
    if z['no_tracks']:
        for pl in z['polys']:
            for L in z['layers']:
                if L in static:
                    static[L].append((Polygon(pl).buffer(0), ''))

pads_on_all = [x for v in pads_on.values() for x in [(y[0], y[1]) for y in v]]
# ---------- 图：同网同层 ----------
groups = collections.defaultdict(list)
seen = set()
for t in d['tracks']:
    a, b = tuple(t['a']), tuple(t['b'])
    if a == b:
        continue
    k = (t['net'], t['layer'], min(a, b), max(a, b))
    if k in seen:
        continue
    seen.add(k); groups[(t['net'], t['layer'])].append([a, b, t['w']])
vias_by_net = collections.defaultdict(list)
for v in d['vias']:
    vias_by_net[v['net']].append((tuple(v['p']), v['d'] // 2))

def proj(P, A, B):
    ax, ay = B[0] - A[0], B[1] - A[1]; l2 = ax * ax + ay * ay
    u = max(0, min(1, ((P[0] - A[0]) * ax + (P[1] - A[1]) * ay) / l2))
    return (round(A[0] + u * ax), round(A[1] + u * ay)), u

def normalize(net, L, segs):
    """线头对齐、T 接拆开、同网孔和焊盘压在线中间时在那里断开。"""
    # 端点吸附 5 µm
    pts = []
    def snap(P):
        for Q in pts:
            if dist(P, Q) <= 5000:
                return Q
        pts.append(P); return P
    segs = [[snap(a), snap(b), w] for a, b, w in segs]
    segs = [s for s in segs if s[0] != s[1]]
    def split_at(P, tol):
        for i, (A, B, w) in enumerate(segs):
            if P in (A, B):
                continue
            Q, u = proj(P, A, B)
            if dist(P, Q) <= tol(w) and 0 < u < 1 and dist(Q, A) > 5000 and dist(Q, B) > 5000:
                segs[i] = [A, Q, w]; segs.append([Q, B, w]); return Q
        return None
    deg = collections.Counter(p for s in segs for p in s[:2])
    loose = [p for p, c in deg.items() if c == 1 and not any(dist(p, v) <= r for v, r in vias_by_net[net]) and not any(g.buffer(1000).covers(Point(p)) for g, *_ in pads_on[(net, L)])]
    for i, P in enumerate(loose):
        for Q in loose[i + 1:]:
            wmin = min(s[2] for s in segs if P in s[:2] or Q in s[:2])
            if dist(P, Q) <= wmin:
                M = ((P[0] + Q[0]) // 2, (P[1] + Q[1]) // 2)
                for s in segs:
                    s[0] = M if s[0] in (P, Q) else s[0]; s[1] = M if s[1] in (P, Q) else s[1]
                report['线头并点'] += 1
                break
    changed = True
    while changed:
        changed = False
        deg = collections.Counter(p for s in segs for p in s[:2])
        for E in [p for p, c in deg.items() if c == 1]:
            Q = split_at(E, lambda w: w // 2)
            if Q:
                for s in segs:
                    if s[0] == E: s[0] = Q
                    if s[1] == E: s[1] = Q
                segs[:] = [s for s in segs if s[0] != s[1]]
                changed = True; break
    for c, r in vias_by_net[net]:
        if not any(dist(p, c) <= r for s in segs for p in s[:2]):
            split_at(c, lambda w: r + w // 2)
    for g, ct, _, _ in pads_on[(net, L)]:
        if not any(g.covers(Point(p)) for s in segs for p in s[:2]):
            for i, (A, B, w) in enumerate(segs):
                ls = LineString([A, B])
                if ls.intersects(g):
                    Q, _u = proj(ct, A, B)
                    if Q not in (A, B):
                        segs[i] = [A, Q, w]; segs.append([Q, B, w])
                    break
    return segs

def anchor(net, L, P, tol=1000):
    """返回 (中心, 允许方向, 名, 焊盘)；P 不在同网孔/焊盘上返回 None。tol 只对真线头放宽到 TOUCH。"""
    for t in ((1000, tol) if tol > 1000 else (tol,)):     # 先认真正落在里面的，再认只是碰到的
        for c, r in vias_by_net[net]:
            if dist(P, c) <= r + t:
                return c, None, 'via', None
        for g, ct, allow, name in pads_on[(net, L)]:
            if g.buffer(t).covers(Point(P)):
                return ct, allow, name, g
    return None

class Chain:
    __slots__ = 'net L pts ws ends'.split()

chains = []; report = collections.Counter(); fails = []
for (net, L), segs in sorted(groups.items()):
    segs = normalize(net, L, segs)
    adj = collections.defaultdict(list)
    for i, (A, B, w) in enumerate(segs):
        adj[A].append(i); adj[B].append(i)
    stop = {P for P in adj if len(adj[P]) != 2 or anchor(net, L, P)}
    used = set()
    def walk(P, i):
        pts = [P]; ws = []
        while True:
            used.add(i); A, B, w = segs[i]; Q = B if A == P else A
            pts.append(Q); ws.append(w); P = Q
            if P in stop:
                return pts, ws
            nx = [j for j in adj[P] if j != i and j not in used]
            if not nx:
                return pts, ws
            i = nx[0]
    for P in sorted(stop):
        for i in adj[P]:
            if i not in used:
                c = Chain(); c.net, c.L = net, L; c.pts, c.ws = walk(P, i); chains.append(c)
    for i in range(len(segs)):
        if i not in used:                      # 没有端点的闭环
            c = Chain(); c.net, c.L = net, L; c.pts, c.ws = walk(segs[i][0], i); chains.append(c); report['闭环'] += 1
print('线段串', len(chains), '网层', len(groups))
DBG = os.environ.get('DBG')
def dbg(tag):
    if DBG:
        for c in chains:
            if c.net == DBG and c.L == 'B.Cu':
                print(tag, [(round(p[0] / MM, 3), round(p[1] / MM, 3)) for p in c.pts], flush=True)
dbg('初')

# ---------- 悬空短线删掉 ----------
def node_deg():
    deg = collections.Counter()
    for c in chains:
        deg[(c.net, c.L, c.pts[0])] += 1; deg[(c.net, c.L, c.pts[-1])] += 1
    return deg
for _ in range(4):
    deg = node_deg(); keep = []
    for c in chains:
        dead = [P for P in (c.pts[0], c.pts[-1]) if deg[(c.net, c.L, P)] == 1 and not anchor(c.net, c.L, P, TOUCH)]
        ln = sum(dist(a, b) for a, b in zip(c.pts, c.pts[1:]))
        if dead and ln < 1.5 * MM and c.net not in SKIP:
            report['删悬空'] += 1; continue
        keep.append(c)
    chains = keep

# ---------- 动态障碍 ----------
def capsule(c):
    return [(LineString([a, b]).buffer(w / 2, 8), c.net) for a, b, w in zip(c.pts, c.pts[1:], c.ws)]
cap = {id(c): capsule(c) for c in chains}
trees = {}
def rebuild(L):
    items = list(static[L])
    for c in chains:
        if c.L == L:
            items += cap[id(c)]
    trees[L] = (STRtree([g for g, _ in items]), np.array([n for _, n in items], dtype=object))
for L in layers:
    rebuild(L)
def clear(net, L, line, w):
    t, nets = trees[L]
    idx = t.query(line, predicate='dwithin', distance=w / 2 + CLR + MARGIN)
    return all(nets[i] == net and net != '' for i in idx)

# ---------- 单串求解 ----------
def connectors(P, Q):
    dx, dy = Q[0] - P[0], Q[1] - P[1]; ax, ay = abs(dx), abs(dy)
    if ax < 300 or ay < 300 or abs(ax - ay) < 300:
        return [[P, Q]]
    sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1); m = min(ax, ay)
    A = (P[0] + sx * m, P[1] + sy * m)                # 先斜后直
    B = (Q[0] - sx * m, Q[1] - sy * m)                # 先直后斜
    return [[P, A, Q], [P, B, Q]]

def samples(pts, ws):
    Q = [pts[0]]; W = [ws[0]]
    for (a, b), w in zip(zip(pts, pts[1:]), ws):
        L_ = dist(a, b); n = max(1, int(L_ // STEP))
        for i in range(1, n + 1):
            p = (round(a[0] + (b[0] - a[0]) * i / n), round(a[1] + (b[1] - a[1]) * i / n))
            if dist(p, Q[-1]) >= 15000 or (i == n and b == pts[-1]):
                Q.append(p); W.append(w)
    if Q[-1] != pts[-1]:
        Q.append(pts[-1]); W.append(ws[-1])
    return Q, W

def solve(c, S, E, sdirs, edirs, wmode):
    Q, W = samples([S] + c.pts[1:-1] + [E], c.ws)
    n = len(Q); wmax = max(c.ws)
    best = {(0, -1): (0.0, None, None)}
    for k in range(n - 1):
        states = [(dd, best[(k, dd)]) for dd in range(-1, 8) if (k, dd) in best]
        if not states:
            continue
        for j in range(min(n - 1, k + WIN), k, -1):
            w = wmax if wmode == 'max' else (W[k] if wmode == 'keep' and j == k + 1 else max(W[k:j + 1]))
            cands = connectors(Q[k], Q[j])
            if wmode == 'keep' and j == k + 1:
                cands = cands + [None]             # None = 这一小段保留原样
            for path in cands:
                orig = path is None
                if orig:
                    path = [Q[k], Q[j]]
                ds = [dir8((b[0] - a[0], b[1] - a[1])) for a, b in zip(path, path[1:])]
                lens = [dist(a, b) for a, b in zip(path, path[1:])]
                line = None; ok = None
                for dd, (cost, _, _) in states:
                    if dd == -1:
                        if sdirs is not None and ds[0] not in sdirs:
                            continue
                    elif turn(dd, ds[0]) > 1 and not orig:
                        continue
                    if j == n - 1 and edirs is not None and ((ds[-1] + 4) % 8) not in edirs:
                        continue
                    if ok is None:
                        ok = orig or clear(c.net, c.L, LineString(path), w)
                    if not ok:
                        break
                    add = sum(lens) + BEND * (len(ds) - 1 + (dd != -1 and dd != ds[0]))
                    add += sum(.8 * MM for x in lens if x < SHORT and len(ds) > 1)
                    if orig:
                        add += 1.0 * MM + (5.0 * MM if dd != -1 and turn(dd, ds[0]) > 1 else 0)
                    key = (j, ds[-1])
                    if key not in best or best[key][0] > cost + add:
                        best[key] = (cost + add, (k, dd), (path, w))
    ends = [(best[(n - 1, dd)][0], dd) for dd in range(8) if (n - 1, dd) in best]
    if not ends:
        return None
    cost, dd = min(ends); key = (n - 1, dd); rev = []
    while best[key][1] is not None:
        _, pk, (path, w) = best[key]; rev.append((path, w)); key = pk
    pts = [rev[-1][0][0]]; ws = []
    for path, w in reversed(rev):
        for a, b in zip(path, path[1:]):
            if len(pts) > 1 and ws[-1] == w and dir8((pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])) == dir8((b[0] - a[0], b[1] - a[1])):
                pts[-1] = b                      # 同向合并
            else:
                pts.append(b); ws.append(w)
    return cost, pts, ws

def score(pts):
    ds = [dir8((b[0] - a[0], b[1] - a[1])) for a, b in zip(pts, pts[1:])]
    return sum(dist(a, b) for a, b in zip(pts, pts[1:])) + BEND * sum(x != y for x, y in zip(ds, ds[1:]))

def defects(c):
    bad = 0
    vs = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(c.pts, c.pts[1:])]
    for v in vs:
        a = ang(v) % 45
        if min(a, 45 - a) > 1 and math.hypot(*v) > .05 * MM: bad += 1
    for u, v in zip(vs, vs[1:]):
        t = abs((ang(u) - ang(v) + 180) % 360 - 180)
        if t > 46: bad += 1
    if len(set(c.ws)) > 1: bad += 1
    return bad

def exit_bad(pts, a0, a1):
    """从长方形焊盘斜着出。"""
    n = 0
    for a, P, Q in ((a0, pts[0], pts[1]), (a1, pts[-1], pts[-2])):
        if a and a[1] is not None:
            v = (Q[0] - P[0], Q[1] - P[1])
            n += dir8(v) not in a[1] or min(ang(v) % 45, 45 - ang(v) % 45) > 1
            if len(pts) > 2 and a[3].covers(Point(Q)):       # 拐弯落在焊盘里，看上去还是斜着出
                n += 1
            if a[0] in LONG and dir8(v) not in LONG[a[0]]:    # 细长焊盘从长边横穿出去
                n += 1
            ln = math.hypot(*v)
            if ln and abs((a[0][0] - P[0]) * v[1] - (a[0][1] - P[1]) * v[0]) / ln > .08 * MM:   # 没对着焊盘中心
                n += 1
    return n

def stub_opts(P, a):
    """焊盘中心沿允许方向走到焊盘外 0.06 mm 的点；不是长方形焊盘就不加。"""
    if not a or a[1] is None:
        return [None]
    g = next(x[0] for x in pads_on_all if x[1] == a[0])
    o = []
    for k in sorted(a[1]):
        u = DIRS[k]; n = math.hypot(*u); far = LineString([P, (P[0] + u[0] / n * 3 * MM, P[1] + u[1] / n * 3 * MM)])
        inside = far.intersection(g).length + .06 * MM
        m = round(inside / n)
        o.append(((P[0] + u[0] * m, P[1] + u[1] * m), k))
    return o

def with_stubs(c, S, E, a0, a1, ss, se):
    best = None; wmax = max(c.ws)
    for s0 in stub_opts(S, a0):
        for s1 in stub_opts(E, a1):
            P0, k0 = s0 if s0 else (S, None); P1, k1 = s1 if s1 else (E, None)
            if dist(P0, P1) < 1000:
                continue
            pre = [[S, P0]] if s0 else []; post = [[P1, E]] if s1 else []
            if not all(clear(c.net, c.L, LineString(x), wmax) for x in pre + post):
                continue
            sd = None if k0 is None else {k0, (k0 + 1) % 8, (k0 - 1) % 8}
            ed = None if k1 is None else {k1, (k1 + 1) % 8, (k1 - 1) % 8}
            if not s0: sd = ss
            if not s1: ed = se
            for wm in ('max', 'prof'):
                r = solve(c, P0, P1, sd, ed, wm)
                if r:
                    cost, pts, ws = r
                    pts2 = ([S] if s0 else []) + pts + ([E] if s1 else [])
                    ws2 = ([ws[0]] if s0 else []) + ws + ([ws[-1]] if s1 else [])
                    rk = (exit_bad(pts2, a0, a1), cost)
                    if best is None or rk < best[3]:
                        best = (cost, pts2, ws2, rk)
                    break
    return best

def tee_dirs(c, P, deg_map):
    """分叉点上，新线外向方向要和别的线夹角 ≥90°。"""
    oth = [v for v in deg_map[(c.net, c.L, P)] if v[0] is not c]
    return {k for k in range(8) if all(abs((k * 45 - ang(o[1]) + 180) % 360 - 180) >= 89 for o in oth)}

for ps in range(PASSES):
    done = 0; failed = []
    dm = collections.defaultdict(list)
    def regmap():
        dm.clear()
        for c in chains:
            dm[(c.net, c.L, c.pts[0])].append((c, (c.pts[1][0] - c.pts[0][0], c.pts[1][1] - c.pts[0][1])))
            dm[(c.net, c.L, c.pts[-1])].append((c, (c.pts[-2][0] - c.pts[-1][0], c.pts[-2][1] - c.pts[-1][1])))
    regmap()
    for c in sorted(chains, key=lambda c: (-defects(c), -len(c.pts))):
        if c.net in SKIP or dist(c.pts[0], c.pts[-1]) == 0:
            continue
        nd = lambda P: len(dm[(c.net, c.L, P)])
        t0, t1 = (TOUCH if nd(c.pts[0]) == 1 else 1000), (TOUCH if nd(c.pts[-1]) == 1 else 1000)
        a0, a1 = anchor(c.net, c.L, c.pts[0], t0), anchor(c.net, c.L, c.pts[-1], t1)
        old_bad = defects(c) + exit_bad(c.pts, a0, a1); old = score(c.pts)
        old_bad += sum(1 for a, P, in ((a0, c.pts[0]), (a1, c.pts[-1])) if a and not anchor(c.net, c.L, P))   # 线头只是碰到，要接到中心
        if old_bad == 0 and len(c.pts) <= 3:
            continue
        S = a0[0] if a0 else c.pts[0]; E = a1[0] if a1 else c.pts[-1]
        strict_s = a0[1] if a0 else tee_dirs(c, c.pts[0], dm)
        strict_e = a1[1] if a1 else tee_dirs(c, c.pts[-1], dm)
        r = None
        ls = LONG.get(a0[0]) if a0 else None; le = LONG.get(a1[0]) if a1 else None
        for sd, ed, wm in ((ls or strict_s, le or strict_e, 'max'), (strict_s, strict_e, 'max'), (None, None, 'max'), (strict_s, strict_e, 'prof'), (None, None, 'prof'), (strict_s, strict_e, 'keep'), (None, None, 'keep')):
            r = solve(c, S, E, sd, ed, wm)
            if r:
                break
        if not r:
            failed.append(c); continue
        _, pts, ws = r
        if exit_bad(pts, a0, a1):
            r2 = with_stubs(c, S, E, a0, a1, strict_s, strict_e)
            if r2 and exit_bad(r2[1], a0, a1) < exit_bad(pts, a0, a1):
                pts, ws = r2[1], r2[2]
        n2 = Chain(); n2.pts, n2.ws, n2.net, n2.L = pts, ws, c.net, c.L
        nb = defects(n2) + exit_bad(pts, a0, a1)
        if nb < old_bad or (nb == old_bad and score(pts) < old - .05 * MM):
            # 端点改到孔/焊盘中心，分叉点不变
            c.pts, c.ws = pts, ws; cap[id(c)] = capsule(c); rebuild(c.L); regmap(); done += 1
    print(f'第 {ps + 1} 轮 改 {done} 串，无解 {len(failed)}', flush=True)
    if not done:
        break
# ---------- 精修 ----------
regmap()
for c in chains:
    if c.net in SKIP: continue
    wide = max([max(o[0].ws) for P in (c.pts[0], c.pts[-1]) for o in dm[(c.net, c.L, P)] if not anchor(c.net, c.L, P)] + c.ws)
    if wide > max(c.ws) and all(clear(c.net, c.L, LineString([a, b]), wide) for a, b in zip(c.pts, c.pts[1:])):
        c.ws = [wide] * len(c.ws); cap[id(c)] = capsule(c); rebuild(c.L); report['支路加宽'] += 1
regmap()
STEP = int(.04 * MM); WIN = 240
for c in list(chains):
    if c.net in SKIP or dist(c.pts[0], c.pts[-1]) == 0: continue
    a0, a1 = anchor(c.net, c.L, c.pts[0]), anchor(c.net, c.L, c.pts[-1])
    bad = defects(c) + exit_bad(c.pts, a0, a1)
    if not bad: continue
    S = a0[0] if a0 else c.pts[0]; E = a1[0] if a1 else c.pts[-1]
    ss = a0[1] if a0 else tee_dirs(c, c.pts[0], dm); se = a1[1] if a1 else tee_dirs(c, c.pts[-1], dm)
    cands = [solve(c, S, E, x, y, m) for x, y, m in ((ss, se, 'max'), (None, None, 'max'))] + [with_stubs(c, S, E, a0, a1, ss, se)]
    cands = [r for r in cands if r]
    n2 = Chain(); n2.net, n2.L = c.net, c.L
    def rate(r):
        n2.pts, n2.ws = r[1], r[2]
        return defects(n2) + exit_bad(r[1], a0, a1), r[0]
    if cands:
        r = min(cands, key=rate)
        if rate(r)[0] < bad:
            c.pts, c.ws = r[1], r[2]; cap[id(c)] = capsule(c); rebuild(c.L); regmap(); report['精修'] += 1
MINMID = int(.155 * MM)
def sgn(a, b):
    t = (b - a) % 8
    return t if t <= 4 else t - 8
def dirs_of(p):
    return [dir8((y[0] - x[0], y[1] - x[1])) for x, y in zip(p, p[1:])]
def cross_pt(A, B, C, D):
    x1, y1, x2, y2, x3, y3, x4, y4 = *A, *B, *C, *D
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if den == 0:
        return None
    u = (x1 * y2 - y1 * x2); v = (x3 * y4 - y3 * x4)
    return (round((u * (x3 - x4) - (x1 - x2) * v) / den), round((u * (y3 - y4) - (y1 - y2) * v) / den))
def widen(c):
    """把夹在两个同向 45° 之间的短段放大到 ≥0.16 mm。A-B-C-D，B-C 是短段，X 是 AB 与 CD 的交点。"""
    a0, a1 = anchor(c.net, c.L, c.pts[0]), anchor(c.net, c.L, c.pts[-1])
    changed = 0
    for i in range(1, len(c.pts) - 2):
        A, B, C, D = c.pts[i - 1:i + 3]
        ds = dirs_of([A, B, C, D])
        if dist(B, C) >= MINMID or sgn(ds[0], ds[1]) != sgn(ds[1], ds[2]) or abs(sgn(ds[0], ds[1])) != 1:
            continue
        X = cross_pt(A, B, C, D)
        if not X:
            continue
        u, v = DIRS[ds[0]], DIRS[ds[2]]; n = math.hypot(*u); m = math.hypot(*v)
        room0 = dist(A, X) - (.04 * MM); room2 = dist(X, D) - (.04 * MM)
        if i == 1 and a0 and a0[3] is not None:       # 拐点要留在焊盘外
            room0 = dist(A, X) - LineString([A, X]).intersection(a0[3]).length - .006 * MM
        if i + 2 == len(c.pts) - 1 and a1 and a1[3] is not None:
            room2 = dist(X, D) - LineString([X, D]).intersection(a1[3]).length - .006 * MM
        if os.environ.get('DBGW'):
            print('W', c.net, c.L, [round(x / MM, 3) for x in B], 'X', X and [round(x / MM, 3) for x in X], 'room', round(room0 / MM, 3), round(room2 / MM, 3), ds, u, v, flush=True)
        kmax = int(min(room0 / n, room2 / m))
        for mid in (.30, .26, .22, .19, .16, 0):
            # 中段长度和 k 的关系：轴段夹 → k·√2；斜段夹 → 2k；0 = 紧挨焊盘时用到头
            k = round(mid * MM / math.hypot(u[0] + v[0], u[1] + v[1])) if mid else kmax
            if k > kmax or k * math.hypot(u[0] + v[0], u[1] + v[1]) <= dist(B, C) * 1.15:
                continue
            B2 = (X[0] - k * u[0], X[1] - k * u[1]); C2 = (X[0] + k * v[0], X[1] + k * v[1])
            if clear(c.net, c.L, LineString([B2, C2]), c.ws[i]):
                c.pts[i], c.pts[i + 1] = B2, C2; changed += 1
                break
    return changed
def dejog(c):
    """A-B、B-C(短)、C-D 里 AB∥CD：把 AB 或 CD 延长到相邻线段上，去掉错位。"""
    n0 = 0; i = 1
    while i < len(c.pts) - 2:
        P = c.pts; ds = dirs_of(P)
        if ds[i - 1] == ds[i + 1] and ds[i] != ds[i - 1] and dist(P[i], P[i + 1]) <= .15 * MM and len(set(c.ws[i - 1:i + 2])) == 1:
            done = False
            if i + 3 < len(P) and abs(sgn(ds[i - 1], ds[i + 2])) == 1:           # 延长前段接到后后段
                Y = cross_pt(P[i - 1], P[i], P[i + 2], P[i + 3])
                if Y and clear(c.net, c.L, LineString([P[i - 1], Y, P[i + 3]]), c.ws[i]) and dir8((Y[0] - P[i - 1][0], Y[1] - P[i - 1][1])) == ds[i - 1] and dir8((P[i + 3][0] - Y[0], P[i + 3][1] - Y[1])) == ds[i + 2]:
                    c.pts[i:i + 3] = [Y]; c.ws[i - 1:i + 2] = [c.ws[i]]; done = True
            if not done and i >= 2 and abs(sgn(ds[i - 2], ds[i + 1])) == 1:       # 延长后段接到前前段
                Y = cross_pt(P[i - 2], P[i - 1], P[i + 1], P[i + 2])
                if Y and clear(c.net, c.L, LineString([P[i - 2], Y, P[i + 2]]), c.ws[i]) and dir8((Y[0] - P[i - 2][0], Y[1] - P[i - 2][1])) == ds[i - 2] and dir8((P[i + 2][0] - Y[0], P[i + 2][1] - Y[1])) == ds[i + 1]:
                    c.pts[i - 1:i + 2] = [Y]; c.ws[i - 1:i + 2] = [c.ws[i]]; done = True
            if done:
                n0 += 1; continue
        i += 1
    return n0
def fix_pad_bend(c):
    """首个拐点还在焊盘里：把斜段顺着出线方向平移到焊盘外。"""
    n0 = 0
    for rev in (False, True):
        P = c.pts[::-1] if rev else c.pts
        W = c.ws[::-1] if rev else c.ws
        a = anchor(c.net, c.L, P[0])
        if not a or a[3] is None or a[1] is None or len(P) < 4 or not a[3].covers(Point(P[1])):
            continue
        ds = dirs_of(P); u = DIRS[ds[0]]; n = math.hypot(*u)
        inside = LineString([P[0], (P[0][0] + u[0] / n * 3 * MM, P[0][1] + u[1] / n * 3 * MM)]).intersection(a[3]).length
        sh = round((inside + .012 * MM - dist(P[0], P[1])) / n)
        B2 = (P[1][0] + u[0] * sh, P[1][1] + u[1] * sh)
        v = DIRS[ds[1]]
        C2 = cross_pt(B2, (B2[0] + v[0] * 1000, B2[1] + v[1] * 1000), P[2], P[3])
        if not C2 or dir8((P[3][0] - C2[0], P[3][1] - C2[1])) != ds[2] or dir8((C2[0] - B2[0], C2[1] - B2[1])) != ds[1]:
            continue
        if clear(c.net, c.L, LineString([P[0], B2, C2, P[3]]), max(W[:3])):
            P[1], P[2] = B2, C2
            c.pts = P[::-1] if rev else P
            n0 += 1
    return n0
for c in chains:
    if c.net not in SKIP and fix_pad_bend(c):
        cap[id(c)] = capsule(c); rebuild(c.L); report['拐点移出焊盘'] += 1
def drop_redundant():
    """同网同层线段压在一起或交叉、而且去掉一条网仍连通时，删较长的那条。地孔都经 In1 相连。"""
    by = collections.defaultdict(list)
    for c in chains:
        if c.net not in SKIP: by[c.net].append(c)
    gone = 0
    for net, cs in by.items():
        def key(c, P):
            a = anchor(net, c.L, P, TOUCH)
            if a is None: return (c.L, P)
            return 'PLANE' if net == 'GND' and a[2] == 'via' else a[0]
        def connected_without(x):
            par = {}
            def f(q):
                par.setdefault(q, q)
                while par[q] != q:
                    par[q] = par[par[q]]; q = par[q]
                return q
            for c in cs:
                if c is not x and c in alive:
                    par[f(key(c, c.pts[0]))] = f(key(c, c.pts[-1]))
            return f(key(x, x.pts[0])) == f(key(x, x.pts[-1]))
        alive = list(cs)
        for L in {c.L for c in cs}:
            ls = [c for c in cs if c.L == L]
            for i, p in enumerate(ls):
                for q in ls[i + 1:]:
                    if p not in alive or q not in alive: continue
                    A, B = LineString(p.pts), LineString(q.pts)
                    w = max(max(p.ws), max(q.ws))
                    near = A.buffer(w).intersection(B).length
                    crosses = A.crosses(B)
                    if not crosses and near < .25 * MM:
                        continue
                    for x in sorted((p, q), key=lambda c: -LineString(c.pts).length):
                        if connected_without(x):
                            alive.remove(x); chains.remove(x); rebuild(L); gone += 1
                            print('删重复', net, L, [round(v / MM, 3) for v in x.pts[0]], [round(v / MM, 3) for v in x.pts[-1]], round(LineString(x.pts).length / MM, 3), flush=True)
                            break
    return gone
report['删重复线'] = drop_redundant()
for c in chains:
    if c.net not in SKIP and len(c.pts) >= 4 and dejog(c):
        cap[id(c)] = capsule(c); rebuild(c.L); report['去错位'] += 1
for rnd in range(2):
    for c in chains:
        if c.net not in SKIP and len(c.pts) >= 4 and widen(c):
            cap[id(c)] = capsule(c); rebuild(c.L); report['放大倒角'] += 1
left = []
for c in chains:
    if c.net in SKIP or len(c.pts) < 4: continue
    for i in range(1, len(c.pts) - 2):
        ds = dirs_of(c.pts[i - 1:i + 3])
        if dist(c.pts[i], c.pts[i + 1]) < MINMID and sgn(ds[0], ds[1]) == sgn(ds[1], ds[2]) and abs(sgn(ds[0], ds[1])) == 1:
            left.append((c.net, c.L, round(c.pts[i][0] / MM, 3), round(c.pts[i][1] / MM, 3), round(dist(c.pts[i], c.pts[i + 1]) / MM, 3)))
print('短中段剩余', left)
deg = collections.Counter()
for c in chains:
    deg[(c.net, c.L, c.pts[0])] += 1; deg[(c.net, c.L, c.pts[-1])] += 1
for c in list(chains):
    if c.net in SKIP: continue
    dead = [P for P in (c.pts[0], c.pts[-1]) if deg[(c.net, c.L, P)] == 1 and not anchor(c.net, c.L, P)]
    if dead:
        print('悬空', c.net, c.L, [round(x / MM, 3) for x in dead[0]], '段', len(c.ws), '长', round(sum(dist(a, b) for a, b in zip(c.pts, c.pts[1:])) / MM, 3))
dbg('终')
before = len(chains)
def same_anchor(c):
    a, b = anchor(c.net, c.L, c.pts[0]), anchor(c.net, c.L, c.pts[-1])
    return a and b and a[0] == b[0] and c.net not in SKIP
chains = [c for c in chains if not same_anchor(c)]
report['孔或焊盘内碎线'] = before - len(chains)
fails = [{'net': c.net, 'layer': c.L, 'at': [round(c.pts[0][0] / MM, 3), round(c.pts[0][1] / MM, 3)], 'n': len(c.pts)} for c in failed]
res = {}
for c in chains:
    for a, b, w in zip(c.pts, c.pts[1:], c.ws):
        if a != b:
            k = (c.net, c.L, min(a, b), max(a, b))
            if k not in res or res[k]['w'] < w:
                res[k] = {'net': c.net, 'layer': c.L, 'a': list(a), 'b': list(b), 'w': w}
res = list(res.values())
json.dump({'tracks': res, 'skip': sorted(SKIP), 'fails': fails, 'report': dict(report)}, open(out_path, 'w'))
print('输出', len(res), '段', dict(report), '无解串', len(fails))
