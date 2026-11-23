#!/usr/bin/env python3
"""核查二进制或 ASCII STL 的闭合性、二流形绕序一致性与外法向符号（用于排查 3D 打印平台「反向三角面」告警真伪）。

用法：
  python3 verify_stl_manifold.py <model.stl>     # 0=闭合正法向真水密体；1=存在破洞/绕序冲突/负体积/法向翻转
  python3 verify_stl_manifold.py --self-test     # 运行 1 组正例 + 4 组必失败反例自测
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
import sys


def parse_stl_facets(data: bytes) -> list[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]]:
    if len(data) >= 84:
        n_tri = struct.unpack("<I", data[80:84])[0]
        if 84 + n_tri * 50 == len(data) and n_tri > 0:
            facets = []
            off = 84
            for _ in range(n_tri):
                vals = struct.unpack("<12fH", data[off : off + 50])
                off += 50
                facets.append((vals[0:3], vals[3:6], vals[6:9], vals[9:12]))
            return facets

    # Fallback: ASCII STL
    text = data.decode("utf-8", errors="replace")
    facets = []
    cur_n = (0.0, 0.0, 0.0)
    verts = []
    for raw in text.splitlines():
        toks = raw.strip().split()
        if len(toks) >= 5 and toks[0].lower() == "facet" and toks[1].lower() == "normal":
            cur_n = (float(toks[2]), float(toks[3]), float(toks[4]))
            verts = []
        elif len(toks) >= 4 and toks[0].lower() == "vertex":
            verts.append((float(toks[1]), float(toks[2]), float(toks[3])))
        elif toks and toks[0].lower() == "endfacet" and len(verts) == 3:
            facets.append((cur_n, verts[0], verts[1], verts[2]))
    return facets


def inspect_facets(facets: list) -> dict:
    if not facets:
        return {"ok": False, "reason": "empty_mesh", "triangles": 0}

    signed_vol = 0.0
    degenerate = 0
    header_vs_winding_flipped = 0
    edge_dir_count: dict[tuple, int] = {}

    def qpt(v: tuple[float, float, float]) -> tuple[float, float, float]:
        return (round(v[0], 5), round(v[1], 5), round(v[2], 5))

    for (nx, ny, nz), (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) in facets:
        ux, uy, uz = x2 - x1, y2 - y1, z2 - z1
        vx, vy, vz = x3 - x1, y3 - y1, z3 - z1
        cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        area2 = math.sqrt(cx * cx + cy * cy + cz * cz)
        if area2 < 1e-10:
            degenerate += 1
        else:
            n_norm = math.sqrt(nx * nx + ny * ny + nz * nz)
            if n_norm > 1e-8:
                dot = (nx * cx + ny * cy + nz * cz) / (area2 * n_norm)
                if dot < -0.1:
                    header_vs_winding_flipped += 1
        signed_vol += (
            x1 * (y2 * z3 - z2 * y3)
            - y1 * (x2 * z3 - z2 * x3)
            + z1 * (x2 * y3 - y2 * x3)
        ) / 6.0
        p1, p2, p3 = qpt((x1, y1, z1)), qpt((x2, y2, z2)), qpt((x3, y3, z3))
        for a, b in ((p1, p2), (p2, p3), (p3, p1)):
            edge_dir_count[(a, b)] = edge_dir_count.get((a, b), 0) + 1

    same_dir_shared = sum(1 for cnt in edge_dir_count.values() if cnt > 1)
    unpaired_boundary = sum(1 for (a, b) in edge_dir_count if (b, a) not in edge_dir_count)
    ok = (
        degenerate == 0
        and unpaired_boundary == 0
        and same_dir_shared == 0
        and header_vs_winding_flipped == 0
        and signed_vol > 1e-9
    )
    return {
        "ok": ok,
        "triangles": len(facets),
        "signed_volume_mm3": round(signed_vol, 4),
        "degenerate": degenerate,
        "unpaired_boundary_half_edges": unpaired_boundary,
        "same_dir_shared_edges_orientation_conflict": same_dir_shared,
        "header_normal_vs_winding_flipped": header_vs_winding_flipped,
    }


def _make_binary_stl(facets: list) -> bytes:
    out = bytearray(b"STL_TEST".ljust(80, b"\0"))
    out += struct.pack("<I", len(facets))
    for n, v1, v2, v3 in facets:
        out += struct.pack("<12fH", *n, *v1, *v2, *v3, 0)
    return bytes(out)


def self_test() -> None:
    # 标准正四面体（外法向一致、闭合正有向体积 = 1/6）
    v0 = (0.0, 0.0, 0.0)
    v1 = (1.0, 0.0, 0.0)
    v2 = (0.0, 1.0, 0.0)
    v3 = (0.0, 0.0, 1.0)

    def tri_with_normal(a, b, c):
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        return ((nx / ln, ny / ln, nz / ln), a, b, c)

    good = [
        tri_with_normal(v0, v2, v1),
        tri_with_normal(v0, v1, v3),
        tri_with_normal(v0, v3, v2),
        tri_with_normal(v1, v2, v3),
    ]
    res_ok = inspect_facets(parse_stl_facets(_make_binary_stl(good)))
    assert res_ok["ok"] and abs(res_ok["signed_volume_mm3"] - round(1.0 / 6.0, 4)) < 1e-3, res_ok

    # 必败反例 1：整体法向朝内（有向体积为负）
    bad_inside_out = [tri_with_normal(a, c, b) for _, a, b, c in good]
    r1 = inspect_facets(parse_stl_facets(_make_binary_stl(bad_inside_out)))
    assert not r1["ok"] and r1["signed_volume_mm3"] < 0, f"Failed to catch inside-out mesh: {r1}"

    # 必败反例 2：单面反向（共享有向边冲突 > 0）
    bad_one_flip = good[:3] + [tri_with_normal(v1, v3, v2)]
    r2 = inspect_facets(parse_stl_facets(_make_binary_stl(bad_one_flip)))
    assert not r2["ok"] and r2["same_dir_shared_edges_orientation_conflict"] > 0, f"Failed to catch single flipped face: {r2}"

    # 必败反例 3：头部法向矢量与顶点绕序相反
    n0, a0, b0, c0 = good[0]
    bad_hdr = [((-n0[0], -n0[1], -n0[2]), a0, b0, c0)] + good[1:]
    r3 = inspect_facets(parse_stl_facets(_make_binary_stl(bad_hdr)))
    assert not r3["ok"] and r3["header_normal_vs_winding_flipped"] == 1, f"Failed to catch flipped header normal: {r3}"

    # 必败反例 4：缺面破洞（未配对边界边 > 0）
    r4 = inspect_facets(parse_stl_facets(_make_binary_stl(good[:3])))
    assert not r4["ok"] and r4["unpaired_boundary_half_edges"] > 0, f"Failed to catch open boundary hole: {r4}"

    print("PASS: verify_stl_manifold --self-test (1 valid manifold + 4 negative controls caught)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stl", nargs="?", type=Path, help="待检查的 .stl 文件路径")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.stl:
        ap.error("Provide <model.stl> or --self-test")
    res = inspect_facets(parse_stl_facets(args.stl.read_bytes()))
    res["file"] = str(args.stl)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
