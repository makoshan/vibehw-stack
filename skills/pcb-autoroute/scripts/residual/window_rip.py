# 局部开窗：剪掉窗内的未锁定走线（只剪窗内那截，窗外保留）和窗内未锁定过孔。保护网（射频/CC/晶振/电源）不动。
# 用法: window_rip.py 源 目标 x0 x1 y0 y1   （一进程：先快照、算好，最后统一增删）
import pcbnew as k,sys,shutil
src,dst=sys.argv[1:3]; X0,X1,Y0,Y1=map(float,sys.argv[3:7])
KEEP={"GND","VBAT","VBAT_SENSE","VBUS","3V3","3V3_AFE","AVDD_SENSE","MCU_DCC","MCU_DCC_L","MCU_DEC1","MCU_DEC2","MCU_DEC3","MCU_DEC4_6","MCU_DEC5","MCU_DECUSB",
      "RF_ANT","ANT_FEED","CC1","CC2","MCU_XL1","MCU_XL2","MCU_XC1","MCU_XC2"}
b=k.LoadBoard(src)
def clip(x1,y1,x2,y2):
    # Liang-Barsky：返回线段在窗内的参数区间 (t0,t1)，不相交返回 None
    t0,t1=0.0,1.0; dx,dy=x2-x1,y2-y1
    for p,q in((-dx,x1-X0),(dx,X1-x1),(-dy,y1-Y0),(dy,Y1-y1)):
        if p==0:
            if q<0: return None
        else:
            r=q/p
            if p<0: t0=max(t0,r)
            else: t1=min(t1,r)
    return (t0,t1) if t0<t1 else None
snap=list(b.GetTracks()); rm=[]; add=[]; nets=set()
for t in snap:
    if t.IsLocked() or t.GetNetname() in KEEP: continue
    if t.GetClass()=="PCB_VIA":
        p=t.GetPosition()
        if X0<=p.x/1e6<=X1 and Y0<=p.y/1e6<=Y1: rm.append(t); nets.add(t.GetNetname())
        continue
    s,e=t.GetStart(),t.GetEnd(); x1,y1,x2,y2=s.x/1e6,s.y/1e6,e.x/1e6,e.y/1e6
    c=clip(x1,y1,x2,y2)
    if not c: continue
    rm.append(t); nets.add(t.GetNetname())
    for a,bb in((0.0,c[0]),(c[1],1.0)):          # 窗外剩下的两截
        if bb-a>1e-6 and ((x2-x1)**2+(y2-y1)**2)**.5*(bb-a)>0.01:
            add.append((t.GetNetCode(),t.GetLayer(),t.GetWidth(),x1+(x2-x1)*a,y1+(y2-y1)*a,x1+(x2-x1)*bb,y1+(y2-y1)*bb))
for t in rm: b.Remove(t)
for net,L,w,ax,ay,bx,by in add:
    t=k.PCB_TRACK(b); t.SetStart(k.VECTOR2I(k.FromMM(ax),k.FromMM(ay))); t.SetEnd(k.VECTOR2I(k.FromMM(bx),k.FromMM(by)))
    t.SetWidth(w); t.SetLayer(L); t.SetNetCode(net); b.Add(t)
k.ZONE_FILLER(b).Fill(b.Zones()); k.SaveBoard(dst,b)
for e in(".kicad_pro",".kicad_dru"): shutil.copyfile(src.replace(".kicad_pcb",e),dst.replace(".kicad_pcb",e))
print("剪",len(rm),"保留窗外截",len(add),"涉及网",sorted(nets))
