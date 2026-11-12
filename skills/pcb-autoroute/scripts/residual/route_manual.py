# 手工定点布线：一次加多条折线和过孔。用法 route.py 源 目标 spec.json
# spec: {"net":"X","w":0.127,"paths":[{"layer":"B.Cu","pts":[[x,y],...]}],"vias":[[x,y]]}
import pcbnew as k,sys,json,shutil
src,dst,spec=sys.argv[1:4]; S=json.load(open(spec)); b=k.LoadBoard(src); n=b.FindNet(S["net"]); w=k.FromMM(S.get("w",0.127))
for P in S["paths"]:
    L=b.GetLayerID(P["layer"])
    for (x1,y1),(x2,y2) in zip(P["pts"],P["pts"][1:]):
        t=k.PCB_TRACK(b); t.SetStart(k.VECTOR2I(k.FromMM(x1),k.FromMM(y1))); t.SetEnd(k.VECTOR2I(k.FromMM(x2),k.FromMM(y2))); t.SetWidth(w); t.SetLayer(L); t.SetNet(n); b.Add(t)
for x,y in S.get("vias",[]):
    v=k.PCB_VIA(b); v.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y))); v.SetViaType(k.VIATYPE_THROUGH); v.SetLayerPair(k.F_Cu,k.B_Cu)
    v.SetWidth(k.FromMM(0.40)); v.SetDrill(k.FromMM(0.30)); v.SetNet(n); b.Add(v)
k.ZONE_FILLER(b).Fill(b.Zones()); k.SaveBoard(dst,b)
if src!=dst:
    for e in(".kicad_pro",".kicad_dru"): shutil.copyfile(src.replace(".kicad_pcb",e),dst.replace(".kicad_pcb",e))
print("ok",S["net"])
