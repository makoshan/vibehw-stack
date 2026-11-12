#!/usr/bin/env python3
"""以一颗 IC 为中心的逐脚摆件审查（pcbnew 只读）。

出处：移植自 BackplaneWorks/kistack 641ade9
skills/layout-placement/scripts/placement_audit.py 与 rules.py。
原版通过 KiCad IPC（kicad-python + shapely）读板；本移植改用 pcbnew.LoadBoard 只读，
几何用 pcbnew 的焊盘多边形与 SHAPE_POLY_SET 膨胀/布尔运算，距离自己算，不加新依赖。
RF 链（--chain / --rf-track / --tie）未移植。

原版许可（MIT）：
  Copyright 2026 American Embedded, LLC

  Permission is hereby granted, free of charge, to any person obtaining a copy of this
  software and associated documentation files (the "Software"), to deal in the Software
  without restriction, including without limitation the rights to use, copy, modify,
  merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
  permit persons to whom the Software is furnished to do so, subject to the following
  conditions:

  The above copyright notice and this permission notice shall be included in all copies
  or substantial portions of the Software.

  The American Embedded logo may not be reproduced without express written permission.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
  INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
  PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
  HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
  CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
  OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

五节输出，每节最差在前：
  pins    IC 每个信号脚 -> 同网最近小件焊盘的距离；同网电容紧挨着这个脚（离脚最近的焊盘间隙
          不超过电容自身长度）且它的地焊盘比信号焊盘更近这个脚时，标「地焊盘朝向芯片」，每颗都判。
          与原版不同：原版对任何小件、任何距离都判，远处的上下拉电阻、ESD 管、晶振全会误报（2026-11-10 Mako 定收窄）
  parts   每个小件焊盘 -> 同网最近 IC 脚的距离
  ground  小件地焊盘 -> 最近合法过孔位的距离（过孔直径 + 离别网铜间距，不压贴片焊盘，离板边够远）；
          1.2 mm 内找不到记「无」
  pairs   两个焊盘各接一个 IC 脚的件（晶振、飞电容）：两边距离和差
  fronts  信号脚往外直走或斜 45° 时，前方 --front mm 内一条走线宽通道被别网小件焊盘挡住
「小件」= 有铜焊盘不超过 4 个的件（原版定义）。距离只报数，不自带合格门限；--max-gap 无默认值。

规则（--track --clear --via --edge）默认取板子最严：所有网络的有效网络类、.kicad_pro 板级最小值、
焊盘与封装的间距覆盖。有自定义 .kicad_dru 时拒跑，除非四个数都显式给出；给的数比板子松也拒跑，
除非 --rules-override，此时每条输出都标「比板子松」。

退出码：0 已量完、无事实性缺陷；1 有事实性缺陷（地焊盘朝向芯片、无过孔位、扇出被堵、超 --max-gap）；
2 没法判（拒跑、找不到 IC、缺工程文件、读板失败）。

用法：
  python3 placement_audit.py board.kicad_pcb --ic U1 [--ground GND] [--front 2.0]
      [--track MM --clear MM --via MM --edge MM] [--rules-override] [--max-gap MM] [--out 新目录]
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys

NM = 1e-6
SEARCH = 1.2          # 过孔位搜索半径，mm（原版 via_gap 的 search）
ERR = 2000            # 多边形化误差，nm
NAMES = (("track", "线宽"), ("clear", "间距"), ("via", "过孔直径"), ("edge", "铜到板边"))
LOOSER = "比板子松"


class Refuse(Exception):
    """没法判：退出码 2。"""


# ---------- 纯几何（mm，y 向下）。区域 = [(外轮廓点列, [孔点列...]), ...] ----------

def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _seg_seg(a, b, c, d):
    d1, d2, d3, d4 = _cross(c, d, a), _cross(c, d, b), _cross(a, b, c), _cross(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4:
        return 0.0
    return min(_pt_seg(a, c, d), _pt_seg(b, c, d), _pt_seg(c, a, b), _pt_seg(d, a, b))


def _in_ring(p, ring):
    x, y, inside = p[0], p[1], False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i - 1], ring[i]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def in_region(p, reg):
    return any(_in_ring(p, o) and not any(_in_ring(p, h) for h in hs) for o, hs in reg)


def _edges(reg):
    for o, hs in reg:
        for ring in [o] + list(hs):
            for i in range(len(ring)):
                yield ring[i - 1], ring[i]


def bbox(reg):
    xs = [p[0] for o, _ in reg for p in o]
    ys = [p[1] for o, _ in reg for p in o]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def distance(A, B):
    """两区域的最短距离，相交或包含为 0（shapely 的 distance 语义）。"""
    if not A or not B:
        return math.inf
    for o, _ in A:
        if o and in_region(o[0], B):
            return 0.0
    for o, _ in B:
        if o and in_region(o[0], A):
            return 0.0
    eb = list(_edges(B))
    best = math.inf
    for a, b in _edges(A):
        for c, d in eb:
            v = _seg_seg(a, b, c, d)
            if v < best:
                best = v
                if best == 0.0:
                    return 0.0
    return best


def rect(s, v, length, width):
    """从 s 沿单位向量 v 长 length、宽 width 的平头通道（原版 buffer cap_style=2）。"""
    nx, ny = -v[1] * width / 2, v[0] * width / 2
    e = (s[0] + v[0] * length, s[1] + v[1] * length)
    return [([(s[0] + nx, s[1] + ny), (e[0] + nx, e[1] + ny), (e[0] - nx, e[1] - ny), (s[0] - nx, s[1] - ny)], [])]


# ---------- pcbnew 读板 ----------

class Pad:
    def __init__(self, ref, num, net, ps, reg, center, kind):
        self.ref, self.num, self.net, self.ps, self.poly, self.center, self.kind = ref, num, net, ps, reg, center, kind
        self.box = bbox(reg)

    @property
    def name(self):
        return f"{self.ref}.{self.num}"


# 认电容：位号 C / EC / CE 加数字（允许层次前缀，如 V4_C55、V4-C55），或值是电容值
# （100nF、4.7uF、10uF/16V、100nF_0402、.1uF、100n、4u7、1n5、2.2u；1N4148、2N7002、10uH 不算）。
# ponytail: 只靠位号和值认：位号、值都不像电容的电容会漏判；TP_C3 这类「_C数字」结尾的非电容会误认。要更准再读符号库 id。
CAP_REF = re.compile(r"(?:^|[_.\-])(?:E?C|CE)\d+[A-Za-z]?$")
CAP_VAL = re.compile(r"^(?:\d+(?:[.,]\d+)?|[.,]\d+)\s*[pnuµμm]?f(?![a-z])|^\d{1,3}[pnuµμ]\d{0,2}$|^\d+[.,]\d+[pnuµμ]$")
EPS = 0.005           # 判「间隙 ≤ 自身长度」的容差，mm；焊盘多边形化本身就有 ERR=2 µm 误差


class Footprint:
    def __init__(self, ref, x, y, pads, value="", orient=0.0):
        self.ref, self.x, self.y, self.pads, self.value = ref, x, y, pads, value
        self.is_cap = bool(CAP_REF.search(ref) or CAP_VAL.match(value.strip().lower()))
        # 件自身长度 = 全部铜焊盘外包络的长边，在封装自身坐标系里量（斜放不会变长）；
        # 自定义封装常没有 courtyard / 本体框，焊盘总有。0402 约 1.5 mm（本体 1.0）
        a = math.radians(orient)
        ca, sa = math.cos(a), math.sin(a)
        pts = [((px - x) * ca - (py - y) * sa, (px - x) * sa + (py - y) * ca)
               for p in pads for o, _ in p.poly for px, py in o]
        self.length = (max(max(u for u, _ in pts) - min(u for u, _ in pts),
                           max(v for _, v in pts) - min(v for _, v in pts)) if pts else 0.0)


def _chain(c):
    return [(c.CPoint(j).x * NM, c.CPoint(j).y * NM) for j in range(c.PointCount())]


def to_region(ps):
    return [(_chain(ps.Outline(i)), [_chain(ps.Hole(i, h)) for h in range(ps.HoleCount(i))])
            for i in range(ps.OutlineCount())]


def load(k, path):
    b = k.LoadBoard(path)
    if b is None:
        raise Refuse(f"读不了板子 {path}")
    fps = []
    for f in b.GetFootprints():
        ref = f.GetReference()
        pads = []
        for p in f.Pads():
            if not p.IsOnCopperLayer():
                continue  # 只有锡膏/阻焊开窗、没有铜的焊盘，原版同样跳过
            ls = p.GetLayerSet()
            lay = k.F_Cu if ls.Contains(k.F_Cu) else k.B_Cu if ls.Contains(k.B_Cu) else None
            if lay is None:
                lay = next(iter(ls.CuStack()))
            ps = k.SHAPE_POLY_SET()
            p.TransformShapeToPolygon(ps, lay, 0, ERR, k.ERROR_INSIDE)
            kind = "thru_hole" if p.GetAttribute() in (k.PAD_ATTRIB_PTH, k.PAD_ATTRIB_NPTH) else "smd"
            pos = p.GetPosition()
            pads.append(Pad(ref, p.GetNumber(), p.GetNetname() or None, ps, to_region(ps),
                            (pos.x * NM, pos.y * NM), kind))
        pos = f.GetPosition()
        fps.append(Footprint(ref, pos.x * NM, pos.y * NM, pads, f.GetValue(), f.GetOrientationDegrees()))
    # 带网络的铜图形（原版的 fixed_copper）；过孔穿全部层，所以各铜层都算。
    # 与原版不同：原版把图形边线围成实心区域（圆环当实心圆盘、开口线段忽略、只看 F/B 层），
    # 这里按 pcbnew 的真实铜形（含线宽）成形。不带网络的铜、铺铜、禁布区两边都不算障碍。
    fixed = []
    for d in b.GetDrawings():
        if d.GetClass() != "PCB_SHAPE" or not d.IsOnCopperLayer() or not d.GetNetname():
            continue
        ps = k.SHAPE_POLY_SET()
        d.TransformShapeToPolygon(ps, d.GetLayer(), 0, ERR, k.ERROR_INSIDE)
        fixed.append((d.GetNetname(), ps, to_region(ps)))
    outline = k.SHAPE_POLY_SET()
    if not b.GetBoardPolygonOutlines(outline, False) or outline.OutlineCount() == 0:
        outline = None
    return b, fps, fixed, outline


# ---------- 规则（照原版 rules.py 的思路） ----------

def _custom_rules(board):
    dru = os.path.splitext(board)[0] + ".kicad_dru"
    if not os.path.isfile(dru):
        return None
    with open(dru, encoding="utf-8") as f:
        body = "\n".join(line.split("#", 1)[0] for line in f.read().splitlines())
    return dru if re.search(r"\(\s*rule\b", body) else None


def _pair_nets(names):
    have, out = set(names), set()
    for n in names:
        for a, b in (("+", "-"), ("P", "N")):
            for x, y in ((a, b), (b, a)):
                if n.endswith(x) and n[:-1] + y in have:
                    out.add(n)
    return out


def _mm(nm):
    return round(nm / 1e6, 6)


def _strictest(cands):
    cands = [c for c in cands if c[0] is not None and c[0] > 0]
    return max(cands, key=lambda c: c[0]) if cands else (None, None)


def derive(k, board_path, b):
    """板子最严的规则 {key: (mm, 出处)}、问题、备注；缺 .kicad_pro 返回 None。"""
    pro = os.path.splitext(board_path)[0] + ".kicad_pro"
    if not os.path.isfile(pro):
        return None, [f"缺 {os.path.basename(pro)}"], []
    try:
        with open(pro, encoding="utf-8") as f:
            j = json.load(f)
    except (OSError, ValueError) as e:
        raise Refuse(f"{pro} 读不了（{e}）")
    r = ((j.get("board") or {}).get("design_settings") or {}).get("rules") or {}
    cand = {key: [] for key, _ in NAMES}
    for key, field in (("track", "min_track_width"), ("clear", "min_clearance"),
                       ("via", "min_via_diameter"), ("edge", "min_copper_edge_clearance")):
        if isinstance(r.get(field), (int, float)) and not isinstance(r.get(field), bool):
            cand[key].append((float(r[field]), f".kicad_pro 板级最小值 {field}"))
    problems, notes = [], []
    nets = [n.GetNetname() for n in b.GetNetsByNetcode().values() if n.GetNetname()]
    paired = _pair_nets(list(nets))
    pairs = []
    ns = b.GetDesignSettings().m_NetSettings
    for name in sorted(nets):
        # 有效网络类（KiCad 自己按显式指定、模式、原理图指令合成的）；
        # NETINFO_ITEM.GetNetClass() 在 Python 里只拿到裸指针，用不了
        nc = ns.GetEffectiveNetClass(name)
        cname = nc.GetHumanReadableName()
        for key, has, get, label in (("track", nc.HasTrackWidth, nc.GetTrackWidth, "track_width"),
                                     ("clear", nc.HasClearance, nc.GetClearance, "clearance"),
                                     ("via", nc.HasViaDiameter, nc.GetViaDiameter, "via_diameter")):
            if not has():
                problems.append(f"网络类 {cname}（网 {name}）没有 {label}")
            else:
                cand[key].append((_mm(get()), f"网络类 {cname}，网 {name}"))
        if name in paired:
            pairs.append(name)
            if nc.HasDiffPairWidth() and nc.GetDiffPairWidth():
                cand["track"].append((_mm(nc.GetDiffPairWidth()), f"网络类 {cname} 差分线宽，网 {name}"))
    for f in b.GetFootprints():
        ref = f.GetReference()
        v = f.GetLocalClearance()
        if v is not None:
            cand["clear"].append((_mm(v), f"封装 {ref} 间距覆盖"))
        for p in f.Pads():
            v = p.GetLocalClearance()
            if v is not None:
                cand["clear"].append((_mm(v), f"焊盘 {ref}.{p.GetNumber()} 间距覆盖"))
    if pairs:
        notes.append(f"{len(pairs)} 根网是差分对（{', '.join(pairs[:6])}{'…' if len(pairs) > 6 else ''}），"
                     "按单线、至少差分线宽处理，不查耦合和线距")
    return {key: _strictest(cand[key]) for key, _ in NAMES}, problems, notes


def resolve(k, board, a, b):
    flags = {key: getattr(a, key) for key, _ in NAMES}
    bad = [f"--{key} {v}" for key, v in flags.items() if v is not None and not (math.isfinite(v) and v >= 0)]
    if bad:
        raise Refuse("规则必须是非负有限数：" + ", ".join(bad))
    derived, problems, notes = derive(k, board, b)
    dru = _custom_rules(board)
    all_flags = all(v is not None for v in flags.values())
    four = "--track --clear --via --edge"
    if derived is None and not all_flags:
        raise Refuse(f"{problems[0]}，规则未知；请给出 {four}")
    if problems and not all_flags:
        raise Refuse("；".join(problems) + f"；要跑请给出 {four}")
    if dru and not all_flags:
        ref = "，".join(f"{label} {derived[key][0]:.3f} mm（{derived[key][1]}）" for key, label in NAMES
                       if derived and derived.get(key, (None,))[0] is not None)
        raise Refuse(f"{dru} 有自定义 DRC 规则，本脚本不解析；请按这些规则给出 {four}"
                     + (f"（板子不含 .kicad_dru 时最严：{ref}；给的数不能比这松）" if ref else ""))
    if dru:
        notes.append(f"{os.path.basename(dru)} 里的自定义规则未解析，检查用的是给出的四个数")
    vals, src, looser = {}, {}, []
    for key, label in NAMES:
        have = derived.get(key, (None, None)) if derived else (None, None)
        if flags[key] is not None:
            vals[key], src[key] = flags[key], "命令行参数"
            if have[0] is not None and flags[key] < have[0] - 1e-9:
                looser.append((key, f"{label} {flags[key]:.3f} mm 比板子的 {have[0]:.3f} mm（{have[1]}）松"))
        elif have[0] is not None:
            vals[key], src[key] = have
        else:
            raise Refuse(f"板子没给出{label}；请给出 --{key}")
    if looser and not a.rules_override:
        raise Refuse("；".join(m for _, m in looser) + "；用松的规则会报出板子没有的余量。"
                     "只有用户要求时才加 --rules-override")
    for key, m in looser:
        src[key] += "，" + LOOSER
        notes.append("规则覆盖：" + m + "；结果在板子自己的规则下不成立")
    return vals, src, notes, bool(looser)


# ---------- 五项检查 ----------

def _union(k, sets):
    u = k.SHAPE_POLY_SET()
    for s in sets:
        u.BooleanAdd(s)
    return u


def _near(box, b2, m):
    return box and b2 and b2[0] - m <= box[2] and box[0] - m <= b2[2] and b2[1] - m <= box[3] and box[1] - m <= b2[3]


def via_gap(k, pad, fps, fixed, outline_ok, rules):
    """地焊盘到最近合法过孔位的距离 mm；SEARCH 内没有返回 None（原版 via_gap）。"""
    via, clear = rules["via"], rules["clear"]
    r = via / 2
    m = SEARCH + 2 * r + clear + 0.1   # 只取附近的铜，结果和全板一样
    others, smd = [], []
    for fp in fps:
        for p in fp.pads:
            if not _near(pad.box, p.box, m):
                continue
            if p.net != pad.net:
                others.append(p.ps)
            if p.kind == "smd":
                smd.append(p.ps)
    others += [ps for n, ps, reg in fixed if n != pad.net and _near(pad.box, bbox(reg), m)]
    cand = k.SHAPE_POLY_SET(pad.ps)
    cand.Inflate(round((SEARCH + r) / NM), k.CORNER_STRATEGY_ROUND_ALL_CORNERS, ERR)
    cand.BooleanIntersection(outline_ok)
    if others:
        u = _union(k, others)
        u.Inflate(round((clear + r) / NM), k.CORNER_STRATEGY_ROUND_ALL_CORNERS, ERR)
        cand.BooleanSubtract(u)
    if smd:
        u = _union(k, smd)
        u.Inflate(round(r / NM), k.CORNER_STRATEGY_ROUND_ALL_CORNERS, ERR)
        cand.BooleanSubtract(u)
    if cand.OutlineCount() == 0 or cand.Area() <= 0:
        return None
    return max(0.0, distance(to_region(cand), pad.poly) - r)


def audit(k, fps, fixed, outline, ic, gnd, rules, front):
    """gnd 是地网名集合。"""
    small = [fp for fp in fps if len(fp.pads) <= 4 and fp.ref != ic.ref]
    icpins = [p for p in ic.pads if p.net and p.kind == "smd"]
    if not icpins:
        # 原版只量贴片脚；全是插件脚时五节全空，不能当成「量完无缺陷」
        raise Refuse(f"{ic.ref} 没有带网络的贴片脚，没法量")
    out = {}

    rows = []
    for p in icpins:
        if p.net in gnd:
            continue
        cands = [(distance(p.poly, q.poly), fp, q) for fp in small for q in fp.pads if q.net == p.net]
        if not cands:
            continue
        d, fp, q = min(cands, key=lambda t: t[0])
        # 朝向只判电容，且只判紧挨着脚的：电容离脚最近的焊盘（不论哪个网）间隙 ≤ 电容自身长度。
        # 同一脚有几颗紧挨着的同网电容就判几颗；比的是离脚最近的地焊盘和离脚最近的同网焊盘（三端、X2Y 电容）
        row = {"pin": p.num, "net": p.net, "gap_mm": d, "part_pad": q.name, "ground_pad_faces_pin": False,
               "faced_caps": [], "cap_pad": None, "cap_gap_mm": None, "cap_len_mm": None}
        caps = {id(cf): cf for _, cf, _ in cands if cf.is_cap}  # 按件去重，不按位号：重号的两颗都要判
        near = None
        for cf in caps.values():
            cd = min(distance(p.poly, x.poly) for x in cf.pads)
            cq = min((x for x in cf.pads if x.net == p.net), key=lambda x: distance(p.poly, x.poly))
            if near is None or cd < near[0]:
                near = (cd, cf, cq)
            gs = [x for x in cf.pads if x.net in gnd]
            if gs and cd <= cf.length + EPS:
                gp = min(gs, key=lambda x: math.dist(x.center, p.center))
                if math.dist(gp.center, p.center) < math.dist(cq.center, p.center):
                    row["faced_caps"].append(cq.name)
        if near:
            row.update(cap_pad=near[2].name, cap_gap_mm=near[0], cap_len_mm=near[1].length)
        row["faced_caps"].sort()
        row["ground_pad_faces_pin"] = bool(row["faced_caps"])
        rows.append(row)
    out["pins"] = sorted(rows, key=lambda x: -x["gap_mm"])

    rows = []
    for fp in small:
        for q in fp.pads:
            if not q.net or q.net in gnd:
                continue
            pins = [p for p in icpins if p.net == q.net]
            if not pins:
                continue
            d, p = min(((distance(q.poly, p.poly), p) for p in pins), key=lambda t: t[0])
            rows.append({"part_pad": q.name, "net": q.net, "gap_mm": d, "pin": p.num})
    out["parts"] = sorted(rows, key=lambda x: -x["gap_mm"])

    rows = []
    if outline is not None:
        ok = k.SHAPE_POLY_SET(outline)
        ok.Deflate(round((rules["edge"] + rules["via"] / 2) / NM), k.CORNER_STRATEGY_ROUND_ALL_CORNERS, ERR)
        for fp in small:
            for q in fp.pads:
                if q.net in gnd:
                    rows.append({"ground_pad": q.name, "via_gap_mm": via_gap(k, q, fps, fixed, ok, rules)})
    out["ground"] = sorted(rows, key=lambda x: -(math.inf if x["via_gap_mm"] is None else x["via_gap_mm"]))

    rows = []
    for fp in small:
        sig = [q for q in fp.pads if q.net and q.net not in gnd]
        if len(sig) != 2 or len({q.net for q in sig}) != 2:
            continue
        ds = []
        for q in sig:
            pins = [p for p in icpins if p.net == q.net]
            if not pins:
                break
            ds.append((min(distance(q.poly, p.poly) for p in pins), q.name))
        if len(ds) == 2:
            rows.append({"part": fp.ref, "a": ds[0][1], "a_gap_mm": ds[0][0], "b": ds[1][1], "b_gap_mm": ds[1][0],
                         "difference_mm": abs(ds[0][0] - ds[1][0])})
    out["pairs"] = sorted(rows, key=lambda x: -x["difference_mm"])

    rows, checked = [], []
    w = rules["track"] + 2 * rules["clear"]
    small_nets = {q.net for fp in small for q in fp.pads}
    for p in icpins:
        # 原版：接小件的脚、地脚不查；另跳过 KiCad 的单脚网 unconnected-*（不需要扇出）
        if p.net in gnd or p.net in small_nets or p.net.startswith("unconnected-"):
            continue
        checked.append(p.num)
        dx, dy = p.center[0] - ic.x, p.center[1] - ic.y
        ux, uy = ((1 if dx > 0 else -1), 0) if abs(dx) >= abs(dy) else (0, (1 if dy > 0 else -1))
        x0, y0, x1, y1 = p.box
        sx = (x1 if ux > 0 else x0) if ux else p.center[0]
        sy = (y1 if uy > 0 else y0) if uy else p.center[1]
        hits, ok = set(), False
        for ang in (0, 45, -45):
            t = math.radians(ang)
            v = (ux * math.cos(t) - uy * math.sin(t), ux * math.sin(t) + uy * math.cos(t))
            ch = rect((sx, sy), v, front, w)
            cb = bbox(ch)
            h = {q.name for fp in small for q in fp.pads
                 if q.net != p.net and _near(cb, q.box, 0) and distance(q.poly, ch) == 0.0}
            if not h:
                ok = True
                break
            hits |= h
        if not ok:
            rows.append({"pin": p.num, "net": p.net, "blocked_by": sorted(hits)})
    out["fronts"] = sorted(rows, key=lambda x: (-len(x["blocked_by"]), x["pin"]))
    out["fronts_checked_pins"] = checked
    return out


# ---------- 输出 ----------

def _f(v):
    return "无" if v is None else f"{v:6.3f} mm"


def render(res, front, max_gap):
    L = []
    over = (lambda d: max_gap is not None and d > max_gap)
    L.append(f"pins（IC 信号脚 -> 同网最近小件焊盘）")
    for r in res["pins"]:
        face = ""
        if r["ground_pad_faces_pin"]:
            face = "  地焊盘朝向芯片" + ("" if r["faced_caps"] == [r["part_pad"]] else f"（{', '.join(r['faced_caps'])}）")
        flag = face + ("  超门限" if over(r["gap_mm"]) else "")
        L.append(f"  脚 {r['pin']:>4} {r['net']:16} {_f(r['gap_mm'])} 到 {r['part_pad']}{flag}")
    L.append(f"parts（小件焊盘 -> 同网最近 IC 脚）")
    for r in res["parts"]:
        L.append(f"  {r['part_pad']:9} {r['net']:16} {_f(r['gap_mm'])} 到脚 {r['pin']}"
                 f"{'  超门限' if over(r['gap_mm']) else ''}")
    L.append(f"ground（小件地焊盘 -> 最近合法过孔位，{SEARCH} mm 内没有记「无」）")
    if res.get("ground_skipped"):
        L.append(f"  未查：{res['ground_skipped']}")
    for r in res["ground"]:
        L.append(f"  {r['ground_pad']:9} {_f(r['via_gap_mm'])}")
    L.append(f"pairs（两个焊盘各接一个 IC 脚的两脚件）")
    for r in res["pairs"]:
        L.append(f"  {r['part']:6} {r['a']} {r['a_gap_mm']:.3f} mm，{r['b']} {r['b_gap_mm']:.3f} mm，"
                 f"差 {r['difference_mm']:.3f} mm")
    L.append(f"fronts（直走和斜 45° 前方 {front} mm 都没有一条走线宽通道的信号脚；"
             f"查了 {len(res['fronts_checked_pins'])} 个不接小件的信号脚）")
    for r in res["fronts"]:
        L.append(f"  脚 {r['pin']:>4} {r['net']:16} 被堵：{', '.join(r['blocked_by'])}")
    return L


def defects(res, max_gap):
    d = [f"地焊盘朝向芯片：脚 {r['pin']} -> {c}" for r in res["pins"] for c in r["faced_caps"]]
    d += [f"无过孔位：{r['ground_pad']}" for r in res["ground"] if r["via_gap_mm"] is None]
    d += [f"扇出被堵：脚 {r['pin']}（{', '.join(r['blocked_by'])}）" for r in res["fronts"]]
    if max_gap is not None:
        d += [f"超门限：脚 {r['pin']} -> {r['part_pad']} {r['gap_mm']:.3f} mm" for r in res["pins"] if r["gap_mm"] > max_gap]
        d += [f"超门限：{r['part_pad']} -> 脚 {r['pin']} {r['gap_mm']:.3f} mm" for r in res["parts"] if r["gap_mm"] > max_gap]
    return d


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description="以一颗 IC 为中心的逐脚摆件审查（pcbnew 只读）")
    ap.add_argument("board")
    ap.add_argument("--ic", required=True, help="IC 位号")
    ap.add_argument("--ground", nargs="+", default=["GND"],
                    help="地网名，默认 GND；可给多个（如 AGND GND），原版只认一个。"
                         "有 AGND 的板必须给全，否则 AGND 脚被当成信号脚、地焊盘朝向漏查")
    ap.add_argument("--front", type=float, default=2.0, help="扇出通道长度 mm，默认 2.0（原版）")
    ap.add_argument("--track", type=float, help="线宽 mm（默认板子最严）")
    ap.add_argument("--clear", type=float, help="间距 mm（默认板子最严）")
    ap.add_argument("--via", type=float, help="过孔直径 mm（默认板子最严）")
    ap.add_argument("--edge", type=float, help="铜到板边 mm（默认板子最严）")
    ap.add_argument("--rules-override", action="store_true", help="允许比板子松的参数，输出逐条标「比板子松」")
    ap.add_argument("--max-gap", type=float, help="距离门限 mm；不给就只报数（门限待 Mako 定）")
    ap.add_argument("--out", help="写 JSON 证据的新目录（已存在则拒绝）")
    a = ap.parse_args(argv)

    board = os.path.abspath(a.board)
    if a.out and os.path.exists(a.out):
        print(f"拒跑：证据目录 {a.out} 已存在", file=sys.stderr)
        return 2
    srcs = [p for p in (board, os.path.splitext(board)[0] + ".kicad_pro", os.path.splitext(board)[0] + ".kicad_dru")
            if os.path.isfile(p)]
    result = {"tool": "placement_audit.py（移植自 kistack 641ade9）", "board": board, "ic": a.ic, "ground": a.ground,
              "front_mm": a.front, "max_gap_mm": a.max_gap, "source_sha256": {p: sha(p) for p in srcs},
              "implementation_sha256": sha(os.path.abspath(__file__)), "not_ported": ["--chain", "--rf-track", "--tie"]}
    lines, code = [], 2
    try:
        if not os.path.isfile(board):
            raise Refuse(f"没有板子文件 {board}")
        if a.max_gap is not None and not (math.isfinite(a.max_gap) and a.max_gap >= 0):
            raise Refuse("--max-gap 必须是非负有限数")
        if not (math.isfinite(a.front) and a.front > 0):
            raise Refuse("--front 必须是正数")
        if sys.platform == "darwin":
            # Mac 上 KiCad 自带 Python 读板前先建 wx.App（同 layout_gate.py）；
            # 无显示的 Linux 容器里建它会直接退出进程，所以只在 Mac 上建
            import wx
            _app = wx.App(False)  # noqa: F841
        import pcbnew as k
        result["kicad_version"] = k.Version()
        b, fps, fixed, outline = load(k, board)
        vals, src, notes, tag = resolve(k, board, a, b)
        result["rules"] = {key: {"mm": vals[key], "source": src[key]} for key, _ in NAMES}
        result["rules_notes"] = notes
        result["looser_than_board"] = tag
        lines.append("规则：" + "，".join(f"{label} {vals[key]:.3f} mm（{src[key]}）" for key, label in NAMES))
        lines += ["规则：" + n for n in notes]
        byref = {fp.ref: fp for fp in fps}
        if a.ic not in byref:
            raise Refuse(f"板上没有位号 {a.ic}")
        missing = set(a.ground) - {p.net for fp in fps for p in fp.pads}
        if missing:
            raise Refuse(f"板上没有地网 {' '.join(sorted(missing))}；请用 --ground 指定")
        res = audit(k, fps, fixed, outline, byref[a.ic], set(a.ground), vals, a.front)
        if outline is None:
            res["ground_skipped"] = "板子没有闭合的 Edge.Cuts 外形，没法定合法过孔位"
        lines += render(res, a.front, a.max_gap)
        dl = defects(res, a.max_gap)
        if outline is None:
            result["sections"] = res  # 已量的各节也留进证据
            raise Refuse(res["ground_skipped"])
        lines.append(f"事实性缺陷 {len(dl)} 项")
        lines += ["  " + x for x in dl]
        if tag:
            for rows in res.values():
                for r in rows if isinstance(rows, list) else []:
                    if isinstance(r, dict):
                        r["looser_than_board"] = True
        result.update(sections=res, defects=dl)
        code = 1 if dl else 0
    except Refuse as e:
        lines.append(f"拒跑/没法判：{e}")
        result["refused"] = str(e)
        code = 2
    except Exception as e:  # 读板或几何出错同样算没法判
        lines.append(f"没法判：{type(e).__name__}: {e}")
        result["error"] = f"{type(e).__name__}: {e}"
        code = 2
    result["source_unchanged"] = all(os.path.isfile(p) and sha(p) == h for p, h in result["source_sha256"].items())
    if not result["source_unchanged"]:
        lines.append("没法判：运行期间源文件变了")
        code = 2
    result["exit_code"] = code
    if result.get("looser_than_board"):
        lines = [x + f"  [{LOOSER}]" for x in lines]  # 每条输出都标出来
    print("\n".join(lines))
    if a.out:
        os.makedirs(a.out)
        with open(os.path.join(a.out, "audit.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write("\n")
        with open(os.path.join(a.out, "audit.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
