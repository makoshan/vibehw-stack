"""只改丝印也要证明铜没动：走线/过孔（含孔径）、焊盘（含尺寸、形状、层、孔）、铺铜区（含填充面积）各自排序后取 SHA-256，两块板逐组比。
加 --teardrops：泪滴区不算进铺铜组，单独报个数；铺铜组只比外形（泪滴会让异网铺铜多让一圈，填充面积必然变）。
用法：KiCad python copper_hash.py 旧板 新板 → 三组 hash 全同返回 0，否则 1。"""
import sys, hashlib, pcbnew as k
TD = '--teardrops' in sys.argv
args = [a for a in sys.argv[1:] if a != '--teardrops']
def groups(path):
    b = k.LoadBoard(path); M = k.ToMM
    drill = lambda t: f"{M(t.GetDrillValue()):.4f}" if t.GetClass() == 'PCB_VIA' else ''
    tr = sorted(f"{t.GetClass()}|{t.GetLayer()}|{t.GetNetname()}|{M(t.GetStart().x):.4f},{M(t.GetStart().y):.4f}|{M(t.GetEnd().x):.4f},{M(t.GetEnd().y):.4f}|{M(t.GetWidth()):.4f}|{drill(t)}" for t in b.GetTracks())
    pd = sorted(f"{f.GetReference()}|{p.GetNumber()}|{p.GetNetname()}|{M(p.GetPosition().x):.4f},{M(p.GetPosition().y):.4f}|{M(p.GetSize().x):.4f}x{M(p.GetSize().y):.4f}|{p.GetShape()}|{p.GetOrientationDegrees():.2f}|{p.GetLayerSet().FmtHex()}|{M(p.GetDrillSize().x):.4f}" for f in b.GetFootprints() for p in f.Pads())
    zs = [z for z in b.Zones() if not (TD and z.IsTeardropArea())]
    zn = sorted(f"{z.GetNetname()}|{z.GetLayerSet().FmtHex()}|{z.Outline().Area():.0f}" + ('' if TD else '|' + ','.join(f"{L}:{z.GetFilledPolysList(L).Area():.0f}" for L in z.GetLayerSet().Seq())) for z in zs)
    out = {n: (len(v), hashlib.sha256('\n'.join(v).encode()).hexdigest()[:16]) for n, v in (('tracks_vias', tr), ('pads', pd), ('zones', zn))}
    if TD: print(path.split('/')[-1], '泪滴区', sum(1 for z in b.Zones() if z.IsTeardropArea()))
    return out
a, c = groups(args[0]), groups(args[1])
for n in a: print(n, a[n], c[n], '同' if a[n] == c[n] else '不同')
sys.exit(0 if a == c else 1)
