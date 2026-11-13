#!/usr/bin/env python3
"""跨 EasyEDA Pro (.epro2/.epcb2) 与 KiCad (.kicad_pcb) 的布局架构与高速/射频预审工具。

源自双 MCU 可穿戴主控板三代同尺对照（纯自动布局布线 6 层版 vs 早期背靠背修补 4 层版 vs 专业重绘 6 层版）：
DRC=0 只能证明不断不短，抓不出让走线暴涨 75% 的布局死结与手滑板框瑕疵。本脚本检查 5 项架构与几何硬指标：
  1. stacked_multi_pin_ic : 顶底两颗多脚芯片（>=min_pins，默认 24）中心距 <3.0 mm 背靠背同坐标硬叠告警
  2. rf_feed_topology     : 射频网络（ANT_FEED / RF_ANT 等）单网走线长 >max_rf_mm（默认 8.0 mm）或过孔 >0 告警
  3. usb_diff_skew        : USB 差分对（DP/DM）长度差 >max_usb_skew_mm（默认 0.50 mm）告警
  4. slanted_ortho_outline: 板框长边（>5.0 mm）非 45° 斜率微歪（0.02..1.50 mm）制造瑕疵告警
  5. passive_0201_ratio   : 0201 阻容占比统计（面积未受限时优先 0402）与自动网名（$/Net-）计数

用法：
  python3 floorplan_si_audit.py <board.epro2|board.epcb2|board.kicad_pcb> [--board-substr 主控] [--json out.json]
  python3 floorplan_si_audit.py --self-test
"""
from __future__ import annotations
import argparse
import collections
import json
import math
import re
import sys
import zipfile
from pathlib import Path

MM_PER_MIL = 0.0254


def _parse_epcb2_lines(lines_iter):
    objs = {}
    for raw in lines_iter:
        raw = raw.strip()
        if not raw:
            continue
        if "||" in raw and raw.lstrip().startswith("{"):
            h_s, b_s = raw.split("||", 1)
            try:
                hj = json.loads(h_s)
            except Exception:
                continue
            b_clean = b_s.rstrip().rstrip("|")
            t = hj.get("type")
            rid = hj.get("id", f"__noid_{len(objs)}")
            if not b_clean:
                objs.pop((t, rid), None)
            else:
                try:
                    objs[(t, rid)] = json.loads(b_clean)
                except Exception:
                    pass
        elif "|" in raw:
            parts = raw.split("|")
            if len(parts) < 3:
                continue
            rec_type, rec_id = parts[0], parts[1]
            body = parts[-1]
            key = (rec_type, rec_id)
            if not body:
                objs.pop(key, None)
                continue
            try:
                objs[key] = json.loads(body)
            except Exception:
                pass
    return objs


def load_board_facts(path: Path, board_substr: str | None = None) -> dict:
    suffix = path.suffix.lower()
    if suffix == ".epro2":
        return _load_epro2(path, board_substr)
    if suffix == ".epcb2":
        objs = _parse_epcb2_lines(path.read_text(encoding="utf-8", errors="replace").splitlines())
        return _extract_epro_facts(path.name, objs, {})
    if suffix == ".kicad_pcb":
        return _load_kicad(path)
    raise ValueError(f"不支持的文件后缀: {path}")


def _load_epro2(path: Path, board_substr: str | None = None) -> dict:
    with zipfile.ZipFile(path, "r") as zf:
        epru_names = [n for n in zf.namelist() if n.endswith(".epru")]
        if not epru_names:
            raise ValueError(f"{path} 内未找到 .epru")
        lines = zf.read(epru_names[0]).decode("utf-8", errors="replace").splitlines()

    docs = []
    cur = None
    for line in lines:
        if "||" not in line:
            continue
        h_s, b_s = line.split("||", 1)
        try:
            hj = json.loads(h_s)
        except Exception:
            continue
        b_clean = b_s.rstrip().rstrip("|")
        t = hj.get("type")
        if t == "DOCHEAD":
            bj = json.loads(b_clean) if b_clean else {}
            cur = {"docType": bj.get("docType"), "uuid": bj.get("uuid"), "objs": {}}
            docs.append(cur)
        elif cur is not None:
            rid = hj.get("id", f"__noid_{len(cur['objs'])}")
            if b_clean == "":
                cur["objs"].pop((t, rid), None)
            else:
                try:
                    cur["objs"][(t, rid)] = json.loads(b_clean)
                except Exception:
                    pass

    fps = {}
    pcbs = []
    for d in docs:
        objs = d["objs"]
        meta = next((v for (t, _), v in objs.items() if t == "META"), {})
        title = str(meta.get("title") or d.get("uuid") or "")
        if d["docType"] == "FOOTPRINT":
            pads = [v for (t, _), v in objs.items() if t == "PAD"]
            fps[d["uuid"]] = {"title": title, "pads": pads}
        elif d["docType"] == "PCB":
            pcbs.append((title, objs))

    if not pcbs:
        raise ValueError(f"{path} 内未找到 PCB 文档")
    chosen_title, chosen_pcb = pcbs[0]
    if board_substr:
        for t, o in pcbs:
            if board_substr in t:
                chosen_title, chosen_pcb = t, o
                break
    return _extract_epro_facts(f"{path.name}:{chosen_title}", chosen_pcb, fps)


def _extract_epro_facts(board_name: str, pcb: dict, fps: dict) -> dict:
    pattrs = collections.defaultdict(dict)
    for (t, rid), v in pcb.items():
        if t == "ATTR" and v.get("parentId"):
            pattrs[v["parentId"]][v.get("key")] = v.get("value")

    pad_nets = collections.defaultdict(dict)
    for (t, rid), v in pcb.items():
        if t == "PAD_NET":
            try:
                arr = json.loads(rid)
                pad_nets[arr[1]][str(arr[2])] = v.get("padNet") or ""
            except Exception:
                pass

    ics = []
    comp_nets_by_ref = {}
    rc_0201 = rc_0402 = rc_other = 0
    for (t, rid), v in pcb.items():
        if t != "COMPONENT":
            continue
        m = {**(v.get("attrs") or {}), **pattrs.get(rid, {})}
        ref = str(m.get("Designator") or rid)
        comp_nets_by_ref[ref] = dict(pad_nets.get(rid, {}))
        fuuid = str(v.get("footprint") or v.get("footprintId") or m.get("Footprint") or "")
        if fuuid.startswith("["):
            try:
                fuuid = json.loads(fuuid)[0]
            except Exception:
                pass
        finfo = fps.get(fuuid, {"title": str(m.get("Package") or m.get("FootprintTitle") or fuuid), "pads": []})
        pin_cnt = len(finfo["pads"]) or len(pad_nets.get(rid, {}))
        x_mm = float(v.get("x") or 0) * MM_PER_MIL
        y_mm = float(v.get("y") or 0) * MM_PER_MIL
        side = "B" if int(v.get("layerId") or 1) == 2 else "T"
        if ref.startswith("U"):
            ics.append({"ref": ref, "x": x_mm, "y": y_mm, "side": side, "pins": pin_cnt})
        elif ref and ref[0] in ("R", "C") and ref[1:].isdigit():
            pkg_txt = f"{finfo['title']} {m.get('Package','')} {m.get('Footprint','')}".upper()
            if "0201" in pkg_txt:
                rc_0201 += 1
            elif "0402" in pkg_txt:
                rc_0402 += 1
            elif len(finfo["pads"]) == 2:
                span = math.hypot(
                    (float(finfo["pads"][0].get("centerX") or 0) - float(finfo["pads"][1].get("centerX") or 0)) * MM_PER_MIL,
                    (float(finfo["pads"][0].get("centerY") or 0) - float(finfo["pads"][1].get("centerY") or 0)) * MM_PER_MIL,
                )
                if span < 0.72:
                    rc_0201 += 1
                elif span < 1.25:
                    rc_0402 += 1
                else:
                    rc_other += 1

    copper_layers = {1, 2} | set(range(15, 47))
    segs = []
    net_len = collections.defaultdict(float)
    net_layers = collections.defaultdict(set)
    for (t, _), v in pcb.items():
        if t == "LINE" and int(v.get("layerId") or 0) in copper_layers:
            x1, y1 = float(v["startX"]) * MM_PER_MIL, float(v["startY"]) * MM_PER_MIL
            x2, y2 = float(v["endX"]) * MM_PER_MIL, float(v["endY"]) * MM_PER_MIL
            ln = math.hypot(x2 - x1, y2 - y1)
            net = str(v.get("netName") or "")
            segs.append((x1, y1, x2, y2, net, int(v["layerId"])))
            if net:
                net_len[net] += ln
                net_layers[net].add(int(v["layerId"]))
        elif t == "ARC" and int(v.get("layerId") or 0) in copper_layers:
            x1, y1 = float(v["startX"]) * MM_PER_MIL, float(v["startY"]) * MM_PER_MIL
            x2, y2 = float(v["endX"]) * MM_PER_MIL, float(v["endY"]) * MM_PER_MIL
            ln = math.hypot(x2 - x1, y2 - y1)
            net = str(v.get("netName") or "")
            if net:
                net_len[net] += ln
                net_layers[net].add(int(v["layerId"]))

    net_vias = collections.Counter()
    total_vias = 0
    for (t, _), v in pcb.items():
        if t == "VIA":
            total_vias += 1
            if v.get("netName"):
                net_vias[str(v["netName"])] += 1

    outline_segs = []
    for (t, _), v in pcb.items():
        if t == "POLY" and int(v.get("layerId") or 0) == 11:
            path = v.get("path") or []
            pts = []
            i = 0
            while i < len(path):
                tok = path[i]
                if tok in ("M", "L") and i + 2 < len(path):
                    pts.append((float(path[i + 1]) * MM_PER_MIL, float(path[i + 2]) * MM_PER_MIL))
                    i += 3
                elif tok in ("ARC", "A") and i + 3 < len(path):
                    pts.append((float(path[i + 2]) * MM_PER_MIL, float(path[i + 3]) * MM_PER_MIL))
                    i += 4
                elif isinstance(tok, (int, float)) and i + 1 < len(path) and isinstance(path[i + 1], (int, float)):
                    pts.append((float(tok) * MM_PER_MIL, float(path[i + 1]) * MM_PER_MIL))
                    i += 2
                else:
                    i += 1
            for k in range(len(pts) - 1):
                outline_segs.append((pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1]))

    all_nets = set(net_len.keys()) | {v.get("netName", "") for (t, _), v in pcb.items() if t == "NET" and v.get("netName")}
    # Discover RF feed nets connected to antenna designators (ANT*, AE*, or 3216 antenna L7) even if auto-named ($1N48)
    pin_rf_nets = set()
    for ref, pmap in comp_nets_by_ref.items():
        if ref.upper().startswith(("ANT", "AE")):
            for n in pmap.values():
                if n and n.upper() not in ("GND", "AGND", "DGND"):
                    pin_rf_nets.add(n)
    return {
        "board": board_name,
        "ics": ics,
        "rc_0201": rc_0201,
        "rc_0402": rc_0402,
        "rc_other": rc_other,
        "total_track_mm": round(sum(net_len.values()), 2),
        "total_vias": total_vias,
        "net_len": dict(net_len),
        "net_vias": dict(net_vias),
        "pin_rf_nets": sorted(pin_rf_nets),
        "outline_segs": outline_segs,
        "all_nets": sorted(n for n in all_nets if n),
    }


def _load_kicad(path: Path) -> dict:
    txt = path.read_text(encoding="utf-8", errors="replace")
    nets = {}
    head_part = txt.split("(footprint ", 1)[0]
    for m in re.finditer(r'\(net\s+(\d+)\s+(?:"([^"]*)"|([^\s)]+))\)', head_part):
        nets[int(m.group(1))] = m.group(2) if m.group(2) is not None else m.group(3)

    def _resolve_net(blk: str) -> str:
        # Compatible with both KiCad <=9 `(net 12)` and KiCad 10 `(net "NET_NAME")`
        m = re.search(r'\(net\s+(?:"([^"]*)"|(\d+)|([^\s)]+))\)', blk)
        if not m:
            return ""
        if m.group(1) is not None:
            return m.group(1)
        if m.group(2) is not None:
            return nets.get(int(m.group(2)), "")
        return m.group(3) or ""

    def iter_blocks(tag: str):
        idx, n, pat = 0, len(txt), f"({tag}"
        plen = len(pat)
        while True:
            p = txt.find(pat, idx)
            if p == -1:
                break
            if p + plen < n and txt[p + plen] not in " \n\t\r":
                idx = p + plen
                continue
            d, end, in_q = 0, p, False
            for i in range(p, n):
                ch = txt[i]
                if ch == '"' and txt[i - 1] != "\\":
                    in_q = not in_q
                elif not in_q:
                    if ch == "(":
                        d += 1
                    elif ch == ")":
                        d -= 1
                        if d == 0:
                            end = i + 1
                            break
            yield txt[p:end]
            idx = end

    net_len = collections.defaultdict(float)
    total_seg_mm = 0.0
    for blk in iter_blocks("segment"):
        ms = re.search(r"\(start\s+([-\d.]+)\s+([-\d.]+)\)", blk)
        me = re.search(r"\(end\s+([-\d.]+)\s+([-\d.]+)\)", blk)
        if ms and me:
            ln = math.hypot(float(me.group(1)) - float(ms.group(1)), float(me.group(2)) - float(ms.group(2)))
            total_seg_mm += ln
            net = _resolve_net(blk)
            if net:
                net_len[net] += ln

    net_vias = collections.Counter()
    total_vias = 0
    for blk in iter_blocks("via"):
        total_vias += 1
        net = _resolve_net(blk)
        if net:
            net_vias[net] += 1

    outline_segs = []
    for blk in iter_blocks("gr_line"):
        if '"Edge.Cuts"' in blk:
            ms = re.search(r"\(start\s+([-\d.]+)\s+([-\d.]+)\)", blk)
            me = re.search(r"\(end\s+([-\d.]+)\s+([-\d.]+)\)", blk)
            if ms and me:
                outline_segs.append((float(ms.group(1)), float(ms.group(2)), float(me.group(1)), float(me.group(2))))

    ics = []
    rc_0201 = rc_0402 = rc_other = 0
    for blk in iter_blocks("footprint"):
        head = blk[:450]
        m_ly = re.search(r'\(layer\s+"([^"]+)"\)', head)
        side = "B" if (m_ly and m_ly.group(1).startswith("B.")) else "T"
        m_at = re.search(r"\(at\s+([-\d.]+)\s+([-\d.]+)", head)
        fx, fy = (float(m_at.group(1)), float(m_at.group(2))) if m_at else (0.0, 0.0)
        m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', blk) or re.search(r'\(fp_text\s+reference\s+"([^"]+)"', blk)
        ref = m_ref.group(1) if m_ref else ""
        pad_ats = [(float(m.group(1)), float(m.group(2))) for m in re.finditer(r"\(pad\s+\S+\s+\S+\s+\S+\s+\(at\s+([-\d.]+)\s+([-\d.]+)", blk)]
        if ref.startswith("U"):
            ics.append({"ref": ref, "x": fx, "y": fy, "side": side, "pins": len(pad_ats)})
        elif ref and ref[0] in ("R", "C") and ref[1:].isdigit() and len(pad_ats) == 2:
            span = math.hypot(pad_ats[0][0] - pad_ats[1][0], pad_ats[0][1] - pad_ats[1][1])
            if span < 0.72:
                rc_0201 += 1
            elif span < 1.25:
                rc_0402 += 1
            else:
                rc_other += 1

    all_k_nets = set(nets.values()) | set(net_len.keys()) | set(net_vias.keys())
    return {
        "board": path.name,
        "ics": ics,
        "rc_0201": rc_0201,
        "rc_0402": rc_0402,
        "rc_other": rc_other,
        "total_track_mm": round(total_seg_mm, 2),
        "total_vias": total_vias,
        "net_len": dict(net_len),
        "net_vias": dict(net_vias),
        "outline_segs": outline_segs,
        "all_nets": sorted(n for n in all_k_nets if n),
    }


def evaluate_facts(
    facts: dict,
    min_ic_pins: int = 24,
    max_rf_mm: float = 8.0,
    max_usb_skew_mm: float = 0.50,
) -> dict:
    findings = []

    # 1. Opposite-side stacked multi-pin ICs
    big_ics = [u for u in facts["ics"] if u["pins"] >= min_ic_pins]
    for i in range(len(big_ics)):
        for j in range(i + 1, len(big_ics)):
            a, b = big_ics[i], big_ics[j]
            if a["side"] != b["side"]:
                dist = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
                if dist < 3.0:
                    findings.append({
                        "rule": "stacked_multi_pin_ic",
                        "severity": "WARN",
                        "detail": f"{a['ref']}({a['side']},{a['pins']}P) 与 {b['ref']}({b['side']},{b['pins']}P) 顶底同坐标硬叠 (中心距 {dist:.2f} mm < 3.0 mm)，抢占同一扇出过孔区",
                    })

    # 2. RF feed topology (both named RF nets and pin-discovered antenna feed nets)
    rf_candidates = set(facts.get("pin_rf_nets") or []) | {
        n for n in facts["net_len"]
        if any(k in n.upper() for k in ("ANT_FEED", "RF_ANT", "ANT_MATCH", "AE1-FEED", "ANT"))
        and "GND" not in n.upper()
    }
    for n in sorted(rf_candidates):
        ln = facts["net_len"].get(n, 0.0)
        vc = facts["net_vias"].get(n, 0)
        if ln > max_rf_mm or vc > 0:
            findings.append({
                "rule": "rf_feed_topology",
                "severity": "WARN",
                "detail": f"射频网络 {n} 走线 {ln:.2f} mm (门限 <={max_rf_mm:.1f} mm), 过孔 {vc} 个 (门限 0 个)",
            })

    # 3. USB differential pair skew (handles USB_DP/USB_DM, MCU_DP/MCU_DM, D+/D-)
    dp_candidates = [n for n in facts["net_len"] if re.search(r"(USB_?DP|MCU_?DP|D\+|^DP$)", n, re.I)]
    dm_candidates = [n for n in facts["net_len"] if re.search(r"(USB_?DM|MCU_?DM|D-|^DM$)", n, re.I)]
    paired = []
    for dp in dp_candidates:
        prefix = re.sub(r"(DP|D\+)$", "", dp, flags=re.I)
        for dm in dm_candidates:
            if re.sub(r"(DM|D-)$", "", dm, flags=re.I) == prefix:
                paired.append((dp, dm))
    for dp, dm in paired:
        l_dp, l_dm = facts["net_len"][dp], facts["net_len"][dm]
        skew = abs(l_dp - l_dm)
        if skew > max_usb_skew_mm:
            findings.append({
                "rule": "usb_diff_skew",
                "severity": "WARN",
                "detail": f"差分对 {dp}({l_dp:.2f} mm) vs {dm}({l_dm:.2f} mm) 长度偏斜 {skew:.2f} mm > {max_usb_skew_mm:.2f} mm",
            })

    # 4. Slanted supposedly-orthogonal board outline edges
    seen_edges = set()
    for x1, y1, x2, y2 in facts["outline_segs"]:
        key = tuple(sorted([(round(x1, 3), round(y1, 3)), (round(x2, 3), round(y2, 3))]))
        if key in seen_edges:
            continue
        seen_edges.add(key)
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if max(dx, dy) > 5.0 and 0.02 <= min(dx, dy) <= 1.50 and abs(dx - dy) > 2.0:
            findings.append({
                "rule": "slanted_ortho_outline",
                "severity": "ERROR",
                "detail": f"板框长边 ({x1:.3f},{y1:.3f})->({x2:.3f},{y2:.3f}) 微斜: dx={dx:.3f} mm, dy={dy:.3f} mm",
            })

    rc_tot = facts["rc_0201"] + facts["rc_0402"] + facts["rc_other"]
    auto_nets = [n for n in facts["all_nets"] if "$" in n or n.startswith("Net-(")]
    return {
        "board": facts["board"],
        "metrics": {
            "total_track_mm": facts["total_track_mm"],
            "total_vias": facts["total_vias"],
            "rc_0402": facts["rc_0402"],
            "rc_0201": facts["rc_0201"],
            "rc_0402_ratio": round(facts["rc_0402"] / rc_tot, 4) if rc_tot else 1.0,
            "auto_named_nets": len(auto_nets),
            "total_nets": len(facts["all_nets"]),
        },
        "findings": findings,
    }


def run_self_test() -> int:
    # 1. Clean board fixture -> 0 findings
    clean = {
        "board": "clean_fixture",
        "ics": [
            {"ref": "U7", "x": 11.4, "y": -7.0, "side": "T", "pins": 33},
            {"ref": "U8", "x": 7.5, "y": -29.1, "side": "T", "pins": 49},
        ],
        "rc_0201": 2, "rc_0402": 84, "rc_other": 0,
        "total_track_mm": 1254.0, "total_vias": 204,
        "net_len": {"ANT_FEED": 2.03, "RF_ANT": 3.46, "USB_DP": 23.47, "USB_DM": 23.35},
        "net_vias": {},
        "outline_segs": [(0.0, 0.0, 43.0, 0.0), (43.0, 0.0, 43.0, -36.5)],
        "all_nets": ["ANT_FEED", "RF_ANT", "USB_DP", "USB_DM", "GND"],
    }
    r_clean = evaluate_facts(clean)
    assert len(r_clean["findings"]) == 0, f"clean fixture had findings: {r_clean['findings']}"

    # 2. Positive-control defective fixture -> must catch all 4 rules!
    bad = {
        "board": "bad_fixture",
        "ics": [
            {"ref": "U13", "x": 37.5, "y": -8.06, "side": "T", "pins": 49},
            {"ref": "U18", "x": 37.5, "y": -7.81, "side": "B", "pins": 33},
        ],
        "rc_0201": 26, "rc_0402": 59, "rc_other": 0,
        "total_track_mm": 1491.0, "total_vias": 218,
        "net_len": {"ANT_FEED": 18.81, "USB_DP": 32.04, "USB_DM": 18.43},
        "net_vias": {"ANT_FEED": 2},
        "outline_segs": [(-0.247, 0.067, 42.853, -0.099)],
        "all_nets": ["$2N73", "ANT_FEED", "USB_DP", "USB_DM"],
    }
    r_bad = evaluate_facts(bad)
    caught = {f["rule"] for f in r_bad["findings"]}
    expected = {"stacked_multi_pin_ic", "rf_feed_topology", "usb_diff_skew", "slanted_ortho_outline"}
    assert caught == expected, f"self-test mismatch: got {caught}, expected {expected}"
    print("SELF-TEST PASS: clean=0 findings, bad caught all 4 architecture/outline rules.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="PCB 布局架构与高速/射频预审")
    ap.add_argument("board", nargs="?", help=".epro2 / .epcb2 / .kicad_pcb 路径")
    ap.add_argument("--board-substr", default=None, help=".epro2 内多块 PCB 时按标题子串筛选")
    ap.add_argument("--json", default=None, help="输出 JSON 路径")
    ap.add_argument("--self-test", action="store_true", help="运行内置正反例自检")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test()
    if not args.board:
        ap.error("请提供板文件路径或 --self-test")

    facts = load_board_facts(Path(args.board), args.board_substr)
    res = evaluate_facts(facts)
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 1 if any(f["severity"] == "ERROR" for f in res["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
