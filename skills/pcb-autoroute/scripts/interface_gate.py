#!/usr/bin/env python3
"""接口路径与配对门（INTERFACES_PASS，pcbnew 只读）。

解决「单板 DRC 0 + 外壳体积干涉 0，但装起来排线/导线插不上或脚序反接」的问题：
  1. connector（FPC/板对板/插接座）：
     - 嘴方向（mouth_dir）：从信号焊盘行中心指向固定耳/机械焊盘中心，与外部来线方向 mate_from 的点积 >= min_dot（默认 0.9）；
     - 三维落脚序（panel + contact）：按板面法向 n 与插入方向 t = -mouth_dir 的叉积 d = n × t 排信号焊盘，
       逐脚核对对手脚 1..N 落到的 PCB 焊盘网络，单列非地信号落到 GND 的致命短路（signals_on_gnd）；
     - 世界坐标单调（monotonic_axis）：断言焊盘 1..N 在世界 X 或 Y 轴严格单调（抓背靠背双座画成同向）。
  2. wire（电池/马达/按键/传感器焊线盘）：
     - 焊盘中心到目标区域 region [x1,y1,x2,y2] 或板边 edge (x_min/x_max/y_min/y_max) 的距离 <= max_mm。
  3. module（带方向模块，如贴装核心板 USB 口朝向壳壁）：
     - 从 from_pad（或 far_pad）指向 to_pad（或 usb_pad）的方向向量与 toward（或 usb_toward）点积 >= min_dot。
  4. 缺件不静默放行：声明里的接口在板上找不到或缺焊盘时直接判 FAIL（仅显式声明 "optional": true 时记 SKIP）。

用法：
  <kicad python> interface_gate.py <board.kicad_pcb> --spec <interfaces.json>
  <kicad python> interface_gate.py --selftest
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys


def _dist_rect(pt: tuple[float, float], rect: tuple[float, float, float, float]) -> float:
    x, y = pt
    x1, y1, x2, y2 = rect
    dx = max(x1 - x, 0.0, x - x2)
    dy = max(y1 - y, 0.0, y - y2)
    return math.hypot(dx, dy)


def _board_bbox(board, k, spec_bbox) -> tuple[float, float, float, float]:
    if spec_bbox is not None:
        if len(spec_bbox) != 4:
            raise ValueError("board_bbox 必须为 [x1, y1, x2, y2]")
        return tuple(float(v) for v in spec_bbox)
    bb = board.GetBoardEdgesBoundingBox()
    if bb.GetWidth() <= 0 or bb.GetHeight() <= 0:
        raise ValueError("板上无有效 Edge.Cuts 边界，且未提供 board_bbox")
    return (
        k.ToMM(bb.GetLeft()),
        k.ToMM(bb.GetTop()),
        k.ToMM(bb.GetRight()),
        k.ToMM(bb.GetBottom()),
    )


def _edge_rect(name: str, bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    table = {
        "x_max": (x2, y1, x2, y2),
        "x_min": (x1, y1, x1, y2),
        "y_max": (x1, y2, x2, y2),
        "y_min": (x1, y1, x2, y1),
    }
    if name not in table:
        raise ValueError(f"未知的板边名称: {name}")
    return table[name]


def _pin_order(fp, k, mouth: tuple[float, float], spec: dict) -> dict:
    mm = k.ToMM
    sig = {p.GetNumber(): p for p in fp.Pads() if p.GetNumber().isdigit()}
    panel = spec["panel"]
    if len(sig) != len(panel):
        return {
            "panel_pin1_on_pad": None,
            "wrong_pins": [{"error": f"信号焊盘数({len(sig)}) != panel 脚数({len(panel)})"}],
            "signals_on_gnd": [],
        }
    t = (-mouth[0], -mouth[1])  # 排线从嘴插入、朝信号脚行前进的方向
    top = fp.GetLayer() == k.F_Cu
    contact = spec.get("contact", "bottom")
    if contact not in ("bottom", "top"):
        raise ValueError(f"contact 必须为 'bottom' 或 'top'，收到: {contact}")
    # KiCad 坐标系 X 向右、Y 向下，因此 z 轴向板内为正；n × t 决定面板脚 1 朝哪边排
    nz = (1 if top else -1) * (1 if contact == "bottom" else -1)
    d = (-nz * t[1], nz * t[0])
    land = sorted(
        sig,
        key=lambda q: -(mm(sig[q].GetPosition().x) * d[0] + mm(sig[q].GetPosition().y) * d[1]),
    )
    pins = [
        {"panel_pin": i + 1, "pad": q, "wants": panel[i], "gets": sig[q].GetNetname()}
        for i, q in enumerate(land)
    ]
    wrong = [p for p in pins if p["wants"] != p["gets"]]
    short_gnd = sorted({p["wants"] for p in wrong if p["gets"] == "GND" and p["wants"] != "GND"})
    return {
        "panel_pin1_on_pad": land[0],
        "wrong_pins": wrong,
        "signals_on_gnd": short_gnd,
    }


def _check_monotonic(pads: dict[str, tuple[float, float]], axis: str, expected_dir: int | None) -> dict:
    num_pads = sorted(((int(n), xy) for n, xy in pads.items() if n.isdigit()), key=lambda item: item[0])
    if len(num_pads) < 2:
        return {"monotonic_ok": False, "monotonic_error": "数字焊盘不足 2 个"}
    idx = 0 if axis.lower() == "x" else 1
    coords = [xy[idx] for _, xy in num_pads]
    diffs = [coords[i + 1] - coords[i] for i in range(len(coords) - 1)]
    inc = all(d > 1e-4 for d in diffs)
    dec = all(d < -1e-4 for d in diffs)
    actual_dir = 1 if inc else (-1 if dec else 0)
    ok = (inc or dec) and (expected_dir is None or actual_dir == expected_dir)
    return {
        "monotonic_axis": axis.lower(),
        "monotonic_dir": actual_dir,
        "monotonic_ok": ok,
        "pad1_coord": round(coords[0], 3),
        "padN_coord": round(coords[-1], 3),
    }


def check_board(board_path: str | Path, spec_data: dict) -> dict:
    if sys.platform == "darwin":
        import wx
        if wx.GetApp() is None:
            check_board._app = wx.App(False)
    import pcbnew as k

    mm = k.ToMM
    board = k.LoadBoard(str(board_path))
    fps = {f.GetReference(): f for f in board.GetFootprints()}
    ifaces = spec_data.get("interfaces", spec_data)
    bbox = None
    if any(isinstance(v, dict) and v.get("kind") == "wire" and "edge" in v for v in ifaces.values()):
        bbox = _board_bbox(board, k, spec_data.get("board_bbox"))

    out = []
    for ref, spec in ifaces.items():
        if not isinstance(spec, dict) or "kind" not in spec:
            continue
        fp = fps.get(ref)
        if fp is None:
            status = "SKIP" if spec.get("optional") else "FAIL"
            out.append({"ref": ref, "result": status, "note": "not on board", "why": spec.get("why", "")})
            continue

        pads: dict[str, tuple[float, float]] = {}
        sig_pts: list[tuple[float, float]] = []
        mp_pts: list[tuple[float, float]] = []
        for p in fp.Pads():
            xy = (mm(p.GetPosition().x), mm(p.GetPosition().y))
            num = p.GetNumber()
            pads[num] = xy
            if num.isdigit():
                sig_pts.append(xy)
            else:
                mp_pts.append(xy)

        kind = spec["kind"]
        if kind == "connector":
            from_pads = spec.get("mouth_from_pads")
            to_pads = spec.get("mouth_to_pads")
            s_list = [pads[str(n)] for n in from_pads if str(n) in pads] if from_pads else sig_pts
            m_list = [pads[str(n)] for n in to_pads if str(n) in pads] if to_pads else mp_pts
            if not s_list or not m_list:
                out.append({
                    "ref": ref,
                    "result": "FAIL",
                    "error": "缺少信号焊盘或机械固定耳焊盘，无法计算连接器开口方向",
                    "why": spec.get("why", ""),
                })
                continue
            cs = (sum(x for x, _ in s_list) / len(s_list), sum(y for _, y in s_list) / len(s_list))
            cm = (sum(x for x, _ in m_list) / len(m_list), sum(y for _, y in m_list) / len(m_list))
            vx, vy = cm[0] - cs[0], cm[1] - cs[1]
            norm = math.hypot(vx, vy)
            if norm < 1e-6:
                out.append({
                    "ref": ref,
                    "result": "FAIL",
                    "error": "信号焊盘中心与固定耳中心重合，无法计算 mouth_dir",
                    "why": spec.get("why", ""),
                })
                continue
            v = (vx / norm, vy / norm)
            mate = spec["mate_from"]
            mn = math.hypot(mate[0], mate[1])
            mate_u = (mate[0] / mn, mate[1] / mn)
            min_dot = float(spec.get("min_dot", 0.9))
            dot = v[0] * mate_u[0] + v[1] * mate_u[1]
            ok = dot >= min_dot
            rec = {
                "ref": ref,
                "kind": "connector",
                "result": "PASS" if ok else "FAIL",
                "mouth_dir": [round(v[0], 3), round(v[1], 3)],
                "mate_from": list(mate),
                "dot": round(dot, 3),
                "why": spec.get("why", ""),
            }
            if "panel" in spec:
                po = _pin_order(fp, k, v, spec)
                rec.update(po)
                if po["wrong_pins"]:
                    rec["result"] = "FAIL"
            if "monotonic_axis" in spec:
                mo = _check_monotonic(pads, spec["monotonic_axis"], spec.get("monotonic_dir"))
                rec.update(mo)
                if not mo["monotonic_ok"]:
                    rec["result"] = "FAIL"
            out.append(rec)

        elif kind == "wire":
            if not pads:
                out.append({"ref": ref, "result": "FAIL", "error": "无焊盘", "why": spec.get("why", "")})
                continue
            rect = tuple(spec["region"]) if "region" in spec else _edge_rect(spec["edge"], bbox)
            all_pts = list(pads.values())
            c = (sum(x for x, _ in all_pts) / len(all_pts), sum(y for _, y in all_pts) / len(all_pts))
            d = _dist_rect(c, rect)
            max_mm = float(spec["max_mm"])
            out.append({
                "ref": ref,
                "kind": "wire",
                "result": "PASS" if d <= max_mm else "FAIL",
                "distance_mm": round(d, 2),
                "max_mm": max_mm,
                "why": spec.get("why", ""),
            })

        elif kind == "module":
            to_pad = str(spec.get("to_pad") or spec["usb_pad"])
            from_pad = str(spec.get("from_pad") or spec["far_pad"])
            toward = spec.get("toward") or spec["usb_toward"]
            if to_pad not in pads or from_pad not in pads:
                out.append({
                    "ref": ref,
                    "kind": "module",
                    "result": "FAIL",
                    "error": f"焊盘 {from_pad} 或 {to_pad} 不存在",
                    "why": spec.get("why", ""),
                })
                continue
            u = pads[to_pad]
            f = pads[from_pad]
            vx, vy = u[0] - f[0], u[1] - f[1]
            norm = math.hypot(vx, vy)
            tn = math.hypot(toward[0], toward[1])
            dot = (vx * toward[0] + vy * toward[1]) / (norm * tn) if norm > 1e-6 and tn > 1e-6 else -1.0
            min_dot = float(spec.get("min_dot", 0.1))
            out.append({
                "ref": ref,
                "kind": "module",
                "result": "PASS" if dot >= min_dot else "FAIL",
                "to_pad_xy": [round(u[0], 2), round(u[1], 2)],
                "dot": round(dot, 3),
                "why": spec.get("why", ""),
            })
        else:
            raise ValueError(f"{ref}: 未知的接口类型 {kind}")

    fails = [r["ref"] for r in out if r["result"] == "FAIL"]
    return {
        "board": Path(board_path).name,
        "gate": "INTERFACES_PASS" if not fails else "INTERFACES_FAIL",
        "fails": fails,
        "checks": out,
    }


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv == ["--selftest"]:
        test_script = Path(__file__).with_name("test_interface_gate.py")
        return subprocess.call([sys.executable, str(test_script)])

    ap = argparse.ArgumentParser(description="PCB 对外接口朝向、落脚序与焊线距离门禁")
    ap.add_argument("board", help="待检 .kicad_pcb 路径")
    ap.add_argument("--spec", required=True, help="接口约束 JSON 路径")
    args = ap.parse_args(argv)

    spec_data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    res = check_board(args.board, spec_data)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["gate"] == "INTERFACES_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
