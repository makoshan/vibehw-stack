"""把新板上指定 (网,层) 的全部走线换回源板该 (网,层) 的走线，重铺铜存盘。用法：revert_layers.py 源板 新板 blame.json 输出板（KiCad Python）"""
import sys, json, pcbnew as k
src, cur = k.LoadBoard(sys.argv[1]), k.LoadBoard(sys.argv[2]); bl = {tuple(x) for x in json.load(open(sys.argv[3]))}
ln = lambda b, t: (t.GetNetname(), b.GetLayerName(t.GetLayer()))
dead = [t for t in cur.GetTracks() if t.GetClass() == 'PCB_TRACK' and ln(cur, t) in bl]
for t in dead: cur.Remove(t)
n = 0
for t in src.GetTracks():
    if t.GetClass() == 'PCB_TRACK' and ln(src, t) in bl:
        c = k.PCB_TRACK(cur); c.SetStart(t.GetStart()); c.SetEnd(t.GetEnd()); c.SetWidth(t.GetWidth()); c.SetLayer(t.GetLayer())
        c.SetNet(cur.FindNet(t.GetNetname())); cur.Add(c); n += 1
k.ZONE_FILLER(cur).Fill(cur.Zones()); cur.Save(sys.argv[4])
print('删', len(dead), '换回', n)
