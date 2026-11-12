"""斜出焊盘的局部重布：只拆「焊盘 → 第一个锚点（过孔 / 焊盘 / 分叉点）」那一小段，同层重走（线宽先保持，走不通才收窄）。
用法：uv run --no-project --with numpy --with shapely python -I fix_exits.py 板.json lint.json 输出.json
  板.json 来自 extract.py；lint.json 来自 route_lint.py（取 pad_exit）；输出给 apply2.py 写回。
走法（11-03 外包工程师反馈 + Mako「不能走 90」）：
  - 起点沿焊盘长轴伸出焊盘端头，再拐；每个拐角只转 45°（拐角 135°），线段只有 0/45/90°。
  - 终点是长方形焊盘时也要沿它的长轴进去；终点是分叉点时，和留下的线夹角 ≥135°（两段）/ ≥90°（T 接）。
  - 网格（0.025 mm、8 方向、带朝向的 A*）只定走向和大致长度，最后按精确坐标解出每段长度（最小二乘投影），保证严格 0/45/90。
  - 间距：别网铜 0.1、别网孔 0.2、板边 0.3、禁布区；同网别的焊盘不压。最后以 KiCad DRC 为准。
优先只重走靠近焊盘的前一截，45° 接回原线某一点（接回点之后不动）；不行再整段重走到锚点。走不通就留原线，记进输出的 skipped。
终点是焊盘/过孔的段，原线宽走不通时整段收窄到 0.2、0.15 再试（宽度只在焊盘/过孔处变）；还走不通就把挡路的邻线一起拆了按几种顺序重走，全通才算，否则退回。"""
import heapq, json, math, sys
import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

MM = 1_000_000
G = 25_000
CLR, HOLE_CLR, EDGE, MARGIN = int(.10 * MM), int(.20 * MM), int(.30 * MM), int(.001 * MM)   # V4 线多是贴着 0.1 走的，余量大了老线自己的通道都算堵
BEND = int(.40 * MM)                                   # 每个拐角加价，少拐（0.2 时 U1.58 拐出 11 段）
RMIN = 3                                               # 两个拐角之间至少走 3 格（0.075 mm）
d = json.load(open(sys.argv[1])); lint = json.load(open(sys.argv[2])); OUT = sys.argv[3]
edge = Polygon(d['edge'][0], d['edge'][1:])
edge_in = edge.buffer(-EDGE)

objs = []                                              # 铜：{net, L(None=通孔全层), g, id, kind}
for t in d['tracks']:
    g = LineString([t['a'], t['b']]).buffer(t['w'] / 2, 8) if t['a'] != t['b'] else Point(t['a']).buffer(t['w'] / 2, 8)
    objs.append({'net': t['net'], 'L': t['layer'], 'g': g, 'id': t['id'], 'kind': 'track'})
for v in d['vias']:
    objs.append({'net': v['net'], 'L': None, 'g': Point(v['p']).buffer(v['d'] / 2, 16), 'id': v['id'], 'kind': 'via'})
PADS = []
for p in d['pads']:
    for L, pl in p['polys'].items():
        for q in pl:
            if len(q) >= 3:
                pg = Polygon(q)
                objs.append({'net': p['net'], 'L': L, 'g': pg, 'id': None, 'kind': 'pad', 'name': f"{p['ref']}.{p['num']}"})
                PADS.append({'name': f"{p['ref']}.{p['num']}", 'net': p['net'], 'L': L, 'g': pg})
for c in d['fpcopper']:
    for q in c['polys']:
        if len(q) >= 3:
            objs.append({'net': '', 'L': c['layer'], 'g': Polygon(q), 'id': None, 'kind': 'fp'})
holes = [(Point(h['p']).buffer(h['r'], 16), None) for h in d['holes']] + [(Point(v['p']).buffer(v['drill'] / 2, 16), v['net']) for v in d['vias']]
keep = [(z['layers'], Polygon(p)) for z in d['keepouts'] if z.get('no_tracks', True) for p in z['polys'] if len(p) >= 3]
tracks = {t['id']: t for t in d['tracks']}
via_at = {(v['p'][0], v['p'][1]): v for v in d['vias']}


def pad_axis(pg):
    """长方形且轴向摆放的焊盘 → (长轴单位向量, 中心, 半长)；方/圆/斜着摆的返回 None。"""
    x0, y0, x1, y1 = pg.bounds
    w, h = x1 - x0, y1 - y0
    if abs(w - h) < .05 * MM or pg.area < .85 * w * h:
        return None
    return ((1, 0) if w > h else (0, 1)), ((x0 + x1) / 2, (y0 + y1) / 2), max(w, h) / 2


def pads_at(pt, net, L):
    P = Point(pt)
    return [p for p in PADS if p['net'] == net and p['L'] == L and p['g'].covers(P)]


def node_tracks(pt, net, L, skip=()):
    return [t for t in tracks.values() if t['net'] == net and t['layer'] == L and t['id'] not in skip and (tuple(t['a']) == pt or tuple(t['b']) == pt)]


def chain_from(t0, P, net, L, start_pad):
    """从焊盘上的 P 沿线走到第一个锚点：过孔 / 焊盘 / 分叉 / 线头。返回 (线段 id 列表, 终点, 终点类型, 终点还剩的线, 顶点序列)。"""
    ids, cur, t, verts = [], P, t0, [P]
    while True:
        ids.append(t['id'])
        nxt = tuple(t['b']) if tuple(t['a']) == cur else tuple(t['a'])
        verts.append(nxt)
        if nxt in via_at and via_at[nxt]['net'] == net:
            return ids, nxt, 'via', node_tracks(nxt, net, L, ids), verts
        if [p for p in pads_at(nxt, net, L) if not p['g'].equals(start_pad)]:
            return ids, nxt, 'pad', node_tracks(nxt, net, L, ids), verts
        rest = node_tracks(nxt, net, L, ids)
        if len(rest) != 1:
            return ids, nxt, 'node', rest, verts
        cur, t = nxt, rest[0]


DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


def unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n)


def route(P, ax_out, ext, X, end_ok, w, net, L, own_ids, start_pad, old_len, tgt=None, start_ok=None, margins=(1.0, 2.0), allow=()):
    """P（焊盘上）先沿 ax_out 伸出 ext，再用 A* 走到 X。end_ok(方向索引) 判到达方向。返回折线点列或 None。
    ax_out=None：起点是过孔/分叉点（连带重布的邻线），不强制沿轴，start_ok(方向索引) 判出发方向。"""
    free = ax_out is None
    pts = [P, P] if free else [P, (P[0] + ax_out[0] * ext, P[1] + ax_out[1] * ext)]
    for margin in margins:
        allp = [P, X]
        bx = box(min(p[0] for p in allp) - margin * MM, min(p[1] for p in allp) - margin * MM,
                 max(p[0] for p in allp) + margin * MM, max(p[1] for p in allp) + margin * MM)
        # 网格原点放在 X 上，到达点正好是格点
        ox, oy = X[0] - math.ceil((X[0] - bx.bounds[0]) / G) * G, X[1] - math.ceil((X[1] - bx.bounds[1]) / G) * G
        NX, NY = int((bx.bounds[2] - ox) // G) + 2, int((bx.bounds[3] - oy) // G) + 2
        GX, GY = np.meshgrid(ox + np.arange(NX) * G, oy + np.arange(NY) * G, indexing='ij')
        blk = ~shapely.contains_xy(edge_in.buffer(-w / 2), GX, GY)
        obst = []
        for o in objs:
            if o['id'] in own_ids or not (o['L'] is None or o['L'] == L) or not o['g'].intersects(bx.buffer(MM)):
                continue
            if o['net'] != net:
                obst.append(o['g'].buffer(w / 2 + CLR + MARGIN, 8))
            elif o['kind'] == 'pad' and o.get('name') not in allow and not (start_pad is not None and o['g'].equals(start_pad)) and not o['g'].covers(Point(X)) and not o['g'].covers(Point(P)):
                obst.append(o['g'].buffer(w / 2, 8))              # 不压同网别的焊盘
            elif o['kind'] == 'track':
                # 同网别的线也不许压着走（D9 第 2 轮查出新线叠在老线上、夹角 0°）；起点焊盘和终点 X 附近放开，好接上
                near = Point(X).buffer(w).union(Point(P).buffer(w) if free else start_pad.buffer(w))
                g_ = o['g'].buffer(w / 2, 8).difference(near)
                if not g_.is_empty:
                    obst.append(g_)
        for hg, hn in holes:
            if hn != net and hg.intersects(bx.buffer(MM)):
                obst.append(hg.buffer(w / 2 + HOLE_CLR + MARGIN, 8))
        for lays, kg in keep:
            if L in lays and kg.intersects(bx):
                obst.append(kg.buffer(w / 2))
        if not free:
            obst.append(start_pad.buffer(w / 2, 8))               # 出去了就别再回到起点焊盘
        if obst:
            U = unary_union(obst)
            blk |= shapely.contains_xy(U, GX, GY)
        A = pts[1]
        si, sj = round((A[0] - ox) / G), round((A[1] - oy) / G)
        ti, tj = round((X[0] - ox) / G), round((X[1] - oy) / G)
        d0 = None if free else DIRS.index((round(ax_out[0]), round(ax_out[1])))
        if not (0 <= si < NX and 0 <= sj < NY):
            WHY.append('起点出窗'); continue
        if tgt is not None:                                       # 终点焊盘里只留沿它长轴、穿过 X 的那一条通道：拐角不许落在焊盘里
            tm = shapely.contains_xy(tgt[0].buffer(w / 2, 8), GX, GY)
            corridor = np.zeros_like(tm)
            if tgt[1][0]: corridor[:, tj] = True
            else: corridor[ti, :] = True
            blk |= tm & ~corridor
        blk[si, sj] = False; blk[ti, tj] = False
        if DEBUG and DEBUG == CUR[0] and (not os.environ.get('DEBUG_T') or [round(v / MM, 2) for v in X] == json.loads(os.environ['DEBUG_T'])):
            np.save(DEBUG_OUT + f'-m{margin}.npy', np.stack([blk, np.zeros_like(blk)]).astype(np.uint8)); json.dump({'s': [si, sj], 't': [ti, tj], 'd0': d0}, open(DEBUG_OUT + f'-m{margin}.json', 'w'))
        # A*：状态 (i, j, 方向, 已直走格数)
        h = lambda i, j: G * (max(abs(i - ti), abs(j - tj)) + (math.sqrt(2) - 1) * min(abs(i - ti), abs(j - tj)))
        starts = [(si, sj, k_, RMIN) for k_ in range(8) if start_ok(k_)] if free else [(si, sj, d0, RMIN)]
        best = {st: 0 for st in starts}; prev = {}; pq = [(h(si, sj), 0, st) for st in starts]; goal = None
        lim = 2 * old_len + MM
        while pq:
            f, g, s = heapq.heappop(pq)
            if g > best.get(s, 1e18) or g > lim:
                continue
            i, j, k, r = s
            if (i, j) == (ti, tj) and end_ok(k):
                goal = s; break
            for dk in (0, -1, 1):
                if dk and r < RMIN:
                    continue
                k2 = (k + dk) % 8; di, dj = DIRS[k2]
                i2, j2 = i + di, j + dj
                if not (0 <= i2 < NX and 0 <= j2 < NY) or blk[i2, j2]:
                    continue
                if di and dj and (blk[i + di, j] and blk[i, j + dj]):
                    continue
                s2 = (i2, j2, k2, min(RMIN, (r + 1) if dk == 0 else 1))
                g2 = g + G * (math.sqrt(2) if di and dj else 1) + (BEND if dk else 0)
                if g2 < best.get(s2, 1e18):
                    best[s2] = g2; prev[s2] = s; heapq.heappush(pq, (g2 + h(i2, j2), g2, s2))
        if not goal:
            WHY.append(f'm{margin}:A*无解'); continue
        seq = [goal]
        while seq[-1] in prev:
            seq.append(prev[seq[-1]])
        seq.reverse()
        # 压成「方向, 格数」序列
        runs = [[seq[0][2], 0]] if free else []
        for a, b2 in zip(seq, seq[1:]):
            k = b2[2]
            if runs and runs[-1][0] == k: runs[-1][1] += 1
            else: runs.append([k, 1])
        # 精确解长度：P + l0·ax + Σ li·di = X，最小改动（对格点长度做最小二乘投影）
        if free:
            runs = [r_ for r_ in runs if r_[1] > 0] or [[seq[0][2], 0]]
        dirs = ([] if free else [ax_out]) + [unit(DIRS[k]) for k, _ in runs]
        lg = np.array(([] if free else [ext]) + [n * G * (math.sqrt(2) if DIRS[k][0] and DIRS[k][1] else 1) for k, n in runs], float)
        # 第一段如果也是沿轴（A* 开头没拐），并进 l0
        while len(dirs) > 1 and abs(dirs[1][0] - dirs[0][0]) < 1e-9 and abs(dirs[1][1] - dirs[0][1]) < 1e-9:
            lg[0] += lg[1]; lg = np.delete(lg, 1); dirs.pop(1)
        # 格点路径和精确 X 差一点（< 1 格）：从后往前找两段不平行的线，只改它们的长度把误差吃掉；第一段（沿轴出焊盘）不动
        e = np.array([X[0] - P[0], X[1] - P[1]], float) - sum(li_ * np.array(dv) for li_, dv in zip(lg, dirs))
        l = None
        if len(dirs) == 1 and abs(dirs[0][0] * e[1] - dirs[0][1] * e[0]) < .002 * MM:
            l = lg + np.array([dirs[0][0] * e[0] + dirs[0][1] * e[1]])   # 一条直线到底
        pairs = [(a_, b_) for a_ in range(len(dirs) - 1, 0, -1) for b_ in range(a_ - 1, -1, -1)]
        for a_, b_ in ([] if l is not None else pairs):
            M = np.array([dirs[a_], dirs[b_]]).T
            if abs(np.linalg.det(M)) < 1e-6:
                continue
            da, db = np.linalg.solve(M, e)
            cand = lg.copy(); cand[a_] += da; cand[b_] += db
            if (free and (cand >= .05 * MM).all()) or (not free and cand[0] >= ext - .002 * MM and (cand[1:] >= .05 * MM).all()):
                l = cand; break
        if l is None:
            WHY.append(f'm{margin}:长度对不上 {np.round(lg / MM, 3).tolist()}'); continue
        out = [P]
        for li_, dv in zip(l, dirs):
            out.append((out[-1][0] + li_ * dv[0], out[-1][1] + li_ * dv[1]))
        out[-1] = X
        out = [(int(round(x)), int(round(y))) for x, y in out]
        if exact_ok(out, w, net, L, own_ids, start_pad, X):
            return out
        WHY.append(f'm{margin}:精确间距不过')
    return None


def overlaps_parallel(pts, t_, w):
    """新线有一段和同网老线近似平行（夹角 < 10°）又贴着走（公共长度 > 0.05 mm）才算叠线。
    D10 复核：0.3 宽的线接到过孔上，过孔上原有的垂直短线在 w/4 范围内就被算成「叠着走」，整条路白白拒掉，只好收窄到 0.2。"""
    tl = LineString([t_['a'], t_['b']]); tb = tl.buffer(w / 4)
    u_ = unit((t_['b'][0] - t_['a'][0], t_['b'][1] - t_['a'][1]))
    for a_, b_ in zip(pts, pts[1:]):
        if a_ == b_:
            continue
        v_ = unit((b_[0] - a_[0], b_[1] - a_[1]))
        if abs(u_[0] * v_[1] - u_[1] * v_[0]) > math.sin(math.radians(10)):
            continue
        if LineString([a_, b_]).intersection(tb).length > .05 * MM:
            return True
    return False


def exact_ok(pts, w, net, L, own_ids, start_pad, X):
    line = LineString(pts)
    if not edge_in.contains(line.buffer(w / 2)):
        return False
    cu = line.buffer(w / 2, 16)
    for o in objs:
        if o['id'] in own_ids or not (o['L'] is None or o['L'] == L):
            continue
        if o['net'] != net and o['g'].distance(cu) < CLR:
            return False
        if o['net'] == net and o['kind'] == 'track':
            t_ = tracks.get(o['id'])
            if t_ and t_['a'] != t_['b'] and overlaps_parallel(pts, t_, w):
                return False                                      # 和同网别的线叠着走（夹角 0°）
    for hg, hn in holes:
        if hn != net and hg.distance(line) < w / 2 + HOLE_CLR:
            return False
    for lays, kg in keep:
        if L in lays and kg.intersects(cu):
            return False
    return True


NPADS = {}
for p_ in d['pads']:
    NPADS[p_['ref']] = NPADS.get(p_['ref'], 0) + 1
removed, added, done, skipped, chains_done = [], {}, [], [], set()
WHY = []
seen_exit = set()
import itertools, os
DENY = set(filter(None, os.environ.get('DENY', '').split(',')))
DEBUG, DEBUG_OUT, CUR = os.environ.get('DEBUG_PAD'), os.environ.get('DEBUG_OUT', '/tmp/dbg'), [None]


def plan(t0, P, pad_name, net, L):
    """焊盘上的点 P 出发的那段线 → 重布要的参数；不能重布返回 (None, 原因)。"""
    sp = [p for p in pads_at(P, net, L) if p['name'] == pad_name] if pad_name else pads_at(P, net, L)
    ax = pad_axis(sp[0]['g']) if sp else None
    if not ax:
        return None, '起点焊盘不是轴向长方形'
    axv, C, half = ax
    ids, X, kind, rest, verts = chain_from(t0, P, net, L, sp[0]['g'])
    w = tracks[ids[0]]['w']
    # 线宽不一的段，只能重走到第一处变宽之前
    wmix = next((i for i, x_ in enumerate(ids) if tracks[x_]['w'] != w), None)
    # 沿轴往哪头出：两头都试，取总长短的（第三轮复核：只按 P 在中心哪侧定方向，终点在反方向时线绕着焊盘掉头，C85.1、C35.1）
    alts = []
    for d_ in (axv, (-axv[0], -axv[1])):
        off_ = (P[0] - C[0]) * d_[0] + (P[1] - C[1]) * d_[1]
        alts.append((d_, half - off_ + max(.1 * MM, w / 2 + .025 * MM)))
    toward = (X[0] - P[0]) * axv[0] + (X[1] - P[1]) * axv[1]
    if toward < 0:
        alts.reverse()                                        # 朝终点那头先试
    if NPADS.get(sp[0]['name'].split('.')[0], 9) <= 3:         # 两脚件：沿元件轴向出常常就是从长边垂直出，也算合规
        x0_, y0_, x1_, y1_ = sp[0]['g'].bounds
        hw = min(x1_ - x0_, y1_ - y0_) / 2
        for d_ in ((-axv[1], axv[0]), (axv[1], -axv[0])):
            off_ = (P[0] - C[0]) * d_[0] + (P[1] - C[1]) * d_[1]
            alts.append((d_, hw - off_ + max(.1 * MM, w / 2 + .025 * MM)))
    ax_out, ext = alts[0]
    tgt_pad = [p for p in pads_at(X, net, L) if not p['g'].equals(sp[0]['g'])] if kind == 'pad' else []
    tax = pad_axis(tgt_pad[0]['g']) if tgt_pad else None
    rest_dirs = [unit(((r['b'][0] if tuple(r['a']) == X else r['a'][0]) - X[0], (r['b'][1] if tuple(r['a']) == X else r['a'][1]) - X[1])) for r in rest]

    def end_ok(k):
        dv = unit(DIRS[k])
        if tax and abs(abs(dv[0] * tax[0][0] + dv[1] * tax[0][1]) - 1) > 1e-6:
            return False                                      # 进终点焊盘也要沿它的长轴
        if kind == 'via':
            return True
        lim = math.cos(math.radians(134 if len(rest_dirs) == 1 else 89))
        return all(-dv[0] * r[0] - dv[1] * r[1] <= lim for r in rest_dirs)
    return dict(ids=ids, P=P, ax_out=ax_out, ext=ext, alts=alts, X=X, end_ok=end_ok, w=w, net=net, L=L, sp=sp[0]['g'], kind=kind,
                pad=sp[0]['name'], old_len=sum(math.dist(tracks[i]['a'], tracks[i]['b']) for i in ids),
                tgt=(tgt_pad[0]['g'], tax[0]) if tax else None, verts=verts, wmix=wmix), None


def prefix_targets(sp_):
    """线上离焊盘由近到远的接回点：(弧长, 点, 接回点所在段号, 这一段方向)。接回点之后的线不动。"""
    v = sp_['verts']; out = []; acc = 0.0
    last = len(v) - 1 if sp_['wmix'] is None else sp_['wmix']
    for i in range(last):
        a, c = v[i], v[i + 1]
        L_ = math.dist(a, c); d_ = unit((c[0] - a[0], c[1] - a[1]))
        n = int(L_ // STEP)
        for j in range(n + 1):
            r = j * STEP
            if L_ - r < .08 * MM and i + 1 < len(v) - 1:
                continue                                      # 太靠段尾的点归下一段
            if L_ - r < .08 * MM:
                continue
            if acc + r < .15 * MM:
                continue
            out.append((acc + r, (int(round(a[0] + d_[0] * r)), int(round(a[1] + d_[1] * r))), i, d_))
        acc += L_
    return out


STEP = int(.1 * MM)


def quality(q, P, T, both_pads=False):
    if both_pads and len(q) >= 4:
        # 两头都是沿轴进出的长方形焊盘（相邻同网管脚从端头接成小 U）：走在焊盘上的那两段不算绕远
        q = q[1:-1]; P, T = q[0], q[-1]
    L_ = sum(math.dist(a, b) for a, b in zip(q, q[1:]))
    dx_, dy_ = abs(T[0] - P[0]), abs(T[1] - P[1])
    octo = max(dx_, dy_) + (math.sqrt(2) - 1) * min(dx_, dy_)      # 只走 0/45/90 的最短长度
    # 质量门：绕成钩子的不要（余量 0.35 时短线照样鼓包过门：C35.1 1.82 倍、LM27762.6/.7 1.68 倍）
    return L_ if L_ <= 1.35 * octo + .15 * MM and len(q) - 1 <= 5 else None


def go(sp_):
    """先试只重走靠近焊盘的前一截，接回原线（接回点之后不动）；再试整段到锚点。
    整段到锚点时，终点是焊盘或过孔的，走不通再整段收窄到 0.2、0.15（线宽只在焊盘/过孔处变，不算中途突变）。
    从焊盘出发的，沿轴两头都试，同一线宽下取总长短的那条。"""
    alts = sp_.get('alts') or [(sp_['ax_out'], sp_['ext'])]
    if sp_['ax_out'] is not None and sp_.get('verts'):
        for arc, T, i, d_ in prefix_targets(sp_):
            ok = lambda k, d_=d_: unit(DIRS[k])[0] * d_[0] + unit(DIRS[k])[1] * d_[1] >= math.cos(math.radians(46))
            own = set(sp_['ids'][:i + 1])
            got = []
            for ax_, ext0 in alts:
                n0 = len(WHY)
                q = route(sp_['P'], ax_, ext0, T, ok, sp_['w'], sp_['net'], sp_['L'], own, sp_['sp'], arc, None, None, margins=(1.0,))
                if DEBUG and DEBUG == CUR[0]:
                    print('  试', round(arc / MM, 2), [round(v / MM, 3) for v in T], '方向', ax_, '结果', (len(q) - 1 if q else None), WHY[n0:], flush=True)
                if q and quality(q, sp_['P'], T) is not None:
                    got.append((quality(q, sp_['P'], T), q))
            if got:
                keep_ids = sp_['ids'][:i + 1]
                nxt = sp_['verts'][i + 1]
                sp_['ids'] = keep_ids
                sp_['tail'] = (T, nxt) if T != nxt else None
                sp_['w_new'] = sp_['w']; sp_['how'] = f'前一截 {arc / MM:.2f} mm'
                return min(got)[1]
    ws = [sp_['w']] + ([x for x in (int(.2 * MM), int(.15 * MM)) if x < sp_['w']] if sp_['kind'] in ('pad', 'via') and sp_.get('wmix') is None else [])
    for w in ws:
        got = []
        for ax_, ext0 in alts:
            ext = 0 if ax_ is None else ext0 - max(.1 * MM, sp_['w'] / 2 + .025 * MM) + max(.1 * MM, w / 2 + .025 * MM)
            q = route(sp_['P'], ax_, ext, sp_['X'], sp_['end_ok'], w, sp_['net'], sp_['L'],
                      set(sp_['ids']), sp_['sp'], sp_['old_len'], sp_['tgt'], sp_.get('start_ok'))
            if q:
                Lq = quality(q, sp_['P'], sp_['X'], bool(sp_.get('tgt')))
                if Lq is not None:
                    got.append((Lq, q))
                else:
                    WHY.append(f'绕远 {sum(math.dist(a, b) for a, b in zip(q, q[1:])) / MM:.2f} mm {len(q) - 1} 段')
        if got:
            sp_['w_new'] = w; sp_['how'] = '整段'
            return min(got)[1]
    return None


def rip(sp_):
    for i in sp_['ids']:
        if i in added: del added[i]                           # 前面新加的段又被重走
        else: removed.append(i)
        del tracks[i]
    gone = set(sp_['ids']); objs[:] = [o for o in objs if o['id'] not in gone]


def lay(sp_, pts):
    if sp_.get('tail'):
        pts_tail = sp_['tail']
        pts = list(pts) + [pts_tail[1]]                       # 接回点到这段原来的终点：同一条线，原样留下
    for k, (a, b2) in enumerate(zip(pts, pts[1:])):
        nid = f'new-{next(SEQ)}'
        w = sp_.get('w_new', sp_['w'])
        added[nid] = {'a': list(a), 'b': list(b2), 'w': w, 'layer': sp_['L'], 'net': sp_['net']}
        tracks[nid] = {'id': nid, 'net': sp_['net'], 'layer': sp_['L'], 'a': list(a), 'b': list(b2), 'w': w}
        objs.append({'net': sp_['net'], 'L': sp_['L'], 'g': LineString([a, b2]).buffer(w / 2, 8), 'id': nid, 'kind': 'track'})
        chains_done.add(nid)                                  # 新线已合规，后面不再重走


SEQ = itertools.count()


def ang_ok(pt_dirs, n_rest):
    """离开/到达点的方向 dv 和该点留下的线 rest 夹角：两段 ≥135°，T 接 ≥90°。"""
    lim = math.cos(math.radians(134 if n_rest == 1 else 89))
    return lambda dv: all(dv[0] * r[0] + dv[1] * r[1] <= lim for r in pt_dirs)


def plan_free(tid):
    """不从焊盘出发的那段（过孔/分叉 → 过孔/分叉/焊盘），连带重布用：起点不强制沿轴，两头都按拐角规矩。"""
    t = tracks[tid]; net, L = t['net'], t['layer']
    ids, ends = [tid], []
    for start in (tuple(t['a']), tuple(t['b'])):
        cur, prev = start, tid
        for _ in range(50):
            if cur in via_at or pads_at(cur, net, L):
                break
            nx = node_tracks(cur, net, L, {prev})
            if len(nx) != 1:
                break
            u = nx[0]; ids.append(u['id']); prev = u['id']; cur = tuple(u['b']) if tuple(u['a']) == cur else tuple(u['a'])
        ends.append(cur)
    P, X = ends
    if P == X or len({tracks[i]['w'] for i in ids}) > 1 or any(i.startswith('new-') is False and i not in tracks for i in ids):
        return None
    def rest_dirs(pt):
        rs = node_tracks(pt, net, L, set(ids))
        return [unit(((r['b'][0] if tuple(r['a']) == pt else r['a'][0]) - pt[0], (r['b'][1] if tuple(r['a']) == pt else r['a'][1]) - pt[1])) for r in rs]
    def kind(pt):
        return 'via' if pt in via_at else ('pad' if pads_at(pt, net, L) else 'node')
    kp, kx = kind(P), kind(X)
    rp, rx = rest_dirs(P), rest_dirs(X)
    tp = pads_at(X, net, L); tax = pad_axis(tp[0]['g']) if tp else None
    sp_ok = (lambda k_: True) if kp == 'via' else ang_ok(rp, len(rp))
    ex_ok = ang_ok(rx, len(rx))
    def start_ok(k_):
        return sp_ok(unit(DIRS[k_]))
    def end_ok(k_):
        dv = unit(DIRS[k_])
        if tax and abs(abs(dv[0] * tax[0][0] + dv[1] * tax[0][1]) - 1) > 1e-6:
            return False
        return kx == 'via' or ex_ok((-dv[0], -dv[1]))
    if kp == 'pad':
        return None                                           # 从焊盘出发的走 plan()
    return dict(ids=ids, P=P, ax_out=None, ext=0, X=X, end_ok=end_ok, start_ok=start_ok, w=t['w'], net=net, L=L, sp=None,
                kind=kx, pad=f'线@{P[0] / MM:.2f},{P[1] / MM:.2f}', old_len=sum(math.dist(tracks[i]['a'], tracks[i]['b']) for i in ids),
                tgt=(tp[0]['g'], tax[0]) if tax else None)


def blockers(sp_):
    """挡路的邻线：同层别网、离这段老线或「沿轴出焊盘」那一截 0.45 mm 以内、自己也从轴向长方形焊盘出发的那几段，按远近取 4 段。"""
    A = (sp_['P'][0] + sp_['ax_out'][0] * sp_['ext'], sp_['P'][1] + sp_['ax_out'][1] * sp_['ext'])
    zone = unary_union([LineString([sp_['P'], A])] + [LineString([tracks[i]['a'], tracks[i]['b']]) for i in sp_['ids']])
    out, seen = [], set(sp_['ids'])
    near = sorted((LineString([t['a'], t['b']]).distance(zone), t['id']) for t in tracks.values()
                  if t['layer'] == sp_['L'] and t['net'] != sp_['net'])
    for dist, tid in near:
        if dist > .45 * MM or len(out) >= 4:
            break
        if tid in seen:
            continue
        # 从这段往两头走到头，哪头落在轴向长方形焊盘上就从哪头出发
        t = tracks[tid]
        for cand in walk_ends(tid):
            pp = pads_at(cand, t['net'], t['layer'])
            if not (pp and pad_axis(pp[0]['g'])):
                continue
            starts = node_tracks(cand, t['net'], t['layer'])
            b_ = plan(starts[0], cand, pp[0]['name'], t['net'], t['layer'])[0] if len(starts) == 1 else None
            if b_ and not (set(b_['ids']) & seen) and tid in b_['ids']:
                out.append(b_); seen |= set(b_['ids'])
                break
        else:
            b_ = plan_free(tid)
            if b_ and not (set(b_['ids']) & seen):
                out.append(b_); seen |= set(b_['ids'])
    return out


def walk_ends(tid):
    """沿线两头走到第一个非两段相接的点（不过焊盘、过孔），返回两头端点。"""
    t = tracks[tid]; ends = []
    for start in (tuple(t['a']), tuple(t['b'])):
        cur, prev = start, tid
        for _ in range(50):
            if cur in via_at or pads_at(cur, t['net'], t['layer']):
                break
            nx = node_tracks(cur, t['net'], t['layer'], {prev})
            if len(nx) != 1:
                break
            u = nx[0]; prev = u['id']; cur = tuple(u['b']) if tuple(u['a']) == cur else tuple(u['a'])
        ends.append(cur)
    return ends


# 长边垂直出：两脚件（阻容、ESD）沿元件轴向出线常是这样，不动；多脚芯片和座子的管脚从长边侧着出才改
SIDE = [dict(x, side=True) for x in lint.get('pad_exit_side', []) if NPADS.get(x['pad'].split('.')[0], 0) > 3]
ITEMS = [] if os.environ.get('CAPS') else lint['pad_exit'] + SIDE
if os.environ.get('ORDER') == 'rev':
    ITEMS = ITEMS[::-1]
elif os.environ.get('ORDER') == 'x':                          # 平行斜线束要先挪外侧的：按坐标排
    ITEMS = sorted(ITEMS, key=lambda i: (i['at'][0], i['at'][1]))
elif os.environ.get('ORDER') == 'xr':
    ITEMS = sorted(ITEMS, key=lambda i: (-i['at'][0], -i['at'][1]))
if os.environ.get('SKIP_MAIN'):                               # 只跑第二阶段：剩下的全当「主循环没改动」
    skipped.extend(dict(i, why='跳过主循环') for i in ITEMS); ITEMS = []
for x in ITEMS:
    net, L = x['net'], x['layer']
    P = None
    pg_ = [p for p in PADS if p['name'] == x['pad'] and p['L'] == L]
    pax = pad_axis(pg_[0]['g']) if pg_ else None
    for t in tracks.values():
        if t['net'] == net and t['layer'] == L:
            for e, o_ in ((t['a'], t['b']), (t['b'], t['a'])):
                if abs(e[0] - x['at'][0] * MM) < 2000 and abs(e[1] - x['at'][1] * MM) < 2000:
                    # 同一点上可能挂好几段，只挑斜着出去的那段（挑错了会去重走本来就正的线）
                    c_ = abs(unit((o_[0] - e[0], o_[1] - e[1]))[0] * pax[0][0] + unit((o_[0] - e[0], o_[1] - e[1]))[1] * pax[0][1]) if pax and o_ != e else 1
                    if (c_ <= 0.0175) if x.get('side') else (0.0175 < c_ < 0.9998):
                        P, t0 = tuple(e), t
    if not P or t0['id'] in chains_done or (x['pad'], tuple(x['at'])) in seen_exit:
        continue
    seen_exit.add((x['pad'], tuple(x['at'])))
    if x['pad'] in DENY:
        skipped.append({**x, 'why': '上一轮改了反而变差，不再动'}); continue
    sp_, why = plan(t0, P, x['pad'], net, L)
    if not sp_:
        skipped.append({**x, 'why': why}); continue
    WHY.clear(); CUR[0] = x['pad']
    pts = go(sp_)
    if pts:
        rip(sp_); lay(sp_, pts); chains_done |= set(sp_['ids'])
        done.append({**x, 'segs': len(pts) - 1, 'end': sp_['kind'], 'with': [], 'w_mm': [sp_['w'] / MM, sp_.get('w_new', sp_['w']) / MM], 'how': sp_.get('how')})
        print('改', x['pad'], sp_['kind'], len(pts) - 1, '段', flush=True)
        continue
    # 单独走不通：把挡路的邻线一起拆掉，按几种顺序重走，全走通才算，否则原样退回
    bl = blockers(sp_)
    ok = False
    orders = [[sp_] + bl, bl + [sp_]] + [list(o) for o in itertools.permutations([sp_] + bl)][:24] if bl else []
    for g_ in [sp_] + bl:
        g_['verts'] = None                                    # 连带重布是整段拆了重走，不走「前一截」
    for order in orders:
        snap = (dict(tracks), list(objs), dict(added), list(removed))
        for g_ in order:
            rip(g_)
        res = []
        for g_ in order:
            q = go(g_)
            if not q:
                break
            lay(g_, q); res.append((g_, q))
        if len(res) == len(order):
            ok = True; break
        tracks.clear(); tracks.update(snap[0]); objs[:] = snap[1]; added.clear(); added.update(snap[2]); removed[:] = snap[3]
    if not ok:
        skipped.append({**x, 'why': '走不通', 'end': sp_['kind'], 'blockers': [b['pad'] for b in bl], 'detail': list(WHY),
                        'X': [sp_['X'][0] / MM, sp_['X'][1] / MM]}); continue
    for g_, q in res:
        chains_done |= set(g_['ids'])
    own_q = [q for g_, q in res if g_ is sp_][0]
    done.append({**x, 'segs': len(own_q) - 1, 'end': sp_['kind'], 'with': [g_['pad'] for g_, _ in res if g_ is not sp_], 'w_mm': [sp_['w'] / MM, sp_.get('w_new', sp_['w']) / MM]})
    print('改', x['pad'], '连带', [g_['pad'] for g_, _ in res if g_ is not sp_], flush=True)
# ---- 第二阶段（MOVES=1）：走线改不动的，挪过孔、挪两脚件 ----
from shapely import affinity
moved_parts, moved_vias = {}, []
FPS = {f['ref']: f for f in d.get('fps', [])}
for f_ in FPS.values():
    f_['g'] = unary_union([Polygon(q) for q in f_['crt'] if len(q) >= 3]) if f_['crt'] else None


def snap():
    return (dict(tracks), list(objs), dict(added), list(removed), [(o, o['g']) for o in objs if o['kind'] in ('pad', 'via')],
            [(p, p['g']) for p in PADS], list(holes), dict(via_at), {k_: list(v) for k_, v in moved_parts.items()}, list(moved_vias),
            {k_: f_['g'] for k_, f_ in FPS.items()})


def restore(st):
    tracks.clear(); tracks.update(st[0]); objs[:] = st[1]; added.clear(); added.update(st[2]); removed[:] = st[3]
    for o, g_ in st[4]: o['g'] = g_
    for p, g_ in st[5]: p['g'] = g_
    holes[:] = st[6]; via_at.clear(); via_at.update(st[7]); moved_parts.clear(); moved_parts.update(st[8]); moved_vias[:] = st[9]
    for k_, g_ in st[10].items(): FPS[k_]['g'] = g_


def part_pads(ref):
    return [p for p in PADS if p['name'].split('.')[0] == ref]


def chains_at_pad(p):
    """焊盘上挂的所有线（端点落在焊盘里的）。"""
    out = []
    for t in list(tracks.values()):
        if t['net'] != p['net'] or t['layer'] != p['L']:
            continue
        for e in (tuple(t['a']), tuple(t['b'])):
            if p['g'].covers(Point(e)):
                o_ = tuple(t['b']) if e == tuple(t['a']) else tuple(t['a'])
                if not p['g'].covers(Point(o_)):
                    out.append((t, e))
    return out


def reroute_from_pad(sp0, dx, dy, pg):
    """焊盘平移后，这段线从焊盘新位置按规矩走到原来的锚点。"""
    P = (sp0['P'][0] + dx, sp0['P'][1] + dy)
    ax = pad_axis(pg)
    if not ax:
        return None
    axv, C, half = ax
    P = (int(C[0]), int(C[1]))                                # 挪过的件从焊盘中心出线
    alts = []
    for d_ in (axv, (-axv[0], -axv[1])):
        alts.append((d_, half + max(.1 * MM, sp0['w'] / 2 + .025 * MM)))
    if (sp0['X'][0] - P[0]) * axv[0] + (sp0['X'][1] - P[1]) * axv[1] < 0:
        alts.reverse()
    got = []
    for ax_, ext in alts:
        q = route(P, ax_, ext, sp0['X'], sp0['end_ok'], sp0['w'], sp0['net'], sp0['L'], set(), pg, sp0['old_len'], sp0['tgt'], None)
        if q and quality(q, P, sp0['X']) is not None:
            got.append((quality(q, P, sp0['X']), q))
    return min(got)[1] if got else None


def try_part(ref, dx, dy):
    """平移一个两脚件：过新位置的间距和外框检查，挂的线全部重走，全通才算。"""
    pads = part_pads(ref)
    side = FPS[ref]['side'] if ref in FPS else None
    st = snap(); specs = []
    for p in pads:
        for t, e in chains_at_pad(p):
            sp_, why = plan(t, e, p['name'], p['net'], p['L'])
            if not sp_ or sp_['wmix'] is not None or sp_['kind'] == 'pad' and sp_['X'] and any(q['g'].covers(Point(sp_['X'])) for q in pads):
                return False
            if any(set(sp_['ids']) & set(o['ids']) for o, _ in specs):
                continue
            specs.append((sp_, p))
    for sp_, _ in specs:
        rip(sp_)
    for p in pads:
        g2 = affinity.translate(p['g'], dx, dy)
        for o in objs:
            if o.get('name') == p['name'] and o['L'] == p['L']:
                o['g'] = g2
        p['g'] = g2
    ok = True
    names = {p['name'] for p in pads}
    for p in pads:                                            # 新位置离别网铜、孔、板边够不够
        if not edge_in.contains(p['g']):
            ok = False
        for o in objs:
            if o.get('name') in names or not (o['L'] is None or o['L'] == p['L']):
                continue
            if o['net'] != p['net'] and o['g'].distance(p['g']) < CLR:
                ok = False; break
            if o['net'] == p['net'] and o['kind'] == 'via' and o['g'].intersects(p['g']):
                ok = False; break                             # 不做盘中孔
        for hg, hn in holes:
            if hg.distance(p['g']) < HOLE_CLR and hn != p['net']:
                ok = False
            if hg.intersects(p['g']):
                ok = False
    if ok and ref in FPS and FPS[ref]['g'] is not None:
        g3 = affinity.translate(FPS[ref]['g'], dx, dy)
        for r2, f_ in FPS.items():
            if r2 != ref and f_['side'] == side and f_['g'] is not None and f_['g'].intersects(g3):
                ok = False; break
        FPS[ref]['g'] = g3
    if ok:
        for sp_, p in specs:
            q = reroute_from_pad(sp_, dx, dy, p['g'])
            if not q:
                ok = False; break
            sp_['tail'] = None; lay(sp_, q)
    if not ok:
        restore(st); return False
    moved_parts[ref] = [moved_parts.get(ref, [0, 0])[0] + dx, moved_parts.get(ref, [0, 0])[1] + dy]
    return True


VIA_D, VIA_H, H2H = int(.4 * MM), int(.3 * MM), int(.2 * MM)


def via_spot_ok(pt, net, skip_id):
    g_ = Point(pt).buffer(VIA_D / 2, 16); h_ = Point(pt).buffer(VIA_H / 2, 16)
    if not edge_in.contains(g_):
        return False
    for o in objs:
        if o.get('id') == skip_id:
            continue
        if o['kind'] == 'pad' and o['g'].distance(h_) < CLR:
            return False                                      # 孔不进任何焊盘
        if o['net'] != net and (o['g'].distance(g_) < CLR or o['g'].distance(h_) < HOLE_CLR):
            return False
    return all(hg.distance(h_) >= H2H for hg, hn in holes if not hg.covers(Point(VIA_OLD[0])))


VIA_OLD = [None]
FAIL = {}


def try_via(V, newp):
    """挪一个过孔：所有层上挂的线都重走到新位置，全通才算。"""
    v = via_at[V]; net = v['net']; VIA_OLD[0] = V
    if not via_spot_ok(newp, net, v['id']):
        FAIL['孔位不行'] = FAIL.get('孔位不行', 0) + 1
        return False
    st = snap(); plans = []
    far = Polygon([(-9e9, -9e9), (-9e9 + 1, -9e9), (-9e9, -9e9 + 1)])
    for L in ('F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'):
        for t in node_tracks(V, net, L):
            ids, X, kind, rest, verts = chain_from(t, V, net, L, far)
            if len({tracks[i]['w'] for i in ids}) > 1:
                return False
            plans.append(dict(ids=ids, X=X, kind=kind, rest=rest, L=L, w=tracks[ids[0]]['w'], net=net))
    seen = set()
    for pl in plans:
        if set(pl['ids']) & seen:
            return False
        seen |= set(pl['ids'])
    for pl in plans:
        for i in pl['ids']:
            if i in added: del added[i]
            else: removed.append(i)
            del tracks[i]
        gone = set(pl['ids']); objs[:] = [o for o in objs if o['id'] not in gone]
    vo = [o for o in objs if o['id'] == v['id']][0]
    vo['g'] = Point(newp).buffer(v['d'] / 2, 16)
    holes[:] = [(hg, hn) for hg, hn in holes if not hg.covers(Point(V))] + [(Point(newp).buffer(v['drill'] / 2, 16), net)]
    del via_at[V]; via_at[tuple(newp)] = dict(v, p=list(newp))
    ok = True
    for pl in plans:
        X, L, w = pl['X'], pl['L'], pl['w']
        tp = [p for p in pads_at(X, net, L)]
        ax = pad_axis(tp[0]['g']) if tp else None
        q = None
        if ax:                                                # 另一头是长方形焊盘：从焊盘沿轴出到新过孔
            axv, C, half = ax
            alts = []
            for d_ in (axv, (-axv[0], -axv[1])):
                off_ = (X[0] - C[0]) * d_[0] + (X[1] - C[1]) * d_[1]
                alts.append((d_, half - off_ + max(.1 * MM, w / 2 + .025 * MM)))
            if (newp[0] - X[0]) * axv[0] + (newp[1] - X[1]) * axv[1] < 0:
                alts.reverse()
            got = []
            for ax_, ext in alts:
                r_ = route(X, ax_, ext, tuple(newp), lambda k: True, w, net, L, set(), tp[0]['g'], 1e9, None, None)
                if r_ and quality(r_, X, newp) is not None:
                    got.append((quality(r_, X, newp), r_))
            q = min(got)[1] if got else None
        else:
            rd = [unit(((r['b'][0] if tuple(r['a']) == X else r['a'][0]) - X[0], (r['b'][1] if tuple(r['a']) == X else r['a'][1]) - X[1])) for r in pl['rest']]
            lim = math.cos(math.radians(134 if len(rd) == 1 else 89))
            e_ok = (lambda k: True) if pl['kind'] == 'via' else (lambda k, rd=rd, lim=lim: all(-unit(DIRS[k])[0] * r[0] - unit(DIRS[k])[1] * r[1] <= lim for r in rd))
            r_ = route(tuple(newp), None, 0, X, e_ok, w, net, L, set(), None, 1e9, None, lambda k: True)
            q = r_ if r_ and quality(r_, newp, X) is not None else None
        if not q:
            FAIL[f'过孔上的线接不回 {L}'] = FAIL.get(f'过孔上的线接不回 {L}', 0) + 1
            ok = False; break
        lay(dict(w=w, L=L, net=net), q)
    if not ok:
        restore(st); return False
    moved_vias.append({'old': list(V), 'new': list(newp), 'id': v['id'], 'net': net, 'd': v['d'], 'drill': v['drill']})
    return True


rotated_parts, cap_log = [], []


def center(g):
    return (int(g.centroid.x), int(g.centroid.y))


def tilted_caps():
    out = []
    for ref, f_ in FPS.items():
        ps = part_pads(ref)
        if f_['n'] != 2 or len(ps) != 2:
            continue
        a_, b_ = center(ps[0]['g']), center(ps[1]['g'])
        ang = math.degrees(math.atan2(b_[1] - a_[1], b_[0] - a_[0]))
        if abs((ang + 22.5) % 45 - 22.5) > 1:
            out.append(ref)
    return sorted(out)


def fix_cap(ref, shift=0, R=.45 * MM):
    """转角电容转正：绕信号焊盘转到 0/90；拆掉绕它的那一小段，信号线从相邻同网焊盘中心接到上下两段线上；信号焊盘沿轴接到相邻焊盘。"""
    ps = part_pads(ref)
    S = [p for p in ps if p['net'] != 'GND'][0]; Gp = [p for p in ps if p['net'] == 'GND'][0]
    net, L = S['net'], S['L']
    sc, gc = center(S['g']), center(Gp['g'])
    nb = [p for p in PADS if p['net'] == net and p['L'] == L and p is not S and p['g'].distance(S['g']) < .03 * MM]
    if not nb:
        return '旁边没有同网焊盘'
    Cp = nb[0]; cc = center(Cp['g'])
    st = snap()
    # GND 焊盘上挂的线先拆下来，转完再接（记下它们的锚点）
    gch = []
    for t, e in chains_at_pad(Gp):
        ids_, X_, kind_, rest_, _v = chain_from(t, e, 'GND', L, Gp['g'])
        gch.append((X_, tracks[ids_[0]]['w']))
        for i in ids_:
            if i in added: del added[i]
            else: removed.append(i)
            del tracks[i]
        gone_ = set(ids_); objs[:] = [o for o in objs if o['id'] not in gone_]
    # 1 找要拆的线，线穿出半径 R 的在圈上切开
    bounds, rip_ids, tails = [], [], []
    inside = lambda p_: math.dist(p_, sc) < R or S['g'].covers(Point(p_)) or Cp['g'].covers(Point(p_))
    w = None
    for t in list(tracks.values()):
        if t['net'] != net or t['layer'] != L:
            continue
        a_, b_ = tuple(t['a']), tuple(t['b'])
        ia, ib = inside(a_), inside(b_)
        if not ia and not ib:
            continue
        w = t['w'] if w is None else w
        if t['w'] != w:
            restore(st); return '线宽不一'
        rip_ids.append(t['id'])
        if ia and ib:
            continue
        i_, o_ = (a_, b_) if ia else (b_, a_)
        d_ = unit((o_[0] - i_[0], o_[1] - i_[1])); L_ = math.dist(i_, o_)
        cut = None
        for r_ in range(0, int(L_), 10000):                   # 沿线走到出圈（10 µm 步长）
            q_ = (i_[0] + d_[0] * r_, i_[1] + d_[1] * r_)
            if not inside(q_):
                cut = r_; break
        if cut is None or L_ - cut < .08 * MM:
            bounds.append((o_, None)); continue               # 线尾太短，整段拆，接到它的远端
        q_ = (int(round(i_[0] + d_[0] * cut)), int(round(i_[1] + d_[1] * cut)))
        tails.append((q_, o_, t['w']))
        bounds.append((q_, d_))
    if not rip_ids:
        restore(st); return '信号焊盘上没有线'
    for i in rip_ids:
        if i in added: del added[i]
        else: removed.append(i)
        del tracks[i]
    gone = set(rip_ids); objs[:] = [o for o in objs if o['id'] not in gone]
    for q_, o_, w_ in tails:
        lay(dict(w=w_, L=L, net=net), [q_, o_])
    # 2 转正
    v = (gc[0] - sc[0], gc[1] - sc[1]); ang = math.degrees(math.atan2(v[1], v[0]))
    th = round(ang / 90) * 90 - ang
    T_ = math.radians(round(ang / 90) * 90)
    # 往外侧（原来 GND 焊盘歪向的那边）挪 shift，给夹在中间的两根信号线让通道
    side_v = (-math.sin(T_), math.cos(T_))
    if v[0] * side_v[0] + v[1] * side_v[1] < 0:
        side_v = (-side_v[0], -side_v[1])
    sx, sy = side_v[0] * shift, side_v[1] * shift
    for p in ps:
        g2 = affinity.translate(affinity.rotate(p['g'], th, origin=sc), sx, sy)
        for o in objs:
            if o.get('name') == p['name'] and o['L'] == p['L']:
                o['g'] = g2
        p['g'] = g2
    if ref in FPS and FPS[ref]['g'] is not None:
        FPS[ref]['g'] = affinity.translate(affinity.rotate(FPS[ref]['g'], th, origin=sc), sx, sy)
        for r2, f_ in FPS.items():
            if r2 != ref and f_['side'] == FPS[ref]['side'] and f_['g'] is not None and r2 != Cp['name'].split('.')[0] and f_['g'].intersects(FPS[ref]['g']):
                restore(st); return '外框压到 ' + r2
    for o in objs:                                            # 转完两个焊盘离别网铜够不够（走线在第 3 步另查）
        if not (o['L'] is None or o['L'] == L) or o['id'] in gone:
            continue
        if o['net'] not in ('GND', net) and o['g'].distance(Gp['g']) < CLR:
            restore(st); return 'GND 焊盘压别网 ' + o['net']
        if o['net'] != net and o.get('name') != Gp['name'] and o['g'].distance(S['g']) < CLR:
            restore(st); return '信号焊盘压别网 ' + o['net']
    allow = {S['name'], Cp['name']}
    # 3 信号线：上下两个切点之间走一条直穿的线，在穿过相邻焊盘的地方加节点（端点在焊盘里才算连上）
    if len(bounds) == 2:
        (B1, d1), (B2, d2) = bounds
        s_ok = (lambda k: True) if d1 is None else (lambda k: -unit(DIRS[k])[0] * d1[0] - unit(DIRS[k])[1] * d1[1] >= math.cos(math.radians(46)))
        e_ok = (lambda k: True) if d2 is None else (lambda k: unit(DIRS[k])[0] * d2[0] + unit(DIRS[k])[1] * d2[1] >= math.cos(math.radians(46)))
        q = route(B1, None, 0, B2, e_ok, w, net, L, set(), None, 1e9, None, s_ok, margins=(1.0,), allow=allow)
        if not q or quality(q, B1, B2) is None:
            # 退路：两段都接到相邻焊盘里的一点，拐角落在焊盘里不算
            core = Cp['g'].buffer(-.05 * MM)
            line_ = LineString([B1, B2])
            m_ = (int(core.centroid.x), int(core.centroid.y)) if not core.contains(line_.centroid) else (int(line_.centroid.x), int(line_.centroid.y))
            q_a = route(B1, None, 0, m_, lambda k: True, w, net, L, set(), None, 1e9, None, s_ok, margins=(1.0,), allow=allow)
            if q_a:
                lay(dict(w=w, L=L, net=net), q_a)
            q_b = route(m_, None, 0, B2, e_ok, w, net, L, set(), None, 1e9, None, lambda k: k % 2 == 0, margins=(1.0,), allow=allow) if q_a else None
            if not q_a or not q_b or quality(q_a, B1, m_) is None or quality(q_b, m_, B2) is None:
                info = f"接不上 B1={[round(v / MM, 3) for v in B1]} B2={[round(v / MM, 3) for v in B2]} {WHY[-1:]}"
                restore(st); return info
            q = None
            lay(dict(w=w, L=L, net=net), q_b)
        q2 = [q[0]] if q else None; hit = False
        for a_, b_ in (zip(q, q[1:]) if q else ()):
            if not hit:
                n_ = max(2, int(math.dist(a_, b_) // 10000))
                for i_ in range(1, n_):
                    m_ = (int(a_[0] + (b_[0] - a_[0]) * i_ / n_), int(a_[1] + (b_[1] - a_[1]) * i_ / n_))
                    if Cp['g'].buffer(-.03 * MM).covers(Point(m_)):
                        q2.append(m_); hit = True; break
            q2.append(b_)
        if q and not hit:
            # 新线没穿过相邻焊盘：拆成两段经焊盘里一点走
            m_ = (int(Cp['g'].centroid.x), int(Cp['g'].centroid.y))
            q_a = route(B1, None, 0, m_, lambda k: True, w, net, L, set(), None, 1e9, None, s_ok, margins=(1.0,), allow=allow)
            if q_a:
                lay(dict(w=w, L=L, net=net), q_a)
            q_b = route(m_, None, 0, B2, e_ok, w, net, L, set(), None, 1e9, None, lambda k: k % 2 == 0, margins=(1.0,), allow=allow) if q_a else None
            if not q_a or not q_b or quality(q_a, B1, m_) is None or quality(q_b, m_, B2) is None:
                restore(st); return '新线没穿过相邻焊盘'
            lay(dict(w=w, L=L, net=net), q_b)
        elif q:
            lay(dict(w=w, L=L, net=net), q2)
    elif len(bounds) == 1:
        B, dv = bounds[0]
        e_ok = (lambda k: True) if dv is None else (lambda k: unit(DIRS[k])[0] * dv[0] + unit(DIRS[k])[1] * dv[1] >= math.cos(math.radians(46)))
        # 只剩一头：从相邻焊盘里、切点那条线的延长线上起步
        Pm = None
        if dv is not None:
            for r_ in range(int(.05 * MM), int(1.0 * MM), int(.01 * MM)):
                m_ = (int(B[0] - dv[0] * r_), int(B[1] - dv[1] * r_))
                if Cp['g'].buffer(-.03 * MM).covers(Point(m_)):
                    Pm = m_; break
        if Pm is None:
            restore(st); return '只有一头，延长线进不了相邻焊盘'
        if not exact_ok([Pm, B], w, net, L, set(), None, B):
            restore(st); return '延长线间距不够'
        lay(dict(w=w, L=L, net=net), [Pm, B])
    else:
        restore(st); return f'切点 {len(bounds)} 个'
    # 4 信号焊盘沿轴接到相邻焊盘
    ax = (-round(math.cos(T_)), -round(math.sin(T_)))         # 背向 GND 焊盘沿长轴出
    # 从信号焊盘和相邻焊盘的重叠段中点沿轴连过去（两头都落在焊盘里）
    ov = S['g'].intersection(Cp['g'].buffer(.1 * MM))
    if ov.is_empty:
        restore(st); return '信号焊盘挪开后够不到相邻焊盘'
    sc2 = center(S['g'])
    if ax[0] == 0:
        x0_, _, x1_, _ = S['g'].intersection(affinity.translate(Cp['g'], 0, S['g'].centroid.y - Cp['g'].centroid.y)).bounds if not S['g'].intersection(affinity.translate(Cp['g'], 0, S['g'].centroid.y - Cp['g'].centroid.y)).is_empty else (None, 0, None, 0)
        if x0_ is None:
            restore(st); return '信号焊盘和相邻焊盘在轴向上不重叠'
        st_pt = (int((x0_ + x1_) / 2), sc2[1])
    else:
        _, y0_, _, y1_ = S['g'].intersection(affinity.translate(Cp['g'], S['g'].centroid.x - Cp['g'].centroid.x, 0)).bounds if not S['g'].intersection(affinity.translate(Cp['g'], S['g'].centroid.x - Cp['g'].centroid.x, 0)).is_empty else (0, None, 0, None)
        if y0_ is None:
            restore(st); return '信号焊盘和相邻焊盘在轴向上不重叠'
        st_pt = (sc2[0], int((y0_ + y1_) / 2))
    e_ = None
    for r_ in range(int(.05 * MM), int(.7 * MM), int(.025 * MM)):
        q_ = (st_pt[0] + ax[0] * r_, st_pt[1] + ax[1] * r_)
        if Cp['g'].buffer(-.02 * MM).covers(Point(q_)):
            e_ = q_; break
    if e_ is None:
        restore(st); return '信号焊盘够不到相邻焊盘'
    if not exact_ok([st_pt, e_], w, net, L, set(), None, e_):
        restore(st); return '短接线间距不够'
    lay(dict(w=w, L=L, net=net), [st_pt, e_])
    # 5 GND 焊盘上的线重接
    for X_, w_ in gch:
        q = reroute_from_pad(dict(P=center(Gp['g']), X=X_, end_ok=lambda k: True, w=w_, net='GND', L=L, old_len=1e9, tgt=None), 0, 0, Gp['g'])
        if not q:
            restore(st); return 'GND 线接不上'
        lay(dict(w=w_, L=L, net='GND'), q)
    rotated_parts.append({'ref': ref, 'dx': int(sx), 'dy': int(sy)})
    return None


if os.environ.get('CAPS'):
    for ref in tilted_caps():
        tried = []
        for sh in (0, .05, .1, .15, .2, .25):
            why = fix_cap(ref, int(sh * MM))
            tried.append(str(why)[:24])
            if why is None:
                break
        cap_log.append({'ref': ref, 'why': why, 'shift': sh, 'tried': tried})
        print('转正' if why is None else '不转', ref, sh, '' if why is None else tried, flush=True)
    print('转正', len(rotated_parts), '/', len(cap_log))


def groups(net):
    """这个网现在分成几块（焊盘、过孔、线按端点和覆盖关系并查集）。铺铜不算，只用来比改前改后。"""
    par = {}
    def f(a):
        par.setdefault(a, a)
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    def u(a, b):
        par[f(a)] = f(b)
    tr = [t for t in tracks.values() if t['net'] == net]
    ps = [p for p in PADS if p['net'] == net]
    vs = [v for v in via_at.values() if v['net'] == net]
    for t in tr:
        u(('t', t['id']), ('t', t['id']))
        lt = LineString([t['a'], t['b']]) if t['a'] != t['b'] else Point(t['a'])
        for e in (t['a'], t['b']):
            pe = Point(e)
            for p in ps:
                if p['L'] == t['layer'] and p['g'].covers(pe):
                    u(('t', t['id']), ('p', p['name']))
            for v in vs:
                if math.dist(e, v['p']) < v['d'] / 2:
                    u(('t', t['id']), ('v', tuple(v['p'])))
            for t2 in tr:
                if t2 is not t and t2['layer'] == t['layer']:
                    l2 = LineString([t2['a'], t2['b']]) if t2['a'] != t2['b'] else Point(t2['a'])
                    if l2.distance(pe) < 1500:
                        u(('t', t['id']), ('t', t2['id']))
    for p in ps:
        f(('p', p['name']))
    for p in ps:
        for q_ in ps:
            if p is not q_ and p['L'] == q_['L'] and p['g'].distance(q_['g']) < 1:
                u(('p', p['name']), ('p', q_['name']))
    for v in vs:
        f(('v', tuple(v['p'])))
    roots = {f(k_) for k_ in list(par) if k_[0] in ('p', 'v')}
    return len(roots)


def rewire(x, sp_):
    """改接：这段线不一定非得接原来那个点。从焊盘出来，接旁边别的同网点（过孔、焊盘、同网线），全网块数不变多才收。"""
    net, L, P, w = sp_['net'], sp_['L'], sp_['P'], sp_['w']
    own = set(sp_['ids']); cands = []
    for v in via_at.values():
        if v['net'] == net and math.dist(v['p'], P) < 1.8 * MM:
            cands.append((math.dist(v['p'], P), tuple(v['p']), 'via', None))
    for p in PADS:
        if p['net'] == net and p['L'] == L and not p['g'].equals(sp_['sp']) and p['g'].centroid.distance(Point(P)) < 1.8 * MM:
            c_ = (int(p['g'].centroid.x), int(p['g'].centroid.y))
            cands.append((math.dist(c_, P), c_, 'pad', p))
    for t in tracks.values():
        if t['net'] != net or t['layer'] != L or t['id'] in own or t['a'] == t['b']:
            continue
        ln = LineString([t['a'], t['b']]); n_ = int(ln.length // (.1 * MM))
        d_ = unit((t['b'][0] - t['a'][0], t['b'][1] - t['a'][1]))
        for i_ in range(1, n_):
            pt = ln.interpolate(i_ * .1 * MM)
            q_ = (int(pt.x), int(pt.y))
            if math.dist(q_, P) < 1.8 * MM:
                cands.append((math.dist(q_, P), q_, 'track', d_))
    # 过孔、焊盘先试，再试接到同网线上（线上采样点太多，不分开会把名额占满）
    first = sorted([c for c in cands if c[2] != 'track'], key=lambda c: c[0])
    cands = first[:16] + sorted([c for c in cands if c[2] == 'track'], key=lambda c: c[0])[:30]
    before = groups(net)
    alts = sp_.get('alts') or []
    for w2 in [w] + [x_ for x_ in (int(.2 * MM), int(.15 * MM)) if x_ < w]:
        margin_new = max(.1 * MM, w2 / 2 + .025 * MM) - max(.1 * MM, w / 2 + .025 * MM)
        for dist_, T, kind_, info in cands:
            if kind_ == 'track' and w2 != w:
                continue                                      # 接到线中间不换线宽，否则就是线中途突变
            tgt = None; ok_ = lambda k: True
            if kind_ == 'pad':
                ax = pad_axis(info['g'])
                two = NPADS.get(info['name'].split('.')[0], 9) <= 3
                if ax:
                    tgt = None if two else (info['g'], ax[0])
                    ok_ = (lambda k, a=ax[0], two=two: abs(abs(unit(DIRS[k])[0] * a[0] + unit(DIRS[k])[1] * a[1]) - 1) < 1e-6 or (two and abs(unit(DIRS[k])[0] * a[0] + unit(DIRS[k])[1] * a[1]) < 1e-6))
            elif kind_ == 'track':
                ok_ = lambda k, c=info: abs(unit(DIRS[k])[0] * c[0] + unit(DIRS[k])[1] * c[1]) < 1e-6      # 垂直接上去（T 接），45° 接会成锐角
            got = []
            for ax_, ext in alts:
                n0 = len(WHY)
                q = route(P, ax_, ext + margin_new, T, ok_, w2, net, L, own, sp_['sp'], 1e9, tgt, None, margins=(1.0,))
                if DEBUG and DEBUG == x['pad']:
                    print('  改接试', w2 / MM, kind_, [round(v / MM, 3) for v in T], '方向', ax_, '路', [(round(a / MM, 2), round(b / MM, 2)) for a, b in q] if q else None, WHY[n0:], flush=True)
                if q and quality(q, P, T, kind_ == 'pad') is not None:
                    got.append((quality(q, P, T, kind_ == 'pad'), q))
            if not got:
                continue
            st = snap()
            sp_['w_new'] = w2
            rip(sp_); lay(sp_, min(got)[1])
            if groups(net) <= before:
                return ('改接', kind_, [round(v / MM, 3) for v in T], w2 / MM)
            restore(st); sp_.pop('w_new', None)
    return None


def corridor_blockers(sp_):
    """沿轴出线那一截附近挡路的别网过孔和两脚件焊盘。"""
    out = []
    w = sp_['w']
    for ax_, ext in sp_.get('alts') or []:
        P = sp_['P']; A = (P[0] + ax_[0] * (ext + .45 * MM), P[1] + ax_[1] * (ext + .45 * MM))
        cor = LineString([P, A])
        for o in objs:
            if o['net'] == sp_['net'] or not (o['L'] is None or o['L'] == sp_['L']):
                continue
            if o['kind'] == 'via' and o['g'].distance(cor) < w / 2 + CLR + HOLE_CLR:
                c_ = o['g'].centroid
                out.append(('via', (int(c_.x), int(c_.y))))
            elif o['kind'] == 'pad' and o['g'].distance(cor) < w / 2 + CLR + .02 * MM:
                ref_ = o['name'].split('.')[0]
                if NPADS.get(ref_, 0) <= 3:
                    out.append(('part', ref_))
    seen_ = []
    for b_ in out:
        if b_ not in seen_:
            seen_.append(b_)
    return seen_


def ring(r_steps=(.2, .3, .4, .5, .6, .8)):
    for r_ in r_steps:
        for k_ in range(8):
            dv = unit(DIRS[k_])
            yield (int(dv[0] * r_ * MM), int(dv[1] * r_ * MM))


def push_and_route(x, sp_):
    """把挡路的别网过孔或两脚件推开一点，再走这条线；走不通原样退回。"""
    for kind_, b_ in corridor_blockers(sp_):
        for dx_, dy_ in ring():
            st = snap()
            if kind_ == 'via':
                if b_ not in via_at or not try_via(b_, (b_[0] + dx_, b_[1] + dy_)):
                    restore(st); continue
            else:
                if not try_part(b_, dx_, dy_):
                    restore(st); continue
            t0_ = next((t for t in tracks.values() if t['id'] == sp_['ids'][0]), None)
            sp2 = plan(t0_, sp_['P'], x['pad'], x['net'], x['layer'])[0] if t0_ else None
            q = go(sp2) if sp2 else None
            if q:
                rip(sp2); lay(sp2, q)
                return ('推开', kind_, [round(v / MM, 3) for v in b_] if kind_ == 'via' else b_, round(dx_ / MM, 3), round(dy_ / MM, 3))
            restore(st)
    return None


if os.environ.get('MOVES'):
    todo = {}
    for x in skipped:
        todo[(x['pad'], tuple(x['at']))] = x
    fixed_by = []
    for key_, x in todo.items():
        net, L = x['net'], x['layer']
        if x['pad'] in DENY or (os.environ.get('ONLY') and x['pad'] != os.environ['ONLY']):
            continue
        P = None
        CUR[0] = x['pad']
        pg_ = [p for p in PADS if p['name'] == x['pad'] and p['L'] == L]
        pax = pad_axis(pg_[0]['g']) if pg_ else None
        if not pax:
            continue
        for t in tracks.values():
            if t['net'] == net and t['layer'] == L:
                for e, o_ in ((t['a'], t['b']), (t['b'], t['a'])):
                    if abs(e[0] - x['at'][0] * MM) < 2000 and abs(e[1] - x['at'][1] * MM) < 2000 and o_ != e:
                        c_ = abs(unit((o_[0] - e[0], o_[1] - e[1]))[0] * pax[0][0] + unit((o_[0] - e[0], o_[1] - e[1]))[1] * pax[0][1])
                        if 0.0175 < c_ < 0.9998:
                            P, t0 = tuple(e), t
        if not P:
            continue                                          # 前面挪动已经顺带改好了
        sp_, why = plan(t0, P, x['pad'], net, L)
        if not sp_:
            continue
        done_it = rewire(x, sp_)
        X = sp_['X']; axv, C, half = pax
        n_ = (-axv[1], axv[0])
        if not done_it and sp_['kind'] == 'via':              # 1 过孔挪到焊盘长轴线上：直出
            for s_ in (1, -1):
                for dist in range(int((half + .35 * MM) // 1000), int((half + 1.6 * MM) // 1000), 50):
                    newp = (int(C[0] + s_ * axv[0] * dist * 1000), int(C[1] + s_ * axv[1] * dist * 1000))
                    if math.dist(newp, X) > 1.2 * MM:
                        continue
                    if try_via(X, newp):
                        done_it = ('挪孔', [round(v / MM, 3) for v in X], [round(v / MM, 3) for v in newp]); break
                if done_it:
                    break
        ref = x['pad'].split('.')[0]
        if not done_it and NPADS.get(ref, 0) <= 3:            # 2 挪起点这颗两脚件，让锚点落到长轴线上（或差一个 45°）
            pe = (X[0] - C[0]) * n_[0] + (X[1] - C[1]) * n_[1]
            al = (X[0] - C[0]) * axv[0] + (X[1] - C[1]) * axv[1]
            cands = []
            for off in (pe, pe - math.copysign(abs(al) - half - .3 * MM, pe) if abs(al) > half + .3 * MM else pe):
                for along in (0, .1, -.1, .2, -.2, .3, -.3, .5, -.5):
                    cands.append((n_[0] * off + axv[0] * along * MM, n_[1] * off + axv[1] * along * MM))
            for dx_ in range(-8, 9):
                for dy_ in range(-8, 9):
                    cands.append((dx_ * .1 * MM, dy_ * .1 * MM))
            cands = sorted({(int(a), int(b_)) for a, b_ in cands if 0 < math.hypot(a, b_) <= 1.0 * MM}, key=lambda c: math.hypot(*c))
            for dx_, dy_ in cands[:120]:
                if try_part(ref, dx_, dy_):
                    done_it = ('挪件', ref, round(dx_ / MM, 3), round(dy_ / MM, 3)); break
        if not done_it:                                       # 3 把挡路的别网过孔 / 两脚件推开
            done_it = push_and_route(x, sp_)
        if done_it:
            fixed_by.append({'pad': x['pad'], 'how': done_it, 'net': net, 'layer': L})
            print('挪', x['pad'], done_it, flush=True)
    print('第二阶段改好', len(fixed_by), FAIL)
json.dump({'removed': [i for i in removed if not i.startswith('new-')], 'added': list(added.values()), 'done': done, 'skipped': skipped,
           'moved_parts': moved_parts, 'rotated_parts': rotated_parts, 'cap_log': cap_log, 'removed_vias': sorted({m['id'] for m in moved_vias}),
           'added_vias': [{'p': m['new'], 'd': m['d'], 'drill': m['drill'], 'net': m['net']} for m in {m['id']: m for m in moved_vias}.values()],
           'moves': globals().get('fixed_by', [])}, open(OUT, 'w'), ensure_ascii=False, indent=1)
print('改', len(done), '跳过', len(skipped))
