"""丝印 + 装配层按标准整理（KiCad python）；只改丝印层/装配层文字和 1 脚点，不动铜。
丝印：位号字高 H（默认 1.25，字形约 1.13、最细笔画 ≥ 0.15；嘉立创安全值 1.0/0.15，高精度下限 0.8/0.1）；
  只给芯片、座子、晶振、电荷泵、测试点印位号（密板标准做法，阻容/ESD 看装配图）；
  在件外框（courtyard）四周找位置：离本件比离任何别的件都近、离焊盘/过孔 ≥ 0.15、不压别的件外框、不压已放的丝印、离板边 ≥ 0.3，
  大件先放；找不到就隐藏，改看装配图。芯片/晶振/电荷泵/J_AFE 补 1 脚圆点；板名、座子脚序找空地放。
装配层（Fab）：每件只留一个居中的 ${REFERENCE}，字号按外框缩；Value 等字段全部隐藏；
  字体 Kanit Medium（OFL）嵌进板文件；
  原本放在丝印层的隐藏字段（LCSC、MPN 等）挪到装配层，免得导到别的 EDA 时冒出来。
用法：KiCad python silk.py 入板 出板 [字高=1.25] [板名]
换板要改（C08B D5 的取值）：FACE（字体须本机已装、可分发）；WANT 白名单正则；HN 板名字高；
板名默认值；背面版本名 target=(0.5, 0.88)；pinout() 现在不调用（Mako 嫌整句太大）。"""
import sys, json, math, re, pcbnew as k
# 丝印字体用 TrueType「Kanit Medium」（方正圆角，照 Mako 给的参考板；OFL 开源可嵌入）。字高 1.0 实测字形只有 0.90，
# 1.15 时最细笔画只有 0.14–0.15（复核用 min_text_thickness 二分查出），所以默认 1.25。Krungthep 更像但 6/8/9/B/Q/R 交接处细于 0.08 不能用。
# pcbnew 的 Python 不暴露字体类，先在文件层给位号写 (face …)，载入后新文字从位号上拷贝字体
FACE = 'Kanit Medium'
src = open(sys.argv[1], encoding='utf-8').read()
src, n_face = re.subn(r'(\(property "Reference" "[^"]*"\s*\(at [^)]*\)\s*\(layer "[FB]\.SilkS"\)\s*(?:\(hide yes\)\s*)?\(uuid "[^"]+"\)\s*\(effects\s*\(font)(?!\s*\(face)', r'\1 (face "%s")' % FACE, src)
src = src.replace('\n\t(embedded_fonts no)', '\n\t(embedded_fonts yes)', 1)        # 板级开关：存盘时把用到的字体嵌进文件
assert n_face > 0, '没找到丝印位号字段，字体没写进去'   # 否则下面 FONT 会拿到默认笔画字
open(sys.argv[2], 'w', encoding='utf-8').write(src)
b = k.LoadBoard(sys.argv[2]); H = float(sys.argv[3]) if len(sys.argv) > 3 else 1.25
NAME = sys.argv[4] if len(sys.argv) > 4 else 'OpenEMG 0.01'
MM, M = k.FromMM, k.ToMM; T, PAD_GAP, TXT_GAP, EDGE = 0.15, 0.15, 0.1, 0.3
SILK = {k.F_Cu: k.F_SilkS, k.B_Cu: k.B_SilkS}; FAB = {k.F_Cu: k.F_Fab, k.B_Cu: k.B_Fab}
CRT = {k.F_Cu: k.F_CrtYd, k.B_Cu: k.B_CrtYd}
ol = k.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(ol, True)
fps = list(b.GetFootprints())

def box(x0, y0, x1, y1): return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
def bb(o):
    r = o.GetBoundingBox(); return box(M(r.GetX()), M(r.GetY()), M(r.GetRight()), M(r.GetBottom()))
def hit(a, c, g=0.0): return not (a[2] + g <= c[0] or c[2] + g <= a[0] or a[3] + g <= c[1] or c[3] + g <= a[1])
def crt(f):
    s = f.GetCourtyard(CRT[f.GetLayer()])
    if s.OutlineCount() == 0: return bb(f)
    r = s.BBox(); return box(M(r.GetX()), M(r.GetY()), M(r.GetRight()), M(r.GetBottom()))
def inside(a):
    for x, y in ((a[0], a[1]), (a[2], a[1]), (a[0], a[3]), (a[2], a[3])):
        v = k.VECTOR2I(MM(x), MM(y))
        if not ol.Contains(v) or ol.Outline(0).SquaredDistance(v, True) < MM(EDGE) ** 2: return False
    return True

# 每面的障碍：焊盘（含双面孔）、件外框、已有丝印图形
obst = {s: {'pad': [], 'crt': [], 'silk': []} for s in SILK}
for f in fps:
    s = f.GetLayer(); obst[s]['crt'].append((f.GetReference(), crt(f)))
    for p in f.Pads():
        for side in SILK:
            if p.IsOnLayer(side) or p.HasHole(): obst[side]['pad'].append(bb(p))
    for g in f.GraphicalItems():
        if g.GetClass() == 'PCB_SHAPE' and g.GetLayer() == SILK[s]: obst[s]['silk'].append(bb(g))
for z in b.Zones():                                                 # 禁布区（定位孔四角）也不印字
    if z.GetIsRuleArea():
        for side in SILK: obst[side]['pad'].append(bb(z))
for v in b.GetTracks():                                             # 过孔两面都算障碍
    if v.GetClass() == 'PCB_VIA':
        for side in SILK: obst[side]['pad'].append(bb(v))

def legal(a, side, own=None):
    if not inside(a): return False
    if any(hit(a, p, PAD_GAP) for p in obst[side]['pad']): return False
    if any(hit(a, c) for r, c in obst[side]['crt'] if r not in (own or ())): return False
    return not any(hit(a, t, TXT_GAP) for t in obst[side]['silk'])

FONT = next(f.Reference().GetFont() for f in fps if f.Reference().GetLayer() in (k.F_SilkS, k.B_SilkS))

def style(t, layer, h, mirror):
    if layer in SILK.values(): t.SetFont(FONT)
    t.SetLayer(layer); t.SetTextSize(k.VECTOR2I(MM(h), MM(h))); t.SetTextThickness(MM(max(T, h * 0.15) if layer in SILK.values() else h * 0.15))
    t.SetMirrored(mirror); t.SetHorizJustify(k.GR_TEXT_H_ALIGN_CENTER); t.SetVertJustify(k.GR_TEXT_V_ALIGN_CENTER); t.SetKeepUpright(True)

def gap(a, q): return math.hypot(max(q[0] - a[2], a[0] - q[2], 0), max(q[1] - a[3], a[1] - q[3], 0))
def nearest_is_own(a, side, own, cbox):
    """文字离本件（组）外框要比离任何别的件近 0.1 以上，否则读的人会认错件"""
    other = [gap(a, q) for r, q in obst[side]['crt'] if r not in (own or ())]
    return not other or gap(a, cbox) + 0.1 < min(other)

def place(t, side, own, cbox, tiers=(0.15, 0.5)):
    """在 cbox 四周找第一个合法位置；返回是否放上。横排放上下、竖排放左右，按离件中心由近到远试。"""
    cx, cy = (cbox[0] + cbox[2]) / 2, (cbox[1] + cbox[3]) / 2; cand = []
    for ang in (0, 90):
        t.SetTextAngleDegrees(ang); t.SetPosition(k.VECTOR2I(0, 0)); w = bb(t); tw, th = w[2] - w[0], w[3] - w[1]
        for g in tiers:
            if ang == 0:
                for y in (cbox[1] - g - th / 2, cbox[3] + g + th / 2):
                    for dx in (0, -tw / 4, tw / 4, -tw / 2, tw / 2): cand.append((ang, cx + dx, y))
            else:
                for x in (cbox[0] - g - tw / 2, cbox[2] + g + tw / 2):
                    for dy in (0, -th / 4, th / 4, -th / 2, th / 2): cand.append((ang, x, cy + dy))
    cand.sort(key=lambda c: (math.dist((c[1], c[2]), (cx, cy)), c[0]))
    if min(cbox[2] - cbox[0], cbox[3] - cbox[1]) >= 3: cand += [(0, cx, cy), (90, cx, cy)]   # 芯片、座子：外面放不下就印在本体中间
    for ang, x, y in cand:
        t.SetTextAngleDegrees(ang); t.SetPosition(k.VECTOR2I(MM(x), MM(y))); a = bb(t)
        if legal(a, side, own) and nearest_is_own(a, side, own, cbox): obst[side]['silk'].append(a); return True
    return False

stat = {'印上': [], '隐藏': [], '不印': [], '1脚点': [], '角框': [], '空地文字': []}
# 1) 1 脚圆点（先于位号：1 脚标记比位号要紧）：没有任何丝印图形的多脚件（芯片、晶振、电荷泵、J_AFE）
for f in fps:
    s = f.GetLayer(); pads = [p for p in f.Pads() if p.GetNumber() in ('1', 'A1')]
    if len(f.Pads()) < 4 or not pads or any(g.GetLayer() == SILK[s] and g.GetClass() == 'PCB_SHAPE' for g in f.GraphicalItems()): continue
    p = pads[0]; pc = p.GetPosition(); fc = f.GetPosition(); pb = bb(p)
    vx, vy = M(pc.x - fc.x), M(pc.y - fc.y); n = math.hypot(vx, vy) or 1
    r = max(pb[2] - pb[0], pb[3] - pb[1]) / 2
    for d in (r + 0.35, r + 0.55, r + 0.8):
        x, y = M(pc.x) + vx / n * d, M(pc.y) + vy / n * d; a = box(x - 0.2, y - 0.2, x + 0.2, y + 0.2)
        if inside(a) and not any(hit(a, q, PAD_GAP) for q in obst[s]['pad']) and not any(hit(a, t, TXT_GAP) for t in obst[s]['silk']):
            dot = k.PCB_SHAPE(f, k.SHAPE_T_CIRCLE); dot.SetLayer(SILK[s]); dot.SetCenter(k.VECTOR2I(MM(x), MM(y)))
            dot.SetEnd(k.VECTOR2I(MM(x + 0.125), MM(y))); dot.SetWidth(MM(T)); dot.SetFilled(True); f.Add(dot)
            obst[s]['silk'].append(a); stat['1脚点'].append(f.GetReference()); break

# 1b) 芯片、晶振、电荷泵、J_AFE：在焊盘外沿外 0.25 画四角折线（参考板 U2 的画法），每段不压焊盘、不出板框
def seg_ok(x0, y0, x1, y1, side):
    a = box(x0 - T / 2, y0 - T / 2, x1 + T / 2, y1 + T / 2)   # 线宽算进去
    return inside(a) and not any(hit(a, q, PAD_GAP) for q in obst[side]['pad']) and not any(hit(a, t, TXT_GAP) for t in obst[side]['silk'])
for f in fps:
    if not re.search(r'(^|_)(U|X|LM)\d|^J_', f.GetReference()): continue
    s = f.GetLayer(); pb = [bb(p) for p in f.Pads() if p.IsOnLayer(s)]   # 这批封装的装配层没有本体外形线，按焊盘外沿
    if len(pb) < 3: continue
    x0, y0 = min(q[0] for q in pb) - 0.25, min(q[1] for q in pb) - 0.25; x1, y1 = max(q[2] for q in pb) + 0.25, max(q[3] for q in pb) + 0.25
    L = min(0.6, (x1 - x0) / 3, (y1 - y0) / 3)
    segs = [(cx, cy, ex, ey) for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1))
            for ex, ey in ((cx + dx * L, cy), (cx, cy + dy * L)) if seg_ok(min(cx, ex), min(cy, ey), max(cx, ex), max(cy, ey), s)]
    if len(segs) < 6: continue                                       # 不到 3 个角就不画，零星一两段像线头
    for cx, cy, ex, ey in segs:
        g = k.PCB_SHAPE(f, k.SHAPE_T_SEGMENT); g.SetLayer(SILK[s]); g.SetStart(k.VECTOR2I(MM(cx), MM(cy))); g.SetEnd(k.VECTOR2I(MM(ex), MM(ey)))
        g.SetWidth(MM(T)); f.Add(g); obst[s]['silk'].append(box(min(cx, ex) - T / 2, min(cy, ey) - T / 2, max(cx, ex) + T / 2, max(cy, ey) + T / 2))
    stat['角框'].append((f.GetReference(), len(segs)))

# 2) 密板只给人会去找的件印位号（芯片、座子、晶振、电荷泵、测试点）；阻容、ESD 不印，看装配图
WANT = re.compile(r'(^|_)(U|J|FPC|X|LM|TP)\d|^J_')
grouped = {f.GetReference() for f in fps if not WANT.search(f.GetReference())}

# 3) 装配层整理 + 丝印位号
for f in sorted(fps, key=lambda f: -(lambda c: (c[2] - c[0]) * (c[3] - c[1]))(crt(f))):
    s = f.GetLayer(); mir = s == k.B_Cu; c = crt(f)
    for fld in f.GetFields():
        if fld.GetName() == 'Reference': continue
        fld.SetVisible(False)
        if fld.GetLayer() in SILK.values(): fld.SetLayer(FAB[s])
    # 装配层：一个居中的 ${REFERENCE}，字号缩到放进外框
    fab = [g for g in f.GraphicalItems() if g.GetClass() == 'PCB_TEXT' and g.GetLayer() == FAB[s] and 'REFERENCE' in g.GetText()]
    keep = {g.m_Uuid.AsString() for g in fab[:1]}                  # SWIG 代理不能用 is/in 比，按 uuid
    for g in f.GraphicalItems():
        if g.GetClass() == 'PCB_TEXT' and g.GetLayer() == FAB[s] and g.m_Uuid.AsString() not in keep: g.SetVisible(False)
    if fab: t = fab[0]
    else: t = k.PCB_TEXT(f); t.SetText('${REFERENCE}'); f.Add(t)
    cw, ch = c[2] - c[0], c[3] - c[1]; ang = 90 if ch > cw * 1.3 else 0; L = max(cw, ch) if ang == 90 else cw
    h = max(0.15, min(0.8, 0.85 * L / (0.95 * len(f.GetReference())), 0.6 * (cw if ang == 90 else ch)))
    style(t, FAB[s], h, mir); t.SetVisible(True); t.SetTextAngleDegrees(ang); t.SetPosition(k.VECTOR2I(MM((c[0] + c[2]) / 2), MM((c[1] + c[3]) / 2)))
    # 丝印位号
    ref = f.Reference(); style(ref, SILK[s], H, mir); ref.SetVisible(True)
    if f.GetReference() in grouped: ref.SetVisible(False); ref.SetFont(None); stat['不印'].append(f.GetReference()); continue   # 不印的清回默认字体，免得文件存字形缓存
    if place(ref, s, {f.GetReference()}, c, tiers=(0.15, 0.5, 1.0)):
        a2 = bb(ref); other = min(gap(a2, q) for r, q in obst[s]['crt'] if r != f.GetReference())
        stat['印上'].append((f.GetReference(), round(gap(a2, c), 2), round(other, 2)))   # (位号, 离本件, 离最近别的件)
    else: ref.SetVisible(False); ref.SetFont(None); stat['隐藏'].append(f.GetReference())

# 4) 板名、座子脚序：在空地按网格找位置
def free_text(txt, side, h, near=None, pad=None, maxgap=3.0, angs=(0, 90), own=True, half=True, target=None):
    """空地网格里找位置；给了 near（座子位号）就按离 pad 那个脚（或座子中心）由近到远试，且离座子外框 ≤ maxgap"""
    t = k.PCB_TEXT(b); t.SetText(txt); style(t, SILK[side], h, side == k.B_Cu)
    bx = ol.BBox(); X0, Y0, X1, Y1 = M(bx.GetX()), M(bx.GetY()), M(bx.GetRight()), M(bx.GetBottom())
    f = next(f for f in fps if f.GetReference() == near) if near else None
    c = crt(f) if f else None
    if f and pad:
        pp = next(p for p in f.Pads() if p.GetNumber() == pad).GetPosition(); anchor = (M(pp.x), M(pp.y))
    elif c: anchor = ((c[0] + c[2]) / 2, (c[1] + c[3]) / 2)
    cand = [(ang, X0 + xi * 0.25, Y0 + yi * 0.25) for ang in angs for xi in range(int((X1 - X0) / 0.25)) for yi in range(int((Y1 - Y0) / 0.25))]
    if c: cand.sort(key=lambda q: math.dist(q[1:], anchor))
    if target: cand.sort(key=lambda q: math.dist(q[1:], (X0 + target[0] * (X1 - X0), Y0 + target[1] * (Y1 - Y0))))   # target 是板框内的相对位置（0–1）
    for ang, x, y in cand:
        t.SetTextAngleDegrees(ang); t.SetPosition(k.VECTOR2I(MM(x), MM(y))); a = bb(t)
        same_half = True
        if f and pad and half:   # 带脚号的短字要落在该脚那半边（沿座子长边），否则会读成另一头
            ax = 0 if c[2] - c[0] >= c[3] - c[1] else 1; mid = (c[ax] + c[ax + 2]) / 2; tc = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
            same_half = (tc[ax] - mid) * (anchor[ax] - mid) > 0
        if same_half and legal(a, side) and (not c or (gap(a, c) <= maxgap and (not own or nearest_is_own(a, side, {near}, c)))):   # 离本座子比离别的件近，免得认成别的件的字
            b.Add(t); obst[side]['silk'].append(a); stat['空地文字'].append((txt, round(x, 2), round(y, 2), ang, round(gap(a, c), 2) if c else None)); return True
    stat['空地文字'].append((txt, '放不下')); return False
HP = 0.89   # 脚序说明用小字：Kanit 名义 0.89 → 实际字形 0.80，正好嘉立创高精度下限 0.8（Mako 11-03「字号缩到 0.8」）
def pinout(ref, side):
    """先在座子旁放两段短字（离本座子比离别的件近）；放不下就退回一段带位号的整句，放离座子最近的空地（写明位号和脚号，不靠位置也对得上）"""
    n0 = len(stat['空地文字'])
    ok = free_text('2 BIAS 3 WCT', side, HP, near=ref, pad='2') and free_text('19-4 IN1P..IN8N', side, HP, near=ref, pad='11', angs=(0,), half=False)   # 只横排：从这一面看左 19 脚、右 4 脚
    if not ok:
        for t in [t for t in b.GetDrawings() if t.GetClass() == 'PCB_TEXT' and t.GetText() in ('2 BIAS 3 WCT', '19-4 IN1P..IN8N') and t.GetLayer() == SILK[side]]:
            b.Remove(t)
        del stat['空地文字'][n0:]
        free_text(ref + ': 2 BIAS 3 WCT 4-19 IN8N..IN1P', side, HP, near=ref, maxgap=99, own=False)
# 脚序不再印（Mako 11-03「明显太大，不合适」）：两面 21 mm 长的整句太占地方，脚序看装配图和参考包说明
HN = 1.4   # 板名单独加大：小写 p/e/n 和句点在 1.25 时最细笔画不到 0.15，1.4 全过（逐字符 DRC 门槛 0.15）
free_text(NAME, k.F_Cu, HN)
free_text(NAME + ' B', k.B_Cu, HN, angs=(0,), target=(0.5, 0.88))   # 背面版本名横放、居中在下方空地（Mako 11-03 框的位置）

b.EmbedFonts()
b.Save(sys.argv[2])
json.dump(stat, open(sys.argv[2] + '.silk.json', 'w'), ensure_ascii=False, indent=1)
print('角框', stat['角框'], '；字高', H, '；印上', len(stat['印上']), '隐藏', len(stat['隐藏']), '；按规则不印', len(stat['不印']), '；放不下', stat['隐藏'], '；1 脚点', stat['1脚点'], '；空地文字', stat['空地文字'])
