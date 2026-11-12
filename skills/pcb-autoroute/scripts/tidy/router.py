"""拆网重布：其余铜全不动，只把目标网的线和过孔拆掉，用多层网格 A* 重新连通它的全部焊盘。
用法：python router.py 板.json 输出.json 网1,网2,...  [跳过网,...]
- 网格 0.05 mm，四层，走 0/45/90°，拐弯、换层（打过孔）加代价，不许急转。
- 障碍按"别网铜外扩间距"栅格化：走线用 线宽/2+0.10，过孔用 0.225+0.10；
  孔：线到孔壁 0.20、孔到孔 0.20；板边 0.30；禁布区。都另加 0.003 余量（最后以 KiCad DRC 为准）。
- In1 只给源板上本来就走 In1 的网用（保持 In1 白名单门）。
- 新路线总长短于原来 90% 或过孔更少才采用；最后由 KiCad DRC 判，出问题的网退回。"""
import json, sys, math, heapq, collections, time
import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box

MM = 1_000_000
G = 50_000                    # 网格步长 0.05 mm
CLR, HOLE_CLR, H2H, EDGE = int(.10 * MM), int(.20 * MM), int(.20 * MM), int(.30 * MM)
MARGIN = int(.003 * MM)
VIA_D, VIA_DRILL = int(.45 * MM), int(.30 * MM)
VIA_KEEP = max(VIA_D // 2 + int(.10 * MM), VIA_DRILL // 2 + int(.20 * MM))   # 过孔中心离别网铜边至少这么远
BEND, VIA_COST = int(.25 * MM), int(1.2 * MM)
LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
import os
# 按层加价（每走一格乘的系数）：LAYER_PEN='F.Cu:1.5,B.Cu:1.5' 让线优先走内层，少切顶底层的 GND 铺铜
LPEN = [1.0] * 4
for kv in filter(None, os.environ.get('LAYER_PEN', '').split(',')):
    k_, v_ = kv.split(':'); LPEN[LAYERS.index(k_)] = float(v_)

d = json.load(open(sys.argv[1]))
targets = [n for n in sys.argv[3].split(',') if n]
skip = set(sys.argv[4].split(',')) if len(sys.argv) > 4 and sys.argv[4] else set()
targets = [n for n in targets if n not in skip]

edge = Polygon(d['edge'][0], d['edge'][1:])
x0, y0, x1, y1 = edge.bounds
NX, NY = int((x1 - x0) // G) + 2, int((y1 - y0) // G) + 2
XS, YS = x0 + np.arange(NX) * G, y0 + np.arange(NY) * G
GX, GY = np.meshgrid(XS, YS, indexing='ij')
li = {L: k for k, L in enumerate(LAYERS)}

# ---------- 铜对象（按网） ----------
objs = []   # (net, layer 或 None=全层, geom 铜形状, kind)
for t in d['tracks']:
    g = LineString([t['a'], t['b']]).buffer(t['w'] / 2, 8) if t['a'] != t['b'] else Point(t['a']).buffer(t['w'] / 2, 8)
    objs.append({'net': t['net'], 'L': t['layer'], 'g': g, 'kind': 'track', 'id': t['id']})
for v in d['vias']:
    objs.append({'net': v['net'], 'L': None, 'g': Point(v['p']).buffer(v['d'] / 2, 12), 'kind': 'via', 'id': v['id'], 'p': v['p'], 'drill': v['drill']})
pads = []
for p in d['pads']:
    for L, pl in p['polys'].items():
        for q in pl:
            if len(q) >= 3:
                objs.append({'net': p['net'], 'L': L, 'g': Polygon(q), 'kind': 'pad'})
    if p['polys']:
        q = next(iter(p['polys'].values()))[0]
        pg = Polygon(q)
        pads.append({'ref': p['ref'], 'num': p['num'], 'net': p['net'], 'layers': list(p['polys']), 'c': (pg.centroid.x, pg.centroid.y),
                     'poly': {L: Polygon(pl[0]) for L, pl in p['polys'].items()}})
holes = [(h['p'], h['r'], None) for h in d['holes']] + [(v['p'], v['drill'] / 2, v['net']) for v in d['vias']]
keep = collections.defaultdict(list)      # 禁走线
keep_v = []                               # 禁过孔（任一层禁就不能打通孔）
for z in d['keepouts']:
    ps = [Polygon(p) for p in z['polys'] if len(p) >= 3]
    if z.get('no_tracks', True):
        for L in z['layers']:
            keep[L] += ps
    if z.get('no_vias', True):
        keep_v += ps


def raster(geom):
    """几何 → 网格布尔掩码（只算外包框内的格点）。返回 (i0, j0, mask)。"""
    bx0, by0, bx1, by1 = geom.bounds
    i0, i1 = max(0, int((bx0 - x0) // G)), min(NX, int((bx1 - x0) // G) + 2)
    j0, j1 = max(0, int((by0 - y0) // G)), min(NY, int((by1 - y0) // G) + 2)
    if i1 <= i0 or j1 <= j0:
        return i0, j0, np.zeros((0, 0), bool)
    m = shapely.contains_xy(geom, GX[i0:i1, j0:j1], GY[i0:i1, j0:j1]) | shapely.intersects_xy(geom.boundary, GX[i0:i1, j0:j1], GY[i0:i1, j0:j1])
    return i0, j0, m


# 计数栅格：track[L] 记"对 0.1 mm 线中心而言被别人占"的对象数；via 记对过孔中心
W = int(.10 * MM)
cnt_t = np.zeros((4, NX, NY), np.int16)
cnt_v = np.zeros((NX, NY), np.int16)
masks = []     # 每个对象的栅格，便于拆网时减回去

def stamp(o, sign):
    ms = []
    lays = [o['L']] if o['L'] else LAYERS
    for L in lays:
        i0, j0, m = raster(o['g'].buffer(W / 2 + CLR + MARGIN, 8))
        cnt_t[li[L], i0:i0 + m.shape[0], j0:j0 + m.shape[1]] += sign * m
    i0, j0, m = raster(o['g'].buffer(VIA_KEEP + MARGIN, 8))
    cnt_v[i0:i0 + m.shape[0], j0:j0 + m.shape[1]] += sign * m
    return o

for o in objs:
    stamp(o, 1)
# 孔：线到孔壁（别网孔）、孔到孔（过孔中心与任何孔）。拆网时要把它自己的过孔孔拿掉再重建
hole_list = [{'p': h['p'], 'r': h['r'], 'net': None, 'id': None} for h in d['holes']] + \
            [{'p': v['p'], 'r': v['drill'] / 2, 'net': v['net'], 'id': v['id']} for v in d['vias']]

def rebuild_holes():
    global cnt_th, vh, hole_owner
    cnt_th = np.zeros((NX, NY), np.int16); vh = np.zeros((NX, NY), bool); hole_owner = []
    for h in hole_list:
        i0, j0, m = raster(Point(h['p']).buffer(h['r'] + HOLE_CLR + W / 2 + MARGIN, 12))
        cnt_th[i0:i0 + m.shape[0], j0:j0 + m.shape[1]] += m
        hole_owner.append((h['p'], h['r'], h['net'], (i0, j0, m)))
        i0, j0, m = raster(Point(h['p']).buffer(h['r'] + H2H + VIA_DRILL / 2 + MARGIN, 12))
        vh[i0:i0 + m.shape[0], j0:j0 + m.shape[1]] |= m

rebuild_holes()
# 板边、禁布区
inside_t = shapely.contains_xy(edge.buffer(-(EDGE + W / 2 + MARGIN)), GX, GY)
inside_v = shapely.contains_xy(edge.buffer(-(EDGE + VIA_D / 2 + MARGIN)), GX, GY)
ko = np.zeros((4, NX, NY), bool)
for L, ps in keep.items():
    for kp in ps:
        i0, j0, m = raster(kp.buffer(W / 2 + MARGIN, 8))
        ko[li[L], i0:i0 + m.shape[0], j0:j0 + m.shape[1]] |= m
ko_v = np.zeros((NX, NY), bool)
for kp in keep_v:
    i0, j0, m = raster(kp.buffer(VIA_D / 2 + MARGIN, 8))
    ko_v[i0:i0 + m.shape[0], j0:j0 + m.shape[1]] |= m
ko_any = ko.any(0) | ko_v

in1_nets = {t['net'] for t in d['tracks'] if t['layer'] == 'In1.Cu'}

# 过孔不许压任何焊盘（含同网）：C08 定普通孔、不做盘中孔；同网焊盘上的孔还会吸锡
pad_via_block = np.zeros((NX, NY), bool)
for o in objs:
    if o['kind'] == 'pad':
        i0, j0, m = raster(o['g'].buffer(VIA_D / 2 + int(.02 * MM), 8))
        pad_via_block[i0:i0 + m.shape[0], j0:j0 + m.shape[1]] |= m

# 可选件（如 8 路版不贴的第二组）：它们的焊盘只能当叶子，不能当树根、也不能当中转
OPT = set(filter(None, os.environ.get('OPTIONAL_REFS', '').split(',')))


def own_counts(net):
    """该网自己的对象在计数栅格里的份额（同网不算障碍）。"""
    own_t = np.zeros_like(cnt_t); own_v = np.zeros_like(cnt_v); own_th = np.zeros_like(cnt_th)
    for o in objs:
        if o['net'] != net:
            continue
        lays = [o['L']] if o['L'] else LAYERS
        for L in lays:
            i0, j0, m = raster(o['g'].buffer(W / 2 + CLR + MARGIN, 8))
            own_t[li[L], i0:i0 + m.shape[0], j0:j0 + m.shape[1]] += m
        i0, j0, m = raster(o['g'].buffer(VIA_KEEP + MARGIN, 8))
        own_v[i0:i0 + m.shape[0], j0:j0 + m.shape[1]] += m
    for p, r, hn, (i0, j0, m) in hole_owner:
        if hn == net:
            own_th[i0:i0 + m.shape[0], j0:j0 + m.shape[1]] += m
    return own_t, own_v, own_th


DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


def route_net(net):
    own_t, own_v, own_th = own_counts(net)
    free = (cnt_t - own_t == 0) & ((cnt_th - own_th) == 0)[None] & inside_t[None] & ~ko
    if net not in in1_nets:
        free[li['In1.Cu']] = False
    vfree = (cnt_v - own_v == 0) & inside_v & ~vh & ~ko_any & ~pad_via_block
    # 该网自己现有的过孔孔位要从 vh 里放开？不放：重布时先拆掉自己的过孔，孔到孔仍按别的孔算
    ps = [p for p in pads if p['net'] == net]
    # 每个焊盘可进入的格点：焊盘铜内的格点（同网，不受间距限制）
    term = []
    for p in ps:
        cells = set()
        for L in p['layers']:
            if L not in li or (L == 'In1.Cu' and net not in in1_nets):
                continue
            i0, j0, m = raster(p['poly'][L].buffer(-W / 2))
            for a, b in zip(*np.nonzero(m)):
                cells.add((li[L], i0 + a, j0 + b))
        if not cells:   # 焊盘太小：取离中心最近的格点
            for L in p['layers']:
                if L in li:
                    i, j = int(round((p['c'][0] - x0) / G)), int(round((p['c'][1] - y0) / G))
                    cells.add((li[L], i, j))
        for c in cells:
            free[c] = True
        term.append((p, cells))
    term.sort(key=lambda t: t[0]['ref'] in OPT)          # 树根选必贴件的焊盘
    tree = set(term[0][1])
    tree_pad = {c: term[0][0] for c in term[0][1]}
    node_dirs = collections.defaultdict(set)   # 树上格点已有支路的方向（指向外）
    left = term[1:]
    paths = []
    while left:
        goal = {}
        for k, (p, cells) in enumerate(left):
            for c in cells:
                goal[c] = k
        gc = [(p['c'][0], p['c'][1]) for p, _ in left]

        def h(i, j):
            x, y = XS[i], YS[j]
            best = 1e30
            for cx, cy in gc:
                dx, dy = abs(x - cx), abs(y - cy)
                best = min(best, max(dx, dy) - min(dx, dy) + min(dx, dy) * 1.4142)
            return best
        pq, dist, prev = [], {}, {}
        for (l, i, j) in tree:
            s = (l, i, j, -2 if (l, i, j) in node_dirs else -1)
            dist[s] = 0
            heapq.heappush(pq, (h(i, j), 0, s))
        end, n = None, 0
        while pq:
            f, g, s = heapq.heappop(pq)
            if g > dist.get(s, 1e30):
                continue
            n += 1
            if n > 3_000_000:
                return None
            l, i, j, d0 = s
            if (l, i, j) in goal and (l, i, j) not in tree:
                end = s
                break
            for dd, (dx, dy) in enumerate(DIRS):
                if d0 >= 0 and d0 < 8 and (dd - d0) % 8 in (3, 4, 5):
                    continue
                if d0 == -2 and any(dx * bx + dy * by > 0 for bx, by in node_dirs[(l, i, j)]):
                    continue   # 从已有线上接出：不许成锐角（线中间只能垂直接）
                ni, nj = i + dx, j + dy
                if not (0 <= ni < NX and 0 <= nj < NY) or not free[l, ni, nj]:
                    continue
                if dx and dy and not (free[l, i + dx, j] or free[l, i, j + dy]):
                    continue   # 斜走不许从两个障碍格之间挤过去
                c = G * (1.4142 if dx and dy else 1) * LPEN[l] + (BEND if 0 <= d0 < 8 and dd != d0 else 0)
                ns = (l, ni, nj, dd)
                if g + c < dist.get(ns, 1e30):
                    dist[ns] = g + c; prev[ns] = s
                    heapq.heappush(pq, (g + c + h(ni, nj), g + c, ns))
            if vfree[i, j]:
                for nl in range(4):
                    if nl != l and free[nl, i, j]:
                        ns = (nl, i, j, 8)
                        if g + VIA_COST < dist.get(ns, 1e30):
                            dist[ns] = g + VIA_COST; prev[ns] = s
                            heapq.heappush(pq, (g + VIA_COST + h(i, j), g + VIA_COST, ns))
        if end is None:
            return None
        path = []
        s = end
        while s in prev:
            path.append(s); s = prev[s]
        path.append(s)
        path.reverse()
        k = goal[(end[0], end[1], end[2])]
        paths.append((path, tree_pad.get((path[0][0], path[0][1], path[0][2])), left[k][0]))
        for (l, i, j, _) in path:
            tree.add((l, i, j))
        for a_, b_ in zip(path, path[1:]):
            if a_[0] == b_[0]:
                node_dirs[a_[:3]].add((b_[1] - a_[1], b_[2] - a_[2]))
                node_dirs[b_[:3]].add((a_[1] - b_[1], a_[2] - b_[2]))
        # 本网刚打的过孔：后续路径的过孔要离它孔到孔够远
        rr = int((VIA_DRILL + H2H + MARGIN) // G) + 1
        for a_, b_ in zip(path, path[1:]):
            if a_[0] != b_[0]:
                i, j = a_[1], a_[2]
                for di in range(-rr, rr + 1):
                    for dj in range(-rr, rr + 1):
                        if di * di + dj * dj <= rr * rr and 0 <= i + di < NX and 0 <= j + dj < NY and (di or dj):
                            vfree[i + di, j + dj] = False
        # 焊盘只当叶子：只把到达的那一格并入树，别的支路不许从这块焊盘另一头接出（否则连通要靠焊盘铜中转）
        tree.add(end[:3]); tree_pad[end[:3]] = left[k][0]
        # 新走过的格子也要占住孔到孔（过孔）——只影响本网后续，简化：不处理
        left.pop(k)
    return paths


def to_copper(net, paths, w):
    """格点路径 → 线段和过孔。"""
    segs, vias = [], []
    for path, pa, pb in paths:
        pts = [(l, int(XS[i]), int(YS[j])) for (l, i, j, _) in path]
        # 起点若在焊盘内且不在中心，补焊盘中心短线
        run = [pts[0]]
        for p in pts[1:]:
            if p[0] != run[-1][0]:
                vias.append((run[-1][1], run[-1][2]))
                segs += poly_segs(run)
                run = [p]
            else:
                run.append(p)
        segs += poly_segs(run)
        # 寻路终点本来就在焊盘铜内，不再往焊盘中心补斜线（补出来的全是焊盘里看不见的非 45° 段）
    return [{'net': net, 'layer': L, 'a': list(a), 'b': list(b), 'w': w} for L, a, b in segs if list(a) != list(b)], \
           [{'net': net, 'p': list(v), 'd': VIA_D, 'drill': VIA_DRILL} for v in sorted(set(vias))]


def poly_segs(run):
    if len(run) < 2:
        return []
    pts = [run[0]]
    for p in run[1:]:
        if len(pts) >= 2:
            a, b = pts[-2], pts[-1]
            if (b[1] - a[1]) * (p[2] - b[2]) == (b[2] - a[2]) * (p[1] - b[1]):
                pts[-1] = p
                continue
        pts.append(p)
    L = LAYERS[run[0][0]]
    return [(L, [pts[k][1], pts[k][2]], [pts[k + 1][1], pts[k + 1][2]]) for k in range(len(pts) - 1)]


res = {'removed': [], 'removed_vias': [], 'added': [], 'added_vias': [], 'report': {}}
t0 = time.time()
for net in targets:
    old_t = [t for t in d['tracks'] if t['net'] == net]
    old_v = [v for v in d['vias'] if v['net'] == net]
    old_len = sum(math.dist(t['a'], t['b']) for t in old_t)
    w = old_t[0]['w'] if old_t else W
    # 拆掉本网的线和过孔（从计数栅格里减掉）
    mine = [o for o in objs if o['net'] == net and o['kind'] in ('track', 'via')]
    for o in mine:
        stamp(o, -1)
    objs = [o for o in objs if not (o['net'] == net and o['kind'] in ('track', 'via'))]
    saved_holes = list(hole_list)
    hole_list[:] = [h for h in hole_list if not (h['net'] == net and h['id'])]
    rebuild_holes()
    paths = route_net(net)
    ok = False
    if paths is not None:
        nt, nv = to_copper(net, paths, w)
        new_len = sum(math.dist(t['a'], t['b']) for t in nt)
        ok = new_len < old_len * 0.9 or len(nv) < len(old_v)
        rep = {'旧长mm': round(old_len / MM, 1), '新长mm': round(new_len / MM, 1), '旧孔': len(old_v), '新孔': len(nv), '采用': ok}
    else:
        rep = {'旧长mm': round(old_len / MM, 1), '结果': '布不通'}
    if ok:
        res['removed'] += [t['id'] for t in old_t]; res['removed_vias'] += [v['id'] for v in old_v]
        res['added'] += nt; res['added_vias'] += nv
        for t in nt:
            o = {'net': net, 'L': t['layer'], 'g': LineString([t['a'], t['b']]).buffer(w / 2, 8), 'kind': 'track', 'id': 'n'}
            objs.append(stamp(o, 1))
        for v in nv:
            o = {'net': net, 'L': None, 'g': Point(v['p']).buffer(VIA_D / 2, 12), 'kind': 'via', 'id': 'n'}
            objs.append(stamp(o, 1))
            hole_list.append({'p': v['p'], 'r': VIA_DRILL / 2, 'net': net, 'id': 'new'})
        rebuild_holes()
    else:
        for o in mine:
            objs.append(stamp(o, 1))
        hole_list[:] = saved_holes
        rebuild_holes()
    res['report'][net] = rep
    print(net, rep, f'{time.time() - t0:.0f}s', flush=True)
json.dump(res, open(sys.argv[2], 'w'), ensure_ascii=False)
