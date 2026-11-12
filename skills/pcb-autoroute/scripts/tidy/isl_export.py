"""导出每层孤岛多边形（总删 vs 从不删的差）到 JSON。用法：isl_export.py 板 out.json（KiCad Python）"""
import sys, json, pcbnew as k
def fill(path, mode):
    b = k.LoadBoard(path)
    for z in b.Zones():
        if not z.GetIsRuleArea(): z.SetIslandRemovalMode(mode)
    k.ZONE_FILLER(b).Fill(b.Zones())
    out = {}
    for z in b.Zones():
        if z.GetIsRuleArea(): continue
        for L in z.GetLayerSet().Seq():
            out.setdefault((z.GetNetname(), b.GetLayerName(L)), k.SHAPE_POLY_SET()).BooleanAdd(z.GetFilledPolysList(L))
    return out
a = fill(sys.argv[1], k.ISLAND_REMOVAL_MODE_ALWAYS); n = fill(sys.argv[1], k.ISLAND_REMOVAL_MODE_NEVER)
res = []
for key, ps in n.items():
    d = k.SHAPE_POLY_SET(ps); d.BooleanSubtract(a.get(key, k.SHAPE_POLY_SET()))
    for i in range(d.OutlineCount()):
        o = d.Outline(i)
        res.append({'net': key[0], 'layer': key[1], 'pts': [[o.CPoint(j).x, o.CPoint(j).y] for j in range(o.PointCount())]})
eff = {f'{k_[0]}@{k_[1]}': v.Area() / 1e12 for k_, v in a.items()}
json.dump({'islands': res, 'effective': eff}, open(sys.argv[2], 'w'))
