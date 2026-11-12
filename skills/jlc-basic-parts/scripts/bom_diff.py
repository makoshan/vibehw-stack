#!/usr/bin/env python3
"""两版 BOM 按位号比：哪些位号新增、删掉、改了哪个字段（值、封装、LCSC、MPN、厂商、DNP），再列数量变了的料。

用法：python3 bom_diff.py <旧.csv> <新.csv>
两边各自可以是 KiCad 导出的 BOM 或嘉立创 SMT BOM（格式见 bom_rules.py）。应当同格式比：
KiCad 的封装写 Capacitor_SMD:C_0402…、嘉立创写 0402，混着比会满表报「改」，所以两边列不一样时打警告；
一边没有的列（比如 KiCad BOM 没有 LCSC）不参与比较。任一边有重复位号也打警告并按有差异退出（diff 只看每个位号的第一行）。
输出 Markdown 表，可以直接贴进新包 README。退出码：0 无差异；1 有差异；2 读不了文件。
照搬 Backplane native/host/src/kicad/bom.rs 的 diff()/summary()/diff_md()；与原版的差别：数量表不计 DNP 件（不贴不采购）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bom_rules import FIELDS, nat_key, read_bom  # noqa: E402


def by_ref(parts):
    """位号 -> 件；同一位号出现多次只取第一次（重复位号由 bom_rules.py 报）。"""
    out = {}
    for p in parts:
        out.setdefault(p["ref"], p)
    return out


def label(p, fields):
    """采购看到的料：LCSC，没有就 MPN，再没有就「值 封装」；只用两边都有的列，免得两边按不同的键计数。"""
    for f in ("lcsc", "mpn"):
        if f in fields and p[f]:
            return p[f]
    return ("%s %s" % (p["value"], p["footprint"])).strip()


def dups(parts):
    seen, out = set(), []
    for p in parts:
        if p["ref"] in seen and p["ref"] not in out:
            out.append(p["ref"])
        seen.add(p["ref"])
    return out


def diff(old, new, fields):
    """返回 [(位号, 增/删/改, [(字段, 旧, 新)])]，位号自然序。"""
    o, n = by_ref(old), by_ref(new)
    out = []
    for ref in sorted(set(o) | set(n), key=nat_key):
        if ref not in n:
            out.append((ref, "删", [(f, o[ref][f], "") for f in fields if o[ref][f]]))
        elif ref not in o:
            out.append((ref, "增", [(f, "", n[ref][f]) for f in fields if n[ref][f]]))
        else:
            ds = [(f, o[ref][f], n[ref][f]) for f in fields if o[ref][f] != n[ref][f]]
            if ds:
                out.append((ref, "改", ds))
    return out


def tally(parts, fields):
    t = {}
    for p in by_ref(parts).values():
        if not p["dnp"]:
            k = label(p, fields)
            t[k] = t.get(k, 0) + 1
    return t


def cell(s):
    return s.replace("|", "/").replace("\n", " ")


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("用法：bom_diff.py <旧.csv> <新.csv>\n")
        return 2
    try:
        old, oc = read_bom(argv[1])
        new, nc = read_bom(argv[2])
    except (OSError, ValueError) as e:
        sys.stderr.write("读不了：%s\n" % e)
        return 2
    fields = [f for f in FIELDS if f in oc and f in nc]
    skipped = [f for f in FIELDS if (f in oc) != (f in nc)]
    cs = diff(old, new, fields)
    warn = []
    if oc != nc:
        warn.append("警告：两边的列不一样（旧 %s，新 %s），可能一边是 KiCad BOM、一边是嘉立创 BOM；封装、值的写法不同会被报成「改」，最好同格式比" % (",".join(oc), ",".join(nc)))
    for name, ps in (("旧", old), ("新", new)):
        d = dups(ps)
        if d:
            warn.append("警告：%s表有重复位号 %s，只比了第一行；先跑 bom_rules.py" % (name, ",".join(d)))
    print("旧 %s（%d 个位号） → 新 %s（%d 个位号）；比较字段：%s%s" % (
        argv[1], len(by_ref(old)), argv[2], len(by_ref(new)), ",".join(fields),
        "；只有一边有、不比：" + ",".join(skipped) if skipped else ""))
    for w in warn:
        print(w)
    if not cs:
        print("\n无变化")
        return 1 if any("重复位号" in w for w in warn) else 0
    cnt = dict((k, sum(1 for c in cs if c[1] == k)) for k in ("增", "删", "改"))
    print("\n增 %d，删 %d，改 %d\n" % (cnt["增"], cnt["删"], cnt["改"]))
    print("| 位号 | 变化 | 旧 | 新 |\n| --- | --- | --- | --- |")
    for ref, kind, ds in cs:
        # 「改」两边都列，清空的字段写 (空)；「增」「删」只列有值的一边
        was = "; ".join("%s: %s" % (f, a or "(空)") for f, a, b in ds if kind == "改" or a)
        now = "; ".join("%s: %s" % (f, b or "(空)") for f, a, b in ds if kind == "改" or b)
        print("| %s | %s | %s | %s |" % (cell(ref), kind, cell(was), cell(now)))
    to, tn = tally(old, fields), tally(new, fields)
    moved = [(k, to.get(k, 0), tn.get(k, 0)) for k in sorted(set(to) | set(tn)) if to.get(k, 0) != tn.get(k, 0)]
    if moved:
        print("\n| 料 | 旧数量 | 新数量 |\n| --- | --- | --- |")
        for k, a, b in moved:
            print("| %s | %d | %d |" % (cell(k), a, b))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
