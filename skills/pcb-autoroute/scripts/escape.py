#!/usr/bin/env python3
"""出口预检：只看板上已固定的铜，判断每个未连缺口物理上有没有路（不布线）。

用法：
  uv run --no-project --with numpy --with scipy --with pillow python escape.py 板.kicad_pcb [--prove 目录] [--json 输出.json]
  uv run --no-project --with numpy --with scipy --with pillow python escape.py --selftest

做法：
  1. KiCad 自带 Python 导出每层的铜（焊盘、线、孔、已铺铜，按网）、钻孔、板框；kicad-cli DRC 给出未连缺口。
  2. 按 pitch 栅格化。对缺口所在的网 N：别网铜按「间距 + 半线宽」、板外按「板边距 + 半线宽」膨胀成障碍，
     本网铜一律可走；每层求可走区的 8 连通块。
  3. 能放下通孔的格点（各层离别网铜 ≥ 间距 + 半孔径、离别的钻孔 ≥ 孔距 + 半钻孔、孔环不碰任何贴片焊盘、离板边够远），
     以及本网已有的孔，把各层的连通块连起来。
  4. 缺口两端落在同一个三维连通块 = 有路；否则无路，并报每一端能走到的面积和可打孔位数。
     铺铜盖住过半块板的层当平面层：只许过孔穿，不许走线（割地平面的「捷径」不算路）。
  5. --prove：对有路的缺口，间距多收紧一格重判；仍有路就在窗口里求最短路，写进板子副本跑 DRC，
     缺口消失、别网没多出未连、没有新违规 = 有路（已证实）。放宽一格通、收紧一格不通 = 临界（差在 ±1 格精度内），
     --prove 时按实际间距再求一次路交给 DRC 裁决。
  判定一律往宽松取（每个距离让出一个栅格），所以「无路」是确定的，「有路」只说明拓扑上不被堵死，
  不保证布得进（没算别的缺口互相抢道，也没算自定义 DRU 里更严的局部规则）。
"""
import argparse, json, os, re, subprocess, sys, tempfile
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

KPY = os.environ.get("KICAD_PYTHON",
                     "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3")

# 在 KiCad 自带 Python 里跑：导出几何和规则（它有 pcbnew，没有 scipy）
EXPORT = r'''
import pcbnew as k, sys, json
b = k.LoadBoard(sys.argv[1]); ds = b.GetDesignSettings(); MM = 1e6; ERR = int(0.002 * MM)
layers = [b.GetLayerName(l) for l in b.GetEnabledLayers().CuStack()]
ids = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
def polys(ps):
    out = []
    for i in range(ps.OutlineCount()):
        ol = ps.Outline(i)
        holes = [[[h.CPoint(j).x / MM, h.CPoint(j).y / MM] for j in range(h.PointCount())] for h in
                 (ps.Hole(i, n) for n in range(ps.HoleCount(i)))]
        out.append([[[ol.CPoint(j).x / MM, ol.CPoint(j).y / MM] for j in range(ol.PointCount())], holes])
    return out
import re
ESC = {"slash": "/", "backslash": "\\", "lt": "<", "gt": ">", "colon": ":", "dblquote": '"', "quote": "'",
       "tab": "\t", "return": "\n", "space": " "}
def unesc(s):  # DRC 描述里是 GetNetname 去掉 {slash} 这类转义后的全名；GetDisplayNetname 会把层次路径截掉，不能用
    return re.sub(r"\{(\w+)\}", lambda m: ESC.get(m[1], m[0]), s)
def nn(item):
    return unesc(item.GetNetname())
def shape(item, lid):
    ps = k.SHAPE_POLY_SET(); item.TransformShapeToPolygon(ps, lid, 0, ERR, k.ERROR_INSIDE); return polys(ps)
copper = {n: [] for n in layers}   # 层 -> [(网, 多边形, 种类)]
holes, smd, zone_outline, segs = [], {n: [] for n in layers}, {n: [] for n in layers}, []
keepout = {"tracks": {n: [] for n in layers}, "vias": []}  # 禁布区（板级 + 封装自带）
for z in list(b.Zones()) + [z for fp in b.GetFootprints() for z in fp.Zones()]:
    if z.GetIsRuleArea():
        ps = z.Outline()
        for n in layers:
            if z.IsOnLayer(ids[n]) and z.GetDoNotAllowTracks():
                keepout["tracks"][n].append(polys(ps))
        if z.GetDoNotAllowVias():
            keepout["vias"].append(polys(ps))
        continue
    for n in layers:
        if z.IsOnLayer(ids[n]):
            zone_outline[n].append(polys(z.Outline()))  # 平面层按轮廓认，没填充的铺铜也算
            if z.IsFilled():
                copper[n].append([nn(z), polys(z.GetFilledPolysList(ids[n])), "zone"])
for t in b.GetTracks():
    for n in layers:
        if t.IsOnLayer(ids[n]):
            copper[n].append([nn(t), shape(t, ids[n]), "via" if t.GetClass() == "PCB_VIA" else "track"])
    if t.GetClass() == "PCB_VIA":
        holes.append([t.GetPosition().x / MM, t.GetPosition().y / MM, t.GetDrillValue() / MM / 2, nn(t)])
    elif t.GetClass() == "PCB_TRACK":  # 直线段的中心线：窗口重布接回老线时要落在精确的线心上
        segs.append([nn(t), b.GetLayerName(t.GetLayer()), t.GetStart().x / MM, t.GetStart().y / MM,
                     t.GetEnd().x / MM, t.GetEnd().y / MM, t.GetWidth() / MM])
for fp in b.GetFootprints():
    for p in fp.Pads():
        for n in layers:
            if p.IsOnLayer(ids[n]) and p.GetAttribute() != k.PAD_ATTRIB_NPTH:
                g = shape(p, ids[n]); copper[n].append([nn(p), g, "pad"])
                if p.GetAttribute() in (k.PAD_ATTRIB_SMD, k.PAD_ATTRIB_CONN):
                    smd[n].append(g)
        if p.GetDrillSize().x > 0:
            holes.append([p.GetPosition().x / MM, p.GetPosition().y / MM, min(p.GetDrillSize().x, p.GetDrillSize().y) / MM / 2, nn(p)])
ol = k.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(ol, True)
nc = ds.m_NetSettings.GetDefaultNetclass()
# 间距 DRC 会按网络类查，取大的；线宽/过孔 DRC 只查板级最小值（网络类里的是布线首选值），取板级
rules = {"clearance": max(ds.m_MinClearance, nc.GetClearance()) / MM, "track": ds.m_TrackMinWidth / MM,
         "via_d": ds.m_ViasMinSize / MM, "via_drill": ds.m_MinThroughDrill / MM,
         "hole_to_hole": ds.m_HoleToHoleMin / MM, "hole_clearance": ds.m_HoleClearance / MM, "edge": ds.m_CopperEdgeClearance / MM}
json.dump({"layers": layers, "copper": copper, "holes": holes, "smd": smd, "keepout": keepout, "zone_outline": zone_outline, "outline": polys(ol), "rules": rules, "segs": segs}, open(sys.argv[2], "w"))
'''


def export(board):
    with tempfile.TemporaryDirectory() as d:
        script, out = Path(d, "export.py"), Path(d, "geom.json")
        script.write_text(EXPORT)
        p = subprocess.run([KPY, str(script), str(board), str(out)], capture_output=True, text=True)
        if not out.exists():
            raise SystemExit(f"KiCad 导出失败：{p.stderr[-400:]}")
        return json.loads(out.read_text())


def vkey(x):
    """DRC 违规的身份：类型 + 各条目 uuid（没有就用描述 + 坐标）。只比第一个条目的坐标时，
    新违规的第一个条目常是老焊盘，会被同一焊盘上的老违规吃掉。"""
    return (x["type"], tuple(sorted(i.get("uuid") or f"{i['description']}@{i['pos']['x']:.3f},{i['pos']['y']:.3f}"
                                    for i in x["items"])))


def unconnected(board):
    with tempfile.TemporaryDirectory() as d:
        out = Path(d, "drc.json")
        p = subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "-o", str(out), str(board)],
                           capture_output=True, text=True)
        if p.returncode not in (0, 5) or not out.exists():
            raise SystemExit(f"kicad-cli 读不进 {board}：{p.stderr[-300:]}")
        d = json.loads(out.read_text())
    gaps = []
    for u in d["unconnected_items"]:
        nets = {n for i in u["items"] for n in re.findall(r"\[([^\]]+)\]", i["description"])}
        gaps.append({"net": nets.pop() if len(nets) == 1 else None, "items": u["items"]})
    return gaps


def item_layers(desc, layers):
    """DRC 条目所在的铜层：过孔（写成「F.Cu - B.Cu」）算全部层，焊盘/走线只算写出来的那层。
    只认层名不认中英文措辞。取种子只在这些层，否则两段异层、端点重合的短线会被当成同一端。"""
    found = [l for _, l in sorted((m.start(), l) for l in layers
                                  for m in re.finditer(rf"(?<![\w.]){re.escape(l)}(?![\w.])", desc))]
    if len(found) >= 2:
        return list(range(layers.index(found[0]), layers.index(found[-1]) + 1))
    return [layers.index(l) for l in found] or list(range(len(layers)))


class Raster:
    """毫米坐标 <-> 栅格下标；多边形（带洞）画进 int32 图。"""

    def __init__(self, outline, pitch):
        pts = np.array([p for ring, _ in outline for p in ring])
        self.x0, self.y0 = pts.min(0) - 0.5
        self.p = pitch
        self.w, self.h = (np.ceil((pts.max(0) + 0.5 - (self.x0, self.y0)) / pitch)).astype(int)

    def px(self, ring):
        return [((x - self.x0) / self.p, (y - self.y0) / self.p) for x, y in ring]

    def draw(self, img, polys, value):
        dr = ImageDraw.Draw(img)
        for ring, holes in polys:
            if len(ring) >= 3:
                dr.polygon(self.px(ring), fill=value)
            for h in holes:
                if len(h) >= 3:
                    dr.polygon(self.px(h), fill=0)

    def cell(self, x, y):
        return int(round((y - self.y0) / self.p)), int(round((x - self.x0) / self.p))

    def crop(self, bbox):
        """同一套栅格（原点对齐）里只取 bbox 这一块。"""
        c = Raster.__new__(Raster)
        c.p = self.p
        i0, j0 = (int(np.floor((v - o) / self.p)) for v, o in zip(bbox[:2], (self.x0, self.y0)))
        c.x0, c.y0 = self.x0 + max(i0, 0) * self.p, self.y0 + max(j0, 0) * self.p
        c.w = min(int(np.ceil((bbox[2] - c.x0) / self.p)) + 1, self.w - max(i0, 0))
        c.h = min(int(np.ceil((bbox[3] - c.y0) / self.p)) + 1, self.h - max(j0, 0))
        return c


def prep(geom, pitch, planes=None, bbox=None):
    """各网共用的栅格：铜（按网编号）、铺铜、板内、离板边/贴片焊盘/禁孔区的距离、禁走线区、平面层。
    bbox=(x0, y0, x1, y1) 时只画这一块（窗口求解用），坐标原点仍对齐整板栅格。"""
    L = geom["layers"]
    rs = Raster(geom["outline"], pitch)
    if bbox:
        rs = rs.crop(bbox)
    netidx = {n: i + 1 for i, n in enumerate(sorted({c[0] for l in L for c in geom["copper"][l]}))}
    # 铺铜单独一张图：别网铺铜不算障碍（新线放下后重铺，铺铜自己让开），本网铺铜可走
    net = np.zeros((len(L), rs.h, rs.w), np.int32)
    zone = np.zeros_like(net)
    for li, l in enumerate(L):
        img, zimg = Image.new("I", (rs.w, rs.h), 0), Image.new("I", (rs.w, rs.h), 0)
        for n, polys, k in geom["copper"][l]:
            rs.draw(zimg if k == "zone" else img, polys, netidx[n] if n else -1)  # 无网铜也是障碍
        net[li], zone[li] = np.asarray(img), np.asarray(zimg)
    img = Image.new("L", (rs.w, rs.h), 0)
    rs.draw(img, geom["outline"], 1)
    inside = np.asarray(img) > 0
    edge_d = ndimage.distance_transform_edt(inside) * pitch
    smd = np.zeros((len(L), rs.h, rs.w), bool)
    for li, l in enumerate(L):
        img = Image.new("L", (rs.w, rs.h), 0)
        for polys in geom["smd"][l]:
            rs.draw(img, polys, 1)
        smd[li] = np.asarray(img) > 0
    smd_d = ndimage.distance_transform_edt(~smd.any(0)) * pitch  # 离最近贴片焊盘的距离
    ko = geom.get("keepout", {"tracks": {l: [] for l in L}, "vias": []})
    no_track = np.zeros((len(L), rs.h, rs.w), bool)
    for li, l in enumerate(L):
        img = Image.new("L", (rs.w, rs.h), 0)
        for polys in ko["tracks"][l]:
            rs.draw(img, polys, 1)
        no_track[li] = np.asarray(img) > 0
    img = Image.new("L", (rs.w, rs.h), 0)
    for polys in ko["vias"]:
        rs.draw(img, polys, 1)
    no_via_d = ndimage.distance_transform_edt(np.asarray(img) == 0) * pitch  # 孔环不能进禁孔区
    if planes is None:  # 地/电源平面层上走线会割平面，只让过孔穿。只认内层：外层整面铺地照样走线
        planes = []
        full = Raster(geom["outline"], pitch)  # 平面层按整板判，窗口里不重判
        img = Image.new("L", (full.w, full.h), 0)
        full.draw(img, geom["outline"], 1)
        board = np.asarray(img) > 0
        for l in L[1:-1]:
            img = Image.new("L", (full.w, full.h), 0)
            for polys in geom.get("zone_outline", {}).get(l, []):
                full.draw(img, polys, 1)
            if ((np.asarray(img) > 0) & board).sum() > 0.5 * board.sum():
                planes.append(l)
    return {"rs": rs, "netidx": netidx, "net": net, "zone": zone, "inside": inside, "edge_d": edge_d, "smd_d": smd_d,
            "no_track": no_track, "no_via_d": no_via_d, "planes": planes, "plane_idx": [L.index(l) for l in planes],
            "pitch": pitch}


def net_maps(geom, P, name, slack):
    """网 name 的可走区：free（各层能放线心的格子）、connector（能换层的格子）、own/own_core/own_holes。"""
    R, L, rs, pitch = geom["rules"], geom["layers"], P["rs"], P["pitch"]
    N = P["netidx"].get(name, -9)
    net, zone, edge_d = P["net"], P["zone"], P["edge_d"]
    own = (net == N) | (zone == N)
    other = (net != 0) & (net != N)
    dist = np.stack([ndimage.distance_transform_edt(~o) * pitch for o in other])
    # 本网铜里只有离边缘 ≥ 半线宽的部分能当线心走，否则线的另一半会探出本网铜撞到邻居
    own_core = np.stack([ndimage.distance_transform_edt(o) * pitch >= R["track"] / 2 - slack for o in own])
    # 钻孔：所有孔给新孔划孔距；别网孔（含无网的 NPTH）还给走线划孔到铜距离；本网孔的钻孔圆盘就是现成换层点
    all_img, oth_img, own_img = (Image.new("L", (rs.w, rs.h), 0) for _ in range(3))
    for x, y, r, hn in geom["holes"]:
        (cy, cx), rr = rs.cell(x, y), r / pitch
        box = [cx - rr, cy - rr, cx + rr, cy + rr]
        for im in (all_img, own_img if hn == name else oth_img):
            ImageDraw.Draw(im).ellipse(box, fill=1)
    hole_d = ndimage.distance_transform_edt(np.asarray(all_img) == 0) * pitch
    oth_hole_d = ndimage.distance_transform_edt(np.asarray(oth_img) == 0) * pitch
    own_holes = np.asarray(own_img) > 0
    clear = (((dist >= R["clearance"] + R["track"] / 2 - slack) & (edge_d >= R["edge"] + R["track"] / 2 - slack)
              & (oth_hole_d >= R["hole_clearance"] + R["track"] / 2 - slack) & ~P["no_track"]) | own_core)
    free = clear.copy()
    for li in P["plane_idx"]:
        free[li] = own_core[li]
    via_ok = ((dist >= max(R["clearance"] + R["via_d"] / 2, R["hole_clearance"] + R["via_drill"] / 2) - slack).all(0)
              & (hole_d >= R["hole_to_hole"] + R["via_drill"] / 2 - slack)
              & (edge_d >= R["edge"] + R["via_d"] / 2 - slack)
              & (P["smd_d"] >= R["via_d"] / 2) & (P["no_via_d"] >= R["via_d"] / 2 - slack))  # 禁盘中孔：孔环整个离开任何贴片焊盘（本网的也算），不让格
    connector = (via_ok & clear.all(0)) | own_holes
    return {"own": own, "own_core": own_core, "free": free, "clear": clear, "connector": connector, "own_holes": own_holes}


def components(free, connector):
    """每层 8 连通块，再经换层点（通孔贯穿各层）并成三维连通区。返回 labels（各层块号）、comp（块号 -> 区号）。"""
    labels = np.zeros(free.shape, np.int32)
    n_nodes = 0
    for li in range(free.shape[0]):
        lab, n = ndimage.label(free[li], structure=np.ones((3, 3)))
        labels[li] = np.where(lab > 0, lab + n_nodes, 0)
        n_nodes += n
    ys, xs = np.nonzero(connector)
    rows = np.unique(labels[:, ys, xs].T, axis=0)  # 每个换层点在各层的连通块号（平面层多为 0）
    if len(rows):
        hub = rows[np.arange(len(rows)), (rows > 0).argmax(1)]  # 通孔贯穿：各层都连到该点第一个非零块
        a = np.concatenate([hub[rows[:, li] > 0] for li in range(free.shape[0])])
        bb = np.concatenate([rows[rows[:, li] > 0, li] for li in range(free.shape[0])])
    else:
        a = bb = np.array([], int)
    graph = coo_matrix((np.ones(len(a)), (a, bb)), shape=(n_nodes + 1, n_nodes + 1))
    _, comp = connected_components(graph, directed=False)
    return labels, comp, n_nodes


def analyze(geom, gaps, pitch=0.02, slack=None, debug=None, planes=None):
    """planes：只许过孔穿、不许走线的平面层；None 时自动认（铺铜盖住过半块板的层）。
    debug 给一个 dict 时，存下最后一个缺口的中间量（free/labels/comp/connector/own/rs/planes），排查用。"""
    L = geom["layers"]
    slack = pitch if slack is None else slack  # 默认每个距离让出一格：只会把「无路」判成「有路」，不会反过来
    P = prep(geom, pitch, planes)
    rs, netidx, planes, plane_idx = P["rs"], P["netidx"], P["planes"], P["plane_idx"]
    results = []
    for g in gaps:
        if g["net"] not in netidx:
            results.append({**g, "verdict": "未知", "why": "缺口的网名没认出", "planes": planes})
            continue
        M = net_maps(geom, P, g["net"], slack)
        own, free, connector, own_holes = M["own"], M["free"], M["connector"], M["own_holes"]
        labels, comp, n_nodes = components(free, connector)
        ys, xs = np.nonzero(connector)
        sides = []
        for it in g["items"][:2]:
            cy, cx = rs.cell(it["pos"]["x"], it["pos"]["y"])
            r = max(1, int(0.05 / pitch))
            on = np.zeros(len(L), bool)
            on[item_layers(it["description"], L)] = True
            win = labels[:, max(cy - r, 0):cy + r + 1, max(cx - r, 0):cx + r + 1]
            seed_own = own[:, max(cy - r, 0):cy + r + 1, max(cx - r, 0):cx + r + 1] & on[:, None, None]
            seeds = set(np.unique(win[seed_own & (win > 0)]).tolist())
            comps = {comp[s] for s in seeds}
            node_area = np.bincount(labels.ravel(), minlength=n_nodes + 1)
            nodes = np.isin(comp, list(comps)) if comps else np.zeros(n_nodes + 1, bool)
            nodes[0] = False
            area = float(node_area[nodes].sum() * pitch ** 2)
            vias = int(np.isin(labels[:, ys, xs], np.nonzero(nodes)[0]).any(0).sum()) if len(ys) else 0
            sides.append({"item": it["description"], "pos": it["pos"], "comps": comps, "area_mm2": round(area, 3),
                          "via_sites": vias, "seeded": bool(seeds)})
        if not all(s["seeded"] for s in sides):
            verdict, why = "未知", "缺口端点 0.05 mm 内没找到本网的铜"
        elif sides[0]["comps"] & sides[1]["comps"]:
            verdict, why = "有路", "只看固定铜，两端在同一个连通区"
        else:
            small = min(sides, key=lambda s: s["area_mm2"])
            verdict = "无路"
            why = (f"{small['item']} 一端只能走到 {small['area_mm2']} mm²、可打孔位 {small['via_sites']} 个"
                   if small["area_mm2"] < 1 or not small["via_sites"] else "两端各自的可走区不相通")
        for s in sides:
            s.pop("comps")
        results.append({"net": g["net"], "verdict": verdict, "why": why, "sides": sides, "planes": planes})
        if debug is not None:
            debug.update(free=free, labels=labels, comp=comp, connector=connector, own=own, rs=rs, planes=plane_idx,
                         own_holes=own_holes)
    return results


# 在 KiCad 自带 Python 里跑：把证据路径写进板子副本
APPLY = r'''
import pcbnew as k, sys, json, re
b = k.LoadBoard(sys.argv[1]); w = json.load(open(sys.argv[2])); MM = 1e6
ESC = {"slash": "/", "backslash": "\\", "lt": "<", "gt": ">", "colon": ":", "dblquote": '"', "quote": "'",
       "tab": "\t", "return": "\n", "space": " "}
unesc = lambda s: re.sub(r"\{(\w+)\}", lambda m: ESC.get(m[1], m[0]), s)
net = next(n for n in b.GetNetsByName().values() if unesc(n.GetNetname()) == w["net"]); ids = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
pt = lambda x, y: k.VECTOR2I(int(round(x * MM)), int(round(y * MM)))
for l, x1, y1, x2, y2 in w["segments"]:
    t = k.PCB_TRACK(b); t.SetStart(pt(x1, y1)); t.SetEnd(pt(x2, y2)); t.SetWidth(int(w["width"] * MM))
    t.SetLayer(ids[l]); t.SetNet(net); b.Add(t)
for x, y in w["vias"]:
    v = k.PCB_VIA(b); v.SetPosition(pt(x, y)); v.SetWidth(int(w["via_d"] * MM)); v.SetDrill(int(w["via_drill"] * MM))
    v.SetViaType(k.VIATYPE_THROUGH); v.SetNet(net); b.Add(v)
b.Save(sys.argv[3])
'''


def witness(geom, gap, pitch=0.02, margin=3.0, via_cost_mm=1.0, planes=None, slack=None):
    """「有路」的举证：间距多收紧一格重判，在两端附近的窗口里求最短路（同层 8 向、换层走通孔），
    返回走线段和孔。路径不求好看，只证明这条路真实存在；写进板子后由 KiCad DRC 判是否合法。"""
    D = {}
    slack = -pitch if slack is None else slack  # 默认间距多收紧一格，求出的路基本不会撞 DRC
    r = analyze(geom, [gap], pitch, slack=slack, debug=D, planes=planes)[0]
    if r["verdict"] != "有路":
        return None, f"按此间距判{r['verdict']}：{r['why']}"
    free, conn, own, rs, L = D["free"], D["connector"], D["own"], D["rs"], geom["layers"]
    pos = [rs.cell(it["pos"]["x"], it["pos"]["y"]) for it in gap["items"][:2]]
    m = int(margin / pitch)
    y0, y1 = max(min(p[0] for p in pos) - m, 0), min(max(p[0] for p in pos) + m, rs.h)
    x0, x1 = max(min(p[1] for p in pos) - m, 0), min(max(p[1] for p in pos) + m, rs.w)
    f, c = free[:, y0:y1, x0:x1], conn[y0:y1, x0:x1]
    nl, hh, ww = f.shape
    idx = np.arange(nl * hh * ww).reshape(f.shape)
    rows, cols, wts = [], [], []
    for dy, dx, wgt in ((0, 1, 1.0), (1, 0, 1.0), (1, 1, 2 ** .5), (1, -1, 2 ** .5)):
        sa = (slice(None), slice(0, hh - dy), slice(max(-dx, 0), ww - max(dx, 0)))  # 格子 A
        sb = (slice(None), slice(dy, hh), slice(max(dx, 0), ww + min(dx, 0)))       # A 往 (dy, dx) 挪一格
        ok = f[sa] & f[sb]
        rows.append(idx[sa][ok]); cols.append(idx[sb][ok]); wts.append(np.full(ok.sum(), wgt))
    vc = via_cost_mm / pitch
    route_layers = [li for li in range(nl) if li not in D["planes"]]
    for li, lj in zip(route_layers, route_layers[1:]):  # 通孔贯穿：相邻的可走线层串起来，平面层跳过
        ok = c & f[li] & f[lj]
        rows.append(idx[li][ok]); cols.append(idx[lj][ok]); wts.append(np.full(ok.sum(), vc))
    g = coo_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))), shape=(idx.size,) * 2).tocsr()
    rr = max(1, int(0.05 / pitch))
    seeds = []
    for (cy, cx), it in zip(pos, gap["items"][:2]):
        s = [idx[l, y - y0, x - x0] for l in item_layers(it["description"], L) for y in range(cy - rr, cy + rr + 1)
             for x in range(cx - rr, cx + rr + 1)
             if y0 <= y < y1 and x0 <= x < x1 and own[l, y, x] and f[l, y - y0, x - x0]]
        seeds.append(s)
    if not all(seeds):
        return None, "窗口里找不到端点"
    from scipy.sparse.csgraph import dijkstra
    dist, pred, _ = dijkstra(g, directed=False, indices=seeds[0], min_only=True, return_predecessors=True)
    end = min(seeds[1], key=lambda n: dist[n])
    if not np.isfinite(dist[end]):
        return None, f"窗口（端点外扩 {margin} mm）里没找到路，可能要绕得更远"
    path = [end]
    while pred[path[-1]] >= 0:
        path.append(pred[path[-1]])
    cells = [np.unravel_index(n, f.shape) for n in path[::-1]]
    mm = lambda y, x: (round(rs.x0 + (x + x0) * pitch, 4), round(rs.y0 + (y + y0) * pitch, 4))
    segs, vias, start = [], [], 0
    for i in range(1, len(cells) + 1):  # 同层同方向的格子并成一段；换层处放孔
        if i == len(cells) or cells[i][0] != cells[i - 1][0] or (
                i - start >= 2 and np.subtract(cells[i][1:], cells[i - 1][1:]).tolist()
                != np.subtract(cells[i - 1][1:], cells[i - 2][1:]).tolist()):
            if i - 1 > start:
                segs.append([L[cells[start][0]], *mm(*cells[start][1:]), *mm(*cells[i - 1][1:])])
            if i < len(cells) and cells[i][0] != cells[i - 1][0]:
                v = mm(*cells[i][1:])
                own_hole = D["own_holes"][cells[i][1] + y0, cells[i][2] + x0]  # 在本网已有孔上换层：不再叠一个孔
                if not own_hole and (not vias or vias[-1] != list(v)):
                    vias.append(list(v))
                start = i
            else:
                start = i - 1
    R = geom["rules"]
    return {"net": gap["net"], "segments": segs, "vias": vias, "width": R["track"], "via_d": R["via_d"],
            "via_drill": R["via_drill"],
            "length_mm": round(sum(((s[3] - s[1]) ** 2 + (s[4] - s[2]) ** 2) ** .5 for s in segs), 2)}, "ok"


def prove(board, w, out):
    """把证据路径写进副本，跑 DRC：本网缺口少了、别的网没多出未连、铜层没有新违规，才算证实。"""
    import shutil
    out = Path(out)
    out.unlink(missing_ok=True)  # 写板失败时不能拿上次的旧板去判
    with tempfile.TemporaryDirectory() as d:
        Path(d, "apply.py").write_text(APPLY)
        Path(d, "w.json").write_text(json.dumps(w))
        p = subprocess.run([KPY, str(Path(d, "apply.py")), str(board), str(Path(d, "w.json")), str(out)],
                           capture_output=True, text=True)
    if not out.exists():
        return {"proved": False, "error": f"写板失败：{p.stderr.strip()[-200:]}", "new_violations": []}
    for ext in (".kicad_pro", ".kicad_dru"):
        if Path(board).with_suffix(ext).exists():
            shutil.copy(Path(board).with_suffix(ext), out.with_suffix(ext))
    tag = f"[{w['net']}]"  # 带方括号精确匹配：「3V3」不能算进「3V3_AFE」

    def viol(b):
        with tempfile.TemporaryDirectory() as d:  # DRC 结果写临时目录，不覆盖用户板旁边的同名文件
            o = Path(d, "drc.json")
            subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "--all-track-errors", "--refill-zones",
                            "-o", str(o), str(b)], capture_output=True)
            dd = json.loads(o.read_text())
        vs = [x for x in dd["violations"] if not any(s in x["type"] for s in ("silk_", "lib_footprint", "courtyard", "text_"))]
        pos = {vkey(x): (x["type"], round(x["items"][0]["pos"]["x"], 2), round(x["items"][0]["pos"]["y"], 2)) for x in vs}
        mine = [tag in " ".join(i["description"] for i in u["items"]) for u in dd["unconnected_items"]]
        return Counter(vkey(x) for x in vs), pos, sum(mine), len(mine) - sum(mine)
    bv, _, bu, bo = viol(board)
    wv, wpos, wu, wo = viol(out)
    new = sorted(wpos[k] for k in (wv - bv).elements())
    # 别网铺铜不当障碍，所以路径可能横穿别网唯一的连接铺铜，重铺后把它切断：别网未连变多也算失败
    return {"net_unconnected_before": bu, "net_unconnected_after": wu, "other_unconnected_added": max(wo - bo, 0),
            "new_violations": new, "proved": wu < bu and not new and wo <= bo}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", nargs="?")
    ap.add_argument("--pitch", type=float, default=0.02, help="栅格 mm；BGA 0.5 间距用 0.02 以下")
    ap.add_argument("--json")
    ap.add_argument("--prove", metavar="目录", help="对判有路的缺口求一条具体路径，写进副本跑 DRC 证实（每个约 10–30 s）")
    ap.add_argument("--margin", type=float, default=10.0, help="举证时端点外扩多少 mm 找路")
    ap.add_argument("--planes", help="平面层（只许过孔穿），逗号分隔；不给就自动认")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.board:
        ap.error("要给板子路径")
    board = Path(a.board).resolve()
    geom, gaps = export(board), unconnected(board)
    planes = a.planes.split(",") if a.planes else None
    res = analyze(geom, gaps, a.pitch, planes=planes)
    print(f"规则 {geom['rules']}；栅格 {a.pitch} mm；平面层 {res[0].get('planes') if res else '-'}；未连 {len(gaps)}")
    loose = [i for i, r in enumerate(res) if r["verdict"] == "有路"]
    strict = analyze(geom, [gaps[i] for i in loose], a.pitch, slack=-a.pitch, planes=planes)  # 间距多收紧一格再判
    for i, s in zip(loose, strict):
        if s["verdict"] == "无路":
            res[i]["verdict"], res[i]["why"] = "临界", f"每个间距放宽一格能通、收紧一格不通，差在 ±{a.pitch} mm 精度内：{s['why']}"
    for gi, (g, r) in enumerate(zip(gaps, res)):
        if a.prove and r["verdict"] in ("有路", "临界"):
            # 有路的先按收紧一格求路；临界的按实际间距求，让 DRC 裁决
            w, why = witness(geom, g, a.pitch, a.margin, planes=planes, slack=0.0 if r["verdict"] == "临界" else None)
            if not w and r["verdict"] == "临界":
                r["why"] += f"；按实际间距也找不到路（{why}）"
                print(f"[{r['verdict']}] {r.get('net')}：{r['why']}")
                continue
            if not w:
                r["verdict"], r["why"] = "有路未证实", why
            else:
                Path(a.prove).mkdir(parents=True, exist_ok=True)
                safe = re.sub(r"[^\w.-]", "_", g["net"])  # 网名带 / 也行；f-string 里不放反斜杠，3.11 也能跑
                out = Path(a.prove) / f"witness-{gi:02d}-{safe}.kicad_pcb"  # 同网多缺口不互盖
                pr = prove(board, w, out)
                r["witness"] = {"board": str(out), "length_mm": w["length_mm"], "vias": len(w["vias"]), **pr}
                was = r["verdict"]
                r["verdict"] = "有路（已证实）" if pr["proved"] else ("临界" if was == "临界" else "有路未证实")
                r["why"] = (f"找到 {w['length_mm']} mm、{len(w['vias'])} 个通孔的路，写板 DRC 无新违规（路径不求好看，只证明存在）"
                            if pr["proved"] else
                            f"{r['why']}；按实际间距求的路写板后 DRC 报 {len(pr['new_violations'])} 处新违规 {pr['new_violations'][:2]}"
                            if was == "临界" else f"找到的路写板后 DRC 不过：新违规 {pr['new_violations'][:3]}，"
                            f"本网未连 {pr.get('net_unconnected_before')}→{pr.get('net_unconnected_after')}，"
                            f"别网多出未连 {pr.get('other_unconnected_added')} {pr.get('error', '')}")
        print(f"[{r['verdict']}] {r.get('net')}：{r['why']}")
    if a.json:
        Path(a.json).write_text(json.dumps({"rules": geom["rules"], "pitch": a.pitch, "gaps": res}, ensure_ascii=False, indent=1))


def selftest():
    """合成板：A 网两块铜之间隔一条 B 网的墙。墙有缺口 → 有路；墙封死但旁边能打孔、底层空 → 有路；
    墙封死、两层都封、也没孔位 → 无路。"""
    sq = lambda x0, y0, x1, y1: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]], []]
    rules = {"clearance": 0.1, "track": 0.127, "via_d": 0.4, "via_drill": 0.3, "hole_to_hole": 0.25,
             "hole_clearance": 0.15, "edge": 0.18}
    outline = [sq(0, 0, 4, 2)]
    pads = [["A", [sq(0.5, 0.9, 0.7, 1.1)], "pad"], ["A", [sq(3.3, 0.9, 3.5, 1.1)], "pad"]]
    gaps = [{"net": "A", "items": [{"description": "pad [A]", "pos": {"x": 0.6, "y": 1.0}},
                                   {"description": "pad [A]", "pos": {"x": 3.4, "y": 1.0}}]}]

    def run(top_wall, bot_wall, smd_all=False, keepout=None):
        geom = {"layers": ["F.Cu", "B.Cu"], "rules": rules, "outline": outline, "holes": [],
                "keepout": keepout or {"tracks": {"F.Cu": [], "B.Cu": []}, "vias": []},
                "copper": {"F.Cu": pads + top_wall, "B.Cu": bot_wall},
                "smd": {"F.Cu": [p[1] for p in pads] + ([[sq(0, 0, 4, 2)]] if smd_all else []), "B.Cu": []}}
        return analyze(geom, gaps, 0.02)[0]["verdict"]

    full = [["B", [sq(2.0, 0, 2.1, 2)], "track"]]
    gap = [["B", [sq(2.0, 0, 2.1, 0.6)], "track"], ["B", [sq(2.0, 1.4, 2.1, 2)], "track"]]
    assert run(gap, []) == "有路"
    assert run(full, []) == "有路"           # 顶层被墙，打孔到底层绕过去
    assert run(full, full) == "无路"         # 两层都被墙
    assert run(full, [], smd_all=True) == "无路"  # 顶层被墙，且处处是焊盘不许打孔
    strip = [sq(1.5, 0, 1.7, 2)]
    assert run([], [], keepout={"tracks": {"F.Cu": [strip], "B.Cu": [strip]}, "vias": []}) == "无路"  # 两层禁走线带
    assert run([], [], keepout={"tracks": {"F.Cu": [strip], "B.Cu": []}, "vias": []}) == "有路"     # 只禁顶层，打孔绕
    assert run([], [], keepout={"tracks": {"F.Cu": [strip], "B.Cu": []}, "vias": [[sq(0, 0, 4, 2)]]}) == "无路"  # 再全板禁孔
    # 三层，中间整层 GND 铺铜 = 平面层：过孔能穿，线不能走
    plane = {"layers": ["F.Cu", "In1.Cu", "B.Cu"], "rules": rules, "outline": outline, "holes": [],
             "smd": {"F.Cu": [p[1] for p in pads], "In1.Cu": [], "B.Cu": []}}
    def run3(bot):
        g = dict(plane, copper={"F.Cu": pads + full, "In1.Cu": [["GND", [sq(0, 0, 4, 2)], "zone"]], "B.Cu": bot},
                 zone_outline={"In1.Cu": [[sq(0, 0, 4, 2)]]})
        r = analyze(g, gaps, 0.02)[0]
        return r["verdict"], r["planes"]
    assert run3([]) == ("有路", ["In1.Cu"])   # 打孔穿过平面到底层绕
    assert run3(full) == ("无路", ["In1.Cu"])  # 底层也被墙，不许借平面层走线
    # 两层板正反面整面铺地：外层不算平面层，照样能走
    two = {"layers": ["F.Cu", "B.Cu"], "rules": rules, "outline": outline, "holes": [],
           "smd": {"F.Cu": [p[1] for p in pads], "B.Cu": []}, "copper": {"F.Cu": pads, "B.Cu": []},
           "zone_outline": {"F.Cu": [[sq(0, 0, 4, 2)]], "B.Cu": [[sq(0, 0, 4, 2)]]}}
    r2 = analyze(two, gaps, 0.02)[0]
    assert (r2["verdict"], r2["planes"]) == ("有路", []), r2
    # 两段异层短线端点重合、全板禁孔：两端不通（种子只取条目所在层，不能把两段当成同一端）
    stub = {"layers": ["F.Cu", "B.Cu"], "rules": rules, "outline": outline, "holes": [], "smd": {"F.Cu": [], "B.Cu": []},
            "keepout": {"tracks": {"F.Cu": [], "B.Cu": []}, "vias": [[sq(0, 0, 4, 2)]]},
            "copper": {"F.Cu": [["A", [sq(1.0, 0.95, 2.0, 1.05)], "track"]], "B.Cu": [["A", [sq(1.9, 0.95, 3.0, 1.05)], "track"]]}}
    sg = [{"net": "A", "items": [{"description": "走线 [A] (F.Cu)", "pos": {"x": 1.95, "y": 1.0}},
                                 {"description": "Track [A] on B.Cu", "pos": {"x": 1.95, "y": 1.0}}]}]
    assert analyze(stub, sg, 0.02)[0]["verdict"] == "无路"
    L4 = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    assert item_layers("过孔 [OE_N] (F.Cu - B.Cu)", L4) == [0, 1, 2, 3] and item_layers("Pad 2 [X] of R1 on B.Cu", L4) == [3]
    assert item_layers("Track [A] on Bottom", ["Top", "Mid1", "Bottom"]) == [2]  # 改过名的铜层
    assert item_layers("Via [A] on Top - Bottom", ["Top", "Mid1", "Bottom"]) == [0, 1, 2]
    assert item_layers("Track [A] on In1.Cu", ["F.Cu", "In1.Cu", "In11.Cu", "B.Cu"]) == [1]
    print("selftest ok")


if __name__ == "__main__":
    main()
