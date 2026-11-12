"""走线分块出图，给人或子代理看图复查（route_lint.py 查不到的「乱、绕、挤」靠看）。KiCad 自带 Python 运行。
用法：KPY route_tiles.py 板.kicad_pcb lint.json 输出目录 [块边长mm=8]
每块画正面（红）、背面（蓝）铜：走线、焊盘、过孔；lint 查出的点按类别画圈（尖角 橙、怪角度 紫、线宽突变 绿、斜出焊盘 青）。
只出有问题的块，外加一张全板总览（overview.svg）。macOS 可用 `qlmanage -t -s 1600 -o 目录 x.svg` 转成 PNG 看。"""
import json, os, sys
import pcbnew

src, lint, out = sys.argv[1], sys.argv[2], sys.argv[3]
T = float(sys.argv[4]) if len(sys.argv) > 4 else 8.0
os.makedirs(out, exist_ok=True)
b = pcbnew.LoadBoard(src); mm = pcbnew.ToMM
L = json.load(open(lint))
COL = {pcbnew.F_Cu: '#d62728', pcbnew.B_Cu: '#1f4fd6'}
MARK = {'sharp': '#ff8c00', 'odd_angle': '#9b30ff', 'width_jump': '#00a651', 'pad_exit': '#00b7c7'}
bb = b.GetBoardEdgesBoundingBox()
X0, Y0, X1, Y1 = mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())
items = []                                                   # (层, svg 片段生成器的参数)
for t in b.GetTracks():
    if t.GetClass() == 'PCB_TRACK' and t.GetLayer() in COL:
        items.append(('line', t.GetLayer(), mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y), mm(t.GetWidth())))
    elif t.GetClass() == 'PCB_VIA':
        items.append(('via', None, mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetWidth(pcbnew.F_Cu)) / 2, mm(t.GetDrillValue()) / 2))
for f in b.GetFootprints():
    for p in f.Pads():
        for ly in (pcbnew.F_Cu, pcbnew.B_Cu):
            if p.IsOnLayer(ly):
                pb = p.GetBoundingBox()
                items.append(('pad', ly, mm(pb.GetLeft()), mm(pb.GetTop()), mm(pb.GetWidth()), mm(pb.GetHeight()), f.GetReference() + '.' + p.GetNumber()))
hits = [(k, h['at']) for k in MARK for h in L.get(k, [])]


def svg(x0, y0, x1, y1, scale, labels):
    w, h = (x1 - x0) * scale, (y1 - y0) * scale
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" viewBox="{x0} {y0} {x1 - x0} {y1 - y0}">',
         f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="#111"/>']
    inside = lambda x, y, m=1: x0 - m <= x <= x1 + m and y0 - m <= y <= y1 + m
    for ly in (pcbnew.B_Cu, pcbnew.F_Cu):                   # 背面先画，正面压在上面
        for it in items:
            if it[0] == 'pad' and it[1] == ly and inside(it[2], it[3]):
                s.append(f'<rect x="{it[2]}" y="{it[3]}" width="{it[4]}" height="{it[5]}" fill="{COL[ly]}" opacity="0.55"/>')
            if it[0] == 'line' and it[1] == ly and (inside(it[2], it[3]) or inside(it[4], it[5])):
                s.append(f'<line x1="{it[2]}" y1="{it[3]}" x2="{it[4]}" y2="{it[5]}" stroke="{COL[ly]}" stroke-width="{it[6]}" stroke-linecap="round" opacity="0.9"/>')
    for it in items:
        if it[0] == 'via' and inside(it[2], it[3]):
            s.append(f'<circle cx="{it[2]}" cy="{it[3]}" r="{it[4]}" fill="#ccc"/><circle cx="{it[2]}" cy="{it[3]}" r="{it[5]}" fill="#111"/>')
    for k, (x, y) in hits:
        if inside(x, y, 0):
            s.append(f'<circle cx="{x}" cy="{y}" r="{0.35 if labels else 0.5}" fill="none" stroke="{MARK[k]}" stroke-width="{0.08 if labels else 0.15}"/>')
    if labels:
        y = y0 + 0.5
        for k, c in MARK.items():
            s.append(f'<text x="{x0 + 0.2}" y="{y}" font-size="0.4" fill="{c}">{k}</text>'); y += 0.5
    s.append('</svg>')
    return '\n'.join(s)


open(os.path.join(out, 'overview.svg'), 'w').write(svg(X0, Y0, X1, Y1, 40, False))
n = 0
y = Y0
while y < Y1:
    x = X0
    while x < X1:
        cnt = sum(1 for _, (hx, hy) in hits if x <= hx < x + T and y <= hy < y + T)
        if cnt:
            open(os.path.join(out, f'tile_x{x - X0:04.1f}_y{y - Y0:04.1f}_n{cnt}.svg'), 'w').write(svg(x, y, x + T, y + T, 120, True)); n += 1
        x += T
    y += T
print('出图', n, '块（有问题的）+ overview.svg →', out)
