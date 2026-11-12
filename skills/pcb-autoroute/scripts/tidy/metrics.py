import sys, math, collections, pcbnew as k
for f in sys.argv[1:]:
    b=k.LoadBoard(f); segs=[t for t in b.GetTracks() if t.GetClass()=='PCB_TRACK']
    L=[k.ToMM(t.GetLength()) for t in segs]
    bends=sum(1 for t in segs)  # 段数即拐点数+网数量级
    odd=sum(1 for t in segs if round(math.degrees(math.atan2((t.GetEnd()-t.GetStart()).y,(t.GetEnd()-t.GetStart()).x)))%45)
    pads={(fp.GetReference(),p.GetNumber()):p.GetNetname() for fp in b.GetFootprints() for p in fp.Pads()}
    print(f.split('/')[-1], '段', len(segs), '短于0.1', sum(l<0.1 for l in L), '短于0.25', sum(l<0.25 for l in L), '总长', round(sum(L)), '非45°', odd, '过孔', len(b.GetTracks())-len(segs), '焊盘网hash', hash(tuple(sorted(pads.items()))))
