"""把 tidy.py 的结果写回 KiCad 板副本并重铺铜：KiCad 自带 Python 跑 apply.py 源板 tidy-out.json 输出板"""
import sys, json, pcbnew as k
b = k.LoadBoard(sys.argv[1]); r = json.load(open(sys.argv[2]))
gone = set(r['removed'])
dead = [t for t in b.GetTracks() if t.GetClass() == 'PCB_TRACK' and t.m_Uuid.AsString() in gone]
assert len(dead) == len(gone), (len(dead), len(gone))
for t in dead:
    b.Remove(t)
lid = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
for s in r['added']:
    t = k.PCB_TRACK(b)
    t.SetStart(k.VECTOR2I(*s['a'])); t.SetEnd(k.VECTOR2I(*s['b'])); t.SetWidth(s['w'])
    t.SetLayer(lid[s['layer']]); t.SetNet(b.FindNet(s['net']))
    assert t.GetNetname() == s['net']
    b.Add(t)
k.ZONE_FILLER(b).Fill(b.Zones())
b.Save(sys.argv[3])
print('删', len(dead), '加', len(r['added']))
