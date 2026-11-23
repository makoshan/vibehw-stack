#!/usr/bin/env python3
"""fitprobe.py 自检：必败反例先判红，修正版判绿。"""
from __future__ import annotations

from pathlib import Path
import sys

from build123d import Box, Cylinder, GeomType, Pos, Shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fitprobe import (  # noqa: E402
    PLUG_ENVELOPE,
    assert_healthy,
    b2b_pitch_check,
    clash,
    edge_io_mutex_check,
    fpc_tail_length,
    floating_stop_check,
    match_holes,
    plug_sweep,
    rivet_hole_fit_check,
    slide_path,
    solids,
    under_board_top_z,
)


def main() -> int:
    # 1. plug_sweep 正反例：壁厚 3mm（X=0..3），USB-C 插座在 X=3.5（凹进 3.5mm）
    # 反例：只按 USB-C 金属舌片开 9.5×3.5 的小口 -> 13×7.5 插头胶壳撞壁判红
    wall_base = Pos(1.5, 0, 0) * Box(3.0, 40.0, 25.0)
    small_hole = Pos(1.5, 0, 0) * Box(4.0, 9.5, 3.5)
    wall_bad = wall_base - small_hole
    r_plug_bad = plug_sweep(
        {"wall": wall_bad},
        axis="x",
        start=-5.0,
        end=3.5,
        center_orth=(0.0, 0.0),
        plug_wh="USB-C",
        opening_orth=(-4.75, 4.75, -1.75, 1.75),
        min_clearance=0.2,
    )
    assert not r_plug_bad["clear"], r_plug_bad
    assert r_plug_bad["clashes"]["wall"] > 50.0, r_plug_bad
    assert r_plug_bad["min_margin"] < 0.0, r_plug_bad

    # 正例：按 PLUG_ENVELOPE (13×7.5) 加单边 0.3 余量开 13.6×8.1 的口 -> 零干涉且余量通过
    good_hole = Pos(1.5, 0, 0) * Box(4.0, 13.6, 8.1)
    wall_good = wall_base - good_hole
    r_plug_good = plug_sweep(
        {"wall": wall_good},
        axis="x",
        start=-5.0,
        end=3.5,
        center_orth=(0.0, 0.0),
        plug_wh=PLUG_ENVELOPE["USB-C"],
        opening_orth=(-6.8, 6.8, -4.05, 4.05),
        min_clearance=0.2,
    )
    assert r_plug_good["clear"], r_plug_good
    assert r_plug_good["clashes"]["wall"] == 0.0 and r_plug_good["min_margin"] >= 0.2, r_plug_good

    # 2. slide_path 正反例：预装板从 Z=-12 沿 +Z 滑入到 Z=0
    # 反例：Z=-6 处有一道凸肋挡在滑入路径上，终点 Z=0 虽无干涉（clash==0），但中途 Z=-6 被刮住
    unit = Pos(0, 0, 0) * Box(20.0, 20.0, 2.0)
    shell_with_rib = (Pos(11.5, 0, 0) * Box(2.0, 24.0, 30.0)) + (Pos(9.5, 0, -6.0) * Box(2.0, 6.0, 2.0))
    assert clash(shell_with_rib, unit) == 0.0  # 静态终点完全无干涉
    r_slide_bad = slide_path(shell_with_rib, unit, axis="z", stations=[-12, -8, -6, -4, 0], tol=0.5)
    assert not r_slide_bad["clear"] and r_slide_bad["max_clash"] > 5.0, r_slide_bad

    # 正例：削平凸肋后全路径无干涉
    shell_clean = Pos(11.5, 0, 0) * Box(2.0, 24.0, 30.0)
    assert solids(shell_clean, "shell")["single"]
    r_slide_good = slide_path(shell_clean, unit, axis="z", stations=[-12, -8, -6, -4, 0], tol=0.5)
    assert r_slide_good["clear"] and r_slide_good["max_clash"] == 0.0, r_slide_good

    # 3. fpc_tail_length：180° 折弯半径 1.4mm 时，半圆弧长 π*1.4=4.398mm 比 2*r=2.8mm 长 1.598mm
    r_fpc = fpc_tail_length(span=4.0, dz=2.8, bend_r=1.4, extra_flat=0.33)
    assert r_fpc["curve_mm"] > r_fpc["scalar_mm"] + 1.5, r_fpc

    # 4. assert_healthy：布尔炸裂成小碎块或双分离实体必须抛 ValueError，正常单实体通过
    try:
        assert_healthy(Pos(0, 0, 0) * Box(1, 1, 1), min_vol=100.0, max_vol=500.0, name="shard")
        raise AssertionError("碎片体积未拦截")
    except ValueError:
        pass
    h_ok = assert_healthy(wall_good, min_vol=2000.0, max_vol=4000.0, name="wall_good")
    assert h_ok["valid"] and h_ok["solids"] == 1, h_ok

    # 5. cylinder_axes / match_holes：验证 Face.center() 在周期圆柱面上会偏一个半径，而 cylinder_axes 精确命中轴心
    plate = (Pos(0, 0, 0) * Box(30.0, 20.0, 3.0)) - (Pos(10.0, 5.0, 0) * Cylinder(1.25, 4.0))
    cyl_face = plate.faces().filter_by(GeomType.CYLINDER)[0]
    fc = cyl_face.center()
    assert abs(fc.X - 10.0) > 0.5 or abs(fc.Y - 5.0) > 0.5, f"Face.center() 未复现周期面半径偏移: {fc}"
    r_hole_bad = match_holes([(10.2, 5.0)], plate, radius=1.25, pos_tol=0.05)
    assert not r_hole_bad["clear"] and r_hole_bad["max_delta_mm"] > 0.15, r_hole_bad
    r_hole_good = match_holes([(10.0, 5.0)], plate, radius=1.25, pos_tol=0.05)
    assert r_hole_good["clear"] and r_hole_good["max_delta_mm"] == 0.0, r_hole_good

    # 6. under_board_top_z：高柱 (z=0..5) 能抓到高出板面 (z=1.0..1.8) 的电极柱顶 z=3.2
    tall_post = Pos(0, 0, 1.6) * Box(2.0, 2.0, 3.2)  # z=0..3.2
    thin_res = under_board_top_z((-10, -10, 10, 10), (1.0, 1.8), [tall_post])
    tall_res = under_board_top_z((-10, -10, 10, 10), (0.0, 5.0), [tall_post])
    assert thin_res["max_top_z"] == 1.8 and tall_res["max_top_z"] == 3.2, (thin_res, tall_res)

    # 7. floating_stop_check：浮动电极顶在 z=3.0，限位压条底在 z=3.15，PCB 底在 z=3.5
    electrode = Pos(0, 0, 1.5) * Box(4.0, 4.0, 3.0)   # z=0..3.0
    limiter = Pos(0, 0, 3.35) * Box(4.0, 4.0, 0.4)    # z=3.15..3.55
    pcb_high = Pos(0, 0, 4.0) * Box(10.0, 10.0, 0.8)  # z=3.6..4.4（安全）
    pcb_low = Pos(0, 0, 3.5) * Box(10.0, 10.0, 0.8)   # z=3.1..3.9（反例：比限位条还低，先撞 PCB）
    r_float_bad = floating_stop_check(electrode, limiter, pcb_low, gap_dz=0.05, stop_dz=0.20)
    assert not r_float_bad["clear"], r_float_bad
    r_float_good = floating_stop_check(electrode, limiter, pcb_high, gap_dz=0.05, stop_dz=0.20)
    assert r_float_good["clear"] and r_float_good["at_stop_dz"]["stop_clash"] >= 0.8, r_float_good

    # 8. b2b_pitch_check：分别独立验证 (8a) 中心距差 0.500mm、(8b) 逐脚世界坐标奇偶列镜像 1.27mm、(8c) 声明式标记，以及对齐正例
    r_b2b_pitch_bad = b2b_pitch_check(
        [(23.0, 5.0), (23.0, 33.5)],
        [(23.0, 5.198), (23.0, 33.198)],
        pitch_tol=0.05,
    )
    assert not r_b2b_pitch_bad["clear"] and abs(r_b2b_pitch_bad["max_mismatch_mm"] - 0.5) < 1e-4 and not r_b2b_pitch_bad["mirror_pin_swap_risk"], r_b2b_pitch_bad
    r_b2b_pin_bad = b2b_pitch_check(
        [(23.0, 5.0), (23.0, 33.5)],
        [(23.0, 5.0), (23.0, 33.5)],
        pitch_tol=0.05,
        pin_world_xy=({"1": (-0.635, 5.0), "2": (0.635, 5.0)}, {"1": (0.635, 5.0), "2": (-0.635, 5.0)}),
    )
    assert not r_b2b_pin_bad["clear"] and len(r_b2b_pin_bad["pin_mismatches"]) == 2, r_b2b_pin_bad
    r_b2b_flag_bad = b2b_pitch_check(
        [(23.0, 5.0), (23.0, 33.5)],
        [(23.0, 5.0), (23.0, 33.5)],
        pitch_tol=0.05,
        dual_row_mirror=("TOP", "BOTTOM", True),
    )
    assert not r_b2b_flag_bad["clear"] and r_b2b_flag_bad["max_mismatch_mm"] == 0.0 and r_b2b_flag_bad["mirror_pin_swap_risk"], r_b2b_flag_bad
    r_b2b_good = b2b_pitch_check(
        [(23.0, 5.0), (23.0, 33.5)],
        [(23.0, 5.0), (23.0, 33.5)],
        pitch_tol=0.05,
        dual_row_mirror=("TOP", "BOTTOM", False),
        pin_world_xy=({"1": (-0.635, 5.0), "2": (0.635, 5.0)}, {"1": (-0.635, 5.0), "2": (0.635, 5.0)}),
    )
    assert r_b2b_good["clear"] and r_b2b_good["max_mismatch_mm"] == 0.0 and not r_b2b_good["mirror_pin_swap_risk"], r_b2b_good

    # 9. edge_io_mutex_check：南北双边出表带时南边同放 USB2 + SW1 判红；北侧出带、东侧放 USB1 + U1 判绿
    r_edge_bad = edge_io_mutex_check(["+Y", "-Y"], [{"name": "USB2", "edge": "-Y"}, {"name": "SW1", "edge": "-Y"}])
    assert not r_edge_bad["clear"] and len(r_edge_bad["conflicts"]) == 2, r_edge_bad
    r_edge_good = edge_io_mutex_check(["+Y"], [{"name": "USB1", "edge": "+X"}, {"name": "U1_SW", "edge": "+X"}])
    assert r_edge_good["clear"], r_edge_good

    # 10. ShapeList 多块相交回归防线：超宽插头横跨 U 型槽双侧壁时，`.intersect()` 返回 ShapeList（非 Shape、无 .volume/.bounding_box）
    u_wall = (Pos(0, 0, 5) * Box(30.0, 4.0, 10.0)) - (Pos(0, 0, 7) * Box(13.6, 6.0, 8.0))
    wide_plug = Pos(0, 0, 7) * Box(15.6, 4.0, 6.0)  # 左右各撞壁 1.0×4.0×6.0 = 24.0 mm^3，共 48.0 mm^3 双块
    raw_inter = u_wall.intersect(wide_plug)
    assert isinstance(raw_inter, list) and not isinstance(raw_inter, Shape) and not hasattr(raw_inter, "volume"), type(raw_inter)
    assert solids(raw_inter, "multi_inter")["count"] == 2
    tot_multi, lumps_multi = clash(u_wall, wide_plug, detail=True)
    assert abs(tot_multi - 48.0) < 0.05 and len(lumps_multi) == 2, (tot_multi, lumps_multi)
    narrow_plug = Pos(0, 0, 7) * Box(13.0, 4.0, 6.0)
    assert clash(u_wall, narrow_plug) == 0.0

    # 11. rivet_hole_fit_check：Φ1.5×Φ0.8 黄铜铆钉穿 Ø1.60/Ø2.20 焊盘孔直穿判红；帽檐过窄判红；Φ3.5×Φ0.8 配 Ø0.90 孔判绿
    r_riv_drop = rivet_hole_fit_check(hole_d=1.60, head_d=1.50, shaft_d=0.80)
    assert not r_riv_drop["clear"] and r_riv_drop["drop_through"], r_riv_drop
    r_riv_rim = rivet_hole_fit_check(hole_d=1.60, head_d=1.90, shaft_d=1.40)
    assert not r_riv_rim["clear"] and not r_riv_rim["grip_ok"], r_riv_rim
    r_riv_good = rivet_hole_fit_check(hole_d=0.90, head_d=3.50, shaft_d=0.80)
    assert r_riv_good["clear"] and r_riv_good["annular_rim_per_side_mm"] == 1.30, r_riv_good

    print("PASS: test_fitprobe.py 11 组必败反例全红，修正版全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
