"""把 router.py 的结果（删/加 线和过孔）写回 KiCad 板并重铺铜：KiCad Python 跑 apply2.py 源板 结果.json 输出板"""
import sys, json, pcbnew as k
b = k.LoadBoard(sys.argv[1]); r = json.load(open(sys.argv[2]))
gone = set(r['removed']) | set(r.get('removed_vias', []))
dead = [t for t in b.GetTracks() if t.m_Uuid.AsString() in gone]
assert len(dead) == len(gone), (len(dead), len(gone))
for t in dead:
    b.Remove(t)
lid = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
for s in r['added']:
    t = k.PCB_TRACK(b); t.SetStart(k.VECTOR2I(*s['a'])); t.SetEnd(k.VECTOR2I(*s['b'])); t.SetWidth(s['w'])
    t.SetLayer(lid[s['layer']]); t.SetNet(b.FindNet(s['net'])); assert t.GetNetname() == s['net']; b.Add(t)
for s in r.get('added_vias', []):
    v = k.PCB_VIA(b); v.SetPosition(k.VECTOR2I(*s['p'])); v.SetWidth(s['d']); v.SetDrill(s['drill'])
    v.SetLayerPair(k.F_Cu, k.B_Cu); v.SetNet(b.FindNet(s['net'])); assert v.GetNetname() == s['net']; b.Add(v)
k.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[3])
print('删', len(dead), '加线', len(r['added']), '加孔', len(r.get('added_vias', [])))
