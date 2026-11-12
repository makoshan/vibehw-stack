# 单层或少层、正方形区域大图：plot1.py geo out x0 x1 y0 y1 "层1,层2" "高亮网"
import json,sys,matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.patches import Polygon,Circle
g=json.load(open(sys.argv[1])); out=sys.argv[2]; x0,x1,y0,y1=map(float,sys.argv[3:7]); LAY=sys.argv[7].split(","); HL=[n for n in sys.argv[8].split(",") if n]
pal=["#d62728","#1f77b4","#2ca02c","#ff7f0e","#9467bd","#8c564b","#e377c2","#17becf"]
col=lambda n:pal[HL.index(n)%len(pal)] if n in HL else ("#bbbbbb" if n!="GND" else "#e4e4e4")
inb=lambda x,y:x0-1<x<x1+1 and y0-1<y<y1+1
n=len(LAY); fig,axs=plt.subplots(1,n,figsize=(6.5*n,6.5*(y1-y0)/(x1-x0)+0.8)); axs=[axs] if n==1 else axs
for ax,L in zip(axs,LAY):
    for p in g["pads"]:
        if p["layer"]!=L or not any(inb(*q) for q in p["poly"]): continue
        ax.add_patch(Polygon(p["poly"],closed=True,fc=col(p["net"]),ec="k",lw=.4,alpha=.85))
        cx=sum(q[0] for q in p["poly"])/len(p["poly"]); cy=sum(q[1] for q in p["poly"])/len(p["poly"])
        if x0<cx<x1 and y0<cy<y1: ax.text(cx,cy,p["ref"],fontsize=5,ha="center",va="center")
    for t in g["tracks"]:
        if t["layer"]!=L or not (inb(*t["a"]) or inb(*t["b"])): continue
        ax.plot([t["a"][0],t["b"][0]],[t["a"][1],t["b"][1]],color=col(t["net"]),lw=2.2 if t["net"] in HL else 1.4,solid_capstyle="round")
    for v in g["vias"]:
        if not inb(v["x"],v["y"]): continue
        ax.add_patch(Circle((v["x"],v["y"]),v["d"]/2,fc=col(v["net"]),ec="k",lw=.5)); ax.add_patch(Circle((v["x"],v["y"]),0.15,fc="white",ec="none"))
    for e in g["edge"]: ax.plot([e[0],e[2]],[e[1],e[3]],color="#e6b800",lw=2)
    ax.set_xlim(x0,x1); ax.set_ylim(y1,y0); ax.set_aspect("equal"); ax.set_title(L); ax.tick_params(labelsize=7); ax.grid(lw=.25)
fig.legend(handles=[plt.Line2D([0],[0],color=col(n),lw=3,label=n) for n in HL],loc="lower center",ncol=len(HL),fontsize=8)
fig.tight_layout(rect=(0,.05,1,1)); fig.savefig(out,dpi=130); print("ok")
