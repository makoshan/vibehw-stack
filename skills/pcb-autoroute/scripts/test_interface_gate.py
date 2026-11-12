#!/usr/bin/env python3
"""interface_gate.py 自检：用 pcbnew 现造最小测试板，先验必败反例全红，再验修正版全绿。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import interface_gate as ig  # noqa: E402

PANEL_NETS = [
    "GND", "LEDK", "LEDA", "VCC_3V3", "GND", "GND",
    "LCD_DC", "LCD_CS", "SPI_SCK", "SPI_MOSI", "LCD_RST", "GND",
]


def _make_board(
    path: Path,
    *,
    mouth_x_offset: float = 2.0,
    reverse_panel_nets: bool = True,
    wire_x: float = 128.0,
    module_usb_left: bool = True,
    omit_j2: bool = False,
) -> None:
    if sys.platform == "darwin":
        import wx
        if wx.GetApp() is None:
            _make_board._app = wx.App(False)
    import pcbnew as k

    mm = k.FromMM
    board = k.BOARD()

    # 1. 板框 100..131 x 100..130
    for x1, y1, x2, y2 in [
        (100.0, 100.0, 131.0, 100.0),
        (131.0, 100.0, 131.0, 130.0),
        (131.0, 130.0, 100.0, 130.0),
        (100.0, 130.0, 100.0, 100.0),
    ]:
        seg = k.PCB_SHAPE(board)
        seg.SetShape(k.SHAPE_T_SEGMENT)
        seg.SetLayer(k.Edge_Cuts)
        seg.SetStart(k.VECTOR2I(mm(x1), mm(y1)))
        seg.SetEnd(k.VECTOR2I(mm(x2), mm(y2)))
        board.Add(seg)

    net_map = {}
    for name in set(PANEL_NETS) | {"VBAT", "USB_DP"}:
        ni = k.NETINFO_ITEM(board, name)
        board.Add(ni)
        net_map[name] = ni

    def add_pad(fp, num: str, x: float, y: float, net_name: str = "") -> None:
        pad = k.PAD(fp)
        pad.SetNumber(num)
        pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetLayerSet(k.PAD.SMDMask())
        pad.SetShape(k.F_Cu, k.PAD_SHAPE_RECT)
        pad.SetSize(k.F_Cu, k.VECTOR2I(mm(0.6), mm(0.25)))
        pad.SetPosition(k.VECTOR2I(mm(x), mm(y)))
        if net_name:
            pad.SetNet(net_map[net_name])
        fp.Add(pad)

    # 2. J1：12P 下接触 FPC 座（嘴朝 +x 时，Hirose FH12 封装 pad 12 在上 y=110.0，pad 1 在下 y=115.5）
    # t = (-1, 0), nz = +1 => d = n x t = (0, -1)，故 panel pin 1 落在最小 y 的焊盘（pad 12）
    j1 = k.FOOTPRINT(board)
    j1.SetReference("J1")
    j1.SetLayer(k.F_Cu)
    j1.SetPosition(k.VECTOR2I(mm(125.0), mm(112.75)))
    for i in range(12):
        pad_num = i + 1  # pad 1 @ y=115.5 .. pad 12 @ y=110.0
        y_pos = 115.5 - i * 0.5
        net_idx = (11 - i) if reverse_panel_nets else i
        add_pad(j1, str(pad_num), 125.0, y_pos, PANEL_NETS[net_idx])
    add_pad(j1, "MP1", 125.0 + mouth_x_offset, 109.2)
    add_pad(j1, "MP2", 125.0 + mouth_x_offset, 116.3)
    board.Add(j1)

    # 3. J2：电池焊线盘
    if not omit_j2:
        j2 = k.FOOTPRINT(board)
        j2.SetReference("J2")
        j2.SetLayer(k.F_Cu)
        j2.SetPosition(k.VECTOR2I(mm(wire_x), mm(124.0)))
        add_pad(j2, "1", wire_x, 123.5, "VBAT")
        add_pad(j2, "2", wire_x, 124.5, "GND")
        board.Add(j2)

    # 4. U1：核心板模块（USB 口在 pad 1，远端在 pad 8）
    u1 = k.FOOTPRINT(board)
    u1.SetReference("U1")
    u1.SetLayer(k.F_Cu)
    u1.SetPosition(k.VECTOR2I(mm(110.0), mm(115.0)))
    usb_x, far_x = (102.0, 118.0) if module_usb_left else (118.0, 102.0)
    add_pad(u1, "1", usb_x, 115.0, "USB_DP")
    add_pad(u1, "8", far_x, 115.0, "GND")
    board.Add(u1)

    k.SaveBoard(str(path), board)


def main() -> int:
    spec = {
        "board_bbox": [100.0, 100.0, 131.0, 130.0],
        "interfaces": {
            "J1": {
                "kind": "connector",
                "mate_from": [1, 0],
                "contact": "bottom",
                "panel": PANEL_NETS,
                "monotonic_axis": "y",
                "monotonic_dir": -1,
                "why": "FPC 从 +x 板边 180° 折回插入",
            },
            "J2": {
                "kind": "wire",
                "edge": "x_max",
                "max_mm": 5.0,
                "why": "电池引线在 +x 侧",
            },
            "U1": {
                "kind": "module",
                "usb_pad": "1",
                "far_pad": "8",
                "usb_toward": [-1, 0],
                "why": "模块 USB 口朝 -x 壳壁开口",
            },
        },
    }

    with tempfile.TemporaryDirectory(prefix="test-iface-gate-") as tmp:
        root = Path(tmp)

        # 反例 1：FPC 脚序未倒序（重现 v0.19B：pin1 落 pad12，LCD_CS/LCD_DC 短到 GND）且嘴朝内、焊盘过远、模块反向
        bad_board = root / "bad.kicad_pcb"
        _make_board(
            bad_board,
            mouth_x_offset=-2.0,       # 嘴朝 -x（板内），与 mate_from=[1,0] 相反
            reverse_panel_nets=False,  # 未按 n x t 倒序
            wire_x=110.0,              # 距 x_max(131) 21 mm > 5 mm
            module_usb_left=False,     # USB 朝 +x，与 [-1,0] 相反
        )
        r_bad = ig.check_board(bad_board, spec)
        assert r_bad["gate"] == "INTERFACES_FAIL", r_bad
        assert set(r_bad["fails"]) == {"J1", "J2", "U1"}, r_bad

        # 反例 2：嘴朝向已修对（+x），但下接触座脚序画反（v0.19B 实测故障：pin1->pad12，LCD_CS/LCD_DC 落 GND）
        mirror_board = root / "mirror.kicad_pcb"
        _make_board(mirror_board, mouth_x_offset=2.0, reverse_panel_nets=False)
        r_mirror = ig.check_board(mirror_board, spec)
        j1_m = next(c for c in r_mirror["checks"] if c["ref"] == "J1")
        assert j1_m["result"] == "FAIL" and j1_m["panel_pin1_on_pad"] == "12", j1_m
        assert {"LCD_CS", "LCD_DC"} <= set(j1_m["signals_on_gnd"]), j1_m

        # 反例 3：缺少必选连接器 J2 不能静默通过，必须判 FAIL
        missing_board = root / "missing.kicad_pcb"
        _make_board(missing_board, omit_j2=True)
        r_missing = ig.check_board(missing_board, spec)
        assert r_missing["gate"] == "INTERFACES_FAIL" and "J2" in r_missing["fails"], r_missing

        # 反例 4：背靠背连接器世界坐标单调方向相反必须判 FAIL
        bad_mono_spec = json.loads(json.dumps(spec))
        bad_mono_spec["interfaces"]["J1"]["monotonic_dir"] = 1
        good_board = root / "good.kicad_pcb"
        _make_board(good_board)
        r_mono = ig.check_board(good_board, bad_mono_spec)
        assert r_mono["gate"] == "INTERFACES_FAIL" and "J1" in r_mono["fails"], r_mono

        # 正例：全部满足 -> INTERFACES_PASS
        r_good = ig.check_board(good_board, spec)
        assert r_good["gate"] == "INTERFACES_PASS" and not r_good["fails"], r_good
        j1_g = next(c for c in r_good["checks"] if c["ref"] == "J1")
        assert j1_g["panel_pin1_on_pad"] == "12" and not j1_g["wrong_pins"], j1_g

    print("PASS: test_interface_gate.py 4 项必败反例全红，修正版 INTERFACES_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
