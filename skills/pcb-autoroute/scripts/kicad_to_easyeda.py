#!/usr/bin/env python3
"""KiCad 板导进嘉立创EDA 后，把导入丢掉的东西补回去：焊盘网络、过孔、铺铜网络、走线、设计规则。

为什么要这个：嘉立创「导入KiCad」(KiCad 10, 2026-10) 只带回器件、过孔位置和铺铜轮廓，
走线一条不剩，焊盘/过孔/铺铜的网络全是空的。本脚本按 .kicad_pcb 逐项写回，再读回比对。

用法：python3 kicad_to_easyeda.py board.kicad_pcb --port 49625 --pcb <嘉立创 PCB 文档 uuid>
前提：工程已用嘉立创「导入KiCad」导入（或 sys_FileManager.importProjectByProjectFile），桥接已连上。
坐标：嘉立创画布单位 mil，y 轴与 KiCad 相反（x_mil = x_mm/0.0254，y_mil = -y_mm/0.0254）。
"""
import argparse, json, math, re, sys, time, urllib.request

MIL = 0.0254
LAYER = {"F.Cu": 1, "B.Cu": 2, "In1.Cu": 15, "In2.Cu": 16}  # 嘉立创 EPCB_LayerId：TOP 1 / BOTTOM 2 / INNER_1 15 / INNER_2 16


# ---------- KiCad 解析（s 表达式，不依赖 pcbnew） ----------
def parse_sexpr(s):
    tok = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', s)
    st = [[]]
    for t in tok:
        if t == "(":
            st.append([])
        elif t == ")":
            x = st.pop(); st[-1].append(x)
        else:
            st[-1].append(t[1:-1] if t.startswith('"') else t)
    return st[0][0]


def kids(n, k):
    return [c for c in n if isinstance(c, list) and c and c[0] == k]


def netname(n, nets):
    v = kids(n, "net")
    if not v:
        return ""
    v = v[0][1]
    return nets.get(int(v), "") if v.isdigit() else v  # KiCad 10 写 (net "名")，旧版写编号


def read_board(path):
    doc = parse_sexpr(open(path, encoding="utf-8").read())
    nets = {int(c[1]): c[2] for c in kids(doc, "net") if len(c) >= 3 and c[1].isdigit()}
    pads, segs, vias, zones = {}, [], [], []
    for fp in kids(doc, "footprint"):
        ref = next((p[2] for p in kids(fp, "property") if p[1] == "Reference"), None)
        if not ref:
            continue
        for p in kids(fp, "pad"):
            n = netname(p, nets)
            if n:
                pads.setdefault(ref, {})[str(p[1])] = n
    for s in kids(doc, "segment"):
        a, b = kids(s, "start")[0], kids(s, "end")[0]
        segs.append(dict(net=netname(s, nets), layer=kids(s, "layer")[0][1], x1=float(a[1]), y1=float(a[2]),
                         x2=float(b[1]), y2=float(b[2]), w=float(kids(s, "width")[0][1])))
    if kids(doc, "arc"):
        sys.exit("板上有圆弧走线，本脚本还没实现 arc（pcb_PrimitiveArc.create），先停下")
    for v in kids(doc, "via"):
        at = kids(v, "at")[0]
        vias.append(dict(net=netname(v, nets), x=float(at[1]), y=float(at[2]),
                         size=float(kids(v, "size")[0][1]), drill=float(kids(v, "drill")[0][1])))
    for z in kids(doc, "zone"):
        if kids(z, "keepout"):
            continue
        ly = kids(z, "layer") or kids(z, "layers")
        pts = [(float(p[1]), float(p[2])) for poly in kids(z, "polygon") for pl in kids(poly, "pts") for p in kids(pl, "xy")]
        zones.append(dict(net=netname(z, nets) or (kids(z, "net_name")[0][1] if kids(z, "net_name") else ""),
                          layer=ly[0][1], bbox=[min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts)]))
    return pads, segs, vias, zones


def read_rules(dru_path, pro_path):
    r = dict(track=0.10, clearance=0.10, via_od=0.45, via_hole=0.30, hole_clear=0.20, edge=0.20, hole_hole=0.20)
    if dru_path:
        t = open(dru_path, encoding="utf-8").read()
        for key, pat in (("track", r"track_width \(min ([\d.]+)mm\)"), ("clearance", r"\(constraint clearance \(min ([\d.]+)mm\)"),
                         ("via_od", r"via_diameter \(min ([\d.]+)mm\)"), ("via_hole", r"hole_size \(min ([\d.]+)mm\)")):
            m = re.search(pat, t)
            if m:
                r[key] = float(m.group(1))
    if pro_path:
        d = json.load(open(pro_path))["board"]["design_settings"]["rules"]
        r["hole_clear"] = d.get("min_hole_clearance", r["hole_clear"])
        r["edge"] = d.get("min_copper_edge_clearance", r["edge"])
        r["hole_hole"] = d.get("min_hole_to_hole", r["hole_hole"])
    return r


# ---------- 桥接 ----------
class Bridge:
    def __init__(self, port):
        self.url = f"http://127.0.0.1:{port}/execute"

    def run(self, code, timeout=60):
        req = urllib.request.Request(self.url, data=json.dumps({"code": code}).encode(), headers={"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=timeout))
        if not r.get("success"):
            raise RuntimeError(r.get("error"))
        return r.get("result")

    def run_async(self, body, key, poll=5, limit=3600):
        """桥接单次调用 30 s 就超时：长任务在 EDA 里后台跑，进度写 globalThis[key]，这里轮询。"""
        self.run(f"globalThis.{key}={{state:'running'}};(async()=>{{{body}}})()"
                 f".then(r=>globalThis.{key}={{state:'done',r}}).catch(e=>globalThis.{key}={{state:'error',e:String(e)}});return 'fired'")
        t0 = time.time()
        while time.time() - t0 < limit:
            time.sleep(poll)
            s = self.run(f"return globalThis.{key}")
            if s and s.get("state") != "running":
                if s["state"] == "error":
                    raise RuntimeError(s.get("e"))
                return s.get("r")
            if s and s.get("p"):
                print("   ", key, s["p"], flush=True)
        raise TimeoutError(key)


def mil(x):
    return round(x / MIL, 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board"); ap.add_argument("--port", type=int, default=49620); ap.add_argument("--pcb", required=True)
    ap.add_argument("--dru"); ap.add_argument("--pro"); ap.add_argument("--chunk", type=int, default=800)
    ap.add_argument("--steps", default="1234567", help="只跑这些步，例如 127：规则、焊盘、读回（建线建孔不能重跑，会重复）")
    a = ap.parse_args()
    pads, segs, vias, zones = read_board(a.board)
    rules = read_rules(a.dru, a.pro)
    print(f"KiCad：器件 {len(pads)}，带网焊盘 {sum(len(v) for v in pads.values())}，线段 {len(segs)}，过孔 {len(vias)}，铺铜 {len(zones)}；规则 {rules}")
    B = Bridge(a.port)
    B.run(f"await eda.dmt_EditorControl.openDocument('{a.pcb}'); await new Promise(r=>setTimeout(r,4000)); return 1")

    if "1" in a.steps:
        # 1 规则：外径/孔径、线宽、间距矩阵（铜到铜 = clearance，孔行 = 孔到铜，板框 = edge）
        print("1 规则", B.run(f"""
    const rc=await eda.pcb_Drc.getCurrentRuleConfiguration(); const c=rc.config;
    const vs=c.Physics['Via Size'].viaSize.form; vs.viaOuterdiameterMin={rules['via_od']}; vs.viaOuterdiameterDefault={rules['via_od']}; vs.viaInnerdiameterMin={rules['via_hole']}; vs.viaInnerdiameterDefault={rules['via_hole']};
    const tr=c.Physics.Track.copperThickness1oz.form.data['1']; tr.minValue={rules['track']}; tr.defaultValue={rules['track']};
    const t=c.Spacing['Safe Spacing'].copperThickness1oz; const C=t.tables['1'].content;
    for(let i=0;i<C.length;i++) for(let j=0;j<C[i].length;j++){{ const r=t.row[i],k=t.column[j]; let v={rules['clearance']};
      if(r==='Hole') v={rules['hole_clear']}; if(r==='Board Outline'||k==='Board Outline') v={rules['edge']}; C[i][j]=v; }}
    const os=c.Spacing['Other Spacing']; let hh=0; (function walk(o){{ if(!o||typeof o!=='object') return; for(const [k,v] of Object.entries(o)){{ if(/hole.?(to|2).?hole/i.test(k)&&typeof v==='number'){{ o[k]={rules['hole_hole']}; hh++; }} else walk(v); }} }})(os);  // 孔到孔在「其它间距」表，默认 0.30
    const ok=await eda.pcb_Drc.overwriteCurrentRuleConfiguration(c);
    const b=(await eda.pcb_Drc.getCurrentRuleConfiguration()).config;   // 读回，写进去不等于生效
    return {{ok,hh,os:JSON.stringify((await eda.pcb_Drc.getCurrentRuleConfiguration()).config.Spacing['Other Spacing']).slice(0,400),od:b.Physics['Via Size'].viaSize.form.viaOuterdiameterMin,hole:b.Physics['Via Size'].viaSize.form.viaInnerdiameterMin,cl:b.Spacing['Safe Spacing'].copperThickness1oz.tables['1'].content[0][0]}};"""))

    if "2" in a.steps:
        # 2 焊盘网络：按 位号 + 脚号
        r = B.run_async(f"""
    const M={json.dumps(pads)}; const miss=[]; let n=0;
    const comps=await eda.pcb_PrimitiveComponent.getAll(); const by={{}}; for(const c of comps) by[c.getState_Designator()]=c;
    for(const [ref,pp] of Object.entries(M)){{ const c=by[ref]; if(!c){{miss.push(ref);continue;}}
      const pins=await eda.pcb_PrimitiveComponent.getAllPinsByPrimitiveId(c.getState_PrimitiveId()); const no={{}}; for(const p of pins){{ const k=String(p.getState_PadNumber()); (no[k]=no[k]||[]).push(p); }}  // 同号多块焊盘（散热焊盘分块、固定脚）每块都要写
      for(const [k,net] of Object.entries(pp)){{ const ps=no[k]; if(!ps){{miss.push(ref+'.'+k);continue;}} for(const p of ps){{ await eda.pcb_PrimitivePad.modify(p.getState_PrimitiveId(),{{net}}); n++; }} globalThis.__k2j_pad.p=n; }} }}
    return {{n,miss}};""", "__k2j_pad")
        print("2 焊盘网络", r["n"], "缺", r["miss"][:10])
        if r["miss"]:
            sys.exit("有焊盘对不上，停下")

    if "3" in a.steps:
        # 3 过孔：删掉导入的（无网络），按 KiCad 重建（网络、外径、孔径）
        r = B.run_async(f"""
    const V={json.dumps([[mil(v['x']),mil(-v['y']),v['net'],mil(v['drill']),mil(v['size'])] for v in vias])};
    const old=await eda.pcb_PrimitiveVia.getAll(); for(const v of old) await eda.pcb_PrimitiveVia.delete(v.getState_PrimitiveId());
    let n=0; for(const [x,y,net,h,d] of V){{ const o=await eda.pcb_PrimitiveVia.create(net,x,y,h,d); if(o) n++; globalThis.__k2j_via.p=n; }}
    return {{deleted:old.length,created:n}};""", "__k2j_via")
        print("3 过孔", r)
        if r["created"] != len(vias):
            sys.exit("过孔没建全，停下")

    if "4" in a.steps:
        # 4 铺铜网络：同层按外框最近的一块配对
        jz = B.run("""const out=[]; for(const q of await eda.pcb_PrimitivePour.getAll()){ const P=q.getState_ComplexPolygon().polygon; const xs=[],ys=[];
    for(let i=0;i<P.length;i++) if(typeof P[i]==='number'&&typeof P[i+1]==='number'){xs.push(P[i]*0.0254);ys.push(-P[i+1]*0.0254);i++;}
    out.push({id:q.getState_PrimitiveId(),layer:q.getState_Layer(),bbox:[Math.min(...xs),Math.max(...xs),Math.min(...ys),Math.max(...ys)]});} return out;""")
        assign = {}
        for z in zones:
            cand = [j for j in jz if j["layer"] == LAYER[z["layer"]] and j["id"] not in assign]
            if not cand:
                sys.exit(f"铺铜 {z} 在嘉立创里找不到同层的")
            best = min(cand, key=lambda j: sum(abs(j["bbox"][i] - z["bbox"][i]) for i in range(4)))
            assign[best["id"]] = z["net"]
        # 嘉立创导入时会把一块 KiCad 铺铜拆成两块（外框差约 0.15 mm 的重影）：剩下的按同层最近外框配网，差太大就停
        for j in jz:
            if j["id"] in assign:
                continue
            same = [z for z in zones if LAYER[z["layer"]] == j["layer"]]
            z = min(same, key=lambda z: sum(abs(j["bbox"][i] - z["bbox"][i]) for i in range(4)))
            dev = sum(abs(j["bbox"][i] - z["bbox"][i]) for i in range(4))
            if dev > 1.0:
                sys.exit(f"嘉立创多出一块铺铜 {j} 对不上 KiCad（外框差 {dev:.2f} mm），停下")
            assign[j["id"]] = z["net"]
        print("4 铺铜", B.run(f"""const M={json.dumps(assign)}; const r={{}};
    for(const [id,net] of Object.entries(M)) r[id]=!!(await eda.pcb_PrimitivePour.modify(id,{{net}}));
    const back={{}}; for(const q of await eda.pcb_PrimitivePour.getAll()) back[q.getState_PrimitiveId()]=q.getState_Net(); return {{r,back}};"""))

    if "5" in a.steps:
        # 5 走线：分块送进 EDA，再在 EDA 里逐段建
        rows = [[s["net"], LAYER[s["layer"]], mil(s["x1"]), mil(-s["y1"]), mil(s["x2"]), mil(-s["y2"]), mil(s["w"])] for s in segs]
        B.run("globalThis.__k2j_rows=[]; return 1")
        for i in range(0, len(rows), a.chunk):
            B.run(f"globalThis.__k2j_rows.push(...{json.dumps(rows[i:i + a.chunk])}); return globalThis.__k2j_rows.length")
        r = B.run_async("""const R=globalThis.__k2j_rows; let n=0; const bad=[];
    for(const [net,ly,x1,y1,x2,y2,w] of R){ const o=await eda.pcb_PrimitiveLine.create(net,ly,x1,y1,x2,y2,w); if(o) n++; else bad.push([net,ly,x1,y1]); if(n%500===0) globalThis.__k2j_line.p=n; }
    return {n,bad:bad.slice(0,10),nbad:bad.length};""", "__k2j_line", poll=10)
        print("5 走线", r)

    if "6" in a.steps:
        # 6 重铺铜、保存
        print("6 重铺", B.run_async("""const r={}; for(const q of await eda.pcb_PrimitivePour.getAll()){ r[q.getState_PourName()]=!!(await q.rebuildCopperRegion()); }
    return {r,saved:await eda.pcb_Document.save()};""", "__k2j_pour"))

    if "7" in a.steps:
        # 7 读回比对。不按段数比：嘉立创会在 T 形接头/过孔处自动断线，也会合并重叠段并吸附约 0.025 mm（实测 8771 段建入、读回 8200 段）。
        #   改成覆盖采样：KiCad 每段每 0.05 mm 一个点，必须落在嘉立创同网同层某段线 0.03 mm 内（线宽 0.10，0.03 内铜仍相连）。
        got = B.run_async("""const out=[]; for(const l of await eda.pcb_PrimitiveLine.getAll()) out.push([l.getState_Net(),l.getState_Layer(),l.getState_StartX(),l.getState_StartY(),l.getState_EndX(),l.getState_EndY()]);
    const vn={}; for(const v of await eda.pcb_PrimitiveVia.getAll()){ const n=v.getState_Net(); vn[n]=(vn[n]||0)+1; } return {lines:out,vn};""", "__k2j_rb")
        J = {}
        for n, l, x1, y1, x2, y2 in got["lines"]:
            J.setdefault((n, l), []).append((x1 * MIL, -y1 * MIL, x2 * MIL, -y2 * MIL))

        def dist(px, py, c):
            x1, y1, x2, y2 = c; dx, dy = x2 - x1, y2 - y1; L = dx * dx + dy * dy
            t = 0 if L == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / L))
            return math.hypot(px - x1 - t * dx, py - y1 - t * dy)
        miss = {}
        for sg in segs:
            cand = J.get((sg["net"], LAYER[sg["layer"]]), [])
            L = math.hypot(sg["x2"] - sg["x1"], sg["y2"] - sg["y1"]); n = max(2, int(L / 0.05) + 1)
            for i in range(n):
                t = i / (n - 1); px = sg["x1"] + t * (sg["x2"] - sg["x1"]); py = sg["y1"] + t * (sg["y2"] - sg["y1"])
                if not cand or min(dist(px, py, c) for c in cand) > 0.03:
                    miss[(sg["net"], sg["layer"])] = miss.get((sg["net"], sg["layer"]), 0) + 1
        vw = {}
        for v in vias:
            vw[v["net"]] = vw.get(v["net"], 0) + 1
        vdiff = {k: (vw.get(k), got["vn"].get(k)) for k in set(vw) | set(got["vn"]) if vw.get(k) != got["vn"].get(k)}
        print(f"7 读回：嘉立创 {len(got['lines'])} 段；KiCad 走线未被覆盖的采样点 {sum(miss.values())} {miss}；过孔按网不一致 {vdiff}")
        ok = not miss and not vdiff
        print("结论：", "走线/过孔全部覆盖、逐网一致" if ok else "有缺口，看上面")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
