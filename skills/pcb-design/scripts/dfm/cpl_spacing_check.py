#!/usr/bin/env python3
"""贴片坐标（CPL）间距检查：下单前找出会被嘉立创 DFM 判「相互干涉」的小封装。

来由：Y27（SMT2611060002）2026-11-08 被嘉立创工程师点名 48 颗 0201 相互干涉。
只用 CPL 的中心和转角，按封装本体矩形（可加公差）算同面两两间隙，低于 --min-gap 就报。
不读焊盘、不读板文件，只查 0201–0805 阻容（0201 贴着 IC 引脚这种查不出），所以只是下单前的粗筛，过了也不等于 DFM 通过。
分离轴法给的是真实距离的下界，斜向分开的两颗会算小（偏严）。CPL 列名按嘉立创格式（Designator/Mid X/Mid Y/Layer/Rotation）。

用法：
  python3 cpl_spacing_check.py CPL.csv --bom BOM.csv [--min-gap 0.10]
  BOM 用来按 Footprint 列认封装尺寸；不给 BOM 时按位号前缀 C/R 一律当 0402。
退出码：有违规 1，无违规 0。
"""
import argparse
import csv
import itertools
import math
import re
import sys

# 本体长×宽（mm），取公差上限，宁严勿松
BODY = {
    "0201": (0.63, 0.33),
    "0402": (1.05, 0.55),
    "0603": (1.70, 0.90),
    "0805": (2.20, 1.45),
}


def mm(s):
    return float(str(s).lower().replace("mm", "").strip())


def rect(cx, cy, w, h, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    pts = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in pts]


def gap(a, b):
    """分离轴法：返回两矩形最大分离距离，<0 表示重叠。"""
    best = -1e9
    for poly in (a, b):
        for i in range(4):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % 4]
            nx, ny = y1 - y2, x2 - x1
            n = math.hypot(nx, ny)
            nx, ny = nx / n, ny / n
            pa = [x * nx + y * ny for x, y in a]
            pb = [x * nx + y * ny for x, y in b]
            best = max(best, min(pb) - max(pa), min(pa) - max(pb))
    return best


def load_pkg(bom):
    pkg = {}
    if not bom:
        return pkg
    with open(bom, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            m = re.search(r"(0201|0402|0603|0805)", r.get("Footprint", ""))
            if not m:
                continue
            for d in re.split(r"[,\s]+", r.get("Designator", "")):
                if d:
                    pkg[d] = m.group(1)
    return pkg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cpl")
    ap.add_argument("--bom")
    ap.add_argument("--min-gap", type=float, default=0.10, help="本体间最小间隙 mm（假设值，嘉立创未公开 0201 硬线）")
    a = ap.parse_args()
    pkg = load_pkg(a.bom)
    parts, skipped = [], 0
    with open(a.cpl, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = r["Designator"]
            p = pkg.get(d) or ("0402" if not a.bom and re.fullmatch(r"[CR]\d+", d) else None)
            if not p:
                skipped += 1
            if p:
                w, h = BODY[p]
                parts.append((d, p, r["Layer"], rect(mm(r["Mid X"]), mm(r["Mid Y"]), w, h, float(r["Rotation"]))))
    bad = []
    for x, y in itertools.combinations(parts, 2):
        if x[2] != y[2]:
            continue
        g = gap(x[3], y[3])
        if g < a.min_gap:
            bad.append((g, x[0], y[0], x[1], y[1], x[2]))
    bad.sort()
    names = sorted({n for b in bad for n in b[1:3]}, key=lambda s: (s[0], int(re.sub(r"\D", "", s) or 0)))
    print(f"只查 0201–0805 阻容：检查 {len(parts)} 颗，跳过 {skipped} 颗（IC、连接器、认不出封装的件不查）")
    print(f"本体间隙 < {a.min_gap} mm 的 {len(bad)} 对，涉及 {len(names)} 颗")
    for g, p, q, pp, qp, layer in bad[:20]:
        print(f"  {layer:6s} {p}({pp}) - {q}({qp})  间隙 {g:+.3f} mm")
    if names:
        print("涉及位号：" + "、".join(names))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
