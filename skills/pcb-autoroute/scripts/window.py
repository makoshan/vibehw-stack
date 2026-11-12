#!/usr/bin/env python3
"""窗口精确求解：escape.py 判「无路/临界」的缺口，拆掉卡点周围一小块里别网的线和过孔，
把这块里所有被拆的网和目标网放进一个 CP-SAT 模型一起重布，写进副本由 KiCad DRC 裁决。

用法：
  uv run --no-project --with numpy --with scipy --with pillow --with ortools python -I window.py 板.kicad_pcb --net USB_DM -o 目录
      [--at x,y] [--half 1.5] [--detour 0.8] [--keep 正则] [--rip-wide] [--time 120]
  ... window.py --selftest

做法：
  1. 窗口 = 卡点（默认取 escape 收紧一格判出的被困那一端）±half mm。窗口里未锁定、线宽等于板级最小值的线
     在窗口边剪断、窗内部分删掉，中心在窗内的过孔删掉；锁定的线、更宽的线（多半是电源，--rip-wide 才拆）、
     --keep 列出的网、圆弧、焊盘、铺铜都不动。拆后新线搭在老线中段留下的悬空残桩和过孔，删掉或缩短，DRC 复查不变差才保留。
  2. 每个被拆的网：按拆之前的连通关系，把拆后断开、又伸进窗口的几块铜当终端，要求重新连上；
     目标网额外要求把缺口两端连上。目标网的远端不在窗口里时，以窗外绕得到远端的窗口边格点当出口，
     窗外那段事后用 escape 的最短路补上。
  3. grid 栅格上建图：同层 8 向走线、通孔把各层串起来；固定铜的避让沿用 escape 的 0.02 mm 精细栅格。
     不同网的格点间距 ≥ 线宽 + 间距，新孔离别网格点 ≥ 半孔径 + 间距 + 半线宽，新孔之间 ≥ 孔距要求；
     每个网用单商品流保证连通，目标最短（线长 + 每孔 via_cost）。
  4. 结果写进副本，DRC 对比原板：目标网未连变少、别的网未连没增加、铜层没有新违规 = 已证实。
  求解器说无解只表示「这个窗口、这个栅格、每网 detour 范围内」无解，不是严格证明；可放大窗口或加密栅格再试。
  新线一律用板级最小线宽，电源网被拆后的载流要另核。
"""
import argparse, json, math, re, shutil, subprocess, sys, tempfile
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

sys.dont_write_bytecode = True  # import escape 不在 mstack 里留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I 不把脚本目录放进 sys.path
import escape as E  # noqa: E402

# 在 KiCad 自带 Python 里跑：拆窗口里未锁定的线和过孔
RIP = r'''
import pcbnew as k, sys, json, re, math
b = k.LoadBoard(sys.argv[1]); x0, y0, x1, y1 = json.loads(sys.argv[3]); opt = json.loads(sys.argv[4]); MM = 1e6
keep_re = re.compile(opt["keep"]) if opt["keep"] else None
ESC = {"slash": "/", "backslash": "\\", "lt": "<", "gt": ">", "colon": ":", "dblquote": '"', "quote": "'",
       "tab": "\t", "return": "\n", "space": " "}
unesc = lambda s: re.sub(r"\{(\w+)\}", lambda m: ESC.get(m[1], m[0]), s)
def clip(ax, ay, bx, by):  # Liang-Barsky：线段落在窗口里的参数区间
    t0, t1, dx, dy = 0.0, 1.0, bx - ax, by - ay
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q < 0: return None
        elif p < 0: t0 = max(t0, q / p)
        else: t1 = min(t1, q / p)
    return (t0, t1) if t0 < t1 else None
ripped, arcs, removed, wide, narrowed = set(), set(), 0, set(), set()
# 有铺铜的网（地/电源平面的缝合孔）和有宽线的网（电源）：过孔一律不拆
keep_via = {unesc(z.GetNetname()) for z in b.Zones() if not z.GetIsRuleArea()} | {
    unesc(t.GetNetname()) for t in b.GetTracks() if t.GetClass() == "PCB_TRACK" and t.GetWidth() / MM > opt["min_w"] + 0.0005}
for t in list(b.GetTracks()):
    if t.IsLocked(): continue
    n, cls = unesc(t.GetNetname()), t.GetClass()
    if keep_re and keep_re.fullmatch(n): continue
    if cls == "PCB_TRACK" and t.GetWidth() / MM > opt["max_w"]:
        if clip(t.GetStart().x / MM, t.GetStart().y / MM, t.GetEnd().x / MM, t.GetEnd().y / MM): wide.add(n)
        continue  # 比最小线宽宽的线（多半是电源）不拆：重布只会用最小线宽
    if cls == "PCB_VIA":
        if n in keep_via and not opt["rip_wide"]: continue
        p = t.GetPosition()
        if x0 <= p.x / MM <= x1 and y0 <= p.y / MM <= y1:
            b.Remove(t); ripped.add(n); removed += 1
        continue
    if cls == "PCB_ARC":
        bb = t.GetBoundingBox()
        if bb.GetRight() / MM >= x0 and bb.GetLeft() / MM <= x1 and bb.GetBottom() / MM >= y0 and bb.GetTop() / MM <= y1:
            arcs.add(n)
        continue
    ax, ay, bx, by = t.GetStart().x / MM, t.GetStart().y / MM, t.GetEnd().x / MM, t.GetEnd().y / MM
    c = clip(ax, ay, bx, by)
    if not c: continue
    ripped.add(n); removed += 1
    if t.GetWidth() / MM > opt["min_w"] + 0.0005: narrowed.add(n)  # 只有 --rip-wide 才会走到：重布会变成最小线宽
    pt = lambda s: k.VECTOR2I(int(round((ax + s * (bx - ax)) * MM)), int(round((ay + s * (by - ay)) * MM)))
    keep = [(s, e) for s, e in ((0.0, c[0]), (c[1], 1.0)) if (e - s) * math.hypot(bx - ax, by - ay) > 0.001]
    if not keep:
        b.Remove(t); continue
    if len(keep) == 2:  # 线横穿窗口：两头各留一段
        t2 = k.PCB_TRACK(b); t2.SetLayer(t.GetLayer()); t2.SetWidth(t.GetWidth()); t2.SetNet(t.GetNet())
        t2.SetStart(pt(keep[1][0])); t2.SetEnd(pt(1.0)); b.Add(t2)
    t.SetStart(pt(keep[0][0])); t.SetEnd(pt(keep[0][1]))
b.Save(sys.argv[2]); print(json.dumps({"ripped": sorted(ripped), "arcs_kept": sorted(arcs), "wide_kept": sorted(wide), "narrowed": sorted(narrowed), "removed": removed}))
'''

KPY = E.KPY
DIRS = ((0, 1, 10), (1, 0, 10), (1, 1, 14), (1, -1, 14))  # (dj, di, 代价)：同层 8 向，只列一半方向，边无向


def sidecar(src, dst):
    for ext in (".kicad_pro", ".kicad_dru"):
        if Path(src).with_suffix(ext).exists():
            shutil.copy(Path(src).with_suffix(ext), Path(dst).with_suffix(ext))


def rip(board, out, win, opt):
    with tempfile.TemporaryDirectory() as d:
        Path(d, "rip.py").write_text(RIP)
        p = subprocess.run([KPY, str(Path(d, "rip.py")), str(board), str(out), json.dumps(win), json.dumps(opt)],
                           capture_output=True, text=True)
    if not Path(out).exists():
        raise SystemExit(f"拆线失败：{p.stderr[-300:]}")
    sidecar(board, out)
    return json.loads(next(l for l in reversed(p.stdout.splitlines()) if l.startswith("{")))  # SWIG 退出时会往 stdout 打泄漏提示


def apply(board, out, routes):
    """逐网调 escape 的 APPLY 写线和孔（一个网一次 KiCad 进程）。"""
    cur = Path(board)
    with tempfile.TemporaryDirectory() as d:
        Path(d, "apply.py").write_text(E.APPLY)
        for i, w in enumerate(r for r in routes if r["segments"] or r["vias"]):
            nxt = Path(d, f"s{i}.kicad_pcb")
            Path(d, "w.json").write_text(json.dumps(w))
            p = subprocess.run([KPY, str(Path(d, "apply.py")), str(cur), str(Path(d, "w.json")), str(nxt)],
                               capture_output=True, text=True)
            if not nxt.exists():
                raise SystemExit(f"写 {w['net']} 失败：{p.stderr[-300:]}")
            cur = nxt
        Path(out).unlink(missing_ok=True)
        shutil.copy(cur, out)
    sidecar(board, out)


def own_groups(geom, name, rs):
    """网 name 的铜在 rs 栅格上的连通块（各层 8 连通，经本网孔贯穿各层并起来）。0 = 不是本网铜。"""
    L = geom["layers"]
    lab = np.zeros((len(L), rs.h, rs.w), np.int32)
    n0 = 0
    for li, l in enumerate(L):
        img = Image.new("L", (rs.w, rs.h), 0)
        for n, polys, _ in geom["copper"][l]:
            if n == name:
                rs.draw(img, polys, 1)
        m, cnt = ndimage.label(np.asarray(img) > 0, structure=np.ones((3, 3)))
        lab[li] = np.where(m > 0, m + n0, 0)
        n0 += cnt
    a, b = [], []
    for x, y, _, hn in geom["holes"]:
        cy, cx = rs.cell(x, y)
        if hn == name and 0 <= cy < rs.h and 0 <= cx < rs.w:
            ids = [i for i in lab[:, cy, cx] if i]
            a += ids[:1] * (len(ids) - 1)
            b += ids[1:]
    _, comp = connected_components(coo_matrix((np.ones(len(a)), (a, b)), shape=(n0 + 1,) * 2), directed=False)
    return np.where(lab > 0, comp[lab] + 1, 0)


def solve(prob, time_limit=60.0, workers=8, log=False):
    """prob["nets"]：每网 {name, edges:[(a, b, cost)], hubs:{(i,j): 已有本网孔?}, parts:[[终端节点...], ...]}。
    节点：("g", l, i, j) 格点，("h", i, j) 换层点，("t", 名) 终端。prob["rt"/"rv"/"rvv"]：格点单位的冲突半径，
    prob["layers"]：可走线层（冲突只在这些层算）。返回每网用到的边和新孔；无解返回 None。"""
    from ortools.sat.python import cp_model
    m = cp_model.CpModel()
    U, Y = {}, {}  # (网号, l, i, j) -> 占用；(网号, i, j) -> 新孔
    X, obj = [], []
    for ni, n in enumerate(prob["nets"]):
        xs = []
        for a, b, cost in n["edges"]:
            x = m.NewBoolVar("")
            xs.append(x)
            obj.append(cost * x)
            for v in (a, b):
                if v[0] == "g":
                    m.AddImplication(x, U.setdefault((ni, *v[1:]), m.NewBoolVar("")))
                elif v[0] == "h" and not n["hubs"][v[1:]]:
                    m.AddImplication(x, Y.setdefault((ni, *v[1:]), m.NewBoolVar("")))
        X.append(xs)
        for terms in n["parts"]:  # 每组要连通的终端一条单商品流：根发 k 份，其余终端各收 1 份
            k = len(terms) - 1
            if k < 1:
                continue
            bal = {}
            for (a, b, _), x in zip(n["edges"], xs):
                fab, fba = m.NewIntVar(0, k, ""), m.NewIntVar(0, k, "")
                m.Add(fab + fba <= k * x)
                for v, s in ((a, fab - fba), (b, fba - fab)):
                    bal.setdefault(v, []).append(s)
            for v, flows in bal.items():
                need = -k if v == terms[0] else (1 if v in terms else 0)
                m.Add(sum(flows) == need)
            if any(t not in bal for t in terms):
                return None, {"status": "INFEASIBLE", "why": f"{n['name']} 有终端挂不上任何格点"}
    occ = {}
    for (ni, l, i, j), u in U.items():
        occ.setdefault((l, i, j), []).append((ni, u))
    for us in occ.values():
        if len(us) > 1:
            m.Add(sum(u for _, u in us) <= 1)

    def offsets(r, zero):
        R = int(math.ceil(r))
        return [(di, dj) for di in range(-R, R + 1) for dj in range(-R, R + 1)
                if di * di + dj * dj < r * r and (zero or (di, dj) != (0, 0))]
    half = [(di, dj) for di, dj in offsets(prob["rt"], False) if (di, dj) > (0, 0)]
    for (l, i, j), us in occ.items():  # 不同网的格点太近：v 归网 n、w 归别的网不许同时成立
        for di, dj in half:
            w = occ.get((l, i + di, j + dj))
            if not w:
                continue
            ow = sum(u for _, u in w)
            for ni, u in us:
                m.Add(u + ow - U.get((ni, l, i + di, j + dj), 0) <= 1)
    vt = offsets(prob["rv"], True)
    for (ni, i, j), y in Y.items():  # 新孔贯穿各层：附近别网的格点都不许用
        for l in prob["layers"]:
            for di, dj in vt:
                w = occ.get((l, i + di, j + dj))
                if w:
                    m.Add(y + sum(u for _, u in w) - U.get((ni, l, i + di, j + dj), 0) <= 1)
    vias = {}
    for (ni, i, j), y in Y.items():
        vias.setdefault((i, j), []).append(y)
    for (i, j), ys in vias.items():
        if len(ys) > 1:
            m.Add(sum(ys) <= 1)
        for di, dj in [o for o in offsets(prob["rvv"], False) if o > (0, 0)]:
            if (i + di, j + dj) in vias:
                m.Add(sum(ys) + sum(vias[(i + di, j + dj)]) <= 1)  # 新孔之间（同网也算）孔距
    EI = [{(e[0], e[1]): k for k, e in enumerate(n["edges"])} for n in prob["nets"]]
    for ni, n in enumerate(prob["nets"]):
        for snk in n.get("sinks", []):  # 出口节点只能当叶子：不许经它把两个终端「瞬移」连起来
            inc = [x for (a, b, _), x in zip(n["edges"], X[ni]) if snk in (a, b)]
            if inc:
                m.Add(sum(inc) == 1)

    def var(ni, kind, key):
        if kind == "e":
            k = EI[ni].get(key)
            return None if k is None else X[ni][k]
        return U.get((ni, *key[1:])) if key[0] == "g" else Y.get((ni, *key[1:]))
    for a_, b_ in prob.get("pairs", []):  # 接回老铜的小段与别网东西的互斥
        va, vb = var(*a_), var(*b_)
        if va is not None and vb is not None:
            m.Add(va + vb <= 1)
    m.Minimize(sum(obj))
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = time_limit
    s.parameters.num_workers = workers
    s.parameters.log_search_progress = log
    st = s.Solve(m)
    info = {"status": s.StatusName(st), "objective": s.ObjectiveValue() if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
            "wall_s": round(s.WallTime(), 1), "bools": len(U) + len(Y) + sum(map(len, X))}
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, info
    out = []
    for ni, n in enumerate(prob["nets"]):
        used = [e for e, x in zip(n["edges"], X[ni]) if s.Value(x)]
        out.append({"edges": used, "vias": sorted({(i, j) for (k, i, j), y in Y.items() if k == ni and s.Value(y)})})
    return out, info


def negotiate(prob, iters=80, log=print):
    """协商式拆线重布（PathFinder）：每网在自己的图上求 Steiner 树（逐个连最近的终端），
    冲突（别网格点在 rt 内、别网新孔在 rv 内、新孔之间 rvv 内）按卷积计价，冲突处历史代价逐轮累加，
    直到没有冲突。只找解，找不到不代表无解。返回格式同 solve。"""
    from scipy.sparse.csgraph import dijkstra
    nets = prob["nets"]
    keys = [v for n in nets for e in n["edges"] for v in e[:2] if v[0] in "gh"]
    if not keys:
        return None, {"status": "INFEASIBLE", "why": "模型里没有格点"}
    i0, j0 = min(v[-2] for v in keys), min(v[-1] for v in keys)
    I, J = max(v[-2] for v in keys) - i0 + 1, max(v[-1] for v in keys) - j0 + 1
    nl = max([v[1] for v in keys if v[0] == "g"] + list(prob["layers"]) + [0]) + 1

    def disk(r, zero):
        R = int(math.ceil(r))
        k = np.array([[di * di + dj * dj < r * r for dj in range(-R, R + 1)] for di in range(-R, R + 1)], float)
        k[R, R] = 1.0 if zero else 0.0
        return k
    kt, kv, kvv = disk(prob["rt"], True), disk(prob["rv"], True), disk(prob["rvv"], True)  # 同一格点两网也算冲突
    conv = lambda x, k: ndimage.convolve(x, k, mode="constant")
    G = []  # 每网：节点表、边、节点在格上的位置
    for n in nets:
        idx = {}
        for a_, b_, _ in n["edges"]:
            idx.setdefault(a_, len(idx))
            idx.setdefault(b_, len(idx))
        ks = list(idx)
        G.append({"idx": idx, "keys": ks, "eid": {(min(idx[e[0]], idx[e[1]]), max(idx[e[0]], idx[e[1]])): k
                                                  for k, e in enumerate(n["edges"])},
                  "sink": [idx[t] for t in n.get("sinks", []) if t in idx],
                  "ea": np.array([idx[e[0]] for e in n["edges"]]), "eb": np.array([idx[e[1]] for e in n["edges"]]),
                  "ec": np.array([e[2] for e in n["edges"]], float),
                  "gl": np.array([(k[1], k[2] - i0, k[3] - j0) if k[0] == "g" else (-1, 0, 0) for k in ks]),
                  "hv": np.array([(k[1] - i0, k[2] - j0) if k[0] == "h" and not n["hubs"][k[1:]] else (-1, 0) for k in ks])})
    occ = np.zeros((len(nets), nl, I, J))  # 各网占的格点
    via = np.zeros((len(nets), I, J))      # 各网的新孔
    hist, hist_v = np.zeros((nl, I, J)), np.zeros((I, J))
    paths, pres, rl, bad = [set()] * len(nets), 0.5, prob["layers"], -1
    used_e = [set() for _ in nets]  # 各网用到的边（按边键），互斥对要用
    pairs = prob.get("pairs", [])
    phist = np.zeros(len(pairs))
    local = [[] for _ in nets]
    for pid, (a_, b_) in enumerate(pairs):
        local[a_[0]].append((pid, a_, b_))
        local[b_[0]].append((pid, b_, a_))

    def in_use(ni, kind, key):
        if kind == "e":
            return key in used_e[ni]
        if key[0] == "g":
            return occ[ni, key[1], key[2] - i0, key[3] - j0] > 0
        return via[ni, key[1] - i0, key[2] - j0] > 0

    def costs(ni):  # 格点冲突：同层别网格点 + 别网新孔；新孔冲突：各走线层别网格点 + 别的新孔（同网也算孔距）
        o, v = occ.sum(0) - occ[ni], via.sum(0) - via[ni]
        cg = np.stack([conv(o[l], kt) for l in range(nl)])
        vt = conv(v, kv)
        for l in rl:  # 平面层上没有别网的线，孔对格点只在走线层算（和 solve 一致）
            cg[l] += vt
        ch = sum(conv(o[l], kv) for l in rl) + conv(v, kvv)
        return cg, ch
    for it in range(iters):
        for ni, n in enumerate(nets):
            occ[ni], via[ni] = 0, 0
            used_e[ni] = set()
            cg, ch = costs(ni)
            g = G[ni]
            ext = np.zeros(len(g["keys"]))
            m = g["gl"][:, 0] >= 0
            l_, a_, b_ = g["gl"][m].T
            ext[m] = ((hist[l_, a_, b_] + 1) * (1 + pres * cg[l_, a_, b_]) - 1) * 10
            m = g["hv"][:, 0] >= 0
            a_, b_ = g["hv"][m].T
            ext[m] = ((hist_v[a_, b_] + 1) * (1 + pres * ch[a_, b_]) - 1) * 10
            epen = np.zeros(len(g["ec"]))
            for pid, (_, lk, lkey), (nj, ok_, okey) in local[ni]:  # 互斥对：对方在用就加价
                if in_use(nj, ok_, okey):
                    pen = pres * 10 * (1 + phist[pid])
                    if lk == "e":
                        epen[g["eid"][tuple(sorted((g["idx"][lkey[0]], g["idx"][lkey[1]])))]] += pen
                    elif lkey in g["idx"]:
                        ext[g["idx"][lkey]] += pen
            w = g["ec"] + (ext[g["ea"]] + ext[g["eb"]]) / 2 + epen + 1e-3
            A = coo_matrix((w, (g["ea"], g["eb"])), shape=(len(g["keys"]),) * 2).tocsr()
            keep = ~(np.isin(g["ea"], g["sink"]) | np.isin(g["eb"], g["sink"]))
            A_ns = coo_matrix((w[keep], (g["ea"][keep], g["eb"][keep])), shape=A.shape).tocsr()  # 出口只当叶子
            used = set()
            for terms in n["parts"]:
                ti = [g["idx"][t] for t in terms]
                rest = [t for t in ti if t not in g["sink"]]
                tree = {rest[0]}
                for left, AA in (({*rest[1:]}, A_ns), ({t for t in ti if t in g["sink"]}, A)):
                    while left:
                        dist, pred, _ = dijkstra(AA, directed=False, indices=sorted(tree), min_only=True,
                                                 return_predecessors=True)
                        t = min(left, key=lambda x: dist[x])
                        if not np.isfinite(dist[t]):
                            return None, {"status": "INFEASIBLE", "why": f"{n['name']} 有终端在图上就不连通"}
                        v = t
                        while v not in tree:
                            u = pred[v]
                            used.add((min(u, v), max(u, v)))
                            tree.add(v)
                            v = u
                        left.discard(t)
            paths[ni] = used
            used_e[ni] = {tuple(n["edges"][g["eid"][e]][:2]) for e in used}
            for x in {x for e in used for x in e}:
                if g["gl"][x][0] >= 0:
                    occ[(ni, *g["gl"][x])] = 1
                if g["hv"][x][0] >= 0:
                    via[(ni, *g["hv"][x])] = 1
        bad, who = 0, set()
        for ni in range(len(nets)):
            cg, ch = costs(ni)
            ch = ch + conv(via[ni], kvv) - via[ni]  # 同网新孔之间的孔距
            hit, hv_ = (occ[ni] > 0) & (cg > 0), (via[ni] > 0) & (ch > 0)
            hist += hit
            hist_v += hv_
            bad += int(hit.sum() + hv_.sum())
            if hit.any() or hv_.any():
                who.add(nets[ni]["name"])
        for pid, (a_, b_) in enumerate(pairs):
            if in_use(*a_) and in_use(*b_):
                phist[pid] += 1
                bad += 1
                who |= {nets[a_[0]]["name"], nets[b_[0]]["name"]}
        if log:
            log(f"  协商第 {it + 1} 轮：冲突 {bad}")
        if bad == 0:
            out = []
            for ni, n in enumerate(nets):
                ks = G[ni]["keys"]
                es = [n["edges"][G[ni]["eid"][e]] for e in paths[ni]]
                vs = sorted({ks[x][1:] for e in paths[ni] for x in e if ks[x][0] == "h" and not n["hubs"][ks[x][1:]]})
                out.append({"edges": es, "vias": vs})
            return out, {"status": "FEASIBLE", "iters": it + 1}
        pres *= 1.5
    return None, {"status": "UNKNOWN", "iters": iters, "conflicts": bad, "nets": sorted(who)}


def seg_dist(p, a, b):
    """点 p 到线段 ab 的距离（numpy 广播：p 形如 (..., 2)，a/b 形如 (2,)）。"""
    d = b - a
    t = np.clip(((p - a) * d).sum(-1) / max((d * d).sum(), 1e-18), 0, 1)
    return np.linalg.norm(a + t[..., None] * d - p, axis=-1)


def segseg(a, b, c, d):
    """线段 ab 与 cd 的最近距离（相交为 0）。"""
    cr = lambda o, p, q: (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    if cr(a, b, c) * cr(a, b, d) < 0 and cr(c, d, a) * cr(c, d, b) < 0:
        return 0.0
    return float(min(seg_dist(c, a, b), seg_dist(d, a, b), seg_dist(a, c, d), seg_dist(b, c, d)))


def build(geom0, geom, ripped, target, gap, anchor, win, a):
    """在拆过的板上建问题。返回 (prob, meta)，meta 里有坐标换算和接回老铜的小段。"""
    R, L, p = geom["rules"], geom["layers"], a.pitch
    # 两格 = 最小线距（线宽 + 间距，再留 0.2 µm）：按最小间距并排的老线进了窗口，新线也排得下；
    # 8 向走线在这个栅格上线到格点最近 2.12 格 > 2 格，只判格点距离就够（别的栅格不保证，所以不开放参数）
    G = a.grid = (R["track"] + R["clearance"] + 0.0002) / 2
    gm = 0.3 + G  # 格点比窗口再外扩一点：窗外空地也能用来绕
    bbox = (win[0] - gm - 1, win[1] - gm - 1, win[2] + gm + 1, win[3] + gm + 1)
    P = E.prep(geom, p, bbox=bbox)
    rs, plane_idx = P["rs"], P["plane_idx"]
    route_layers = [li for li in range(len(L)) if li not in plane_idx]
    full = E.Raster(geom["outline"], p)
    oj, oi = int(round((rs.y0 - full.y0) / p)), int(round((rs.x0 - full.x0) / p))
    crop = lambda arr: arr[:, oj:oj + rs.h, oi:oi + rs.w]
    ni0, nj0 = (int(math.floor((w - gm - c) / G)) for w, c in zip(win[:2], anchor))
    ni1, nj1 = (int(math.ceil((w + gm - c) / G)) for w, c in zip(win[2:], anchor))
    mm = lambda i, j: (round(anchor[0] + i * G, 6), round(anchor[1] + j * G, 6))
    pix = {}
    for i in range(ni0, ni1 + 1):
        for j in range(nj0, nj1 + 1):
            cy, cx = rs.cell(*mm(i, j))
            if 0 <= cy < rs.h and 0 <= cx < rs.w:
                pix[(i, j)] = (cy, cx)
    nodes_ij = list(pix)
    P_mm = np.array([mm(i, j) for i, j in nodes_ij])
    in_win = lambda x, y: win[0] < x < win[2] and win[1] < y < win[3]

    def seg_ok(ok_map, li, s, e):  # 精细栅格上逐像素查一小段
        n_s = int(max(abs(s[0] - e[0]), abs(s[1] - e[1]))) + 1
        yy = np.linspace(s[0], e[0], n_s + 1).round().astype(int)
        xx = np.linspace(s[1], e[1], n_s + 1).round().astype(int)
        return bool(ok_map[li, yy, xx].all())
    nets, meta = [], {"mm": mm, "stubs": {}, "exit": None, "route_layers": route_layers, "planes": P["planes"]}
    stub_list = []  # (网名, 边键, 层号, 起点 mm, 终点 mm)：小段不在格点上，单独和别网的东西配冲突对
    for name in sorted(set(ripped) | {target}):
        M = E.net_maps(geom, P, name, a.slack)
        free, core, conn, holes, own = M["free"], M["own_core"], M["connector"], M["own_holes"], M["own"]
        free0 = E.net_maps(geom, P, name, 0.0)["free"] | own  # 小段：本网铜里随便走，铜外按实际间距
        core_l = E.net_maps(geom, P, name, p)["own_core"]
        gid = crop(own_groups(geom, name, full))
        gfull0 = own_groups(geom0, name, full)
        gid0 = crop(gfull0)
        tmask = np.zeros_like(own)  # 本网走线铜：接走线一律从精确线心出发，不用栅格上的近似线心
        for li, l in enumerate(L):
            img = Image.new("L", (rs.w, rs.h), 0)
            for n_, polys, k_ in geom["copper"][l]:
                if n_ == name and k_ == "track":
                    rs.draw(img, polys, 1)
            tmask[li] = np.asarray(img) > 0
        attach, stubs = {}, {}

        def add(g, li, ij, start, dist):
            key = (g, li, *ij)
            if key in stubs or any(v == ("g", li, *ij) for v, _ in attach.get(g, [])):
                return
            attach.setdefault(g, []).append((("g", li, *ij), int(round(dist / G * 10))))
            if dist > 1e-6:
                stubs[key] = (L[li], round(start[0], 6), round(start[1], 6), *mm(*ij))
        segs = [s_ for s_ in geom.get("segs", []) if s_[0] == name]
        for li in range(len(L)):
            # 1) 格点落在本网铜的严线心区（焊盘、铺铜、比最小线宽宽的线里）：直接挂
            for (i, j), (cy, cx) in pix.items():
                g = int(gid[li, cy, cx])
                if g and core[li, cy, cx]:
                    add(g, li, (i, j), None, 0.0)
            if li in plane_idx:
                continue
            # 2) 走线：从格点到最近一段本网走线中心线的垂足拉小段
            S = np.array([s_[2:6] for s_ in segs if s_[1] == L[li]]).reshape(-1, 4)
            if len(S):
                A_, B_ = S[:, :2], S[:, 2:]
                D = B_ - A_
                t = np.clip((((P_mm[:, None, :] - A_) * D).sum(-1)) / np.maximum((D * D).sum(-1), 1e-18), 0, 1)
                Q = A_ + t[..., None] * D
                dist = np.linalg.norm(Q - P_mm[:, None, :], axis=-1)
                k = dist.argmin(1)
                for n_i, (i, j) in enumerate(nodes_ij):
                    dd = dist[n_i, k[n_i]]
                    cy, cx = pix[(i, j)]
                    if dd > 2.5 * G or not free[li, cy, cx]:
                        continue
                    qx, qy = Q[n_i, k[n_i]]
                    sp = rs.cell(qx, qy)
                    if not (0 <= sp[0] < rs.h and 0 <= sp[1] < rs.w) or not gid[li, sp[0], sp[1]]:
                        continue
                    if dd < 1e-6 or seg_ok(free0, li, sp, (cy, cx)):
                        add(int(gid[li, sp[0], sp[1]]), li, (i, j), (qx, qy), dd)
            # 3) 焊盘、过孔盘等非走线铜：从最近的线心像素拉小段（走线铜不走这条，免得起点偏离线心）
            for g in [int(g) for g in np.unique(gid[li]) if g]:
                cg = core_l[li] & (gid[li] == g) & ~tmask[li]
                if not cg.any():
                    continue
                d, (ry, rx) = ndimage.distance_transform_edt(~cg, return_indices=True)
                for (i, j), (cy, cx) in pix.items():
                    if d[cy, cx] * p > 2.5 * G or not free[li, cy, cx]:
                        continue
                    ty, tx = ry[cy, cx], rx[cy, cx]
                    if seg_ok(free0, li, (ty, tx), (cy, cx)):
                        add(g, li, (i, j), (rs.x0 + tx * p, rs.y0 + ty * p), d[cy, cx] * p)
        # 拆之前连在一起的块要重新连上：按拆前的块号分组
        parts = {}
        for g in attach:
            ys, xs = np.nonzero((gid == g).any(0))
            li = int(np.argmax((gid[:, ys[0], xs[0]] == g)))
            parts.setdefault(int(gid0[li, ys[0], xs[0]]), []).append(("t", int(g)))
        edges = [(("t", g), v, c) for g, vs in attach.items() for v, c in vs]
        sinks = []
        if name == target:  # 缺口两端按拆前的块认（被困那头常是一小段刚被拆掉的线），再映射到拆后的终端
            pre = []
            for it in gap["items"][:2]:
                cy, cx = full.cell(it["pos"]["x"], it["pos"]["y"])
                gg = gfull0[:, cy, cx]
                pre.append(next((int(gg[li]) for li in E.item_layers(it["description"], L) if gg[li]), 0))
            if not all(pre):
                return None, {"why": "缺口端点在拆前的板上没认到本网铜", "unknown": True}
            missing = [it for e, it in zip(pre, gap["items"][:2]) if e not in parts]
            if len(missing) == 2:
                return None, {"why": "缺口两端在窗口里都没有能挂的铜", "unknown": True}
            if missing and in_win(missing[0]["pos"]["x"], missing[0]["pos"]["y"]):  # 在窗口里却挂不上：建模问题，不是远端
                return None, {"why": f"{missing[0]['description']} 在窗口里但挂不上任何格点", "unknown": True}
            merged = [t for e in dict.fromkeys(pre) for t in parts.pop(e, [])]
            if missing:  # 远端在窗外：窗外绕得到远端的格点当出口，出口节点只能当叶子（不许当中转）
                fe = [(("t", "far"), ("g", li, i, j), 0) for li, i, j in far_exits(geom, name, missing[0], win, a, pix, full, oj, oi)
                      if free[li, pix[(i, j)][0], pix[(i, j)][1]]]
                if not fe:
                    return None, {"why": "目标网远端在窗口外，且只走窗外（固定铜）绕不到窗口边：放大窗口再试"}
                edges += fe
                merged.append(("t", "far"))
                sinks = [("t", "far")]
                meta["exit"] = missing[0]["pos"]
            parts["gap"] = merged
        tnodes = [v for _, v, _ in edges]
        if not tnodes:
            continue
        # 每网只在终端外包框 + detour 里走，模型才小得下来
        bi = (min(v[2] for v in tnodes) - a.detour / G, max(v[2] for v in tnodes) + a.detour / G)
        bj = (min(v[3] for v in tnodes) - a.detour / G, max(v[3] for v in tnodes) + a.detour / G)
        ok = lambda i, j: bi[0] <= i <= bi[1] and bj[0] <= j <= bj[1] and (i, j) in pix
        for li in route_layers:
            for (i, j), (cy, cx) in pix.items():
                if not ok(i, j) or not free[li, cy, cx]:
                    continue
                for dj, di, c in DIRS:
                    q = (i + di, j + dj)
                    if ok(*q) and free[li, pix[q][0], pix[q][1]] and seg_ok(free, li, (cy, cx), pix[q]):
                        edges.append((("g", li, i, j), ("g", li, *q), c))
        hubs = {}
        vc = int(round(a.via_cost / G * 10))
        for (i, j), (cy, cx) in pix.items():
            if not ok(i, j) or not conn[cy, cx]:
                continue
            ex = bool(holes[cy, cx])
            if not ex and not in_win(*mm(i, j)):
                continue  # 新孔只打在窗口里（窗外是没拆的固定铜，孔距靠精细栅格保证不了全部）
            hubs[(i, j)] = ex
            for li in range(len(L)):
                if (li in route_layers and free[li, cy, cx]) or (li in plane_idx and core[li, cy, cx]):
                    edges.append((("h", i, j), ("g", li, i, j), 0 if ex else vc // 2))
        parts = [ts for ts in parts.values() if len(ts) >= 2]
        if not parts:
            continue
        nets.append({"name": name, "edges": edges, "hubs": hubs, "parts": parts, "sinks": sinks})
        meta["stubs"][name] = stubs
        stub_list += [(len(nets) - 1, (("t", k_[0]), ("g", *k_[1:])), k_[1], np.array(v_[1:3]), np.array(v_[3:5]))
                      for k_, v_ in stubs.items()]
    q = lambda d: (d + 0.0001) / G  # 真实距离再加 0.1 µm：正好等于要求的距离算合格
    prob = {"nets": nets, "layers": route_layers, "rt": q(R["track"] + R["clearance"]),
            "rv": q(R["via_d"] / 2 + R["clearance"] + R["track"] / 2),
            "rvv": q(max(R["via_d"] + R["clearance"], R["via_drill"] + R["hole_to_hole"]))}
    # 小段与别网的格点、新孔位、小段之间按真实几何距离配互斥对（格点之间的冲突在求解器里按半径算）
    tt, tv = R["track"] + R["clearance"] + 0.0001, R["via_d"] / 2 + R["clearance"] + R["track"] / 2 + 0.0001
    gnodes = []
    for ni, n in enumerate(nets):
        gs = sorted({v for e in n["edges"] for v in e[:2] if v[0] == "g"})
        hs = sorted(h for h, ex in n["hubs"].items() if not ex)
        gnodes.append((np.array([v[1:] for v in gs]).reshape(-1, 3), gs, hs))
    pairs = []
    for ni, ek, li, s0, s1 in stub_list:
        for nj, (arr, gs, hs) in enumerate(gnodes):
            if nj == ni:
                continue
            m_ = arr[:, 0] == li
            if m_.any():
                pts = np.array([mm(i, j) for i, j in arr[m_][:, 1:]])
                for k_ in np.nonzero(seg_dist(pts, s0, s1) < tt)[0]:
                    pairs.append(((ni, "e", ek), (nj, "n", tuple(gs[np.nonzero(m_)[0][k_]]))))
            if hs:
                pts = np.array([mm(*h) for h in hs])
                for k_ in np.nonzero(seg_dist(pts, s0, s1) < tv)[0]:
                    pairs.append(((ni, "e", ek), (nj, "n", ("h", *hs[k_]))))
    for x in range(len(stub_list)):
        for y in range(x + 1, len(stub_list)):
            a_, b_ = stub_list[x], stub_list[y]
            if a_[0] != b_[0] and a_[2] == b_[2] and segseg(a_[3], a_[4], b_[3], b_[4]) < tt:
                pairs.append(((a_[0], "e", a_[1]), (b_[0], "e", b_[1])))
    keys = [({e[:2] for e in n["edges"]}, {v for e in n["edges"] for v in e[:2]}) for n in nets]
    for x in (x for pr in pairs for x in pr):  # 键写错会被求解器静默跳过，这里先拦
        assert (x[2] in keys[x[0]][0]) if x[1] == "e" else (x[2] in keys[x[0]][1]), x
    prob["pairs"] = pairs
    meta["n_pairs"] = len(pairs)
    return prob, meta


def far_exits(geom, name, far, win, a, pix, full, oj, oi):
    """远端在窗外：窗口内部封死，只走窗外，看远端能走到哪些格点。"""
    P = E.prep(geom, a.pitch)
    M = E.net_maps(geom, P, name, a.slack)
    free, conn = M["free"].copy(), M["connector"].copy()
    y0, x0 = (max(v, -1) for v in full.cell(win[0], win[1]))  # 窗口越过栅格左上边时别让负下标倒着切
    y1, x1 = full.cell(win[2], win[3])
    free[:, y0 + 1:y1, x0 + 1:x1] = False
    conn[y0 + 1:y1, x0 + 1:x1] = False
    labels, comp, _ = E.components(free, conn)
    cy, cx = full.cell(far["pos"]["x"], far["pos"]["y"])
    r = max(1, int(0.05 / a.pitch))
    lis = E.item_layers(far["description"], geom["layers"])
    seeds = {comp[s] for li in lis for s in np.unique(labels[li, max(cy - r, 0):cy + r + 1, max(cx - r, 0):cx + r + 1]) if s}
    out = []
    for (i, j), (py, px) in pix.items():
        for li in range(len(geom["layers"])):
            s = labels[li, py + oj, px + oi]
            if s and comp[s] in seeds:
                out.append((li, i, j))
    return out


def merge(segs, pins):
    """同层共线、首尾相接、接点只连这两段且不是孔位的线并成一段（格点上每格一段太碎）。"""
    segs = [list(x) for x in segs]
    while True:
        ends = {}
        for k, (l, x1, y1, x2, y2) in enumerate(segs):
            ends.setdefault((l, x1, y1), []).append(k)
            ends.setdefault((l, x2, y2), []).append(k)
        for (l, x, y), ks in ends.items():
            if len(ks) != 2 or (x, y) in pins:
                continue
            a_, b_ = segs[ks[0]], segs[ks[1]]
            pa = a_[3:5] if a_[1:3] == [x, y] else a_[1:3]  # 两段各自的另一端
            pb = b_[3:5] if b_[1:3] == [x, y] else b_[1:3]
            if abs((pa[0] - x) * (pb[1] - y) - (pa[1] - y) * (pb[0] - x)) < 1e-9:
                segs = [q for k, q in enumerate(segs) if k not in ks] + [[l, *pa, *pb]]
                break
        else:
            return segs


def to_routes(prob, sol, meta, geom):
    R, L, mm = geom["rules"], geom["layers"], meta["mm"]
    routes = []
    for n, s in zip(prob["nets"], sol):
        segs = []
        for a_, b_, _ in s["edges"]:
            if a_[0] == b_[0] == "g":
                segs.append([L[a_[1]], *mm(*a_[2:]), *mm(*b_[2:])])
            elif a_[0] == "t" and b_[0] == "g" and a_[1] != "far":
                st = meta["stubs"][n["name"]].get((a_[1], *b_[1:]))
                if st:
                    segs.append(list(st))
        vias = [list(mm(i, j)) for i, j in s["vias"]]
        segs = merge(segs, {tuple(v) for v in vias} | {tuple(mm(*v[1:])) for v in [h for e in s["edges"] for h in e[:2]] if v[0] == "h"})
        routes.append({"net": n["name"], "segments": segs, "vias": vias,
                       "width": R["track"], "via_d": R["via_d"], "via_drill": R["via_drill"]})
    return routes


def drc_counts(board):
    with tempfile.TemporaryDirectory() as d:
        o = Path(d, "drc.json")
        subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "--all-track-errors", "--refill-zones",
                        "-o", str(o), str(board)], capture_output=True)
        dd = json.loads(o.read_text())
    vs = [x for x in dd["violations"] if not any(s in x["type"] for s in ("silk_", "lib_footprint", "courtyard", "text_"))]
    v = Counter(E.vkey(x) for x in vs)  # 按类型 + 各条目 uuid 计数：同一焊盘上的老违规吃不掉新违规
    pos = {E.vkey(x): (x["type"], round(x["items"][0]["pos"]["x"], 2), round(x["items"][0]["pos"]["y"], 2)) for x in vs}
    dang = {x["items"][0]["uuid"] for x in dd["violations"] if x["type"] in ("track_dangling", "via_dangling")}
    per = {}
    for u in dd["unconnected_items"]:
        for n in set(re.findall(r"\[([^\]]+)\]", " ".join(i["description"] for i in u["items"]))):
            per[n] = per.get(n, 0) + 1
    return v, per, dang, pos


DROP = r'''
import pcbnew as k, sys, json
b = k.LoadBoard(sys.argv[1]); ids = set(json.loads(sys.argv[3]))
# 先把列表取全，最后统一删：Remove 之后再取列表拿到的是裸对象（KiCad 10.0.7 实测）
allt = list(b.GetTracks())
tracks = [t for t in allt if t.GetClass() == "PCB_TRACK"]
rm = [v for v in allt if v.GetClass() == "PCB_VIA" and v.m_Uuid.AsString() in ids]  # 悬空过孔：它原来连的残桩已经删了
gone = {v.m_Uuid.AsString() for v in rm}
others = [x for x in allt if x.m_Uuid.AsString() not in gone] + [p for f in b.GetFootprints() for p in f.Pads()]
def same(u, t):
    return {(u.GetStart().x, u.GetStart().y), (u.GetEnd().x, u.GetEnd().y)} == {(t.GetStart().x, t.GetStart().y), (t.GetEnd().x, t.GetEnd().y)}
def on_seg(p, a, c):  # p 到线段 ac 的参数位置和距离
    dx, dy = c.x - a.x, c.y - a.y; L2 = dx * dx + dy * dy or 1
    s = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
    return s, ((a.x + s * dx - p.x) ** 2 + (a.y + s * dy - p.y) ** 2) ** .5
for t in [t for t in tracks if t.m_Uuid.AsString() in ids]:
    a, c, net, lay = k.VECTOR2I(t.GetStart()), k.VECTOR2I(t.GetEnd()), t.GetNetCode(), t.GetLayer()
    hits = []  # 本网同层别的东西搭在 t 上的位置（参数 0..1）
    for u in others:
        if u is t or u.GetNetCode() != net or not u.IsOnLayer(lay) or (u.GetClass() == "PCB_TRACK" and same(u, t)):
            continue
        if u.GetClass() == "PCB_TRACK":
            for p in (u.GetStart(), u.GetEnd()):
                s, d = on_seg(p, a, c)
                if d < t.GetWidth() / 2: hits.append(s)  # 挂接点来自精细栅格，离线心可能偏 0.01 mm
        else:  # 过孔、焊盘：端点落在它里面就算连着
            for s, p in ((0.0, a), (1.0, c)):
                if u.HitTest(p): hits.append(s)
    s0, s1 = min(hits, default=0), max(hits, default=0)
    if not hits or (s1 - s0 < 1e-6 and (s0 < 1e-6 or s0 > 1 - 1e-6)):
        rm.append(t); continue  # 没被搭上，或只连着一个端点：整段都是悬空的
    if s0 > 1e-6 and s1 < 1 - 1e-6 and s0 == s1:
        continue  # 只在中间一点被搭上，两头都悬空：不知道留哪头，不动
    end = lambda s: k.VECTOR2I(int(round(a.x + s * (c.x - a.x))), int(round(a.y + s * (c.y - a.y))))
    if s0 > 1e-6: t.SetStart(end(s0))   # 起点那头悬空：缩到第一个搭接点
    if s1 < 1 - 1e-6: t.SetEnd(end(s1)) # 终点那头悬空：缩到最后一个搭接点
for x in rm:
    b.Remove(x)
b.Save(sys.argv[2])
'''


def trim(board, base_dang, base_per):
    """新线搭在剪断的老线中段时，老线靠窗口边那截会悬空：把新增的悬空段缩到最远的搭接点（没有搭接点就删），
    改完哪个网未连变多就退回。"""
    for _ in range(6):
        v, per, dang, _ = drc_counts(board)
        new = sorted(dang - base_dang)
        if not new:
            return []
        with tempfile.TemporaryDirectory() as d:
            Path(d, "drop.py").write_text(DROP)
            tmp = Path(d, "t.kicad_pcb")
            subprocess.run([KPY, str(Path(d, "drop.py")), str(board), str(tmp), json.dumps(new)], capture_output=True)
            if not tmp.exists():
                return new
            sidecar(board, tmp)
            _, per2, _, _ = drc_counts(tmp)
            if any(c > max(per.get(n, 0), base_per.get(n, 0)) for n, c in per2.items()):
                return new  # 删了会断：留着，交给 DRC 报
            shutil.copy(tmp, board)
    return sorted(drc_counts(board)[2] - base_dang)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", nargs="?")
    ap.add_argument("--net", help="要解的缺口所在的网")
    ap.add_argument("--at", help="窗口中心 x,y（mm）；不给就取 escape 判出的小的那一端")
    ap.add_argument("--half", type=float, default=1.5, help="窗口半宽 mm")
    ap.add_argument("--pitch", type=float, default=0.02, help="固定铜避让的精细栅格 mm")
    ap.add_argument("--slack", type=float, default=-0.01, help="精细栅格判避让时每个距离多让出多少 mm（负数 = 更严）")
    ap.add_argument("--detour", type=float, default=0.8, help="每网只在终端外包框外扩这么多 mm 里走")
    ap.add_argument("--via-cost", type=float, default=1.0, help="一个新孔折合多少 mm 线长")
    ap.add_argument("--keep", default="", help="整网不拆的网名正则（射频、CC、晶振、去耦等），例如 'RF.*|CC[12]|MCU_X.*'")
    ap.add_argument("--rip-wide", action="store_true", help="比最小线宽宽的线也拆（重布后变成最小线宽，报告里会列出）")
    ap.add_argument("--time", type=float, default=120)
    ap.add_argument("-o", "--out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.board and a.net and a.out):
        ap.error("要给板子、--net 和 -o")
    board, out = Path(a.board).resolve(), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    geom0, gaps = E.export(board), E.unconnected(board)
    gap = next((g for g in gaps if g["net"] == a.net), None)
    if not gap:
        raise SystemExit(f"{a.net} 没有未连缺口")
    if a.at:
        anchor = tuple(map(float, a.at.split(",")))
    else:
        r = E.analyze(geom0, [gap], a.pitch, slack=-a.pitch)[0]  # 收紧一格判：临界缺口这样才分得出被困的那一端
        s = min(r["sides"], key=lambda s: s["area_mm2"])
        anchor = (s["pos"]["x"], s["pos"]["y"])
    win = [anchor[0] - a.half, anchor[1] - a.half, anchor[0] + a.half, anchor[1] + a.half]
    report = {"board": str(board), "net": a.net, "anchor": anchor, "window": win, "args": vars(a)}
    tw = geom0["rules"]["track"]
    rp = rip(board, out / "ripped.kicad_pcb", win,
             {"keep": a.keep, "max_w": 1e9 if a.rip_wide else tw + 0.0005, "min_w": tw, "rip_wide": a.rip_wide})
    report.update(rp)
    thin = f"；会变细的网 {rp['narrowed']}" if rp["narrowed"] else ""
    print(f"窗口 {win}：拆 {rp['removed']} 段/孔，涉及 {len(rp['ripped'])} 个网 {rp['ripped']}；宽线没拆 {rp['wide_kept']}{thin}")
    geom = E.export(out / "ripped.kicad_pcb")
    prob, meta = build(geom0, geom, rp["ripped"], a.net, gap, anchor, win, a)
    if prob is None:
        report["verdict"] = "没建起来" if meta.get("unknown") else "窗口内无解"
        report["why"] = meta["why"]
    elif not any(n["name"] == a.net for n in prob["nets"]):
        report["verdict"], report["why"] = "没建起来", "目标网在窗口里没有要连的终端"
    else:
        print(f"建模：{len(prob['nets'])} 个网要连，边 {sum(len(n['edges']) for n in prob['nets'])}，小段互斥对 {meta['n_pairs']}；"
              f"平面层 {meta['planes']}；目标网{'要从窗口边出去' if meta['exit'] else '两端都在窗口里'}")
        sol, info = negotiate(prob, log=None)  # 先协商找解（快）；找不到再让 CP-SAT 判是否真无解
        report["negotiate"] = info
        print(f"协商：{info}")
        if sol is None and info.get("nets"):
            # 只留协商里还冲突的网：去掉别的网只会更宽松，这样都无解 = 原问题无解（模型小，证得快）
            keep = [k for k, n in enumerate(prob["nets"]) if n["name"] in info["nets"]]
            ren = {k: x for x, k in enumerate(keep)}  # 互斥对里的网号跟着换；牵涉被去掉的网的对一并去掉（只会更宽松）
            sub = dict(prob, nets=[prob["nets"][k] for k in keep],
                       pairs=[((ren[x[0]], *x[1:]), (ren[y[0]], *y[1:])) for x, y in prob.get("pairs", [])
                              if x[0] in ren and y[0] in ren])
            _, si = solve(sub, a.time)
            report["solver_subset"] = dict(si, nets=info["nets"])
            print(f"CP-SAT（只含 {info['nets']}）：{si}")
            if si["status"] == "INFEASIBLE":
                info = si
        if sol is None and info["status"] != "INFEASIBLE":
            sol, info = solve(prob, a.time)
            report["solver"] = info
            print(f"CP-SAT：{info}")
        if sol is None:
            report["verdict"] = "窗口内无解" if info["status"] == "INFEASIBLE" else "限时内没找到解"
            how = (info["why"] + "（单网图不连通，不用求解器）" if info.get("why")
                   else "，CP-SAT 证明无解" if info["status"] == "INFEASIBLE" else "")
            report["why"] = (f"{a.grid:.4f} mm 栅格、窗口 ±{a.half} mm、每网 detour {a.detour} mm 内{how}"
                             f"{'（只含冲突网就已无解）' if report.get('solver_subset', {}).get('status') == 'INFEASIBLE' else ''}；"
                             f"协商到最后还冲突的网 {report['negotiate'].get('nets')}")
        else:
            routes = to_routes(prob, sol, meta, geom)
            report["routes"] = [{"net": r["net"], "segments": len(r["segments"]), "vias": len(r["vias"]),
                                 "length_mm": round(sum(math.hypot(s[3] - s[1], s[4] - s[2]) for s in r["segments"]), 2)}
                                for r in routes]
            stage = out / "window.kicad_pcb"
            apply(out / "ripped.kicad_pcb", stage, routes)
            final = stage
            if meta["exit"]:  # 窗外那段：在已写入窗口线的板上，用 escape 的最短路补；挑离远端最近的那个缺口
                fx, fy = meta["exit"]["x"], meta["exit"]["y"]
                g2 = min((g for g in E.unconnected(stage) if g["net"] == a.net), default=None,
                         key=lambda g: min(math.hypot(i["pos"]["x"] - fx, i["pos"]["y"] - fy) for i in g["items"]))
                if g2:
                    w, why = E.witness(E.export(stage), g2, a.pitch, 10.0)
                    if w:
                        final = out / "final.kicad_pcb"
                        apply(stage, final, [w])
                        report["outside"] = {"length_mm": w["length_mm"], "vias": len(w["vias"])}
                    else:
                        report["outside"] = {"error": why}
            bv, bu, bd, _ = drc_counts(board)
            report["dangling_left"] = trim(final, bd, bu)
            fv, fu, _, fpos = drc_counts(final)
            new = sorted(fpos[k] for k in (fv - bv).elements())
            worse = {n: (bu.get(n, 0), c) for n, c in fu.items() if c > bu.get(n, 0)}
            proved = fu.get(a.net, 0) < bu.get(a.net, 0) and not new and not worse
            report.update(final=str(final), new_violations=new, nets_worse=worse,
                          target_unconnected=(bu.get(a.net, 0), fu.get(a.net, 0)),
                          unconnected_total=(sum(bu.values()), sum(fu.values())),
                          verdict="已证实" if proved else "DRC 不过")
    print(f"[{report['verdict']}] {a.net}：{ {k: report[k] for k in ('why', 'target_unconnected', 'unconnected_total', 'nets_worse', 'new_violations') if k in report} }")
    (out / "window.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))


def selftest():
    """只测求解核心：两层 9×9 格，A 横穿、B 竖穿。单层不许换层 = 必然交叉 = 无解；给换层点 = 有解，
    且解里不同网格点距离 ≥ rt、新孔周围 rv 内没有别网格点。"""
    def grid_edges(layers, n=9):
        es = []
        for l in layers:
            for i in range(n):
                for j in range(n):
                    for dj, di, c in DIRS:
                        if 0 <= i + di < n and 0 <= j + dj < n:
                            es.append((("g", l, i, j), ("g", l, i + di, j + dj), c))
        return es

    def net(name, ta, tb, layers, hubs):
        es = grid_edges(layers) + [(("t", 0), ("g", 0, *ta), 0), (("t", 1), ("g", 0, *tb), 0)]
        es += [(("h", i, j), ("g", l, i, j), 20) for i, j in hubs for l in layers]
        return {"name": name, "edges": es, "hubs": {h: False for h in hubs}, "parts": [[("t", 0), ("t", 1)]]}
    base = {"layers": [0, 1], "rt": 2.37, "rv": 3.7, "rvv": 5.6}

    def check(sol):  # 不同网格点距离 ≥ rt；新孔 rv 内没有别网格点；新孔之间 ≥ rvv
        used = {}
        for k, s in enumerate(sol):
            for a_, b_, _ in s["edges"]:
                for v in (a_, b_):
                    if v[0] == "g":
                        used.setdefault(v[1:], set()).add(k)
        assert all(len(ks) == 1 for ks in used.values())
        pts = [(v, next(iter(ks))) for v, ks in used.items()]
        for (l, i, j), k in pts:
            for (l2, i2, j2), k2 in pts:
                if l == l2 and k != k2:
                    assert (i - i2) ** 2 + (j - j2) ** 2 >= base["rt"] ** 2, ((l, i, j), (l2, i2, j2))
        vs = [(v, k) for k, s in enumerate(sol) for v in s["vias"]]
        for (i, j), k in vs:
            for (l2, i2, j2), k2 in pts:
                assert k2 == k or (i - i2) ** 2 + (j - j2) ** 2 >= base["rv"] ** 2
            for (i2, j2), _ in vs:
                assert (i, j) == (i2, j2) or (i - i2) ** 2 + (j - j2) ** 2 >= base["rvv"] ** 2
        assert sum(len(s["vias"]) for s in sol) >= 2  # 要过交叉，至少一网换层两次

    no_via = dict(base, nets=[net("A", (0, 4), (8, 4), [0], []), net("B", (4, 0), (4, 8), [0], [])])
    assert solve(no_via, 10)[0] is None
    assert negotiate(no_via, 20, log=None)[1]["status"] == "UNKNOWN"  # 协商找不到只说「没找到」，不说无解
    hubs = [(i, j) for i in range(9) for j in range(9)]
    p = dict(base, nets=[net("A", (0, 4), (8, 4), [0, 1], hubs), net("B", (4, 0), (4, 8), [0, 1], hubs)])
    sol, info = solve(p, 30)
    assert sol is not None, info
    check(sol)
    sol2, info2 = negotiate(p, 60, log=None)
    assert sol2 is not None, info2
    check(sol2)
    # 复核反例：同一格点两网（协商也得认冲突）；出口节点只当叶子（不许经它瞬移）；小段互斥对生效
    g1 = lambda i, j: ("g", 0, i, j)
    b1 = {"layers": [0], "rt": 1.999, "rv": 3.2, "rvv": 4.84}
    same = [{"name": n_, "edges": [(("t", 0), g1(0, 0), 0), (("t", 1), g1(0, 0), 0), (g1(0, 0), g1(d, d), 10)], "hubs": {},
             "parts": [[("t", 0), ("t", 1)]]} for n_, d in (("A", 5), ("B", -5))]
    assert solve(dict(b1, nets=same), 10)[0] is None and negotiate(dict(b1, nets=same), 5, log=None)[0] is None
    far = {"name": "C", "edges": [(("t", 2), g1(0, 0), 0), (("t", 3), g1(20, 0), 0), (("t", "far"), g1(0, 0), 0),
                                  (("t", "far"), g1(20, 0), 0)], "hubs": {}, "parts": [[("t", 2), ("t", 3), ("t", "far")]],
           "sinks": [("t", "far")]}
    assert solve(dict(b1, nets=[far]), 10)[0] is None and negotiate(dict(b1, nets=[far]), 5, log=None)[0] is None
    two = [{"name": n_, "edges": [(("t", 0), g1(*v), 0), (g1(*v), ("t", 1), 0)], "hubs": {}, "parts": [[("t", 0), ("t", 1)]]}
           for n_, v in (("A", (0, 0)), ("B", (9, 9)))]
    pr = dict(b1, nets=two, pairs=[((0, "e", (("t", 0), g1(0, 0))), (1, "n", g1(9, 9)))])
    assert solve(pr, 10)[0] is None and negotiate(pr, 10, log=None)[0] is None
    assert solve(dict(pr, pairs=[]), 10)[0] is not None and negotiate(dict(pr, pairs=[]), 10, log=None)[0] is not None
    alt = dict(b1, nets=[{"name": "A", "edges": [(("t", 0), g1(0, 0), 0), (g1(0, 0), ("t", 1), 0), (("t", 0), g1(0, 5), 50),
                                                 (g1(0, 5), ("t", 1), 0)], "hubs": {}, "parts": [[("t", 0), ("t", 1)]]}, two[1]],
               pairs=pr["pairs"])  # 便宜的小段和 B 冲突、贵的绕路不冲突：协商要靠互斥对加价改走绕路
    sa, _ = negotiate(alt, 20, log=None)
    assert sa is not None and (("t", 0), g1(0, 5), 50) in sa[0]["edges"], sa
    print("selftest ok", info, info2)


if __name__ == "__main__":
    main()
