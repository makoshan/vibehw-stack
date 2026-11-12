#!/usr/bin/env python3
"""BOM 规则检查：重复位号、未标注、缺封装/缺值、别的件有 LCSC/MPN 而这件没有、同一 LCSC/MPN 配了不同封装。

用法：python3 bom_rules.py <bom.csv>
认两种格式：KiCad 导出的 BOM（Reference/Refs、Value、Footprint……）和嘉立创 SMT BOM
（Comment、Designator、Footprint、LCSC Part #，位号逗号连写在一格里）。
退出码：0 没有错误（可能有警告）；1 有错误；2 读不了文件或认不出表头。
规则照搬 Backplane native/host/src/kicad/bom.rs 的 rules()/mpnfp()，另加 LCSC 一列；
与原版的差别：标了 DNP 的件不查「缺 LCSC/MPN」（不贴的件不需要料号）。
"""
import csv
import io
import re
import sys

# 规范字段名 -> 表头别名（小写、去掉空格 _ # . / -）
ALIASES = {
    "ref": ["reference", "references", "ref", "refs", "designator", "designators", "位号"],
    "value": ["value", "comment", "val", "值", "valuecandidatempn"],
    "footprint": ["footprint", "package", "封装"],
    "lcsc": ["lcsc", "lcscpart", "lcscpartnumber", "lcscpartno", "jlcpcbpart", "jlcpcbpartnumber", "lcsc编号", "立创编号"],
    "mpn": ["mpn", "manufacturerpartnumber", "mfrpn", "mfrpartnumber", "pn"],
    "mfr": ["manufacturer", "mfr", "品牌", "厂商"],
    "dnp": ["dnp", "donotpopulate"],
    "qty": ["qty", "quantity", "数量"],
}
FIELDS = ["value", "footprint", "lcsc", "mpn", "mfr", "dnp"]
YES = {"dnp", "yes", "y", "true", "1", "x", "是"}


def _key(h):
    return re.sub(r"[\s_#./\-]", "", (h or "").strip().lower())


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    # 嘉立创EDA 导出的 BOM 是带 BOM 头的 UTF-16、制表符分隔
    encs = ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "gb18030")
    for enc in encs:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    raise ValueError("编码既不是 UTF-8 也不是 GB18030")


RANGE = re.compile(r"^(.*?[A-Za-z_])(\d+)-(?:\1)?(\d+)$")


def split_refs(cell):
    """「R1, R2,R3」「R1 R2」拆成位号列表，嘉立创的区间「C3-5」「C3-C5」展开成 C3,C4,C5；
    空格子给 [""]，好让「未标注」规则看到它。"""
    refs = []
    for r in re.split(r"[,;\s]+", (cell or "").strip()):
        m = RANGE.match(r)
        if m and int(m.group(2)) < int(m.group(3)) <= int(m.group(2)) + 999:
            refs.extend("%s%d" % (m.group(1), i) for i in range(int(m.group(2)), int(m.group(3)) + 1))
        elif r:
            refs.append(r)
    return refs or [""]


def read_bom(path):
    """读 BOM，按位号展开成件的列表：[{ref, value, footprint, lcsc, mpn, mfr, dnp, line}]。"""
    text = read_text(path)
    lines = text.splitlines()
    # 有的导出在表头前有标题行：取第一行能认出位号列的当表头
    start = None
    delim = ","
    for i, ln in enumerate(lines[:20]):
        for d in (",", ";", "\t"):
            cells = next(csv.reader([ln], delimiter=d))
            if any(_key(c) in ALIASES["ref"] for c in cells):
                start, delim = i, d
                break
        if start is not None:
            break
    if start is None:
        raise ValueError("前 20 行里找不到位号列（Reference/Refs/Designator）")
    rows = list(csv.reader(io.StringIO("\n".join(lines[start:])), delimiter=delim))
    header = [_key(h) for h in rows[0]]
    col = {}
    for name, al in ALIASES.items():
        for i, h in enumerate(header):
            if h in al and name not in col:
                col[name] = i
    parts = []
    for n, row in enumerate(rows[1:], start=start + 2):
        if not any(c.strip() for c in row):
            continue

        def get(name):
            i = col.get(name)
            v = row[i].strip() if i is not None and i < len(row) else ""
            return "" if v == "~" else v  # KiCad 空字段写 ~

        dnp = get("dnp").lower() in YES
        for r in split_refs(get("ref")):
            parts.append({"ref": r, "value": get("value"), "footprint": get("footprint"),
                          "lcsc": get("lcsc").upper(), "mpn": get("mpn"), "mfr": get("mfr"),
                          "dnp": "yes" if dnp else "", "line": n, "qty": get("qty"), "cell": get("ref")})
    return parts, sorted(col)


def nat_key(ref):
    """自然序：C2 排在 C10 前面。"""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.findall(r"\d+|\D+", ref)]


def rules(parts, cols=None):
    """返回 [(严重度, 规则, 位号, 说明)]，严重度 error / warning。cols：表里认出的列，整列缺失只报一条。"""
    out = []
    cols = FIELDS if cols is None else cols
    for f, rid, word in (("footprint", "bom_missing_footprint", "封装"), ("value", "bom_missing_value", "值")):
        if f not in cols:
            out.append(("warning", rid, "(全表)", "表里没有%s列" % word))
    seen = {}
    for p in parts:
        seen.setdefault(p["ref"], []).append(p["line"])
    for ref in sorted(seen, key=nat_key):
        if ref and len(seen[ref]) > 1:
            out.append(("error", "bom_duplicate_ref", ref, "位号出现 %d 次（行 %s）" % (len(seen[ref]), ",".join(map(str, seen[ref])))))
    done = set()
    for p in parts:
        # 嘉立创表的 Quantity 要等于这一行的位号数（只报一次）
        if p["qty"] and p["line"] not in done:
            done.add(p["line"])
            n = sum(1 for q in parts if q["line"] == p["line"])
            if not p["qty"].isdigit() or int(p["qty"]) != n:
                out.append(("warning", "bom_qty_mismatch", p["cell"] or "(空)", "数量写 %s，位号有 %d 个（行 %d）" % (p["qty"], n, p["line"])))
    for p in parts:
        if not p["ref"] or p["ref"].endswith("?"):
            out.append(("error", "bom_unannotated", p["ref"] or "(空)", "位号未标注（行 %d）" % p["line"]))
    for p in parts:
        if "footprint" in cols and not p["footprint"]:
            out.append(("warning", "bom_missing_footprint", p["ref"], "缺封装"))
    for p in parts:
        if "value" in cols and not p["value"]:
            out.append(("warning", "bom_missing_value", p["ref"], "缺值"))
    for f, rid in (("lcsc", "bom_missing_lcsc"), ("mpn", "bom_missing_mpn")):
        if any(p[f] for p in parts):
            for p in parts:
                if not p[f] and not p["dnp"]:
                    out.append(("warning", rid, p["ref"], "别的件有 %s，这件没有" % f.upper()))
    for f in ("lcsc", "mpn"):
        groups = {}
        for p in parts:
            if p[f]:
                groups.setdefault(p[f], []).append(p)
        for code in sorted(groups):
            fps = sorted(set(p["footprint"] for p in groups[code]))
            if len(fps) > 1:
                for p in sorted(groups[code], key=lambda q: nat_key(q["ref"])):
                    out.append(("error", "bom_%s_footprint" % f, p["ref"],
                                "%s %s 配了不同封装：%s" % (f.upper(), code, " / ".join(x or "(空)" for x in fps))))
    return out


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("用法：bom_rules.py <bom.csv>\n")
        return 2
    try:
        parts, cols = read_bom(argv[1])
    except (OSError, ValueError) as e:
        sys.stderr.write("读不了 %s：%s\n" % (argv[1], e))
        return 2
    fs = rules(parts, cols)
    errs = sum(1 for f in fs if f[0] == "error")
    print("%s：%d 个位号，认出的列：%s" % (argv[1], len(parts), ",".join(cols)))
    for sev, rid, ref, msg in fs:
        print("%s\t%s\t%s\t%s" % ("错误" if sev == "error" else "警告", rid, ref, msg))
    print("错误 %d，警告 %d" % (errs, len(fs) - errs))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
