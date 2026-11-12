#!/usr/bin/env python3
"""placement_audit.py 自检：用 pcbnew 现造最小板，反例先判红，再跑正例。断言写法，任一失败即非零退出。

用法（KiCad 自带 Python 或带 pcbnew 的容器）：
  python3 test_placement_audit.py [--keep 目录]

造板（mm，IC 中心 (50,50)，y 向下，板框 30×30）：
  U1  16 脚，焊盘 0.8×0.3，四边各 4 脚、距中心 2.0、脚距 0.5；1 VDD、2 GND、3 SIG3、6 VREF、7 GND、
      10 XA、11 XB，其余无网。
  C1  VDD 去耦，横放，1 脚(VDD) 在 (46.8,49.25)、2 脚(GND) 在 (45.8,49.25)，焊盘 0.5×0.5；
      到 1 脚的间隙 = 2.95 - 2.4 = 0.55。
  C2  VREF 去耦，竖放，1 脚 (49.75,53.2)、2 脚(GND) (49.75,54.2)。
  Y1  晶振，两脚接 XA/XB。
规则：.kicad_pro 的网络类 Default 间距 0.2/线宽 0.2/过孔 0.6，板级最小 0.15/0.15/0.5/边 0.3。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT = os.path.join(HERE, "placement_audit.py")

PRO = {
    "board": {"design_settings": {"rules": {"min_clearance": 0.15, "min_track_width": 0.15,
                                            "min_via_diameter": 0.5, "min_copper_edge_clearance": 0.3}}},
    "meta": {"filename": "t.kicad_pro", "version": 3},
    "net_settings": {"classes": [{"name": "Default", "clearance": 0.2, "track_width": 0.2, "via_diameter": 0.6,
                                  "via_drill": 0.3, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                                  "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                                  "wire_width": 6, "bus_width": 12, "line_style": 0, "pcb_color": "rgba(0,0,0,0)",
                                  "schematic_color": "rgba(0,0,0,0)", "priority": 2147483647}],
                     "meta": {"version": 4}, "net_colors": None, "netclass_assignments": None,
                     "netclass_patterns": []},
}


def build(path, c1_dx=0.0, c1_rot=0.0, ring=None, blocker=False, dru=False, island=False, blocker8=False,
          tp8=False, pro=True, rules_variant=False, far3=False, g1_ring=False, c1_ref="C1", c1_val="",
          c1_3term=False, c3=False, c1_x2y=False):
    """ring=(中心半径, 线宽)：C2 地焊盘外的别网铜环；island：Edge.Cuts 开环形槽，把 C2 地焊盘留在 r0.9 的孤岛上；
    blocker8/tp8：8 脚（下边）前方放别网两脚件/只挡直走的单焊盘；rules_variant：焊盘间距覆盖 + 差分对。"""
    if sys.platform == "darwin":
        import wx  # 同 placement_audit.py：Mac 上先建 wx.App
        build.app = getattr(build, "app", None) or wx.App(False)
    import pcbnew as k
    mm = k.FromMM
    b = k.BOARD()
    nets = {}

    def net(n):
        if n not in nets:
            nets[n] = k.NETINFO_ITEM(b, n)
            b.Add(nets[n])
        return nets[n]

    def part(ref, at, pads, rot=0.0):
        fp = k.FOOTPRINT(b)
        fp.SetReference(ref)
        fp.SetPosition(k.VECTOR2I(mm(at[0]), mm(at[1])))
        b.Add(fp)
        for num, (x, y), (w, h), n in pads:
            p = k.PAD(fp)
            p.SetNumber(num)
            p.SetAttribute(k.PAD_ATTRIB_SMD)
            p.SetLayerSet(k.PAD.SMDMask())
            p.SetShape(k.F_Cu, k.PAD_SHAPE_RECT)
            p.SetSize(k.F_Cu, k.VECTOR2I(mm(w), mm(h)))
            fp.Add(p)
            p.SetFPRelativePosition(k.VECTOR2I(mm(x), mm(y)))
            if n:
                p.SetNet(net(n))
        if rot:
            fp.SetOrientationDegrees(rot)
        return fp

    sig = {1: "VDD", 2: "GND", 3: "SIG3", 6: "VREF", 7: "GND", 8: "SIG8", 10: "XA", 11: "XB"}
    if rules_variant:
        sig.update({13: "INP", 14: "INN"})
    pads = []
    off = [-0.75, -0.25, 0.25, 0.75]
    for i in range(16):
        side, j = divmod(i, 4)
        if side == 0:
            xy, sz = (-2.0, off[j]), (0.8, 0.3)          # 左边 1–4，自上而下
        elif side == 1:
            xy, sz = (off[j], 2.0), (0.3, 0.8)           # 下边 5–8，自左而右
        elif side == 2:
            xy, sz = (2.0, -off[j]), (0.8, 0.3)          # 右边 9–12，自下而上
        else:
            xy, sz = (-off[j], -2.0), (0.3, 0.8)         # 上边 13–16
        pads.append((str(i + 1), xy, sz, sig.get(i + 1)))
    part("U1", (50, 50), pads)
    cap = lambda n1: [("1", (0.5, 0), (0.5, 0.5), n1), ("2", (-0.5, 0), (0.5, 0.5), "GND")]
    c1_pads = cap("VDD")
    if c1_3term:  # 三端电容：3 脚(VDD) 朝 1 脚、2 脚 GND 在中间、1 脚(VDD) 最外；远的 1 脚排第一，平手取第一的写法会误报
        c1_pads = [("1", (-1.5, 0), (0.5, 0.5), "VDD"), ("2", (-0.5, 0), (0.5, 0.5), "GND"),
                   ("3", (0.5, 0), (0.5, 0.5), "VDD")]
    if c1_x2y:    # 两个地焊盘：排第一的地焊盘最远，离脚最近的地焊盘朝脚；只看第一个地焊盘的写法会漏判
        c1_pads = [("3", (-1.5, 0), (0.5, 0.5), "GND"), ("1", (-0.5, 0), (0.5, 0.5), "VDD"),
                   ("2", (0.5, 0), (0.5, 0.5), "GND")]
    c1 = part(c1_ref, (46.3 - c1_dx, 49.25), c1_pads, rot=c1_rot)
    if c1_val:
        c1.SetValue(c1_val)
    if c3:        # 1 脚第二颗 VDD 电容，在 C1 左上，地焊盘朝 1 脚，离脚 ≈1.2（自身长 1.5）
        part("C3", (46.0, 48.0), [("1", (-0.5, 0), (0.5, 0.5), "VDD"), ("2", (0.5, 0), (0.5, 0.5), "GND")])
    if rules_variant:
        next(p for p in c1.Pads() if p.GetNumber() == "1").SetLocalClearance(mm(0.25))
    part("C2", (49.75, 53.7), cap("VREF"), rot=90)         # 1 脚朝上（朝芯片）
    part("Y1", (53.4, 50), [("1", (0, 0.5), (0.5, 0.5), "XA"), ("2", (0, -0.5), (0.5, 0.5), "XB")])
    if blocker:   # 别网小件竖放在 3 脚（SIG3，左边 y=50.25）正前方
        part("R9", (47.0, 50.25), [("1", (0, -0.45), (0.5, 0.5), "OTHER1"), ("2", (0, 0.45), (0.5, 0.5), "OTHER2")])
    if blocker8:  # 别网两脚件横放在 8 脚（SIG8，下边 x=50.75）正前方
        part("R8", (50.75, 53.0), [("1", (-0.45, 0), (0.5, 0.5), "OTHER3"), ("2", (0.45, 0), (0.5, 0.5), "OTHER4")])
    if far3:      # 3 脚外沿 x=47.6 前方约 1.8 mm，直走和 ±45° 各一颗别网焊盘；从焊盘中心起算时直走够不着
        part("F3", (46.3, 50.25), [("1", (-0.55, 0), (0.4, 0.4), "OTHER6"), ("2", (0.027, -1.273), (0.4, 0.4), "OTHER7"),
                                   ("3", (0.027, 1.273), (0.4, 0.4), "OTHER8")])
    if tp8:       # 只挡 8 脚直走通道的单焊盘，+45° 通道仍通
        part("TP8", (50.75, 53.4), [("1", (0, 0), (0.4, 0.4), "OTHER5")])

    def circle(layer, r, width, n=None, c=(49.75, 54.2)):  # 默认圆心 = C2 地焊盘中心
        s = k.PCB_SHAPE(b)
        s.SetShape(k.SHAPE_T_CIRCLE)
        s.SetLayer(layer)
        s.SetCenter(k.VECTOR2I(mm(c[0]), mm(c[1])))
        s.SetEnd(k.VECTOR2I(mm(c[0] + r), mm(c[1])))
        s.SetWidth(mm(width))
        if n:
            s.SetNet(net(n))
        b.Add(s)
    if ring:
        circle(k.F_Cu, ring[0], ring[1], "SHIELD")
    if g1_ring:   # 孤立的单焊盘地件 G1 加一圈完整铜环：候选过孔区是带孔的环，专测多边形孔
        part("G1", (40, 60), [("1", (0, 0), (0.5, 0.5), "GND")])
        circle(k.F_Cu, 1.0, 0.4, "SHIELD", c=(40, 60))
    if island:
        circle(k.Edge_Cuts, 0.9, 0.05)
        circle(k.Edge_Cuts, 1.3, 0.05)
    e = k.PCB_SHAPE(b)
    e.SetShape(k.SHAPE_T_RECT)
    e.SetLayer(k.Edge_Cuts)
    e.SetStart(k.VECTOR2I(mm(35), mm(35)))
    e.SetEnd(k.VECTOR2I(mm(65), mm(65)))
    e.SetWidth(mm(0.1))
    b.Add(e)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    assert k.SaveBoard(path, b), "存板失败"
    # 工程文件在存板之后写，免得被 SaveBoard 覆盖
    pro_path = path[:-len(".kicad_pcb")] + ".kicad_pro"
    if pro:
        p = json.loads(json.dumps(PRO))
        if rules_variant:
            p["net_settings"]["classes"][0]["diff_pair_width"] = 0.25
        with open(pro_path, "w") as f:
            json.dump(p, f)
    elif os.path.exists(pro_path):
        os.remove(pro_path)
    if dru:
        with open(path[:-len(".kicad_pcb")] + ".kicad_dru", "w") as f:
            f.write('(version 1)\n(rule "c" (constraint clearance (min 0.3mm)) (condition "A.NetName == \'VDD\'"))\n')
    return path


def run(board, out, *args):
    p = subprocess.run([sys.executable, AUDIT, board, "--ic", "U1", "--out", out] + list(args),
                       capture_output=True, text=True, timeout=300)
    j = json.load(open(os.path.join(out, "audit.json"), encoding="utf-8"))
    txt = open(os.path.join(out, "audit.txt"), encoding="utf-8").read()
    return p.returncode, j, txt


def pin(j, num):
    return next(r for r in j["sections"]["pins"] if r["pin"] == num)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", help="把造的板和每例输出留在这个新目录")
    a = ap.parse_args()
    root = a.keep or tempfile.mkdtemp(prefix="placement-audit-test-")
    if a.keep:
        os.makedirs(root)
    passed = []

    def case(name):
        d = os.path.join(root, name)
        return os.path.join(d, "t.kicad_pcb"), os.path.join(d, "out")

    try:
        # 基准量一次（只取数，不在这里判正例）
        bd, out = case("base-measure")
        build(bd)
        code0, base, _ = run(bd, out)
        g0 = pin(base, "1")["gap_mm"]

        # 反例 2：C1 往外挪 8 mm
        bd, out = case("neg2-move-8mm")
        build(bd, c1_dx=8.0)
        code, j, _ = run(bd, out)
        s = j["sections"]
        assert s["pins"][0]["pin"] == "1" and s["pins"][0]["part_pad"] == "C1.1", s["pins"][:2]
        assert s["parts"][0]["part_pad"] == "C1.1", s["parts"][:2]
        assert abs(s["pins"][0]["gap_mm"] - g0 - 8.0) < 0.05, (g0, s["pins"][0])
        assert abs(s["parts"][0]["gap_mm"] - g0 - 8.0) < 0.05, s["parts"][0]
        passed.append("反例2 挪远 8 mm：C1.1 在 pins/parts 排最差，距离增加 %.3f mm" % (s["pins"][0]["gap_mm"] - g0))

        # 反例 3：C1 转 180°
        bd, out = case("neg3-rotate-180")
        build(bd, c1_rot=180)
        code, j, txt = run(bd, out)
        assert code == 1, code
        assert pin(j, "1")["ground_pad_faces_pin"] is True, pin(j, "1")
        assert "地焊盘朝向芯片" in txt
        passed.append("反例3 转 180°：报地焊盘朝向芯片，退出码 1")

        # 收窄（2026-11-10 Mako）：只判紧挨着脚的电容。C1 焊盘外包络长 1.5 mm；转 180° 后地焊盘离脚 0.55 + dx
        def face(name, **kw):
            bd, out = case(name)
            build(bd, c1_rot=180, **kw)
            code, j, _ = run(bd, out)
            return code, pin(j, "1")
        code, r = face("narrow-resistor", c1_ref="R1")
        assert code == 0 and r["ground_pad_faces_pin"] is False and r["cap_pad"] is None, (code, r)
        code, r = face("narrow-far", c1_dx=1.2)            # 最近焊盘 1.75 > 1.5：不判
        assert code == 0 and r["ground_pad_faces_pin"] is False and abs(r["cap_gap_mm"] - 1.75) < 0.01, (code, r)
        assert abs(r["cap_len_mm"] - 1.5) < 0.01, r
        code, r = face("narrow-edge-in", c1_dx=0.8)        # 1.35 ≤ 1.5：判
        assert code == 1 and r["ground_pad_faces_pin"] is True and r["cap_pad"] == "C1.1", (code, r)
        code, r = face("narrow-hier-ref", c1_ref="V4_C55")  # 层次前缀的位号也认
        assert code == 1 and r["ground_pad_faces_pin"] is True, (code, r)
        code, r = face("narrow-by-value", c1_ref="X7", c1_val="4.7uF")  # 位号不像、值像电容
        assert code == 1 and r["ground_pad_faces_pin"] is True, (code, r)
        code, r = face("narrow-not-cap", c1_ref="X7")      # 位号、值都不像电容：不判
        assert code == 0 and r["ground_pad_faces_pin"] is False, (code, r)
        code, r = face("narrow-diode", c1_ref="D1", c1_val="1N4148")  # 1N4148 不能被当成电容（100n 那种写法）
        assert code == 0 and r["ground_pad_faces_pin"] is False, (code, r)
        code, r = face("narrow-equal", c1_dx=0.95)         # 正好 1.50 = 自身长：判（≤ 不是 <）
        assert code == 1 and r["ground_pad_faces_pin"] is True and abs(r["cap_gap_mm"] - 1.5) < 1e-6, (code, r)
        bd, out = case("narrow-3term")                     # 三端电容同网焊盘朝脚：不误报
        build(bd, c1_3term=True)
        code, j, _ = run(bd, out)
        r = pin(j, "1")
        assert code == 0 and r["ground_pad_faces_pin"] is False and r["cap_pad"] == "C1.3", (code, r)
        bd, out = case("narrow-x2y")                       # 两个地焊盘的电容：按离脚最近的地焊盘判
        build(bd, c1_x2y=True)
        code, j, _ = run(bd, out)
        r = pin(j, "1")
        assert code == 1 and r["faced_caps"] == ["C1.1"], (code, r)
        bd, out = case("narrow-second-cap")                # 同一脚第二颗紧挨着的电容反朝：照判
        build(bd, c3=True)
        code, j, txt = run(bd, out)
        r = pin(j, "1")
        assert code == 1 and r["faced_caps"] == ["C3.1"] and r["cap_pad"] == "C1.1", (code, r)
        assert "地焊盘朝向芯片：脚 1 -> C3.1" in j["defects"] and "（C3.1）" in txt, j["defects"]
        bd, out = case("narrow-rot30")                     # 斜放 30°：自身长仍按封装坐标量 1.5
        build(bd, c1_rot=30)
        code, j, _ = run(bd, out)
        assert abs(pin(j, "1")["cap_len_mm"] - 1.5) < 0.01, pin(j, "1")
        sys.path.insert(0, HERE)
        import placement_audit as pa
        is_cap = lambda ref, val="": pa.Footprint(ref, 0, 0, [], val).is_cap
        for ref in ("C1", "V4_C55", "V4-C55", "V4.C55", "EC1", "CE2", "C12A"):
            assert is_cap(ref), ref
        for ref in ("CN1", "R1", "U2", "IC1", "SC1"):
            assert not is_cap(ref), ref
        for v in ("100nF", "4.7uF", "10uF/16V", "100nF_0402", ".1uF", "0.1uF", "100n", "4u7", "1n5", "2.2u", "10pF"):
            assert is_cap("X1", v), v
        for v in ("1N4148", "2N7002", "1N5819", "10uH", "1M", "10k", "DNP", "15kΩ", "ESD401DPYR"):
            assert not is_cap("X1", v), v
        passed.append("收窄：电阻、1N4148、离脚 1.75 > 自身长 1.5、认不出的件都不判；1.35 与正好 1.50 的电容、V4_C55、值 4.7uF 照判；"
                      "三端电容不误报；两个地焊盘按最近的判；同脚第二颗反朝的电容照判；斜放 30° 自身长仍 1.5；位号/值认法 32 例")

        # 反例 4：别网铜环围住 C2 地焊盘
        bd, out = case("neg4-ring")
        build(bd, ring=(1.2, 0.8))
        code, j, txt = run(bd, out)
        gr = {r["ground_pad"]: r["via_gap_mm"] for r in j["sections"]["ground"]}
        assert code == 1, code
        assert gr["C2.2"] is None, gr
        assert j["sections"]["ground"][0]["ground_pad"] == "C2.2"
        assert any(x.split()[:2] == ["C2.2", "无"] for x in txt.splitlines()), txt
        passed.append("反例4 别网铜围住地焊盘：ground 报「无」，退出码 1")

        # 反例 5：别网小件挡在 3 脚正前方
        bd, out = case("neg5-blocker")
        build(bd, blocker=True)
        code, j, _ = run(bd, out)
        fr = {r["pin"]: r["blocked_by"] for r in j["sections"]["fronts"]}
        assert "3" in fr and {"R9.1", "R9.2"} <= set(fr["3"]), fr
        assert code == 1, code
        passed.append("反例5 别网小件挡脚：fronts 报 3 脚被 R9 堵，退出码 1")

        # 反例 6：有 .kicad_dru 不给四个数
        bd, out = case("neg6-dru")
        build(bd, dru=True)
        code, j, _ = run(bd, out)
        assert code == 2 and "kicad_dru" in j.get("refused", ""), (code, j.get("refused"))
        code, j, _ = run(bd, out + "-flags", "--track", "0.2", "--clear", "0.3", "--via", "0.6", "--edge", "0.3")
        assert code == 0 and j["rules"]["clear"]["source"] == "命令行参数", (code, j.get("rules"))
        passed.append("反例6 有 .kicad_dru 不给四个数：退出码 2；给齐四个数后能跑")

        # 反例 7：比板子松的 --clear
        bd, out = case("neg7-loose")
        build(bd)
        code, j, _ = run(bd, out, "--clear", "0.1")
        assert code == 2 and "松" in j.get("refused", ""), (code, j.get("refused"))
        code, j, txt = run(bd, out + "-override", "--clear", "0.1", "--rules-override")
        assert code in (0, 1) and j["looser_than_board"] is True, code
        lines = [x for x in txt.splitlines() if x.strip()]
        assert lines and all(x.endswith("[比板子松]") for x in lines), [x for x in lines if not x.endswith("[比板子松]")]
        rows = [r for sec in j["sections"].values() if isinstance(sec, list) for r in sec if isinstance(r, dict)]
        assert rows and all(r.get("looser_than_board") for r in rows), "JSON 行缺标注"
        passed.append("反例7 松的 --clear：退出码 2；加 --rules-override 能跑，%d 行输出全标「比板子松」" % len(lines))

        # 反例 4b：板边围住（Edge.Cuts 环形槽把 C2 地焊盘留在 r0.9 孤岛上）
        bd, out = case("neg4b-edge-island")
        build(bd, island=True)
        code, j, _ = run(bd, out)
        gr = {r["ground_pad"]: r["via_gap_mm"] for r in j["sections"]["ground"]}
        assert code == 1 and gr["C2.2"] is None, (code, gr)
        passed.append("反例4b 板边围住地焊盘：ground 报「无」，退出码 1")

        # ground 非零间隙手算：铜环 0.8–1.2，过孔中心须离环心 >= 1.2+0.2+0.3=1.7，
        # 最近处在焊盘角的对角线上：1.7 - 0.25*sqrt(2) - 0.3 = 1.0464
        bd, out = case("ground-far-ring")
        build(bd, ring=(1.0, 0.4), g1_ring=True)
        code, j, _ = run(bd, out)
        gr = {r["ground_pad"]: r["via_gap_mm"] for r in j["sections"]["ground"]}
        want = 1.7 - 0.25 * 2 ** 0.5 - 0.3
        assert gr["C2.2"] is not None and abs(gr["C2.2"] - want) < 0.01, gr
        assert gr["G1.1"] is not None and abs(gr["G1.1"] - want) < 0.01, gr
        far = gr
        # 铜环 1.3–1.5：环内 0.55–0.8 有过孔位（间隙 0）；过孔半径错成直径会报「无」
        bd, out = case("ground-inner-ring")
        build(bd, ring=(1.4, 0.2))
        code, j, _ = run(bd, out)
        gr = {r["ground_pad"]: r["via_gap_mm"] for r in j["sections"]["ground"]}
        assert gr["C2.2"] is not None and gr["C2.2"] < 0.01, gr
        passed.append("ground 手算：环 0.8–1.2 间隙 C2.2 %.3f、G1.1（候选区带孔）%.3f mm（手算 %.3f），环 1.3–1.5 间隙 0"
                      % (far["C2.2"], far["G1.1"], want))

        # 反例 5b：下边 8 脚（y 向下）正前方放别网件
        bd, out = case("neg5b-blocker-bottom")
        build(bd, blocker8=True)
        code, j, _ = run(bd, out)
        fr = {r["pin"]: r["blocked_by"] for r in j["sections"]["fronts"]}
        assert code == 1 and "8" in fr and {"R8.1", "R8.2"} <= set(fr["8"]), (code, fr)
        # 只挡直走、+45° 通道通：不算被堵
        bd, out = case("pos5c-straight-only")
        build(bd, tp8=True)
        code, j, _ = run(bd, out)
        assert code == 0 and j["sections"]["fronts"] == [], (code, j["sections"]["fronts"])
        # 通道从焊盘外沿起算：前方 1.7–1.9 mm 的三颗焊盘要算堵
        bd, out = case("neg5d-far")
        build(bd, far3=True)
        code, j, _ = run(bd, out)
        fr = {r["pin"]: r["blocked_by"] for r in j["sections"]["fronts"]}
        assert code == 1 and {"F3.1", "F3.2", "F3.3"} <= set(fr.get("3", [])), (code, fr)
        passed.append("反例5b 下边脚被堵报出；只挡直走、斜 45° 通时不报；通道从焊盘外沿起算")

        # --max-gap：最远 0.75 mm（Y1），门限 0.6 判红，1.0 不判
        bd, out = case("max-gap")
        build(bd)
        code, j, txt = run(bd, out, "--max-gap", "0.6")
        assert code == 1 and any("超门限" in d for d in j["defects"]) and "超门限" in txt, (code, j["defects"])
        code, j, _ = run(bd, out + "-1.0", "--max-gap", "1.0")
        assert code == 0, (code, j["defects"])
        passed.append("--max-gap 0.6 退出码 1、1.0 退出码 0")

        # 缺 .kicad_pro：退出码 2
        bd, out = case("neg-no-pro")
        build(bd, pro=False)
        code, j, _ = run(bd, out)
        assert code == 2 and "kicad_pro" in j.get("refused", ""), (code, j.get("refused"))
        passed.append("缺 .kicad_pro：退出码 2")

        # 焊盘间距覆盖、差分线宽进规则
        bd, out = case("rules-variant")
        build(bd, rules_variant=True)
        code, j, _ = run(bd, out)
        r = j["rules"]
        assert r["clear"]["mm"] == 0.25 and "焊盘 C1.1 间距覆盖" in r["clear"]["source"], r["clear"]
        assert r["track"]["mm"] == 0.25 and "差分线宽" in r["track"]["source"], r["track"]
        passed.append("规则：焊盘间距覆盖 0.25、差分线宽 0.25 被取为最严，出处写明")

        # 找不到 IC、证据目录已存在
        bd, out = case("neg-no-ic")
        build(bd)
        p = subprocess.run([sys.executable, AUDIT, bd, "--ic", "U99", "--out", out], capture_output=True, text=True)
        assert p.returncode == 2, p.returncode
        p = subprocess.run([sys.executable, AUDIT, bd, "--ic", "U1", "--out", out], capture_output=True, text=True)
        assert p.returncode == 2 and "已存在" in p.stderr, p.stderr
        passed.append("找不到 IC：退出码 2；证据目录已存在：拒绝，退出码 2")

        # 正例：未改的板
        assert code0 == 0, (code0, base.get("defects"), base.get("refused"), base.get("error"))
        s = base["sections"]
        r = base["rules"]
        assert (r["track"]["mm"], r["clear"]["mm"], r["via"]["mm"], r["edge"]["mm"]) == (0.2, 0.2, 0.6, 0.3), r
        assert "网络类" in r["clear"]["source"] and "min_copper_edge_clearance" in r["edge"]["source"], r
        assert abs(g0 - 0.55) < 0.01, g0                                   # 单位/坐标：手算 0.55 mm
        assert abs(pin(base, "6")["gap_mm"] - 0.55) < 0.01, pin(base, "6")  # C2 竖放同样 0.55
        assert not pin(base, "1")["ground_pad_faces_pin"] and not pin(base, "6")["ground_pad_faces_pin"]
        gr = {x["ground_pad"]: x["via_gap_mm"] for x in s["ground"]}
        assert gr["C1.2"] is not None and gr["C1.2"] < 0.01 and gr["C2.2"] is not None, gr
        assert s["fronts"] == [] and s["fronts_checked_pins"] == ["3", "8"], (s["fronts"], s["fronts_checked_pins"])
        py = s["pairs"]
        assert len(py) == 1 and py[0]["part"] == "Y1" and abs(py[0]["difference_mm"]) < 0.01, py
        assert base["defects"] == [] and base["source_unchanged"] is True
        passed.append("正例 未改的板：退出码 0，C1/C2/3 脚无缺陷；间隙 0.550 mm 与手算一致；规则取网络类 0.2/0.2/0.6、边 0.3")
    finally:
        for i, x in enumerate(passed, 1):
            print(f"通过 {i}: {x}")
        if not a.keep:
            shutil.rmtree(root, ignore_errors=True)
    print(f"全部通过：{len(passed)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
