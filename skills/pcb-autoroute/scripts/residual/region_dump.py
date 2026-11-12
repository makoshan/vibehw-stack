# KiCad python：导出板上全部铜（走线/过孔/焊盘多边形）+ 板边 → JSON（mm）
import pcbnew as k,json,sys
b=k.LoadBoard(sys.argv[1]); CU=[l for l in b.GetEnabledLayers().CuStack()]; LN={l:b.GetLayerName(l) for l in CU}
out={"tracks":[],"vias":[],"pads":[],"edge":[]}
for t in b.GetTracks():
    if t.GetClass()=="PCB_VIA":
        p=t.GetPosition(); out["vias"].append({"net":t.GetNetname(),"x":p.x/1e6,"y":p.y/1e6,"d":t.GetWidth(k.F_Cu)/1e6,"lock":t.IsLocked()})
    else:
        s,e=t.GetStart(),t.GetEnd(); out["tracks"].append({"net":t.GetNetname(),"layer":LN[t.GetLayer()],"a":[s.x/1e6,s.y/1e6],"b":[e.x/1e6,e.y/1e6],"w":t.GetWidth()/1e6,"lock":t.IsLocked()})
for f in b.GetFootprints():
    for p in f.Pads():
        for l in CU:
            if not p.IsOnLayer(l): continue
            ps=k.SHAPE_POLY_SET(); p.TransformShapeToPolygon(ps,l,0,5000,k.ERROR_INSIDE)
            for i in range(ps.OutlineCount()):
                o=ps.Outline(i); out["pads"].append({"ref":f"{f.GetReference()}.{p.GetNumber()}","net":p.GetNetname(),"layer":LN[l],"poly":[[o.CPoint(j).x/1e6,o.CPoint(j).y/1e6] for j in range(o.PointCount())]})
for d in b.GetDrawings():
    if d.GetLayer()==k.Edge_Cuts and d.GetShape()==k.SHAPE_T_SEGMENT:
        s,e=d.GetStart(),d.GetEnd(); out["edge"].append([s.x/1e6,s.y/1e6,e.x/1e6,e.y/1e6])
json.dump(out,open(sys.argv[2],"w")); print({k2:len(v) for k2,v in out.items()})
