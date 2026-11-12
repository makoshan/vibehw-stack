"""把 router.py / fix_exits.py 的结果（删/加 线和过孔、挪件、转件）写回 KiCad 板并重铺铜：KiCad Python 跑 apply2.py 源板 结果.json 输出板
removed / removed_vias 里的条目既可以是 KiCad uuid，也可以是几何描述（D10 起，回放到别的机器上时新加的线 uuid 不同）：
  线 {"net","layer","a":[x,y],"b":[x,y]}（nm，端点不分先后，容差 2 µm）；过孔 {"p":[x,y]}。
moved_vias：[{"from":[x,y],"to":[x,y]}] 只挪位置，网和尺寸不变（D10）。"""
import sys, json, pcbnew as k
b = k.LoadBoard(sys.argv[1]); r = json.load(open(sys.argv[2]))
TOL = 2000
def near(P, xy): return abs(P.x - xy[0]) <= TOL and abs(P.y - xy[1]) <= TOL
def find(spec, cls):
    hit = []
    for t in b.GetTracks():
        if t.GetClass() != cls: continue
        if isinstance(spec, str):
            if t.m_Uuid.AsString() == spec: hit.append(t)
        elif cls == 'PCB_VIA':
            if near(t.GetPosition(), spec['p']): hit.append(t)
        else:
            if t.GetNetname() != spec['net'] or b.GetLayerName(t.GetLayer()) != spec['layer']: continue
            A, B = t.GetStart(), t.GetEnd()
            if (near(A, spec['a']) and near(B, spec['b'])) or (near(A, spec['b']) and near(B, spec['a'])): hit.append(t)
    assert len(hit) == 1, ('找不到或不唯一', spec, len(hit))
    return hit[0]
dead = [find(s, 'PCB_TRACK') for s in r['removed']] + [find(s, 'PCB_VIA') for s in r.get('removed_vias', [])]
assert len({t.m_Uuid.AsString() for t in dead}) == len(dead), '重复删除'
for t in dead:
    b.Remove(t)
for m in r.get('moved_vias', []):
    v = find({'p': m['from']}, 'PCB_VIA'); v.SetPosition(k.VECTOR2I(*m['to']))
lid = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
for s in r['added']:
    t = k.PCB_TRACK(b); t.SetStart(k.VECTOR2I(*s['a'])); t.SetEnd(k.VECTOR2I(*s['b'])); t.SetWidth(s['w'])
    t.SetLayer(lid[s['layer']]); t.SetNet(b.FindNet(s['net'])); assert t.GetNetname() == s['net']; b.Add(t)
for s in r.get('added_vias', []):
    v = k.PCB_VIA(b); v.SetPosition(k.VECTOR2I(*s['p'])); v.SetWidth(s['d']); v.SetDrill(s['drill'])
    v.SetLayerPair(k.F_Cu, k.B_Cu); v.SetNet(b.FindNet(s['net'])); assert v.GetNetname() == s['net']; b.Add(v)
for ref, (dx, dy) in r.get('moved_parts', {}).items():          # 挪件：连焊盘一起平移
    f = b.FindFootprintByReference(ref); f.SetPosition(f.GetPosition() + k.VECTOR2I(int(dx), int(dy)))
for it in r.get('rotated_parts', []):                           # 转角电容转正：绕信号焊盘转到 0/90，再按 dx/dy 平移让通道
    f = b.FindFootprintByReference(it['ref']); keep = [p for p in f.Pads() if p.GetNetname() != 'GND'][0]
    p0 = k.VECTOR2I(keep.GetPosition().x, keep.GetPosition().y)
    f.SetOrientationDegrees(round(f.GetOrientationDegrees() / 90) * 90)
    f.SetPosition(f.GetPosition() + (p0 - keep.GetPosition()) + k.VECTOR2I(it['dx'], it['dy']))
k.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[3])
print('删', len(dead), '加线', len(r['added']), '加孔', len(r.get('added_vias', [])), '挪孔', len(r.get('moved_vias', [])), '挪件', len(r.get('moved_parts', {})), '转件', len(r.get('rotated_parts', [])))
