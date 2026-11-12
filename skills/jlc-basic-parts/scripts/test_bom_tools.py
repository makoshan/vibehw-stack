"""bom_rules.py / bom_diff.py 的断言：每条规则先给干净表（不报），再给改坏的表（必须报）。
阳性对照（真板 BOM）用环境变量 BOM_REAL=<jlc_smt_bom.csv>、BOM_REAL_OLD=<旧版> 打开，没设就跳过。"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bom_rules import read_bom, rules  # noqa: E402

JLC = (
    "Comment,Designator,Footprint,LCSC Part #,Quantity\n"
    'CL05B104KO5NNNC,"C1,C2,C10",0402,C1525,3\n'
    '0402WGF1002TCE,"R1,R2",0402,C25744,2\n'
    "ADS1298IPAGR,U1,TQFP-64(10x10),C527440,1\n"
)
KICAD = (
    '"Reference";"Value";"Footprint";"Qty";"DNP"\n'
    '"C1,C2,C10";"100n";"Capacitor_SMD:C_0402_1005Metric";"3";""\n'
    '"R1,R2";"10k";"Resistor_SMD:R_0402_1005Metric";"2";""\n'
    '"U1";"ADS1298";"Package_QFP:TQFP-64_10x10mm_P0.5mm";"1";""\n'
)


def write(d, name, text):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def ids(text, d):
    parts, cols = read_bom(write(d, "b.csv", text))
    return [(f[1], f[2]) for f in rules(parts, cols)]


def run(script, *args):
    r = subprocess.run([sys.executable, os.path.join(HERE, script)] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return r.returncode, r.stdout


class Rules(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = self.t.name

    def tearDown(self):
        self.t.cleanup()

    def test_clean_both_formats(self):
        self.assertEqual(ids(JLC, self.d), [])
        self.assertEqual(ids(KICAD, self.d), [])
        parts, cols = read_bom(write(self.d, "k.csv", KICAD))
        self.assertEqual([p["ref"] for p in parts], ["C1", "C2", "C10", "R1", "R2", "U1"])
        self.assertIn("footprint", cols)

    def test_duplicate_ref(self):
        bad = JLC + "0402WGF1002TCE,R2,0402,C25744,1\n"
        self.assertIn(("bom_duplicate_ref", "R2"), ids(bad, self.d))
        bad2 = JLC.replace('"R1,R2"', '"R1,R1"')
        self.assertIn(("bom_duplicate_ref", "R1"), ids(bad2, self.d))

    def test_unannotated(self):
        self.assertIn(("bom_unannotated", "R?"), ids(JLC.replace('"R1,R2"', '"R1,R?"'), self.d))
        self.assertIn(("bom_unannotated", "(空)"), ids(JLC + "X,,0402,C1,1\n", self.d))

    def test_missing_footprint_value(self):
        self.assertIn(("bom_missing_footprint", "U1"), ids(JLC.replace("TQFP-64(10x10)", ""), self.d))
        self.assertIn(("bom_missing_value", "U1"), ids(JLC.replace("ADS1298IPAGR,", ","), self.d))
        nofp = "Reference,Value\nR1,10k\nR2,10k\n"
        self.assertEqual(ids(nofp, self.d), [("bom_missing_footprint", "(全表)")])

    def test_missing_lcsc(self):
        self.assertIn(("bom_missing_lcsc", "U1"), ids(JLC.replace("C527440", ""), self.d))
        # 标了 DNP 的件不要 LCSC
        dnp = "Reference,Value,Footprint,LCSC,DNP\nR1,10k,0402,C25744,\nR2,10k,0402,,DNP\n"
        self.assertEqual(ids(dnp, self.d), [])
        self.assertIn(("bom_missing_lcsc", "R2"), ids(dnp.replace(",DNP", ","), self.d))

    def test_missing_mpn(self):
        mpn = "Reference,Value,Footprint,MPN\nR1,10k,0402,RC0402\nR2,10k,0402,RC0402\n"
        self.assertEqual(ids(mpn, self.d), [])
        self.assertIn(("bom_missing_mpn", "R2"), ids(mpn[:-7] + "\n", self.d))

    def test_same_code_different_footprint(self):
        bad = JLC + "CL05B104KO5NNNC,C3,0603,C1525,1\n"
        got = ids(bad, self.d)
        for r in ("C1", "C2", "C3", "C10"):
            self.assertIn(("bom_lcsc_footprint", r), got)
        mpn = "Reference,Value,Footprint,MPN\nR1,10k,0402,RC0402\nR2,10k,0603,RC0402\n"
        self.assertIn(("bom_mpn_footprint", "R2"), ids(mpn, self.d))

    def test_exit_codes(self):
        self.assertEqual(run("bom_rules.py", write(self.d, "ok.csv", JLC))[0], 0)
        self.assertEqual(run("bom_rules.py", write(self.d, "bad.csv", JLC.replace('"R1,R2"', '"R1,R?"')))[0], 1)
        self.assertEqual(run("bom_rules.py", write(self.d, "warn.csv", JLC.replace("C527440", "")))[0], 0)
        self.assertEqual(run("bom_rules.py", write(self.d, "x.csv", "a,b\n1,2\n"))[0], 2)

    def test_gbk(self):
        p = os.path.join(self.d, "g.csv")
        with open(p, "w", encoding="gb18030") as f:
            f.write("位号,值,封装\nR1,10k,0402\n")
        parts, _ = read_bom(p)
        self.assertEqual(parts[0]["ref"], "R1")


    def test_ranges_and_utf16(self):
        from bom_rules import split_refs
        self.assertEqual(split_refs("C3-5, V4_C7-V4_C8,R1"), ["C3", "C4", "C5", "V4_C7", "V4_C8", "R1"])
        self.assertEqual(split_refs("J-1"), ["J-1"])
        # 区间展开后和单写的位号撞了也要判重
        self.assertIn(("bom_duplicate_ref", "C2"), ids(JLC + "CL05B104KO5NNNC,C2-3,0402,C1525,2\n", self.d))
        p = os.path.join(self.d, "u16.csv")
        with open(p, "w", encoding="utf-16") as f:
            f.write("Designator\tComment\tFootprint\nR1\t10k\t0402\n")
        parts, _ = read_bom(p)
        self.assertEqual((parts[0]["ref"], parts[0]["value"]), ("R1", "10k"))


    def test_parse_variants(self):
        # 表头前有标题行、~ 当空、小写 c 号、MPN/DNP 别名、空格分隔位号
        t = "导出于 2026-11-10\nRefs,Value,Footprint,LCSC,Manufacturer_Part_Number,DNP\nR1 R2,~,0402,c25744,RC0402,yes\n"
        parts, cols = read_bom(write(self.d, "v.csv", t))
        self.assertEqual([p["ref"] for p in parts], ["R1", "R2"])
        self.assertEqual((parts[0]["value"], parts[0]["lcsc"], parts[0]["mpn"], parts[0]["dnp"]), ("", "C25744", "RC0402", "yes"))
        for v in ("1", "x", "DNP", "true"):
            self.assertEqual(read_bom(write(self.d, "d.csv", t.replace(",yes", "," + v)))[0][0]["dnp"], "yes")
        self.assertEqual(read_bom(write(self.d, "d.csv", t.replace(",yes", ",")))[0][0]["dnp"], "")

    def test_dnp_and_empty_footprint_in_conflict(self):
        bad = JLC + "CL05B104KO5NNNC,C3,,C1525,1\n"
        self.assertIn(("bom_lcsc_footprint", "C3"), ids(bad, self.d))
        dnp = "Reference,Value,Footprint,LCSC,DNP\nR1,10k,0402,C25744,\nR2,10k,0603,C25744,DNP\n"
        self.assertIn(("bom_lcsc_footprint", "R2"), ids(dnp, self.d))

    def test_qty_mismatch(self):
        self.assertIn(("bom_qty_mismatch", "R1,R2"), ids(JLC.replace('"R1,R2",0402,C25744,2', '"R1,R2",0402,C25744,3'), self.d))


class Diff(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = self.t.name
        self.old = write(self.d, "old.csv", JLC)

    def tearDown(self):
        self.t.cleanup()

    def test_same(self):
        code, out = run("bom_diff.py", self.old, write(self.d, "same.csv", JLC))
        self.assertEqual(code, 0)
        self.assertIn("无变化", out)

    def test_add_remove_change(self):
        new = JLC.replace('"C1,C2,C10"', '"C1,C2"').replace("C527440", "C999") + "X,R3,0402,C25744,1\n"
        code, out = run("bom_diff.py", self.old, write(self.d, "new.csv", new))
        self.assertEqual(code, 1)
        self.assertIn("增 1，删 1，改 1", out)
        self.assertIn("| C10 | 删 |", out)
        self.assertIn("| R3 | 增 |", out)
        self.assertIn("| U1 | 改 | lcsc: C527440 | lcsc: C999 |", out)
        self.assertIn("| C1525 | 3 | 2 |", out)
        self.assertNotIn("| R1 |", out)

    def test_every_field(self):
        base = "Reference,Value,Footprint,LCSC,MPN,Manufacturer,DNP\nR1,10k,0402,C1,M1,F1,\n"
        for i, (f, now) in enumerate((("value", "22k"), ("footprint", "0603"), ("lcsc", "C2"), ("mpn", "M2"), ("mfr", "F2"))):
            row = ["R1", "10k", "0402", "C1", "M1", "F1", ""]
            was = row[i + 1]
            row[i + 1] = now
            new = base.split("\n")[0] + "\n" + ",".join(row) + "\n"
            code, out = run("bom_diff.py", write(self.d, "o.csv", base), write(self.d, "n.csv", new))
            self.assertIn("| R1 | 改 | %s: %s | %s: %s |" % (f, was, f, now), out)
        code, out = run("bom_diff.py", write(self.d, "o.csv", base), write(self.d, "n.csv", base.replace("F1,", "F1,DNP")))
        self.assertIn("| R1 | 改 | dnp: (空) | dnp: yes |", out)

    def test_duplicates_warned(self):
        dup = JLC + "0402WGF1001TCE,R1,0402,C11702,1\n"
        code, out = run("bom_diff.py", write(self.d, "d.csv", dup), self.old)
        self.assertEqual(code, 1)
        self.assertIn("重复位号 R1", out)

    def test_cleared_field_shown(self):
        new = JLC.replace("C527440", "")
        code, out = run("bom_diff.py", self.old, write(self.d, "n.csv", new))
        self.assertIn("| U1 | 改 | lcsc: C527440 | lcsc: (空) |", out)

    def test_mixed_formats_skip_one_sided_columns(self):
        code, out = run("bom_diff.py", write(self.d, "k.csv", KICAD), self.old)
        self.assertIn("只有一边有、不比：lcsc", out)
        self.assertIn("最好同格式比", out)
        self.assertNotIn("lcsc: C", out)
        self.assertNotIn("| C1525 |", out)  # 数量表不按一边才有的 LCSC 计


@unittest.skipUnless(os.environ.get("BOM_REAL"), "没设 BOM_REAL")
class Real(unittest.TestCase):
    def test_real_bom_clean(self):
        code, out = run("bom_rules.py", os.environ["BOM_REAL"])
        self.assertEqual(code, 0, out)

    @unittest.skipUnless(os.environ.get("BOM_REAL_OLD"), "没设 BOM_REAL_OLD")
    def test_real_diff(self):
        code, out = run("bom_diff.py", os.environ["BOM_REAL_OLD"], os.environ["BOM_REAL"])
        self.assertIn(code, (0, 1), out)
        self.assertIn("个位号", out)


if __name__ == "__main__":
    unittest.main()
