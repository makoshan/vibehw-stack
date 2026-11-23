#!/usr/bin/env python3
"""路线 B：基于本机无头 FreeCAD `TechDraw.projectEx` + 布尔薄片全剖的国标（GB/T）机械总装配工程图生成器。

功能：
1. 读取 JSON 装配清单（`--manifest assy.json`）指定的多个 `.step` 零件，调用 FreeCAD `Part` 与 `TechDraw.projectEx`：
   - 外露视图合并为单一 `Part.makeCompound` 执行全局三维 HLR 消隐投影（杜绝下层零件孔洞穿透上盖）；
   - 全剖视图用 `0.04 mm` 薄片 `shape.common(slab)` 提取真实闭合切面环，自动分配相邻件 `±45°` 异向变距剖面线（`<2mm` 薄片涂黑）；
   - 爆炸等轴测视图用顶件与底件投影均值做 `assert y_svg_top < y_svg_bottom` 硬断言自动校准 Z 向符号；
2. 渲染 `GB/T 14689-2008` 标准图框（A0–A2 `c=10mm`、A3–A4 `c=5mm`、左 `a=25mm`）、`GB/T 14692` 第一角锥台符号（左小右大）、
   `GB/T 10609.1` 标题栏与 `GB/T 10609.2` `180mm` 自下而上明细栏，通过无头浏览器直出 1:1 矢量 PDF。
3. `--self-test`：检查标准参数、锥台几何反例与 Z 向防翻转反例；若本机安装了 FreeCAD，还现场构造上下双零件执行真实布尔剖切与单 `Compound` `TechDraw.projectEx` 遮挡消隐端到端验证。
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import subprocess
import sys


# GB/T 14689-2008 表 1：留装订边图幅 (width_mm, height_mm, a_left_mm, c_other_mm)
GB_SHEET_SPECS_MM = {
    "A0": (1189.0, 841.0, 25.0, 10.0),
    "A1": (841.0, 594.0, 25.0, 10.0),
    "A2": (594.0, 420.0, 25.0, 10.0),
    "A3": (420.0, 297.0, 25.0, 5.0),
    "A4": (297.0, 210.0, 25.0, 5.0),
}

# GB/T 10609.2 明细栏标准列宽：序号(8) + 代号(40) + 名称(44) + 数量(8) + 材料(38) + 重量(22) + 备注(20) = 180 mm
GB_BOM_COL_WIDTHS_MM = (8.0, 40.0, 44.0, 8.0, 38.0, 22.0, 20.0)


def try_import_freecad():
    for candidate in ("/Applications/FreeCAD.app/Contents/Resources/lib", "/usr/lib/freecad/lib", "/usr/lib/freecad-python3/lib"):
        if Path(candidate).is_dir() and candidate not in sys.path:
            sys.path.append(candidate)
    import FreeCAD as F  # type: ignore
    import Part  # type: ignore
    import TechDraw  # type: ignore
    return F, Part, TechDraw


def calibrate_svg_y_sign(top_proj_y: float, bottom_proj_y: float) -> float:
    """根据已知顶层件(+Z)与底层件(-Z)在 TechDraw 2D 投影系中的平均 Y 坐标，计算映射到向下+Y 的 SVG 符号并硬断言。"""
    if abs(top_proj_y - bottom_proj_y) < 1e-6:
        raise ValueError("Top and bottom parts project to identical Y; check explode vector or view direction")
    y_sign = -1.0 if top_proj_y > bottom_proj_y else 1.0
    pcy = 0.5 * (top_proj_y + bottom_proj_y)
    y_svg_top = y_sign * (top_proj_y - pcy)
    y_svg_bottom = y_sign * (bottom_proj_y - pcy)
    assert y_svg_top < y_svg_bottom, f"Exploded Z inverted: top={y_svg_top}, bottom={y_svg_bottom}"
    return y_sign


def build_first_angle_frustum_pts(cx: float, cy: float, half_len: float = 4.25, r_left: float = 2.0, r_right: float = 3.8) -> list[tuple[float, float]]:
    """构造 GB/T 14692 第一角画法识别符号左锥台顶点，强制断言左端半径严格小于右端半径（防画反成左大右小）。"""
    if r_left <= 0 or r_right <= 0 or r_left >= r_right:
        raise ValueError(f"GB/T 14692 first-angle frustum requires 0 < r_left < r_right, got ({r_left}, {r_right})")
    x_l, x_r = cx - half_len, cx + half_len
    return [(x_l, cy - r_left), (x_r, cy - r_right), (x_r, cy + r_right), (x_l, cy + r_left)]


def discretize_edges_2d(shape_2d, deflection: float = 0.08) -> list[list[tuple[float, float]]]:
    polys = []
    for edge in shape_2d.Edges:
        if edge.Length < 1e-4:
            continue
        pts = edge.discretize(Deflection=deflection)
        if len(pts) >= 2:
            polys.append([(float(p.x), float(p.y)) for p in pts])
    return polys


def project_compound_hlr(shapes: list, view_dir) -> list[list[tuple[float, float]]]:
    """将多个零件合并为单一 Part.makeCompound 做全局三维 HLR 消隐，自动裁掉被上层实心件遮挡的内孔线。"""
    _, Part, TechDraw = try_import_freecad()
    comp = Part.makeCompound(shapes)
    groups = TechDraw.projectEx(comp, view_dir)
    vis = []
    for idx in (0, 1):
        if idx < len(groups) and not groups[idx].isNull():
            vis.extend(discretize_edges_2d(groups[idx], 0.08))
    return vis


def slice_section_loops(shape, axis: str = "X", coord: float = 0.0) -> list[list[tuple[float, float]]]:
    """用 0.04mm 薄片布尔求交提取精确闭合剖面环。"""
    F, Part, _ = try_import_freecad()
    eps = 0.04
    bb = shape.BoundBox
    pad = 10.0
    if axis.upper() == "X":
        slab = Part.makeBox(eps, bb.YLength + 2 * pad, bb.ZLength + 2 * pad,
                            F.Vector(coord - eps / 2.0, bb.YMin - pad, bb.ZMin - pad))
        n_idx = 0
    else:
        slab = Part.makeBox(bb.XLength + 2 * pad, eps, bb.ZLength + 2 * pad,
                            F.Vector(bb.XMin - pad, coord - eps / 2.0, bb.ZMin - pad))
        n_idx = 1
    inter = shape.common(slab)
    target = coord + eps / 2.0
    loops = []
    for face in inter.Faces:
        n = face.normalAt(0.5, 0.5)
        c = face.CenterOfMass
        if abs([n.x, n.y, n.z][n_idx]) > 0.95 and abs([c.x, c.y, c.z][n_idx] - target) < eps * 0.45:
            for wire in face.Wires:
                pts = wire.discretize(Deflection=0.05)
                if len(pts) >= 3:
                    poly = [(float(p.y if n_idx == 0 else p.x), float(p.z)) for p in pts]
                    if poly[0] != poly[-1]:
                        poly.append(poly[0])
                    loops.append(poly)
    return loops


def render_manifest_to_html(manifest: dict) -> str:
    """将 JSON 装配清单指定的 STEP 零件投影渲染为自包含 GB/T 图框+自下而上明细栏+全剖+爆炸图 HTML/SVG。"""
    F, Part, TechDraw = try_import_freecad()
    sheet_name = manifest.get("sheet", "A2").upper()
    w_mm, h_mm, a_mm, c_mm = GB_SHEET_SPECS_MM[sheet_name]
    ix0, iy0 = a_mm, c_mm
    iw, ih = w_mm - a_mm - c_mm, h_mm - 2 * c_mm
    ix1, iy1 = ix0 + iw, iy0 + ih

    items = manifest["parts"]
    shapes = [Part.read(str(Path(it["step"]).expanduser())) for it in items]

    els: list[str] = []
    defs: list[str] = []
    # 生成交替 ±45° 异向变距剖面线图案
    steps_pool = (1.1, 1.6, 2.2)
    for idx in range(len(items)):
        ang = 45 if idx % 2 == 0 else -45
        stp = steps_pool[idx % len(steps_pool)]
        defs.append(
            f'<pattern id="ht-{idx}" width="{stp}" height="{stp}" patternUnits="userSpaceOnUse" '
            f'patternTransform="rotate({ang})"><rect width="{stp}" height="{stp}" fill="#f6f7f8"/>'
            f'<line x1="0" y1="0" x2="0" y2="{stp}" stroke="#111" stroke-width="0.24"/></pattern>'
        )

    els.append(f'<rect x="{ix0}" y="{iy0}" width="{iw}" height="{ih}" fill="none" stroke="#111" stroke-width="0.7"/>')

    # 1) 左上 A-A 全剖主视图（厚度 < 2.0mm 薄件或显式标 thin_black 自动涂黑）
    scale = float(manifest.get("section_scale", 1.0))
    ox, oy = ix0 + iw * 0.28, iy0 + ih * 0.34
    els.append(f'<text x="{ox:.2f}" y="{iy0+16:.2f}" font-size="4.2" font-weight="bold" text-anchor="middle">A — A 全剖主视图 ({scale:g}:1)</text>')
    for idx, (it, shp) in enumerate(zip(items, shapes)):
        is_thin = bool(it.get("thin_black", shp.BoundBox.ZLength < 2.0))
        fill = "#111111" if is_thin else f"url(#ht-{idx})"
        for lp in slice_section_loops(shp, axis="X", coord=float(manifest.get("section_x", 0.0))):
            d = "M " + " L ".join(f"{ox + u*scale:.2f} {oy - v*scale:.2f}" for u, v in lp) + " Z"
            els.append(f'<path d="{d}" fill="{fill}" fill-rule="evenodd" stroke="#111" stroke-width="0.55"/>')

    # 2) 左下单一 Compound 全局 HLR 俯视图（同比例对齐 + A-A 剖切符号）
    pox, poy = ox, iy0 + ih * 0.70
    vis_top = project_compound_hlr(shapes, F.Vector(0, 0, 1))
    for poly in vis_top:
        pts = " ".join(f"{pox + y*scale:.2f},{poy + x*scale:.2f}" for x, y in poly)
        els.append(f'<polyline points="{pts}" fill="none" stroke="#111" stroke-width="0.50"/>')
    els.append(f'<line x1="{pox-36:.2f}" y1="{poy:.2f}" x2="{pox-30:.2f}" y2="{poy:.2f}" stroke="#111" stroke-width="0.95"/>')
    els.append(f'<line x1="{pox+30:.2f}" y1="{poy:.2f}" x2="{pox+36:.2f}" y2="{poy:.2f}" stroke="#111" stroke-width="0.95"/>')
    els.append(f'<text x="{pox-38:.2f}" y="{poy-1.5:.2f}" font-size="4.0" font-weight="bold">A</text>')
    els.append(f'<text x="{pox+37:.2f}" y="{poy-1.5:.2f}" font-size="4.0" font-weight="bold">A</text>')

    # 3) 右上爆炸轴测图（含 Z 符号校准硬断言）
    iso_dir = F.Vector(1.0, -1.15, 0.82)
    moved = []
    for idx, shp in enumerate(shapes):
        m = shp.copy()
        m.translate(F.Vector(0, 0, (idx - (len(shapes) - 1) / 2.0) * float(manifest.get("explode_step_mm", 15.0))))
        moved.append(m)
    if len(moved) >= 2:
        g_bot = TechDraw.projectEx(moved[0], iso_dir)[0]
        g_top = TechDraw.projectEx(moved[-1], iso_dir)[0]
        by = sum(v.Y for e in g_bot.Edges for v in e.Vertexes) / max(1, sum(len(e.Vertexes) for e in g_bot.Edges))
        ty = sum(v.Y for e in g_top.Edges for v in e.Vertexes) / max(1, sum(len(e.Vertexes) for e in g_top.Edges))
        y_sign = calibrate_svg_y_sign(top_proj_y=ty, bottom_proj_y=by)
    else:
        y_sign = 1.0
    vis_iso = project_compound_hlr(moved, iso_dir)
    if vis_iso:
        xs = [p[0] for poly in vis_iso for p in poly]
        ys = [p[1] for poly in vis_iso for p in poly]
        pcx, pcy = 0.5 * (min(xs) + max(xs)), 0.5 * (min(ys) + max(ys))
        iox, ioy, isc = ix1 - 90.0, iy0 + ih * 0.34, float(manifest.get("iso_scale", 1.0))
        for poly in vis_iso:
            pts = " ".join(f"{iox + (x - pcx)*isc:.2f},{ioy + y_sign*(y - pcy)*isc:.2f}" for x, y in poly)
            els.append(f'<polyline points="{pts}" fill="none" stroke="#111" stroke-width="0.38"/>')

    # 4) 右下 GB/T 10609.1 标题栏 (180x56mm) + GB/T 10609.2 180mm 七列自下而上明细栏 (行高 7mm) + 第一角锥台
    tb_w, tb_h = 180.0, 56.0
    tb_x, tb_y = ix1 - tb_w, iy1 - tb_h
    els.append(f'<rect x="{tb_x}" y="{tb_y}" width="{tb_w}" height="{tb_h}" fill="#fff" stroke="#111" stroke-width="0.7"/>')
    title_txt = html.escape(manifest.get("title", "机械总装配图"))
    els.append(f'<text x="{tb_x+110}" y="{tb_y+30}" font-size="4.8" font-weight="bold" text-anchor="middle">{title_txt}</text>')
    trap_pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in build_first_angle_frustum_pts(ix1 - 24.0, tb_y + 9.0))
    els.append(f'<polygon points="{trap_pts}" fill="none" stroke="#111" stroke-width="0.45"/>')
    els.append(f'<circle cx="{ix1-12.5:.2f}" cy="{tb_y+9.0:.2f}" r="3.8" fill="none" stroke="#111" stroke-width="0.45"/>')
    els.append(f'<circle cx="{ix1-12.5:.2f}" cy="{tb_y+9.0:.2f}" r="2.0" fill="none" stroke="#111" stroke-width="0.45"/>')

    row_h, hdr_h = 7.0, 9.0
    bom_top = tb_y - hdr_h - len(items) * row_h
    els.append(f'<rect x="{tb_x}" y="{bom_top:.2f}" width="{tb_w}" height="{tb_y - bom_top:.2f}" fill="#fff" stroke="#111" stroke-width="0.5"/>')
    hdr_y = tb_y - hdr_h
    els.append(f'<line x1="{tb_x}" y1="{hdr_y:.2f}" x2="{ix1}" y2="{hdr_y:.2f}" stroke="#111" stroke-width="0.45"/>')
    col_xs = [tb_x]
    for w_c, h_lbl in zip(GB_BOM_COL_WIDTHS_MM, ("序号", "代号", "名称", "数量", "材料", "重量", "备注")):
        cx = col_xs[-1]
        els.append(f'<text x="{cx + w_c/2:.2f}" y="{hdr_y+6.0:.2f}" font-size="3.0" font-weight="600" text-anchor="middle">{h_lbl}</text>')
        col_xs.append(cx + w_c)
        if col_xs[-1] < ix1 - 0.1:
            els.append(f'<line x1="{col_xs[-1]:.2f}" y1="{bom_top:.2f}" x2="{col_xs[-1]:.2f}" y2="{tb_y:.2f}" stroke="#111" stroke-width="0.25"/>')
    for i, it in enumerate(items):
        ry = hdr_y - (i + 1) * row_h
        els.append(f'<line x1="{tb_x}" y1="{ry:.2f}" x2="{ix1}" y2="{ry:.2f}" stroke="#111" stroke-width="0.25"/>')
        vals = (str(i + 1), it.get("code", ""), it.get("name", ""), str(it.get("qty", 1)),
                it.get("material", ""), str(it.get("mass", "")), it.get("remark", ""))
        for ci, val in enumerate(vals):
            els.append(f'<text x="{col_xs[ci] + GB_BOM_COL_WIDTHS_MM[ci]/2:.2f}" y="{ry+4.8:.2f}" font-size="2.7" text-anchor="middle">{html.escape(val)}</text>')

    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"/><style>@page{{size:{w_mm}mm {h_mm}mm;margin:0;}}'
        f'html,body{{margin:0;width:{w_mm}mm;height:{h_mm}mm;font-family:"PingFang SC","Heiti SC",sans-serif;}}</style></head>'
        f'<body><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w_mm} {h_mm}">'
        f'<defs>{"".join(defs)}</defs><rect width="{w_mm}" height="{h_mm}" fill="#fff"/>{"".join(els)}</svg></body></html>'
    )


def self_test() -> None:
    # 1. 标准表核验
    assert sum(GB_BOM_COL_WIDTHS_MM) == 180.0
    assert GB_SHEET_SPECS_MM["A2"][3] == 10.0 and GB_SHEET_SPECS_MM["A3"][3] == 5.0 and GB_SHEET_SPECS_MM["A4"][3] == 5.0

    # 2. GB/T 14692 第一角锥台正反例（左大右小必败拦截）
    pts = build_first_angle_frustum_pts(100.0, 50.0, r_left=2.0, r_right=3.8)
    assert (pts[3][1] - pts[0][1]) < (pts[2][1] - pts[1][1])
    for bad_l, bad_r in ((3.8, 2.0), (2.5, 2.5), (-1.0, 2.0)):
        try:
            build_first_angle_frustum_pts(100.0, 50.0, r_left=bad_l, r_right=bad_r)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Failed to reject invalid frustum radii ({bad_l}, {bad_r})")

    # 3. 爆炸图 Z 向校准正反例
    assert calibrate_svg_y_sign(12.5, -14.0) == -1.0
    assert calibrate_svg_y_sign(-12.5, 14.0) == 1.0
    try:
        calibrate_svg_y_sign(4.0, 4.0)
    except ValueError:
        pass
    else:
        raise AssertionError("Failed to reject degenerate Z projection")

    # 4. 若本机装有 FreeCAD，现场跑真实双零件单 Compound HLR 遮挡消隐对照（对比逐件投影伪穿透 vs Compound 消隐）
    try:
        F, Part, _ = try_import_freecad()
        # 下层托盘带中心 10x10 方孔，上层实心上盖完全盖住方孔
        tray = Part.makeBox(30, 30, 4, F.Vector(-15, -15, 0)).cut(Part.makeBox(10, 10, 4, F.Vector(-5, -5, 0)))
        cover = Part.makeBox(30, 30, 3, F.Vector(-15, -15, 5))
        naive_edges = len(project_compound_hlr([tray], F.Vector(0, 0, 1))) + len(project_compound_hlr([cover], F.Vector(0, 0, 1)))
        comp_edges = len(project_compound_hlr([tray, cover], F.Vector(0, 0, 1)))
        # 逐件投影会把下层方孔 4 条边额外画出来 (8 > 4)；单 Compound 消隐后顶视仅剩外框 4 条边！
        assert comp_edges < naive_edges and comp_edges == 4, f"Expected occluded 4 edges, got comp={comp_edges} naive={naive_edges}"
        loops_tray = slice_section_loops(tray, axis="X", coord=0.0)
        assert len(loops_tray) == 2, f"Expected 2 section loops across center hole, got {len(loops_tray)}"
        fc_msg = f" + FreeCAD live HLR ({naive_edges}->4 occluded edges & 2 section loops)"
    except ImportError:
        fc_msg = " (FreeCAD not in current python; pure-Python gates passed)"

    print("PASS: freecad_gb_assembly_drawing --self-test (GB/T 14689/14692/10609.2 + Z-sign" + fc_msg + ")")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--manifest", type=Path, help="装配清单 JSON 路径")
    ap.add_argument("--out-html", type=Path, help="输出 HTML/SVG 路径")
    ap.add_argument("--out-pdf", type=Path, help="经无头 Chrome 导出的 1:1 矢量 PDF 路径")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return
    if not args.manifest:
        ap.error("Specify --self-test or --manifest <assy.json>")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    doc_html = render_manifest_to_html(manifest)
    html_out = args.out_html or (args.out_pdf.with_suffix(".html") if args.out_pdf else Path("assembly_gb.html"))
    html_out.parent.mkdir(parents=True, exist_ok=True)
    html_out.write_text(doc_html, encoding="utf-8")
    if args.out_pdf:
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        subprocess.run([chrome, "--headless", "--disable-gpu", f"--print-to-pdf={args.out_pdf}", "--no-pdf-header-footer", str(html_out)], check=True)
        print("Wrote PDF:", args.out_pdf)
    else:
        print("Wrote HTML:", html_out)


if __name__ == "__main__":
    main()
