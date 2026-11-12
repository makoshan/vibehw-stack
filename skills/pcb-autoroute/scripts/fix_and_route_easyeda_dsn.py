#!/usr/bin/env python3
"""修复嘉立创 EDA 专业版 (EasyEDA Pro) 导出的 Specctra .dsn 过孔与间距规则缺陷，并调用 Freerouting 无头自动布线输出 .ses。

根因背景（实测 Autorouter_v09imp_2026-10-25.dsn）：
1. EasyEDA Pro 导出 DSN 时常将 (padstack via0) 外径写为 12 mil (0.305 mm)，真实过孔外径应为 24 mil (0.61 mm)；
2. 全局规则写 (clear 6.05)，但每个网络自动生成的 (class ...) 内写死 (clearance 4)，导致 Freerouting 按 4 mil 贴孔走线，
   导回嘉立创后触发 200 条间距 DRC 报错（Track to Via 114、Hole to Track 52）。

用法：
  python3 fix_and_route_easyeda_dsn.py <input.dsn> [--out-dsn fixed.dsn] [--out-ses fixed.ses] [--passes 100]
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


def patch_easyeda_dsn(text: str, via_mil: float = 24.0, clearance_mil: float = 6.05) -> tuple[str, int]:
    """修补 via0 焊盘栈外径与 class 内部过小间距，返回 (修补后文本, 修改行数)。"""
    fixed = re.sub(
        r"(\(padstack\s+via0\s*\n\s*\(shape\s*\(circle\s+TopLayer\s+)12(\)\)\s*\n\s*\(shape\s*\(circle\s+BottomLayer\s+)12(\)\))",
        rf"\g<1>{via_mil:g}\g<2>{via_mil:g}\g<3>",
        text,
    )
    fixed = re.sub(r"\(clearance\s+4(?:\.0+)?\)", f"(clearance {clearance_mil:g})", fixed)
    diff_lines = sum(1 for a, b in zip(text.splitlines(), fixed.splitlines()) if a != b)
    return fixed, diff_lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Fix EasyEDA Pro Specctra DSN rules and run headless Freerouting.")
    ap.add_argument("input_dsn", type=Path, help="Input .dsn exported from EasyEDA Pro")
    ap.add_argument("--out-dsn", type=Path, default=None, help="Output patched .dsn path")
    ap.add_argument("--out-ses", type=Path, default=None, help="Output routed .ses path")
    ap.add_argument("--via-mil", type=float, default=24.0, help="Target via0 outer diameter in mil (default: 24)")
    ap.add_argument("--clearance-mil", type=float, default=6.05, help="Target net class clearance in mil (default: 6.05)")
    ap.add_argument("--passes", type=int, default=100, help="Max routing passes (default: 100)")
    ap.add_argument("--jar", type=Path, default=Path.home() / ".local/share/freerouting/freerouting.jar")
    args = ap.parse_args()

    src_path = args.input_dsn.resolve()
    out_dsn = (args.out_dsn or src_path.with_name(src_path.stem + "-fixed.dsn")).resolve()
    out_ses = (args.out_ses or out_dsn.with_suffix(".ses")).resolve()

    raw = src_path.read_text(encoding="utf-8", errors="replace")
    patched, diff_lines = patch_easyeda_dsn(raw, via_mil=args.via_mil, clearance_mil=args.clearance_mil)
    out_dsn.parent.mkdir(parents=True, exist_ok=True)
    out_dsn.write_text(patched, encoding="utf-8")
    print(f"[1/2] 已写出修复版 DSN: {out_dsn} (共修正 {diff_lines} 行)")

    java_bin = "/opt/homebrew/opt/openjdk@26/bin/java"
    if not Path(java_bin).exists():
        java_bin = "java"

    cmd = [
        java_bin,
        "-jar",
        str(args.jar),
        "-de",
        str(out_dsn),
        "-do",
        str(out_ses),
        "-mp",
        str(args.passes),
        "-mt",
        "1",
        "--gui.enabled=false",
    ]
    env = os.environ.copy()
    env["FREEROUTING__USAGE_AND_DIAGNOSTIC_DATA__DISABLE_ANALYTICS"] = "true"
    print(f"[2/2] 正在运行无头 Freerouting: {' '.join(cmd)}")
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    for line in res.stdout.splitlines()[-15:]:
        print("  ", line)
    if res.returncode != 0 or not out_ses.exists():
        print("Freerouting 执行异常:", res.stderr[-500:], file=sys.stderr)
        return 1
    print(f"[OK] SES 已生成: {out_ses} ({out_ses.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
