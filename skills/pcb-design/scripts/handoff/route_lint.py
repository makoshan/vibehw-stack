"""走线质量检查（DRC 不管的那些）：KiCad 自带 Python 运行。
用法：KPY route_lint.py 板.kicad_pcb [输出.json]；有问题退出码 1。
查五类，交付前都应为 0（来源：2026-11-03 外包工程师看 C08B D7 的反馈）：
  odd_angle   线段不是 0/45/90°（容差 1°，短于 0.05 mm 的不计）
  sharp       同层同网两段在非焊盘、非过孔处相接，夹角 < 135°（90° 直角、锐角都算「尖角」）
  width_jump  同层同网两段在非焊盘、非过孔处相接但线宽不同（线中途突变）
  tee_acute   T 接（三段以上相接）里有两段夹角 < 90°（锐角，酸槽）；tee_90 是 90° 的 T 接，只报告
  hidden_90   两个同向 45° 之间夹的线段短于 0.0625 mm（约 0.414×0.15）：中心线每处 135°，铜皮内角却是纯 90°（C08B t6 复核查出，sharp 看不到）
  dangling    线头悬空：端点不接别的线、不在焊盘/过孔上（KiCad 靠铜皮重叠算连通，DRC 不报）
  （T 接——线头接在另一段线中间——先在内存里拆成端点再查，否则接点上的尖角和线宽突变看不到）
  multi_width 同一个网用了不止一种线宽（只报告，不算失败：电源出焊盘处收窄是允许的，但要在焊盘或过孔上收）
  pad_exit    从长方形焊盘出来的第一段既不沿焊盘长轴、也不垂直于焊盘（斜着出）；圆形/方形焊盘不查
  pad_exit_rotated  同上，但封装转了非 45° 整数倍，只报告不算失败
  pad_exit_side     垂直长轴、从焊盘长边出（片式阻容沿元件轴向出线常是这样），只报告
阳性对照：D6（换座阶梯走线）sharp 应 ≥ 72；D5 sharp 9。"""
import collections, json, math, sys
import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
mm = pcbnew.ToMM
tr = [t for t in b.GetTracks() if t.GetClass() == 'PCB_TRACK']
# 先在内存里拆 T 接（线头接在另一段线中间）：不拆的话那个接点上的拐角、线宽变化都看不到（D8 复核查出）
for t in list(tr):
    for E in (pcbnew.VECTOR2I(t.GetStart().x, t.GetStart().y), pcbnew.VECTOR2I(t.GetEnd().x, t.GetEnd().y)):
        for s in list(tr):
            if s is t or s.GetNetCode() != t.GetNetCode() or s.GetLayer() != t.GetLayer():
                continue
            A, B = pcbnew.VECTOR2I(s.GetStart().x, s.GetStart().y), pcbnew.VECTOR2I(s.GetEnd().x, s.GetEnd().y)
            if (E.x, E.y) in ((A.x, A.y), (B.x, B.y)) or pcbnew.SEG(A, B).Distance(E) > pcbnew.FromMM(0.001):
                continue
            n = pcbnew.PCB_TRACK(b); n.SetStart(E); n.SetEnd(B); n.SetWidth(s.GetWidth()); n.SetLayer(s.GetLayer()); n.SetNetCode(s.GetNetCode())
            s.SetEnd(E); b.Add(n); tr.append(n)
            break
vias = [t for t in b.GetTracks() if t.GetClass() == 'PCB_VIA']
pads = [p for f in b.GetFootprints() for p in f.Pads()]
MIN_LEN = pcbnew.FromMM(0.05)


def deg(P, Q):
    return math.degrees(math.atan2(-(Q.y - P.y), Q.x - P.x)) % 360


def off45(a):
    a %= 180
    return min(abs(a - k) for k in (0, 45, 90, 135, 180))


def on_pad_or_via(P, net, layer=None):
    return any(p.GetNetCode() == net and p.HitTest(P) and (layer is None or p.IsOnLayer(layer)) for p in pads) or \
        any(v.GetNetCode() == net and (v.GetPosition() - P).EuclideanNorm() <= v.GetWidth(pcbnew.F_Cu) // 2 for v in vias)


xy = lambda P: [round(mm(P.x), 3), round(mm(P.y), 3)]
out = {k: [] for k in ('odd_angle', 'sharp', 'hidden_90', 'tee_acute', 'tee_90', 'width_jump', 'dangling', 'pad_exit', 'pad_exit_side', 'pad_exit_rotated')}
for t in tr:
    if t.GetLength() > MIN_LEN and off45(deg(t.GetStart(), t.GetEnd())) > 1:
        out['odd_angle'].append({'net': t.GetNetname(), 'layer': b.GetLayerName(t.GetLayer()), 'at': xy((t.GetStart() + t.GetEnd()) / 2),
                                 'deg': round(deg(t.GetStart(), t.GetEnd()) % 180, 1)})
ends = collections.defaultdict(list)                        # (网, 层, x, y) → [(线, 另一端)]
for t in tr:
    for P, Q in ((t.GetStart(), t.GetEnd()), (t.GetEnd(), t.GetStart())):
        ends[(t.GetNetCode(), t.GetLayer(), P.x, P.y)].append((t, Q))
for (net, layer, x, y), lst in ends.items():
    P = pcbnew.VECTOR2I(x, y)
    if len(lst) < 2 or on_pad_or_via(P, net, layer):
        continue
    name = lst[0][0].GetNetname()
    if len({t.GetWidth() for t, _ in lst}) > 1:
        out['width_jump'].append({'net': name, 'layer': b.GetLayerName(layer), 'at': xy(P), 'widths': sorted({round(mm(t.GetWidth()), 3) for t, _ in lst})})
    angs = [abs((deg(P, Q1) - deg(P, Q2) + 180) % 360 - 180) for i, (_, Q1) in enumerate(lst) for _, Q2 in lst[i + 1:]]   # 每两段夹角，180 = 一条直线
    if len(lst) == 2 and angs[0] < 134:
        out['sharp'].append({'net': name, 'layer': b.GetLayerName(layer), 'at': xy(P), 'deg': round(angs[0])})
    elif len(lst) > 2 and min(angs) < 89:
        out['tee_acute'].append({'net': name, 'layer': b.GetLayerName(layer), 'at': xy(P), 'deg': round(min(angs))})
    elif len(lst) > 2 and min(angs) < 134:
        out['tee_90'].append({'net': name, 'layer': b.GetLayerName(layer), 'at': xy(P)})
HID = pcbnew.FromMM(0.0625)
for t in tr:                                                  # 伪装的 90°：a→P→Q→b，P、Q 都是普通拐点，PQ 很短，两次同向转 45°
    if t.GetLength() > HID:
        continue
    P, Q = t.GetStart(), t.GetEnd()
    lp = ends[(t.GetNetCode(), t.GetLayer(), P.x, P.y)]; lq = ends[(t.GetNetCode(), t.GetLayer(), Q.x, Q.y)]
    if len(lp) != 2 or len(lq) != 2 or on_pad_or_via(P, t.GetNetCode(), t.GetLayer()) or on_pad_or_via(Q, t.GetNetCode(), t.GetLayer()):
        continue
    A = next(o for s, o in lp if not (o.x == Q.x and o.y == Q.y)) if any(not (o.x == Q.x and o.y == Q.y) for s, o in lp) else None
    B = next(o for s, o in lq if not (o.x == P.x and o.y == P.y)) if any(not (o.x == P.x and o.y == P.y) for s, o in lq) else None
    if A is None or B is None:
        continue
    t1 = (deg(P, Q) - deg(A, P) + 180) % 360 - 180; t2 = (deg(Q, B) - deg(P, Q) + 180) % 360 - 180
    if abs(abs(t1) - 45) < 3 and abs(abs(t2) - 45) < 3 and t1 * t2 > 0:
        out['hidden_90'].append({'net': t.GetNetname(), 'layer': b.GetLayerName(t.GetLayer()), 'at': xy(P), 'len': round(mm(t.GetLength()), 3)})
for (net, layer, x, y), lst in ends.items():                 # 悬空线头：端点只有它自己，又不在焊盘/过孔上（KiCad 靠铜皮重叠算连上，不报）
    P = pcbnew.VECTOR2I(x, y)
    if len(lst) == 1 and not on_pad_or_via(P, net, layer):
        out['dangling'].append({'net': lst[0][0].GetNetname(), 'layer': b.GetLayerName(layer), 'at': xy(P)})
for t in tr:
    for P, Q in ((t.GetStart(), t.GetEnd()), (t.GetEnd(), t.GetStart())):
        # 线头压着的同网焊盘全部查：同网焊盘常互相重叠，只看第一个时，近方形邻盘会把旁边的长方形焊盘漏掉（D10 C60.2、C64.2）
        for p in [p for p in pads if p.GetNetCode() == t.GetNetCode() and p.HitTest(P) and p.IsOnLayer(t.GetLayer())]:
            sz = p.GetSize()
            if abs(sz.x - sz.y) < pcbnew.FromMM(0.05) or p.GetShape() == pcbnew.PAD_SHAPE_CIRCLE:
                continue                                     # 方/圆焊盘不分长轴
            ax = (p.GetOrientation().AsDegrees() + (0 if sz.x > sz.y else 90)) % 180   # 焊盘长轴方向
            rel = (deg(P, Q) - ax) % 180
            if abs((ax + 22.5) % 45 - 22.5) > 0.5:                 # 封装转了非 45° 整数倍（V4 照搬的扇形电容），0/45/90 走线出不正，单列
                if min(rel, 180 - rel) > 1 and abs(rel - 90) > 1:
                    out['pad_exit_rotated'].append({'pad': f'{p.GetParentFootprint().GetReference()}.{p.GetNumber()}', 'at': xy(P)})
                continue
            if abs(rel - 90) <= 1:                                   # 垂直长轴、从长边出：片式阻容沿元件轴向出线常是这样，只报告
                out['pad_exit_side'].append({'pad': f'{p.GetParentFootprint().GetReference()}.{p.GetNumber()}', 'net': t.GetNetname(),
                                             'layer': b.GetLayerName(t.GetLayer()), 'at': xy(P)})
            if min(rel, 180 - rel) > 1 and abs(rel - 90) > 1:
                out['pad_exit'].append({'pad': f'{p.GetParentFootprint().GetReference()}.{p.GetNumber()}', 'net': t.GetNetname(),
                                        'layer': b.GetLayerName(t.GetLayer()), 'at': xy(P), 'rel_deg': round(min(rel, 180 - rel), 1)})
bynet = collections.defaultdict(set)
for t in tr:
    bynet[t.GetNetname()].add(round(mm(t.GetWidth()), 3))
out['multi_width'] = {n: sorted(w) for n, w in sorted(bynet.items()) if len(w) > 1}
summary = {k: len(v) for k, v in out.items()}
print(json.dumps(summary, ensure_ascii=False))
if len(sys.argv) > 2:
    json.dump({'board': sys.argv[1], 'summary': summary, **out}, open(sys.argv[2], 'w'), ensure_ascii=False, indent=1)
sys.exit(1 if any(summary[k] for k in ('odd_angle', 'sharp', 'hidden_90', 'tee_acute', 'width_jump', 'dangling', 'pad_exit')) else 0)
