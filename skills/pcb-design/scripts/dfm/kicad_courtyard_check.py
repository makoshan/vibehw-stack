#!/usr/bin/env python3
"""KiCad 板上 courtyard 自检：找出 courtyard 没包住焊盘的封装。

为什么要有：KiCad 的 courtyards_overlap 只比 courtyard 框。导入来的封装（Altium / 嘉立创 /
参考设计转换）常把 courtyard 画成本体大小甚至更小，这时器件挤在一起 DRC 也是 0，
要等嘉立创 DFM 才报「相互干涉」。2026-11-08 原力手环 D18：0201 courtyard 0.54×0.26，
焊盘外沿 0.92×0.32，KiCad 官方 0201 是 1.4×0.7。

判法（封装本地坐标，不受转角影响）：courtyard 外包框每边至少比焊盘外包框大 --margin（默认 0.05 mm）。
只看 SMD 焊盘；没有 courtyard 的封装单独列出。
用法：python3 kicad_courtyard_check.py board.kicad_pcb [--margin 0.05]
退出码：有问题 1，无问题 0，一个封装都没解析到 2。只比外包框，L 形等非矩形 courtyard 可能漏报。
"""
import argparse
import math
import re
import sys


def blocks(s, head):
    """按括号配对切出所有 (head ...) 顶层块。"""
    out, i = [], 0
    # KiCad 10 写成 "(fp_line\n"，旧版是 "(fp_line "，两种都要认
    pat = re.compile(r"\(" + head + r"\s")
    while True:
        m = pat.search(s, i)
        if not m:
            return out
        i = m.start()
        depth, j = 0, i
        while True:
            c = s[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(s[i:j + 1])
        i = j + 1


def bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pcb")
    ap.add_argument("--margin", type=float, default=0.05)
    a = ap.parse_args()
    s = open(a.pcb, encoding="utf-8").read()
    bad, nocy, n = [], [], 0
    for fp in blocks(s, "footprint"):
        m = re.search(r'\(property "Reference" "([^"]+)"', fp) or re.search(r'\(fp_text reference "([^"]+)"', fp)
        ref = m.group(1) if m else "?"
        fpa = re.search(r'\(at [-\d.]+ [-\d.]+(?: ([-\d.]+))?\)', fp)
        fang = float(fpa.group(1)) if fpa and fpa.group(1) else 0.0
        pads = []
        for pd in blocks(fp, "pad"):
            if not re.match(r'\(pad\s+"[^"]*"\s+smd\b', pd):
                continue
            at = re.search(r'\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', pd)
            sz = re.search(r'\(size ([-\d.]+) ([-\d.]+)\)', pd)
            x, y = float(at.group(1)), float(at.group(2))
            # KiCad 文件里焊盘角度含封装角度，减掉得到封装本地角度
            ang = math.radians((float(at.group(3)) if at.group(3) else 0.0) - fang)
            w, h = float(sz.group(1)) / 2, float(sz.group(2)) / 2
            c, si = math.cos(ang), math.sin(ang)
            for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h)):
                pads.append((x + dx * c - dy * si, y + dx * si + dy * c))
        if not pads:
            continue
        n += 1
        cy = []
        for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
            for g in blocks(fp, kind):
                if "CrtYd" not in g:
                    continue
                if kind == "fp_circle":
                    # 圆：center + 圆上一点，按半径展开成外包框
                    c0 = re.search(r'\(center ([-\d.]+) ([-\d.]+)\)', g)
                    e0 = re.search(r'\(end ([-\d.]+) ([-\d.]+)\)', g)
                    cxc, cyc = float(c0.group(1)), float(c0.group(2))
                    r = math.hypot(float(e0.group(1)) - cxc, float(e0.group(2)) - cyc)
                    cy += [(cxc - r, cyc - r), (cxc + r, cyc + r)]
                else:
                    cy += [(float(x), float(y)) for x, y in re.findall(r'\((?:start|end|xy|mid) ([-\d.]+) ([-\d.]+)\)', g)]
        if not cy:
            nocy.append(ref)
            continue
        px0, py0, px1, py1 = bbox(pads)
        cx0, cy0, cx1, cy1 = bbox(cy)
        short = min(px0 - cx0, py0 - cy0, cx1 - px1, cy1 - py1)
        if short < a.margin:
            bad.append((round(short, 3), ref, round(cx1 - cx0, 2), round(cy1 - cy0, 2), round(px1 - px0, 2), round(py1 - py0, 2)))
    if n == 0:
        # 解析不到任何封装（比如 KiCad 5 的 module 格式）就报错，不能当通过
        print("没解析到任何带 SMD 焊盘的封装，检查无效（文件格式不认？）")
        return 2
    bad.sort()
    print(f"检查 {n} 个带 SMD 焊盘的封装：courtyard 没包住焊盘（余量 < {a.margin} mm）{len(bad)} 个，没有 courtyard {len(nocy)} 个")
    for short, ref, cw, ch, pw, ph in bad[:15]:
        print(f"  {ref}: courtyard {cw}×{ch}，焊盘外沿 {pw}×{ph}，最窄边 {short:+.3f} mm")
    if nocy:
        print("  无 courtyard：" + "、".join(nocy[:20]))
    return 1 if bad or nocy else 0


if __name__ == "__main__":
    sys.exit(main())
