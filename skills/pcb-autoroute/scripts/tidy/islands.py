"""每层铺铜孤岛：同一块板按"总删孤岛"和"从不删"各填一次，差出来的就是孤岛。用法：islands.py 板 [板2 ...]（KiCad Python）"""
import sys, pcbnew as k
def fill(path, mode):
    b = k.LoadBoard(path)
    for z in b.Zones():
        if not z.GetIsRuleArea():
            z.SetIslandRemovalMode(mode)
    k.ZONE_FILLER(b).Fill(b.Zones())
    out = {}
    for z in b.Zones():
        if z.GetIsRuleArea(): continue
        for L in z.GetLayerSet().Seq():
            ps = z.GetFilledPolysList(L)
            key = (z.GetNetname(), b.GetLayerName(L))
            out.setdefault(key, k.SHAPE_POLY_SET()).BooleanAdd(ps)
    return out
for path in sys.argv[1:]:
    a = fill(path, k.ISLAND_REMOVAL_MODE_ALWAYS); n = fill(path, k.ISLAND_REMOVAL_MODE_NEVER)
    tot = 0
    print(path.split('/')[-1])
    for key in sorted(n):
        d = k.SHAPE_POLY_SET(n[key]); d.BooleanSubtract(a.get(key, k.SHAPE_POLY_SET()))
        area = d.Area() / 1e12; tot += area
        big = []
        for i in range(d.OutlineCount()):
            o = k.SHAPE_POLY_SET(); o.AddOutline(d.Outline(i)); ar = o.Area() / 1e12
            if ar > 1.0:
                c = d.Outline(i).Centre() if hasattr(d.Outline(i), 'Centre') else d.Outline(i).BBox().Centre()
                big.append((round(ar, 1), round(k.ToMM(c.x), 1), round(k.ToMM(c.y), 1)))
        print(f'  {key[0]}@{key[1]} 孤岛 {area:.1f} mm²  有效 {a.get(key, k.SHAPE_POLY_SET()).Area() / 1e12:.1f}  >1mm²: {sorted(big, reverse=True)[:6]}')
    print('  孤岛合计', round(tot, 1))
