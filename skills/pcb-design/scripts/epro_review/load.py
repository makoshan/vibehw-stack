"""解析嘉立创专业版 .epro 解包后的 PCB（逐行 JSON，单位 mil）：元件、焊盘绝对位置、焊盘网、走线、过孔、铺铜填充。"""
import json, glob, math, os
X = os.environ.get('EPRO_X', os.path.join(os.path.dirname(__file__), 'x'))   # .epro 解压目录
MM = 0.0254
def rows(p): return [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]
P = rows(glob.glob(X + '/PCB/*.epcb')[0])
LAYER = {a[1]: a[3] for a in P if a[0] == 'LAYER'}
attr = {}
for a in P:
    if a[0] == 'ATTR' and a[3]: attr.setdefault(a[3], {})[a[7]] = a[8]
comps = {a[1]: dict(id=a[1], layer=a[3], x=a[4], y=a[5], rot=a[6], ref=attr.get(a[1], {}).get('Designator'), fp=attr.get(a[1], {}).get('Footprint')) for a in P if a[0] == 'COMPONENT'}
padnet = {(a[1], a[2]): a[3] for a in P if a[0] == 'PAD_NET'}
FP = {}
for f in glob.glob(X + '/FOOTPRINT/*.efoo'):
    uid = os.path.basename(f)[:-5]
    FP[uid] = [a for a in rows(f) if a[0] == 'PAD']
def pad_wh(sh):
    if sh[0] == 'POLY':
        xs = [v for v in sh[1] if not isinstance(v, str)]
        return max(xs[0::2]) - min(xs[0::2]), max(xs[1::2]) - min(xs[1::2])
    return (sh[1], sh[2]) if len(sh) > 2 and not isinstance(sh[2], list) else (sh[1], sh[1])
def pads_rect():
    """焊盘真实矩形（POLY 取外接框）：[(ref, 脚, 网, x, y, 宽, 高, 转角, 层)]，转角 = 元件角"""
    out = []
    for c in comps.values():
        for p in FP.get(c['fp'], []):
            lx, ly, pr = p[6], p[7], 0   # 焊盘第 14 字段不是转角（加上后同件相邻焊盘重叠 397 处，不加 0 处）
            if c['layer'] == 2: ly = -ly   # 背面元件：焊盘 y 镜像、角度不变（按线端全部落在同网焊盘反推，906/906）
            t = math.radians(c['rot']); x = c['x'] + lx * math.cos(t) - ly * math.sin(t); y = c['y'] + lx * math.sin(t) + ly * math.cos(t)
            w, h = pad_wh(p[10]); L = c['layer'] if p[4] in (1, 2) else 12
            if p[4] == 1 and c['layer'] == 2: L = 2
            out.append((c['ref'], p[5], padnet.get((c['id'], p[5]), ''), x, y, w, h, c['rot'] + pr, L))
    return out
def inside(px, py, p, tol=0.5):
    t = math.radians(-p[7]); dx, dy = px - p[3], py - p[4]
    u = dx * math.cos(t) - dy * math.sin(t); v = dx * math.sin(t) + dy * math.cos(t)
    return abs(u) <= p[5] / 2 + tol and abs(v) <= p[6] / 2 + tol
def pads(conv=(1, True)):
    """conv = (旋转方向符号, 背面是否镜像 x)。返回 [(ref, 脚号, 网, x, y, 半宽, 层)]"""
    sgn, mir = conv; out = []
    for c in comps.values():
        for p in FP.get(c['fp'], []):
            lx, ly = p[6], p[7]
            if c['layer'] == 2 and mir: lx = -lx
            t = math.radians(sgn * c['rot'])
            x = c['x'] + lx * math.cos(t) - ly * math.sin(t); y = c['y'] + lx * math.sin(t) + ly * math.cos(t)
            sh = p[10]; r = max(sh[1], sh[2]) / 2 if isinstance(sh, list) and len(sh) > 2 else 5
            pl = p[4]; L = c['layer'] if pl in (1, 2) else 12   # 12 = 多层（通孔）
            if pl == 1 and c['layer'] == 2: L = 2
            out.append((c['ref'], p[5], padnet.get((c['id'], p[5]), ''), x, y, r, L))
    return out
lines = [dict(layer=a[4], net=a[3], x1=a[5], y1=a[6], x2=a[7], y2=a[8], w=a[9]) for a in P if a[0] == 'LINE']
vias = [dict(net=a[3], x=a[5], y=a[6], d=a[8], h=a[7]) for a in P if a[0] == 'VIA']   # 字段 7 = 孔、8 = 外径（按我方从嘉立创导出的 0.4/0.3 过孔反推）
pours = {a[1]: dict(net=a[3], layer=a[4]) for a in P if a[0] == 'POUR'}
def poly_pts(seq):
    pts, i = [], 0; xs = seq
    cur = (xs[0], xs[1]); pts.append(cur); i = 2
    while i < len(xs):
        if xs[i] == 'L':
            i += 1
            while i + 1 < len(xs) and not isinstance(xs[i], str):
                pts.append((xs[i], xs[i + 1])); i += 2
        elif xs[i] == 'ARC':
            pts.append((xs[i + 2], xs[i + 3])); i += 4   # 弧按端点近似
        else: i += 1
    return pts
poured = []
for a in P:
    if a[0] == 'POURED' and a[2] in pours:
        for ring in a[5]:
            try: poured.append((pours[a[2]]['net'], pours[a[2]]['layer'], [(x * 10, y * 10) for x, y in poly_pts(ring)]))   # 填充坐标单位是 10 mil（乘 10 后 GND 焊盘 119/132 落在同层填充里）
            except Exception: pass
outline = [a for a in P if a[0] == 'POLY' and a[4] == 11]
