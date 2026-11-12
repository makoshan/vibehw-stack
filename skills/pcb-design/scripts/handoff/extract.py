"""用 KiCad 自带 Python 把走线、过孔、焊盘、板框、禁布区导成 JSON（单位 nm）。"""
import sys, json, pcbnew as k
b = k.LoadBoard(sys.argv[1]); out = {'tracks': [], 'vias': [], 'pads': [], 'holes': [], 'edge': [], 'keepouts': [], 'fpcopper': []}
CU = [l for l in b.GetEnabledLayers().CuStack()]
LN = {l: b.GetLayerName(l) for l in CU}
def poly(ps):
    res = []
    for i in range(ps.OutlineCount()):
        o = ps.Outline(i); res.append([[o.CPoint(j).x, o.CPoint(j).y] for j in range(o.PointCount())])
    return res
for t in b.GetTracks():
    c = t.GetClass()
    if c == 'PCB_TRACK':
        out['tracks'].append({'id': t.m_Uuid.AsString(), 'net': t.GetNetname(), 'layer': LN[t.GetLayer()],
            'a': [t.GetStart().x, t.GetStart().y], 'b': [t.GetEnd().x, t.GetEnd().y], 'w': t.GetWidth()})
    elif c == 'PCB_VIA':
        out['vias'].append({'id': t.m_Uuid.AsString(), 'net': t.GetNetname(), 'p': [t.GetPosition().x, t.GetPosition().y],
            'd': t.GetWidth(k.F_Cu), 'drill': t.GetDrillValue()})
    else:
        raise SystemExit('未处理的走线类型 ' + c)
for f in b.GetFootprints():
    for p in f.Pads():
        ls = [LN[l] for l in CU if p.IsOnLayer(l)]
        polys = {}
        for l in CU:
            if p.IsOnLayer(l):
                ps = k.SHAPE_POLY_SET(); p.TransformShapeToPolygon(ps, l, 0, 5000, k.ERROR_INSIDE); polys[LN[l]] = poly(ps)
        out['pads'].append({'ref': f.GetReference(), 'num': p.GetNumber(), 'net': p.GetNetname(), 'polys': polys})
        if p.HasHole():
            ds = p.GetDrillSize(); out['holes'].append({'p': [p.GetPosition().x, p.GetPosition().y], 'r': max(ds.x, ds.y) // 2})
    for g in f.GraphicalItems():
        if g.GetLayer() in CU:
            ps = k.SHAPE_POLY_SET(); g.TransformShapeToPolygon(ps, g.GetLayer(), 0, 5000, k.ERROR_INSIDE)
            out['fpcopper'].append({'net': '', 'layer': LN[g.GetLayer()], 'polys': poly(ps)})
ps = k.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(ps, False); out['edge'] = poly(ps) + [[[ps.Hole(i, h).CPoint(j).x, ps.Hole(i, h).CPoint(j).y] for j in range(ps.Hole(i, h).PointCount())] for i in range(ps.OutlineCount()) for h in range(ps.HoleCount(i))]   # 内孔（电池窗）也算板边，漏了新线会贴上去
for z in list(b.Zones()) + [z for f in b.GetFootprints() for z in f.Zones()]:
    if z.GetIsRuleArea() and (z.GetDoNotAllowTracks() or z.GetDoNotAllowVias()):
        out['keepouts'].append({'layers': [LN[l] for l in CU if z.IsOnLayer(l)], 'polys': poly(z.Outline()),
                                'no_tracks': bool(z.GetDoNotAllowTracks()), 'no_vias': bool(z.GetDoNotAllowVias())})
for d in b.Drawings():
    if d.GetLayer() in CU:
        ps = k.SHAPE_POLY_SET(); d.TransformShapeToPolygon(ps, d.GetLayer(), 0, 5000, k.ERROR_INSIDE)
        out['fpcopper'].append({'net': '', 'layer': LN[d.GetLayer()], 'polys': poly(ps)})
json.dump(out, open(sys.argv[2], 'w'))
print({k2: len(v) for k2, v in out.items()})
