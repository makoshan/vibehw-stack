"""按位置比锐角/折回（T 形接点：端点落在别的线中间，也按两个方向算）：源板没有、新板才有的锐角点（同层同网同点）。用法：joints.py 源板 新板"""
import sys, math, itertools, collections, pcbnew as k
def pt(v): return (round(k.ToMM(v.x), 4), round(k.ToMM(v.y), 4))
def joints(bd):
    ends = collections.defaultdict(list)
    for t in bd.GetTracks():
        if t.GetClass() != 'PCB_TRACK': continue
        a, b = pt(t.GetStart()), pt(t.GetEnd())
        if a == b: continue
        ends[(bd.GetLayerName(t.GetLayer()), t.GetNetname(), a)].append(b); ends[(bd.GetLayerName(t.GetLayer()), t.GetNetname(), b)].append(a)
    # T 形接点：某端点落在同层同网另一段线中间，把那段线的两个方向也算进这个点
    segs = collections.defaultdict(list)
    for t in bd.GetTracks():
        if t.GetClass() == 'PCB_TRACK' and t.GetStart() != t.GetEnd():
            segs[(bd.GetLayerName(t.GetLayer()), t.GetNetname())].append((pt(t.GetStart()), pt(t.GetEnd())))
    for (L, n, p) in list(ends):
        for a, b in segs[(L, n)]:
            if p in (a, b): continue
            ab = (b[0]-a[0], b[1]-a[1]); ap = (p[0]-a[0], p[1]-a[1]); l2 = ab[0]**2 + ab[1]**2
            s_ = (ap[0]*ab[0] + ap[1]*ab[1]) / l2
            if 0 < s_ < 1 and abs(ab[0]*ap[1] - ab[1]*ap[0]) / l2**.5 < 1e-3:
                ends[(L, n, p)] += [a, b]
    res = {}
    for key, os in ends.items():
        p = key[2]; m = 180
        for u, v in itertools.combinations(set(os), 2):
            u2 = (u[0]-p[0], u[1]-p[1]); v2 = (v[0]-p[0], v[1]-p[1])
            c = (u2[0]*v2[0]+u2[1]*v2[1]) / math.hypot(*u2) / math.hypot(*v2)
            m = min(m, math.degrees(math.acos(max(-1, min(1, c)))))
        if m < 89.9: res[key] = round(m, 1)
    return res
A, B = joints(k.LoadBoard(sys.argv[1])), joints(k.LoadBoard(sys.argv[2]))
new = {x: v for x, v in B.items() if x not in A}
print('锐角/折回点 源', len(A), '新', len(B), '新板才有', len(new))
for x, v in sorted(new.items(), key=lambda z: z[1]): print('  ', x, v)
