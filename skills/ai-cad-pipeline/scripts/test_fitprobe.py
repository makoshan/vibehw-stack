#!/usr/bin/env python3
"""fitprobe.py 自检：必败反例先判红，修正版判绿。"""
from __future__ import annotations

from pathlib import Path
import sys

from build123d import Box, Pos

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fitprobe import (  # noqa: E402
    PLUG_ENVELOPE,
    clash,
    fpc_tail_length,
    plug_sweep,
    slide_path,
    solids,
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

    print("PASS: test_fitprobe.py 必败反例全红，修正版全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
