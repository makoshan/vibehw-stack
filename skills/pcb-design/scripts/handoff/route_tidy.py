"""走线整理（DRC 不管、工程师一眼看得出的毛病）：KiCad 自带 Python 运行。
用法：KPY route_tidy.py 源板 输出板 [报告.json]
先把 T 接（线头接在别的线中间）拆成端点、差一点没对上的线头接到交点，再做三步，顺序固定；每处改动先过间距检查（铜 0.1、线到异网孔 0.2），过不了就跳过并记进报告：
  1 同宽：同一个网里，不经过焊盘/过孔就连在一起的那串线段（一段「连线」）统一宽度：先试整串用最宽的，间距不够才用最细的——
          线宽只在焊盘或过孔上变，不在线中间突变（11-03 工程师：「同一根线用一样的线宽」）。
  2 出线：从长方形焊盘斜着出去的那段 P→Q，Q 不动，改成「先沿焊盘长轴出到端头外 → 45° → 必要时横向」到 Q
          （工程师：「别从焊盘出来就刁钻地斜着走」）；Q 处新拐角也要 ≥135°。转了非 45° 整数倍的焊盘不动。
  2b 小台阶：两个 90° 拐角夹一段 ≤0.1 mm 的短线（Z 形），改成一段 45° 斜线。
  3 倒角：两段 90°（或更尖）相接、接点不在焊盘/过孔上的拐角，切掉一个 45° 角（切长 ≤ 0.3 mm，且不超过两边线段可用长度的一半）。
改完必须再跑 kicad-cli DRC 两口径、route_lint.py、pour_screen.py。"""
import collections, json, math, os, sys
import pcbnew

src, dst = sys.argv[1], sys.argv[2]
rep_path = sys.argv[3] if len(sys.argv) > 3 else None
b = pcbnew.LoadBoard(src)
MM, mm = pcbnew.FromMM, pcbnew.ToMM
CLR, HOLE_CLR = MM(0.1), MM(0.2)
EDGE_CLR = MM(float(os.environ.get('EDGE', '0.3')))   # 铜到板边；不查的话整理会把线推到板边外（C08B 主控 thru3：MCU_RST_N 3 条 copper_edge_clearance）
_ol = pcbnew.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(_ol, True)
EDGES = []
for _i in range(_ol.OutlineCount()):
    for _c in [_ol.Outline(_i)] + [_ol.Hole(_i, _h) for _h in range(_ol.HoleCount(_i))]:
        _n = _c.PointCount()
        EDGES += [pcbnew.SEG(_c.CPoint(_j), _c.CPoint((_j + 1) % _n)) for _j in range(_n)]
report = collections.defaultdict(list)
pads = [p for f in b.GetFootprints() for p in f.Pads()]
vias = [t for t in b.GetTracks() if t.GetClass() == 'PCB_VIA']


TR = [t for t in b.GetTracks() if t.GetClass() == 'PCB_TRACK']   # 只取一次：SWIG 每次 GetTracks() 给新代理对象，is / id 比不了


def tracks():
    return list(TR)


def add_track(A, B, w, L, net):
    n2 = pcbnew.PCB_TRACK(b); n2.SetStart(A); n2.SetEnd(B); n2.SetWidth(w); n2.SetLayer(L); n2.SetNetCode(net); b.Add(n2); TR.append(n2)


def anchored(P, net):
    """P 在同网焊盘或过孔上：线宽可以在这里变、拐角在这里不算。"""
    return any(p.GetNetCode() == net and p.HitTest(P) for p in pads) or \
        any(v.GetNetCode() == net and (v.GetPosition() - P).EuclideanNorm() <= v.GetWidth(pcbnew.F_Cu) // 2 for v in vias)


def others(L, net, skip):
    for it in list(b.GetTracks()) + pads:
        if it.GetNetCode() == net or it in skip:
            continue
        if it.GetClass() == 'PCB_VIA' or it.IsOnLayer(L):
            yield it


def seg_ok(a, c, w, L, net, skip=()):
    """新线段 a→c 离同层异网铜 ≥ 0.1、离异网孔 ≥ 0.2。"""
    sg = pcbnew.SEG(a, c)
    if any(e.Distance(sg) < EDGE_CLR + w // 2 for e in EDGES):
        return False
    shp = pcbnew.SHAPE_SEGMENT(a, c, w); bb = shp.BBox()
    x0, y0, x1, y1 = bb.GetLeft() - HOLE_CLR, bb.GetTop() - HOLE_CLR, bb.GetRight() + HOLE_CLR, bb.GetBottom() + HOLE_CLR
    for it in others(L, net, skip):
        ib = it.GetBoundingBox()
        if ib.GetRight() < x0 or ib.GetLeft() > x1 or ib.GetBottom() < y0 or ib.GetTop() > y1:
            continue
        if it.GetEffectiveShape(L).Collide(shp, CLR - 1):
            return False
        if it.GetClass() == 'PCB_VIA' and pcbnew.SHAPE_CIRCLE(it.GetPosition(), it.GetDrillValue() // 2).Collide(pcbnew.SEG(a, c), HOLE_CLR + w // 2 - 1):
            return False
    return True


def key(P):
    return (P.x, P.y)


def cp(P):
    return pcbnew.VECTOR2I(P.x, P.y)                         # GetStart/GetEnd 给的是线段内部坐标的引用，改线前必须复制


xy = lambda P: [round(mm(P.x), 3), round(mm(P.y), 3)]


def V(P, u, k):
    return P + pcbnew.VECTOR2I(int(round(u[0] * k)), int(round(u[1] * k)))




def unit(v):
    n = math.hypot(v.x, v.y)
    return (v.x / n, v.y / n) if n else (0, 0)



# ---- 0 拆 T 接：线头接在另一段线中间时，把那段在接点处断开，后面各步才看得到这个接点（D8 复核查出漏看 T 接）----
for t in tracks():
    for E in (cp(t.GetStart()), cp(t.GetEnd())):
        for s in tracks():
            if s is t or s.GetNetCode() != t.GetNetCode() or s.GetLayer() != t.GetLayer():
                continue
            A, B = cp(s.GetStart()), cp(s.GetEnd())
            if key(E) in (key(A), key(B)) or pcbnew.SEG(A, B).Distance(E) > MM(0.001):
                continue
            s.SetEnd(E); add_track(E, B, s.GetWidth(), s.GetLayer(), s.GetNetCode())
            report['tee_split'].append({'net': t.GetNetname(), 'at': xy(E)})
            break

# ---- 0b 接缝：两段线头差一点没对上、只靠铜皮重叠连着（V4 参考走线里有），两段各自伸缩到两条线的交点 ----
def loose_ends():
    cnt = collections.Counter((t.GetNetCode(), t.GetLayer(), key(P)) for t in TR for P in (t.GetStart(), t.GetEnd()))
    out = []
    for t in TR:
        for P, O in ((t.GetStart(), t.GetEnd()), (t.GetEnd(), t.GetStart())):
            if cnt[(t.GetNetCode(), t.GetLayer(), key(P))] == 1 and not anchored(P, t.GetNetCode()):
                out.append((t, cp(P), cp(O)))
    return out


done_ends = set()
for t1, E1, O1 in loose_ends():
    if key(E1) in done_ends:
        continue
    for t2, E2, O2 in loose_ends():
        if t2 is t1 or t2.GetNetCode() != t1.GetNetCode() or t2.GetLayer() != t1.GetLayer() or key(E2) in done_ends \
                or (E1 - E2).EuclideanNorm() > max(t1.GetWidth(), t2.GetWidth()):
            continue
        d1, d2 = unit(E1 - O1), unit(E2 - O2)
        det = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(det) < 1e-6:
            X = E1                                            # 共线：第二段直接接到第一段头上
        else:
            wv = E2 - O1; k = (wv.x * d2[1] - wv.y * d2[0]) / det
            X = V(O1, d1, k)                                  # 两条线的交点
        if (X - E1).EuclideanNorm() > MM(0.5) or (X - O1).EuclideanNorm() < MM(0.02) or (X - O2).EuclideanNorm() < MM(0.02):
            continue
        if not (seg_ok(O1, X, t1.GetWidth(), t1.GetLayer(), t1.GetNetCode()) and seg_ok(O2, X, t2.GetWidth(), t2.GetLayer(), t2.GetNetCode())):
            report['gap_skip'].append({'net': t1.GetNetname(), 'at': xy(E1)}); continue
        for t, E in ((t1, E1), (t2, E2)):
            if key(t.GetStart()) == key(E): t.SetStart(X)
            else: t.SetEnd(X)
        done_ends |= {key(E1), key(E2), key(X)}
        report['gap'].append({'net': t1.GetNetname(), 'at': xy(X), 'mm': round(mm((E1 - E2).EuclideanNorm()), 3)})
        break

# ---- 1 同宽 ----
nodes = collections.defaultdict(list)
for t in tracks():
    for P in (t.GetStart(), t.GetEnd()):
        nodes[(t.GetNetCode(), t.GetLayer(), key(P))].append(t)
seen = set()
for t0 in tracks():
    if id(t0) in seen:
        continue
    run, stack = [], [t0]                                     # 一串：经非锚点相连的同网同层线段
    while stack:
        t = stack.pop()
        if id(t) in seen:
            continue
        seen.add(id(t)); run.append(t)
        for P in (t.GetStart(), t.GetEnd()):
            if anchored(P, t.GetNetCode()):
                continue
            stack += [u for u in nodes[(t.GetNetCode(), t.GetLayer(), key(P))] if id(u) not in seen]
    ws = {t.GetWidth() for t in run}
    if len(ws) > 1:
        # 从最宽往下试，整串都放得下的那档就用（电源线尽量别收窄）；最细那档一定放得下
        w = next(x for x in sorted(ws, reverse=True)
                 if x == min(ws) or all(seg_ok(t.GetStart(), t.GetEnd(), x, t.GetLayer(), t.GetNetCode()) for t in run))
        for t in run:
            t.SetWidth(w)
        report['width'].append({'net': t0.GetNetname(), 'layer': b.GetLayerName(t0.GetLayer()), 'segs': len(run),
                                'from': sorted(round(mm(x), 3) for x in ws), 'to': round(mm(w), 3)})


# ---- 2 出线 ----
def pad_axis(p):
    """长方形焊盘的长轴单位向量和半长；方/圆焊盘、或转了非 45° 整数倍的焊盘（V4 照搬的扇形电容）返回 None。"""
    sz = p.GetSize()
    if abs(sz.x - sz.y) < MM(0.05) or p.GetShape() == pcbnew.PAD_SHAPE_CIRCLE:
        return None
    deg = p.GetOrientation().AsDegrees() + (0 if sz.x > sz.y else 90)
    if abs((deg + 22.5) % 45 - 22.5) > 0.5:
        return None
    a = math.radians(deg)
    return (math.cos(a), -math.sin(a)), max(sz.x, sz.y) / 2


def build_nodes():
    n = collections.defaultdict(list)
    for t in tracks():
        for P in (t.GetStart(), t.GetEnd()):
            n[(t.GetNetCode(), t.GetLayer(), key(P))].append(t)
    return n


def exit_path(P, Q, ax, half_l, C):
    """P 在焊盘上、Q 是这段的另一端（不动）。返回先沿长轴出、再 45°、必要时再横向的折线 [P, …, Q]。"""
    d = Q - P
    al = d.x * ax[0] + d.y * ax[1]
    a = ax if al > 0 else (-ax[0], -ax[1]); al = abs(al)
    n = (-a[1], a[0])
    pe = d.x * n[0] + d.y * n[1]
    if pe < 0:
        n, pe = (-n[0], -n[1]), -pe
    clear = half_l - ((P.x - C.x) * a[0] + (P.y - C.y) * a[1]) + MM(0.1)    # 沿长轴走到焊盘端头外 0.1
    x = clear                                                 # 拐点必须在焊盘外，否则斜段还是从焊盘里出来
    if al - pe >= x:                                          # 两段：沿轴 → 45° 到 Q
        return [P, V(P, a, al - pe), Q]
    y = al - x; z = pe - y
    if y < MM(0.02) or z < MM(0.02):
        return None
    return [P, V(P, a, x), V(Q, n, -z), Q]                    # 三段：沿轴 → 45° → 横向到 Q


def bend_ok(Q, din, others_at_Q, net):
    """Q 处新进线方向 din 和原来接在 Q 的线夹角都 ≥ 135°（Q 在焊盘/过孔上不算）。"""
    if anchored(Q, net):
        return True
    for u in others_at_Q:
        R = u.GetEnd() if key(u.GetStart()) == key(Q) else u.GetStart()
        r = unit(R - Q)
        if -din[0] * r[0] - din[1] * r[1] > math.cos(math.radians(134)):
            return False
    return True


def square_into(Q, din, net, L):
    """Q 落在长方形焊盘上时，经过 Q 的线段方向要沿它的长轴或垂直长轴，不然只是把斜出挪到了对面焊盘（中间拐点也算）。"""
    for p in pads:
        if p.GetNetCode() == net and p.IsOnLayer(L) and p.HitTest(Q) and pad_axis(p):
            c = abs(din[0] * pad_axis(p)[0][0] + din[1] * pad_axis(p)[0][1])
            if 0.0175 < c < 0.9998:
                return False
    return True


nodes = build_nodes()
for t in tracks():
    if t not in TR:
        continue                                              # 前面重走时被并掉了
    for end in (0, 1):
        P, Q = (cp(t.GetStart()), cp(t.GetEnd())) if end == 0 else (cp(t.GetEnd()), cp(t.GetStart()))
        hit = [p for p in pads if p.GetNetCode() == t.GetNetCode() and p.HitTest(P) and p.IsOnLayer(t.GetLayer())]
        if not hit or not pad_axis(hit[0]) or hit[0].HitTest(Q):
            continue
        p = hit[0]; ax, half_l = pad_axis(p)
        d1 = unit(Q - P); c = abs(d1[0] * ax[0] + d1[1] * ax[1])
        if c > 0.9998 or c < 0.0175:
            continue                                          # 已沿长轴或垂直出
        name = f'{p.GetParentFootprint().GetReference()}.{p.GetNumber()}'
        w, L, net = t.GetWidth(), t.GetLayer(), t.GetNetCode()
        nb = [u for u in nodes[(net, L, key(Q))] if u is not t]
        u = nb[0] if len(nb) == 1 and not anchored(Q, net) else None
        R = cp(u.GetEnd() if key(u.GetStart()) == key(Q) else u.GetStart()) if u else None
        cands = []                                            # (新折线, 要并掉的下一段, 下一段改到的新起点)
        cands.append((exit_path(P, Q, ax, half_l, p.GetPosition()), None, None))
        if u:
            # QFP 扇出最常见：焊盘 → 45° → Q → 往外直走。把这条 45° 整体沿长轴往外推，拐点落到 QR 上
            path = exit_path(P, Q, ax, half_l, p.GetPosition())
            A = path[1] if path else None
            d1, d3 = unit(Q - P), unit(R - Q)
            det = d1[0] * d3[1] - d1[1] * d3[0]
            if A is not None and len(path) > 2 and abs(det) > 1e-6:
                A = V(P, unit(A - P), (A - P).EuclideanNorm())
                wv = Q - A
                k = (wv.x * d3[1] - wv.y * d3[0]) / det; m = (wv.x * d1[1] - wv.y * d1[0]) / det
                if k > MM(0.05) and 0 < m < (R - Q).EuclideanNorm() - MM(0.05):
                    cands.append(([P, A, V(A, d1, k)], None, V(A, d1, k)))
            if u.GetWidth() == w:
                cands.append((exit_path(P, R, ax, half_l, p.GetPosition()), u, None))   # 连下一段一起重走到 R
        why = '离焊盘太近，折不出来'
        for path, drop, newQ in cands:
            if not path:
                continue
            end_pt = path[-1]
            tail = [x for x in nodes[(net, L, key(end_pt))] if x is not t and x is not drop] if newQ is None else [u]
            if newQ is None and not bend_ok(end_pt, unit(path[-1] - path[-2]), tail, net):
                why = '终点会出尖角'; continue
            if not all(square_into(path[i], unit(path[i] - path[i - 1]), net, L) and square_into(path[i - 1], unit(path[i] - path[i - 1]), net, L)
                       for i in range(2, len(path))):
                why = '会斜着压进别的焊盘'; continue
            if not all(seg_ok(path[i], path[i + 1], w, L, net) for i in range(len(path) - 1)):
                why = '间距不够'; continue
            why = None; break
        if why:
            report['exit_skip'].append({'pad': name, 'at': xy(P), 'why': why}); continue
        if drop is not None:
            b.Remove(drop); TR.remove(drop)
        if newQ is not None:
            if key(u.GetStart()) == key(Q): u.SetStart(newQ)
            else: u.SetEnd(newQ)
        if end == 0: t.SetStart(P); t.SetEnd(path[1])
        else: t.SetStart(path[1]); t.SetEnd(P)
        for i in range(1, len(path) - 1):
            add_track(path[i], path[i + 1], w, L, net)
        nodes = build_nodes()
        report['exit'].append({'pad': name, 'net': t.GetNetname(), 'at': xy(P), 'segs': len(path) - 1})
        break

# ---- 2b 小台阶：两个 90° 拐角夹一段很短的线（Z 形，换座阶梯里常见），改成一段 45° 斜线 ----
nodes = build_nodes()
for s in tracks():
    if s.GetLength() > MM(0.1):
        continue
    net, L = s.GetNetCode(), s.GetLayer()
    A, B = cp(s.GetStart()), cp(s.GetEnd())
    na = [u for u in nodes[(net, L, key(A))] if u is not s]; nb = [u for u in nodes[(net, L, key(B))] if u is not s]
    if len(na) != 1 or len(nb) != 1 or anchored(A, net) or anchored(B, net) or na[0] is nb[0]:
        continue
    t1, t2 = na[0], nb[0]
    R1 = cp(t1.GetEnd() if key(t1.GetStart()) == key(A) else t1.GetStart())
    R2 = cp(t2.GetEnd() if key(t2.GetStart()) == key(B) else t2.GetStart())
    u1, u2, us = unit(R1 - A), unit(R2 - B), unit(B - A)
    if abs(u1[0] * us[0] + u1[1] * us[1]) > 0.02 or u1[0] * u2[0] + u1[1] * u2[1] > -0.999:
        continue                                              # 只处理两边平行反向、中间垂直的 Z
    jog = s.GetLength()
    if (R1 - A).EuclideanNorm() < jog + MM(0.05):
        continue
    A2 = V(A, u1, jog)                                        # t1 缩回一个台阶长，斜着接到 B
    w = min(t1.GetWidth(), s.GetWidth())
    if not seg_ok(A2, B, w, L, net, (t1, s, t2)):
        report['jog_skip'].append({'net': s.GetNetname(), 'at': xy(A)}); continue
    if key(t1.GetStart()) == key(A): t1.SetStart(A2)
    else: t1.SetEnd(A2)
    s.SetStart(A2); s.SetEnd(B); s.SetWidth(w)
    nodes = build_nodes()
    report['jog'].append({'net': s.GetNetname(), 'at': xy(A), 'mm': round(mm(jog), 3)})

# ---- 3 倒角 ----
nodes = build_nodes()
corner_ends = collections.Counter()                           # 每段有几个端点是待倒的拐角
corners = []
for (net, L, k0), lst in nodes.items():
    P = pcbnew.VECTOR2I(*k0)
    if len(lst) != 2 or anchored(P, net):
        continue
    t1, t2 = lst
    Q1 = cp(t1.GetEnd() if key(t1.GetStart()) == k0 else t1.GetStart())
    Q2 = cp(t2.GetEnd() if key(t2.GetStart()) == k0 else t2.GetStart())
    u1, u2 = unit(Q1 - P), unit(Q2 - P)
    inner = math.degrees(math.acos(max(-1, min(1, u1[0] * u2[0] + u1[1] * u2[1]))))
    if inner < 134:
        corners.append((P, t1, t2, Q1, Q2, u1, u2, inner)); corner_ends[id(t1)] += 1; corner_ends[id(t2)] += 1
for P, t1, t2, Q1, Q2, u1, u2, inner in corners:
    lim = lambda t, Q: (Q - P).EuclideanNorm() * (0.45 if corner_ends[id(t)] > 1 else 0.8)
    done = False
    for c in (MM(0.3), MM(0.2), MM(0.12), MM(0.06)):
        c = min(c, lim(t1, Q1), lim(t2, Q2))
        if c < MM(0.03):
            break
        A = P + pcbnew.VECTOR2I(int(round(u1[0] * c)), int(round(u1[1] * c)))
        B = P + pcbnew.VECTOR2I(int(round(u2[0] * c)), int(round(u2[1] * c)))
        w = min(t1.GetWidth(), t2.GetWidth())
        on_pad = any(p.IsOnLayer(t1.GetLayer()) and (p.HitTest(A) or p.HitTest(B)) for p in pads)
        if on_pad:
            continue                                          # 切点落进焊盘，斜段就成了斜着出焊盘（复核查出 U1.20 等 4 处），切短点再试
        if seg_ok(A, B, w, t1.GetLayer(), t1.GetNetCode(), (t1, t2)):
            for t in (t1, t2):
                if key(t.GetStart()) == key(P): t.SetStart(A if t is t1 else B)
                else: t.SetEnd(A if t is t1 else B)
            add_track(A, B, w, t1.GetLayer(), t1.GetNetCode())
            report['chamfer'].append({'net': t1.GetNetname(), 'at': xy(P), 'deg': round(inner), 'cut_mm': round(mm(c), 3)}); done = True
            break
    if not done:
        report['chamfer_skip'].append({'net': t1.GetNetname(), 'at': xy(P), 'deg': round(inner)})

b.Save(dst)
summary = {k: len(v) for k, v in report.items()}
print(json.dumps(summary, ensure_ascii=False))
if rep_path:
    json.dump({'summary': summary, **report}, open(rep_path, 'w'), ensure_ascii=False, indent=1)
