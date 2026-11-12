"""盘中孔门：过孔真实穿过某铜层、焊盘也在该层、且焊盘几何覆盖到过孔盘 → 记一次（同网也算）。
KiCad DRC 不报同网盘中孔，自写/外部布线器常借它「连通」。有压盘退出 1。
用法：KiCad 自带 python via_in_pad.py 板1.kicad_pcb [板2 ...]"""
import pcbnew as k, sys
bad = 0
for p in sys.argv[1:]:
    b = k.LoadBoard(p); hits = []
    pads = [(f.GetReference(), pd) for f in b.GetFootprints() for pd in f.Pads()]
    for v in b.GetTracks():
        if v.GetClass() != "PCB_VIA": continue
        for ref, pd in pads:
            for L in (k.F_Cu, k.B_Cu):
                if pd.IsOnLayer(L) and v.IsOnLayer(L) and pd.HitTest(v.GetPosition(), int(v.GetWidth(k.F_Cu) / 2)):
                    hits.append((v.GetNetname(), round(v.GetPosition().x / 1e6, 3), round(v.GetPosition().y / 1e6, 3), f"{ref}.{pd.GetNumber()}", pd.GetNetname(), b.GetLayerName(L)))
    print(p.split("/")[-1], "盘中孔", len(hits))
    for h in hits: print("  ", h)
    bad += len(hits)
sys.exit(1 if bad else 0)
