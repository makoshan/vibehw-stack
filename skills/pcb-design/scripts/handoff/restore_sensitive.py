"""整理后把不该动的网整网退回对照板：射频、晶振、地、去耦、DCDC、电源。整理只留在信号线上。
逐段比对退不干净：t4 只退同端点的段，端点微动过的电源段漏了 13 段（约 7 mm 被收窄），还挪出 0.096 mm 偏心的过孔连接，所以一律整网退。
过孔两板相同，不动。短于 2 µm 的信号线碎段删掉，相邻线头对齐到一点。
用法：KEEP_RE=正则 KiCad python restore_sensitive.py 整理后.kicad_pcb 对照.kicad_pcb 输出.kicad_pcb
默认 KEEP_RE 是 C08B 主控 thru3 的网名，换板要改。"""
import os, re, sys
import pcbnew as k

src, ref, dst = sys.argv[1:4]
KEEP_RE = re.compile(os.environ.get('KEEP_RE', r'^(ANT_FEED|RF_ANT|GND|MCU_X[CL]\d|MCU_DEC.*|MCU_DCC.*|VBAT|VBUS|3V3|3V3_AFE|PWR_EN)$'))
b, r = k.LoadBoard(src), k.LoadBoard(ref)
seg = lambda t: t.GetClass() == 'PCB_TRACK'
keep_src = [(t.GetNetname(), t.GetLayer(), t.GetWidth(), (t.GetStart().x, t.GetStart().y), (t.GetEnd().x, t.GetEnd().y)) for t in r.GetTracks() if seg(t) and KEEP_RE.match(t.GetNetname())]

TR = [t for t in b.GetTracks() if seg(t)]
near = lambda e, q, d=3000: abs(e.x - q.x) <= d and abs(e.y - q.y) <= d
tiny = [t for t in TR if t.GetLength() < 2000 and not KEEP_RE.match(t.GetNetname())]
big = [t for t in TR if t.GetLength() >= 2000]
n_s = 0
for t in tiny:
    A = k.VECTOR2I(t.GetStart().x, t.GetStart().y)
    for o in big:
        if o.GetNetCode() == t.GetNetCode() and o.GetLayer() == t.GetLayer():
            if near(o.GetStart(), A): o.SetStart(A); n_s += 1
            if near(o.GetEnd(), A): o.SetEnd(A); n_s += 1
dead = [t for t in TR if KEEP_RE.match(t.GetNetname())]
print('退回网删', len(dead), '段、加', len(keep_src), '段；碎段', len(tiny), '对齐线头', n_s)
for t in dead + tiny:
    b.Remove(t)
for net, L, w, a, c in keep_src:
    t = k.PCB_TRACK(b); t.SetStart(k.VECTOR2I(*a)); t.SetEnd(k.VECTOR2I(*c)); t.SetWidth(w); t.SetLayer(L); t.SetNet(b.FindNet(net)); b.Add(t)
assert k.SaveBoard(dst, b)
