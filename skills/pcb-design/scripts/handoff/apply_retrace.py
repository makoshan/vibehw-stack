"""把 octi_retrace 的结果写回板子：改过的网整网换线，孔和件不动，铺铜留给 kicad-cli 重铺。用法：KPY apply_retrace.py 源板 结果.json 输出板"""
import json, sys, pcbnew as k
b = k.LoadBoard(sys.argv[1]); r = json.load(open(sys.argv[2]))
LN = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
nets = {t['net'] for t in r['tracks']} - set(r['skip'])
dead = [t for t in b.GetTracks() if t.GetClass() == 'PCB_TRACK' and t.GetNetname() not in r['skip']]
for t in dead:
    b.Remove(t)
nc = b.GetNetsByName()
n = 0
for t in r['tracks']:
    if t['net'] in r['skip']:
        continue
    s = k.PCB_TRACK(b); s.SetStart(k.VECTOR2I(*t['a'])); s.SetEnd(k.VECTOR2I(*t['b'])); s.SetWidth(t['w']); s.SetLayer(LN[t['layer']])
    s.SetNet(nc[t['net']]); b.Add(s); n += 1
k.SaveBoard(sys.argv[3], b); print('写回', n)
