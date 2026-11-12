"""对方 C08B-AFE-R2 板：连通（并查集，含铺铜）、同层异网间距、统计。单位 mil，输出 mm。"""
import load, math, collections, json, os
MM = 0.0254
pads = load.pads_rect(); lines = load.lines; vias = load.vias
par = {}
def f(a):
    while par.setdefault(a, a) != a: par[a] = par[par[a]]; a = par[a]
    return a
def u(a, b): par[f(a)] = f(b)
def segd(px, py, l):
    ax, ay, bx, by = l['x1'], l['y1'], l['x2'], l['y2']; dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)
grid = collections.defaultdict(list)
def add(key, x0, y0, x1, y1):
    for gx in range(int(min(x0, x1) // 40) - 1, int(max(x0, x1) // 40) + 2):
        for gy in range(int(min(y0, y1) // 40) - 1, int(max(y0, y1) // 40) + 2): grid[(gx, gy)].append(key)
for i, p in enumerate(pads): add(('P', i), p[3] - p[5], p[4] - p[5], p[3] + p[5], p[4] + p[5])
for i, l in enumerate(lines): add(('L', i), l['x1'], l['y1'], l['x2'], l['y2'])
for i, v in enumerate(vias): add(('V', i), v['x'], v['y'], v['x'], v['y'])
def near(x, y): return {k for k in grid[(int(x // 40), int(y // 40))]}
def rect_d(px, py, p):
    """点到焊盘矩形的距离（在里面为 0）"""
    t = math.radians(-p[7]); dx, dy = px - p[3], py - p[4]
    u_ = dx * math.cos(t) - dy * math.sin(t); v_ = dx * math.sin(t) + dy * math.cos(t)
    return math.hypot(max(abs(u_) - p[5] / 2, 0), max(abs(v_) - p[6] / 2, 0))
def seg_seg(l, m):
    return min(segd(m['x1'], m['y1'], l), segd(m['x2'], m['y2'], l), segd(l['x1'], l['y1'], m), segd(l['x2'], l['y2'], m))
# 线端接焊盘 / 过孔 / 别的线
for i, l in enumerate(lines):
    for x, y in ((l['x1'], l['y1']), (l['x2'], l['y2'])):
        for k in near(x, y):
            if k[0] == 'P':
                p = pads[k[1]]
                if p[8] in (l['layer'], 12) and rect_d(x, y, p) <= l['w'] / 2 and p[2] == l['net']: u(('L', i), k)
            elif k[0] == 'V':
                v = vias[k[1]]
                if segd(v['x'], v['y'], l) <= (v['d'] + l['w']) / 2 and v['net'] == l['net']: u(('L', i), k)
            elif k[0] == 'L' and k[1] != i:
                m = lines[k[1]]
                if m['layer'] == l['layer'] and m['net'] == l['net'] and seg_seg(l, m) <= (l['w'] + m['w']) / 2: u(('L', i), k)
for i, l in enumerate(lines):   # 线中段压过同网焊盘
    for gx in range(int(min(l['x1'], l['x2']) // 40) - 1, int(max(l['x1'], l['x2']) // 40) + 2):
        for gy in range(int(min(l['y1'], l['y2']) // 40) - 1, int(max(l['y1'], l['y2']) // 40) + 2):
            for k in grid[(gx, gy)]:
                if k[0] == 'P':
                    p = pads[k[1]]
                    if p[2] == l['net'] and p[8] in (l['layer'], 12):
                        n_ = max(2, int(math.hypot(l['x2'] - l['x1'], l['y2'] - l['y1'])))
                        if min(rect_d(l['x1'] + (l['x2'] - l['x1']) * s_ / n_, l['y1'] + (l['y2'] - l['y1']) * s_ / n_, p) for s_ in range(n_ + 1)) <= l['w'] / 2: u(('L', i), k)
                elif k[0] == 'V':
                    v = vias[k[1]]
                    if v['net'] == l['net'] and segd(v['x'], v['y'], l) <= (v['d'] + l['w']) / 2: u(('L', i), k)
for i, v in enumerate(vias):   # 过孔落在焊盘里（盘中孔）
    for k in near(v['x'], v['y']):
        if k[0] == 'P' and rect_d(v['x'], v['y'], pads[k[1]]) <= v['d'] / 2 and pads[k[1]][2] == v['net']: u(('V', i), k)
def corners(p):
    t = math.radians(p[7]); return [(p[3] + a * p[5] / 2 * math.cos(t) - b * p[6] / 2 * math.sin(t), p[4] + a * p[5] / 2 * math.sin(t) + b * p[6] / 2 * math.cos(t)) for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
# 同网焊盘直接相碰（0201 电容并排贴着）
def rect_overlap(p, q):
    A, B = corners(p), corners(q)
    for poly in (A, B):
        for k in range(4):
            x1, y1 = poly[k]; x2, y2 = poly[(k + 1) % 4]; nx, ny = y2 - y1, x1 - x2; L_ = math.hypot(nx, ny) or 1; nx /= L_; ny /= L_
            pa = [x * nx + y * ny for x, y in A]; pb = [x * nx + y * ny for x, y in B]
            if max(pa) < min(pb) or max(pb) < min(pa): return False
    return True
pad_touch = []
for i, p in enumerate(pads):
    for k in near(p[3], p[4]):
        if k[0] == 'P' and k[1] > i:
            q = pads[k[1]]
            if q[2] == p[2] and p[2] and (q[8] == p[8] or 12 in (p[8], q[8])) and rect_overlap(p, q): u(('P', i), k); pad_touch.append((p[0] + '.' + p[1], q[0] + '.' + q[1]))
# 铺铜：同网焊盘/过孔落在某块填充多边形内 → 接到这块
def pip(x, y, poly):
    c = False; n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: c = not c
    return c
for j, (net, layer, poly) in enumerate(load.poured):
    if len(poly) < 3: continue
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]; bx = (min(xs), min(ys), max(xs), max(ys))
    for i, p in enumerate(pads):
        if p[2] == net and p[8] in (layer, 12) and bx[0] - 60 <= p[3] <= bx[2] + 60 and bx[1] - 60 <= p[4] <= bx[3] + 60 and any(pip(x, y, poly) for x, y in [(p[3], p[4])] + corners(p)): u(('P', i), ('Z', j))   # 焊盘中心或任一角在填充里
    for i, l in enumerate(lines):   # 同网线端伸进填充
        if l['net'] == net and l['layer'] == layer and any(bx[0] <= x <= bx[2] and bx[1] <= y <= bx[3] and pip(x, y, poly) for x, y in ((l['x1'], l['y1']), (l['x2'], l['y2']))): u(('L', i), ('Z', j))
    for i, v in enumerate(vias):
        if v['net'] == net and bx[0] <= v['x'] <= bx[2] and bx[1] <= v['y'] <= bx[3] and pip(v['x'], v['y'], poly): u(('V', i), ('Z', j))
isl = collections.defaultdict(set)
for i, p in enumerate(pads):
    if p[2] and not p[2].lower().startswith('unconnected'): isl[p[2]].add(f(('P', i)))
broken = {n: len(s) for n, s in isl.items() if len(s) > 1}
# 悬空线/过孔：所在连通块里一个焊盘都没有
padroots = {f(('P', i)) for i in range(len(pads))}
dangling_l = [i for i in range(len(lines)) if f(('L', i)) not in padroots]
dangling_v = [i for i in range(len(vias)) if f(('V', i)) not in padroots]
# 同层异网：线-线、线-过孔
viol = []
for i, l in enumerate(lines):
    cand = set()
    for gx in range(int(min(l['x1'], l['x2']) // 40) - 1, int(max(l['x1'], l['x2']) // 40) + 2):
        for gy in range(int(min(l['y1'], l['y2']) // 40) - 1, int(max(l['y1'], l['y2']) // 40) + 2): cand |= set(grid[(gx, gy)])
    for k in cand:
        if k[0] == 'L' and k[1] > i:
            m = lines[k[1]]
            if m['layer'] != l['layer'] or m['net'] == l['net']: continue
            d = seg_seg(l, m) - (l['w'] + m['w']) / 2   # ponytail: 只比端点到线段，平行交叉的线会漏（交叉同层异网少见）
            viol.append((d * MM, 'LL', l['net'], m['net'], load.LAYER[l['layer']]))
        elif k[0] == 'V':
            v = vias[k[1]]
            if v['net'] == l['net']: continue
            viol.append(((segd(v['x'], v['y'], l) - l['w'] / 2 - v['d'] / 2) * MM, 'LV', l['net'], v['net'], load.LAYER[l['layer']]))
        elif k[0] == 'P':
            p = pads[k[1]]
            if p[2] == l['net'] or p[8] not in (l['layer'], 12): continue
            # 焊盘按矩形采样边界点求距离
            n = max(2, int(math.hypot(l['x2'] - l['x1'], l['y2'] - l['y1']) / 1.0))
            d = min(rect_d(l['x1'] + (l['x2'] - l['x1']) * s_ / n, l['y1'] + (l['y2'] - l['y1']) * s_ / n, p) for s_ in range(n + 1)) - l['w'] / 2
            viol.append((d * MM, 'LP', l['net'], p[0] + '.' + p[1] + ' ' + p[2], load.LAYER[l['layer']]))
for i, v in enumerate(vias):
    for j in range(i + 1, len(vias)):
        w = vias[j]
        if w['net'] != v['net']: viol.append(((math.hypot(v['x'] - w['x'], v['y'] - w['y']) - (v['d'] + w['d']) / 2) * MM, 'VV', v['net'], w['net'], '-'))
viol.sort()
out = {'pad_touch_same_net': pad_touch, 'pads': len(pads), 'lines': len(lines), 'vias': len(vias), 'broken_nets': broken, 'dangling_lines': len(dangling_l), 'dangling_vias': len(dangling_v),
       'clear_min': [(round(d, 3), t, a, b, L) for d, t, a, b, L in viol[:12]],
       'clear_lt_0.1': sum(1 for v in viol if v[0] < 0.1 - 1e-6), 'clear_lt_0.127': sum(1 for v in viol if v[0] < 0.127 - 1e-6), 'clear_lt_0': sum(1 for v in viol if v[0] < -1e-6)}
L = collections.defaultdict(float)
for l in lines: L[load.LAYER[l['layer']]] += math.hypot(l['x2'] - l['x1'], l['y2'] - l['y1']) * MM
out['len_by_layer_mm'] = {k: round(v) for k, v in L.items()}
json.dump(out, open(os.path.join(os.path.dirname(__file__), 'ana.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in out.items() if k != 'clear_min'}, ensure_ascii=False)); [print(x) for x in out['clear_min']]
