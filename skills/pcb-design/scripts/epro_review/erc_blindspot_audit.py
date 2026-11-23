#!/usr/bin/env python3
"""外部回稿 / 嘉立创专业版 (.epro2 / .epro) 原理图与网表五类 ERC=0 高频盲点审计工具。

原生 ERC=0 只检查未连脚与电源标志冲突，抓不出以下五类真实致命硬件错误：
  1. multi_afe_biasout_short  : 多片 ADS1298/1299 级联时，多颗芯片的 BIASOUT (pin 63) 经低阻 (<1 kΩ) 并联在同一右腿驱动网互顶
  2. afe_vcap3_wrong_return   : 双极性供电 (±2.5V) 下，ADS1298/1299 第 55 脚 VCAP3 去耦电容误回 AGND/GND 而非 AVSS (-2.5V)
  3. skin_tvs_before_resistor : 贴肤电极座信号脚未先经 >=10 kΩ 限流电阻就直接挂 TVS/二极管到数字 GND（故障态正向导通零阻灌人）
  4. ch340_3v3_v3_unstrapped  : CH340N/C/G 在 3.3V 供电下，V3 脚仅接退耦电容到地、漏短接 3.3V 导致枚举失败
  5. nrf52840_dcc_inductor_nh : nRF52840 片内 DC-DC (DCC) 主电感误用 nH 级 (<1 uH，如把 10uH 写成 10nH)

用法：
  python3 erc_blindspot_audit.py <project.epro2 | project.epro> [--json out.json]
  python3 erc_blindspot_audit.py --self-test
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import zipfile
from pathlib import Path


def parse_resistor_ohms(text: str) -> float | None:
    s = text.strip().upper().replace("Ω", "").replace("OHM", "")
    # 识别标准 EIA 4位码如 3923 -> 392k, 1002 -> 10k, 1502 -> 15k, 0000 -> 0
    m_eia4 = re.search(r"(?:0201|0402|0603|0805|1206)[A-Z]*([0-9]{4})(?![0-9])", s)
    if m_eia4:
        code = m_eia4.group(1)
        if code == "0000":
            return 0.0
        return float(code[:3]) * (10 ** int(code[3]))
    # 识别标准 EIA 3位码如 0402WGJ103TCE -> 10k, 0402WGJ220TCE -> 22, 0402WGJ000TCE -> 0
    m_eia3 = re.search(r"(?:0201|0402|0603|0805|1206)[A-Z]+([0-9]{3})[A-Z]+", s)
    if m_eia3:
        code = m_eia3.group(1)
        if code == "000":
            return 0.0
        return float(code[:2]) * (10 ** int(code[2]))
    m_yc = re.search(r"YC[0-9]+-[A-Z0-9-]+?([0-9]+)([RKM])L?", s)
    if m_yc:
        mul = {"R": 1.0, "K": 1e3, "M": 1e6}[m_yc.group(2)]
        return float(m_yc.group(1)) * mul
    m = re.search(r"(?<![A-Z0-9])([0-9]+(?:\.[0-9]+)?)\s*([KMR]?)(?![A-Z0-9])", s)
    if m and (m.group(2) or "Ω" in text or "OHM" in text.upper()):
        val = float(m.group(1))
        mul = {"": 1.0, "R": 1.0, "K": 1e3, "M": 1e6}[m.group(2)]
        return val * mul
    return None


def parse_inductor_uh(text: str) -> float | None:
    s = text.strip()
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(uH|µH|nH|mH)", s, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        u = m.group(2).lower()
        if u == "nh":
            return v * 1e-3
        if u == "mh":
            return v * 1e3
        return v
    # 识别电感型号码如 AHW1608FE100KTF (100 -> 10uH), LQG15HS15NJ02D (15N -> 15nH), LQP03TN4N7 (4N7 -> 4.7nH)
    su = s.upper()
    m_n = re.search(r"([0-9]+)N([0-9]*)", su)
    if m_n and any(k in su for k in ("LQ", "IND", "NH")):
        frac = f".{m_n.group(2)}" if m_n.group(2) else ""
        return float(f"{m_n.group(1)}{frac}") * 1e-3
    m_u = re.search(r"(?:FE|IND|SWPA|VLS|LQH)[A-Z0-9]*?([0-9]{2})([0-9])[A-Z]", su)
    if m_u:
        return float(m_u.group(1)) * (10 ** int(m_u.group(2)))
    return None


def load_netlists(path: Path) -> list[dict]:
    suf = path.suffix.lower()
    if suf == ".epro2":
        return _load_epro2(path)
    if suf == ".epro":
        return _load_epro(path)
    raise ValueError(f"不支持的工程格式: {path}")


def _load_epro2(path: Path) -> list[dict]:
    with zipfile.ZipFile(path, "r") as zf:
        epru = next((n for n in zf.namelist() if n.endswith(".epru")), None)
        if not epru:
            raise ValueError(f"{path} 缺少 .epru")
        lines = zf.read(epru).decode("utf-8", errors="replace").splitlines()

    docs, cur = [], None
    for line in lines:
        if "||" not in line:
            continue
        h_s, b_s = line.split("||", 1)
        b_s = b_s.rstrip().rstrip("|")
        try:
            hj = json.loads(h_s)
        except Exception:
            continue
        t = hj.get("type")
        if t == "DOCHEAD":
            bj = json.loads(b_s) if b_s else {}
            cur = {"docType": bj.get("docType"), "uuid": bj.get("uuid"), "objs": {}}
            docs.append(cur)
        elif cur is not None:
            rid = hj.get("id", f"__id_{len(cur['objs'])}")
            if not b_s:
                cur["objs"].pop((t, rid), None)
            else:
                try:
                    cur["objs"][(t, rid)] = json.loads(b_s)
                except Exception:
                    pass

    devices = {}
    for d in docs:
        if d["docType"] == "DEVICE":
            attrs = {}
            for (t, _), v in d["objs"].items():
                if t == "ATTR" and v.get("key"):
                    attrs[v["key"]] = v.get("value")
                elif t == "META" and v.get("title"):
                    attrs.setdefault("title", v["title"])
            devices[d["uuid"]] = attrs

    boards = []
    for d in docs:
        if d["docType"] != "PCB":
            continue
        objs = d["objs"]
        meta = next((v for (t, _), v in objs.items() if t == "META"), {})
        title = str(meta.get("title") or d["uuid"])
        pattrs = collections.defaultdict(dict)
        pad_nets = collections.defaultdict(dict)
        for (t, rid), v in objs.items():
            if t == "ATTR" and v.get("parentId"):
                pattrs[v["parentId"]][v.get("key")] = v.get("value")
            elif t == "PAD_NET":
                try:
                    arr = json.loads(rid)
                    pad_nets[arr[1]][str(arr[2])] = v.get("padNet") or ""
                except Exception:
                    pass
        comps = {}
        for (t, rid), v in objs.items():
            if t != "COMPONENT":
                continue
            ca = {**(v.get("attrs") or {}), **pattrs.get(rid, {})}
            dev_id = str(ca.get("Device") or v.get("device") or "")
            da = devices.get(dev_id, {})
            ref = str(ca.get("Designator") or rid)
            desc = " | ".join(
                str(x)
                for x in (
                    ca.get("Value"),
                    ca.get("Comment"),
                    ca.get("Manufacturer Part"),
                    da.get("Value"),
                    da.get("Manufacturer Part"),
                    da.get("Comment"),
                    da.get("title"),
                )
                if x
            )
            comps[ref] = {"ref": ref, "desc": desc, "pads": dict(pad_nets.get(rid, {}))}
        boards.append({"board": f"{path.name}:{title}", "components": comps})
    return boards


def _load_epro(path: Path) -> list[dict]:
    with zipfile.ZipFile(path, "r") as zf:
        pj = json.loads(zf.read("project.json").decode("utf-8", errors="replace"))
        devs = pj.get("devices", {})
        boards = []
        for name in zf.namelist():
            if not name.endswith(".epcb"):
                continue
            rows = [json.loads(ln) for ln in zf.read(name).splitlines() if ln.startswith(b"[")]
            pcb_starts = [i for i, r in enumerate(rows) if r[:2] == ["DOCTYPE", "PCB"]]
            if pcb_starts:
                rows = rows[pcb_starts[-1] :]
            attrs = collections.defaultdict(dict)
            for r in rows:
                if r[0] == "ATTR" and len(r) >= 9:
                    attrs[r[3]][r[7]] = r[8]
            pads = collections.defaultdict(dict)
            for r in rows:
                if r[0] == "PAD_NET" and len(r) >= 4:
                    pads[r[1]][str(r[2])] = r[3] or ""
            comps = {}
            for cid, ca in attrs.items():
                ref = ca.get("Designator")
                if not ref or cid not in pads:
                    continue
                dev_info = devs.get(ca.get("Device", ""), {}).get("attributes", {})
                desc = " | ".join(
                    str(x)
                    for x in (
                        ca.get("Value"),
                        ca.get("Manufacturer Part"),
                        dev_info.get("Value"),
                        dev_info.get("Manufacturer Part"),
                        dev_info.get("Comment"),
                    )
                    if x
                )
                comps[ref] = {"ref": ref, "desc": desc, "pads": dict(pads[cid])}
            boards.append({"board": f"{path.name}:{Path(name).stem}", "components": comps})
    return boards


def audit_board(board_doc: dict) -> list[dict]:
    comps = board_doc["components"]
    by_net = collections.defaultdict(list)
    for ref, c in comps.items():
        for pin, net in c["pads"].items():
            if net:
                by_net[net].append((ref, pin))

    findings: list[dict] = []

    # 识别 ADS1298 / ADS1299 多通道 AFE 芯片
    afe_chips = []
    for ref, c in comps.items():
        pads = c["pads"]
        if any(k in c["desc"].upper() for k in ("ADS1298", "ADS1299")) or (
            len(pads) >= 50 and "63" in pads and "55" in pads and "28" in pads
        ):
            afe_chips.append(c)

    # 1. 多片 AFE BIASOUT (pin 63) 经低阻 (<1 kΩ) 硬并联检查
    bias_target_nets = collections.defaultdict(list)
    for chip in afe_chips:
        bnet = chip["pads"].get("63", "")
        if not bnet:
            continue
        bias_target_nets[bnet].append((chip["ref"], 0.0))
        for nref, npin in by_net.get(bnet, []):
            if nref == chip["ref"] or not nref.startswith("R"):
                continue
            ohms = parse_resistor_ohms(comps[nref]["desc"])
            if ohms is None:
                findings.append({
                    "rule": "unparsed_biasout_resistor",
                    "severity": "warning",
                    "resistor": nref,
                    "desc": comps[nref]["desc"],
                    "detail": f"{chip['ref']} BIASOUT 串联电阻 {nref} ({comps[nref]['desc']!r}) 阻值无法解析，请人工核对是否 >=1kΩ",
                })
            elif ohms < 1000.0:
                other_pins = [n for p, n in comps[nref]["pads"].items() if p != npin and n]
                for onet in other_pins:
                    if onet not in ("AGND", "GND"):
                        bias_target_nets[onet].append((chip["ref"], ohms))

    for net, drivers in bias_target_nets.items():
        uniq = sorted({d[0] for d in drivers})
        if len(uniq) >= 2:
            findings.append({
                "rule": "multi_afe_biasout_short",
                "severity": "error",
                "net": net,
                "chips": uniq,
                "detail": f"多颗 AFE ({', '.join(uniq)}) 的 BIASOUT 经 <1kΩ 低阻并联到同一网络 {net}，闭环运放将互顶饱和",
            })

    # 2. 双极性供电下 VCAP3 (pin 55) 去耦回流参考面核对
    for chip in afe_chips:
        avss_net = chip["pads"].get("20") or chip["pads"].get("32") or ""
        avss_u = avss_net.upper()
        is_bipolar_avss = bool(avss_net) and (
            "AVSS" in avss_u or "VSS" in avss_u or bool(re.match(r"^-\s*\d", avss_u))
        )
        vcap3_net = chip["pads"].get("55") or ""
        if is_bipolar_avss and vcap3_net:
            cap_returns = []
            for nref, npin in by_net.get(vcap3_net, []):
                if nref.startswith("C"):
                    others = [n for p, n in comps[nref]["pads"].items() if p != npin and n]
                    cap_returns.extend((nref, o) for o in others)
            bad_ret = [(cr, ret) for cr, ret in cap_returns if ret != avss_net]
            if cap_returns and bad_ret:
                findings.append({
                    "rule": "afe_vcap3_wrong_return",
                    "severity": "error",
                    "chip": chip["ref"],
                    "expected_return": avss_net,
                    "actual": bad_ret,
                    "detail": f"{chip['ref']}.55 (VCAP3) 去耦电容回到了 {bad_ret}，双极性供电下必须回 {avss_net}",
                })

    # 3. 贴肤电极连接器 (FPC*/J*) 信号脚未先串 >=10kΩ 限流电阻而直挂 TVS 到数字 GND
    if afe_chips:
        non_electrode_tokens = ("SCLK", "DRDY", "MISO", "MOSI", "SWDIO", "SWCLK", "USB", "VBUS", "CC1", "CC2", "VBAT", "TYPE-C", "UART")
        for ref, c in comps.items():
            if not ref.startswith(("FPC", "J")):
                continue
            # 排除板间数字排线座、USB 口、调试口与电池座
            sig_blob = f"{c['desc']} {' '.join(c['pads'].values())}".upper()
            if any(tok in sig_blob for tok in non_electrode_tokens):
                continue
            for pin, pnet in c["pads"].items():
                if not pnet or pnet.upper() in ("GND", "AGND", "WCT", "WCT2", "REF1", "REF2"):
                    continue
                for dref, dpin in by_net.get(pnet, []):
                    if dref.startswith(("D", "TVS", "ESD")):
                        d_others = {n.upper() for p, n in comps[dref]["pads"].items() if p != dpin and n}
                        if "GND" in d_others or "DGND" in d_others:
                            findings.append({
                                "rule": "skin_tvs_before_resistor",
                                "severity": "error",
                                "connector_pin": f"{ref}.{pin}",
                                "tvs": dref,
                                "net": pnet,
                                "detail": f"电极脚 {ref}.{pin} ({pnet}) 在限流电阻前直接挂 {dref} 到数字 GND，存在漏电零阻导通风险",
                            })

    # 4. CH340N/C/G 在 3.3V 供电下 V3 脚漏短接 3.3V
    for ref, c in comps.items():
        du = c["desc"].upper()
        if "CH340" in du:
            pads = c["pads"]
            vcc_pin, v3_pin = ("5", "8") if len(pads) <= 9 else ("16", "4")
            vcc_net = pads.get(vcc_pin, "")
            v3_net = pads.get(v3_pin, "")
            if any(k in vcc_net.upper() for k in ("3V3", "3.3V")) and v3_net != vcc_net:
                findings.append({
                    "rule": "ch340_3v3_v3_unstrapped",
                    "severity": "error",
                    "chip": ref,
                    "vcc_net": vcc_net,
                    "v3_net": v3_net,
                    "detail": f"{ref} 使用 3.3V ({vcc_net}) 供电，但 V3 脚接在 {v3_net!r} 未短接 3.3V",
                })

    # 5. nRF52840 DCC 片内开关电感数量级错误 (<1 uH)
    for ref, c in comps.items():
        if "NRF52840" in c["desc"].upper() or ("MCU_DCC" in c["pads"].values()):
            dcc_net = next(
                (n for p, n in c["pads"].items() if "DCC" in n.upper() or p in ("47", "B3")),
                "",
            )
            if not dcc_net:
                continue
            visited_nets = {dcc_net}
            ind_uh_values = []
            frontier = [dcc_net]
            for _ in range(2):
                nxt = []
                for fnet in frontier:
                    for lref, lpin in by_net.get(fnet, []):
                        if lref.startswith("L"):
                            uh = parse_inductor_uh(comps[lref]["desc"])
                            if uh is not None:
                                ind_uh_values.append((lref, comps[lref]["desc"], uh))
                            for lp, ln in comps[lref]["pads"].items():
                                if lp != lpin and ln and ln not in visited_nets and "DEC" not in ln.upper():
                                    visited_nets.add(ln)
                                    nxt.append(ln)
                frontier = nxt
            if ind_uh_values and max(v[2] for v in ind_uh_values) < 1.0:
                findings.append({
                    "rule": "nrf52840_dcc_inductor_nh",
                    "severity": "error",
                    "chip": ref,
                    "inductors": ind_uh_values,
                    "detail": f"{ref} DCC 链路最大电感仅 {max(v[2] for v in ind_uh_values):.4f} uH (<1 uH)，疑似将 10uH 误填为 nH",
                })

    return findings


def run_selftest() -> int:
    bad_board = {
        "board": "selftest_v10_bad",
        "components": {
            "U1": {"ref": "U1", "desc": "ADS1299IPAGR", "pads": {"20": "-2V5", "55": "VCAP3_1", "63": "BOUT1"}},
            "U6": {"ref": "U6", "desc": "ADS1299IPAGR", "pads": {"20": "-2V5", "55": "VCAP3_2", "63": "BOUT2"}},
            "R1": {"ref": "R1", "desc": "22Ω", "pads": {"1": "BOUT1", "2": "RLDOUT"}},
            "R16": {"ref": "R16", "desc": "22Ω", "pads": {"1": "BOUT2", "2": "RLDOUT"}},
            "C22": {"ref": "C22", "desc": "1uF", "pads": {"1": "VCAP3_1", "2": "AGND"}},
            "C115": {"ref": "C115", "desc": "1uF", "pads": {"1": "VCAP3_2", "2": "-2V5"}},
            "FPC1": {"ref": "FPC1", "desc": "AFC01-S20FCC", "pads": {"2": "INP13_RAW", "10": "RLDOUT"}},
            "D1": {"ref": "D1", "desc": "SOD-882 TVS", "pads": {"1": "INP13_RAW", "2": "GND"}},
            "U14": {"ref": "U14", "desc": "CH340N", "pads": {"5": "VDD_3V3", "8": "CH_V3", "3": "GND"}},
            "U4": {"ref": "U4", "desc": "nRF52840-QIAA", "pads": {"B3": "DCC_NET"}},
            "L3": {"ref": "L3", "desc": "15nH", "pads": {"1": "DCC_NET", "2": "DCC_MID"}},
            "L2": {"ref": "L2", "desc": "10nH", "pads": {"1": "DCC_MID", "2": "DEC4_6"}},
        },
    }
    bad_res = audit_board(bad_board)
    got_rules = {f["rule"] for f in bad_res}
    expected_rules = {
        "multi_afe_biasout_short",
        "afe_vcap3_wrong_return",
        "skin_tvs_before_resistor",
        "ch340_3v3_v3_unstrapped",
        "nrf52840_dcc_inductor_nh",
    }
    assert got_rules == expected_rules, f"反例漏检: got={got_rules}, expected={expected_rules}"

    good_board = {
        "board": "selftest_u9_afe_good",
        "components": {
            "U1": {"ref": "U1", "desc": "ADS1298IPAGR", "pads": {"20": "-2.5V", "55": "VCAP3_1", "63": "BOUT1"}},
            "U2": {"ref": "U2", "desc": "ADS1298IPAGR", "pads": {"20": "-2.5V", "55": "VCAP3_2", "63": "BOUT2"}},
            "R34": {"ref": "R34", "desc": "0402WGF3923TCE 392kΩ", "pads": {"1": "BOUT1", "2": "BIAS_DRV1"}},
            "R36": {"ref": "R36", "desc": "0402WGJ103TCE", "pads": {"1": "BOUT2", "2": "BIAS_DRV12"}},
            "C20": {"ref": "C20", "desc": "1uF", "pads": {"2": "VCAP3_1", "1": "-2.5V"}},
            "C19": {"ref": "C19", "desc": "1uF", "pads": {"2": "VCAP3_2", "1": "-2.5V"}},
            "FPC1": {"ref": "FPC1", "desc": "FH34SRJ-30S", "pads": {"3": "EXT_CH1P", "29": "BIAS_DRV1"}},
            "RN1": {"ref": "RN1", "desc": "YC124-JR-0715KL", "pads": {"1": "EXT_CH1P", "8": "IN1P"}},
            "J400": {"ref": "J400", "desc": "TYPE-C-16P", "pads": {"A4": "VBUS_5V", "A6": "USB_DP", "A12": "GND"}},
            "ESD1": {"ref": "ESD1", "desc": "USBLC6-2SC6", "pads": {"1": "USB_DP", "2": "GND", "5": "VBUS_5V"}},
            "U8": {"ref": "U8", "desc": "nRF52840-QIAA", "pads": {"B3": "MCU_DCC"}},
            "L3": {"ref": "L3", "desc": "AHW1608FE100KTF", "pads": {"2": "MCU_DCC", "1": "MCU_DCC_L"}},
            "L4": {"ref": "L4", "desc": "LQG15HS15NJ02D", "pads": {"2": "MCU_DCC_L", "1": "MCU_DEC4_6"}},
        },
    }
    good_res = audit_board(good_board)
    assert good_res == [], f"正例误报: {good_res}"
    print("PASS: erc_blindspot_audit.py --self-test 5 类反例全红，正例全绿")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="原理图与网表 ERC=0 五类盲点审计")
    ap.add_argument("files", nargs="*", help=".epro2 或 .epro 工程文件")
    ap.add_argument("--self-test", action="store_true", help="运行内置正反例自检")
    ap.add_argument("--json", dest="json_out", help="输出 JSON 路径")
    args = ap.parse_args()

    if args.self_test:
        return run_selftest()
    if not args.files:
        ap.error("请指定至少一个 .epro2/.epro 文件或使用 --self-test")

    all_reports = []
    total_err = 0
    for f in args.files:
        for bdoc in load_netlists(Path(f)):
            res = audit_board(bdoc)
            total_err += len(res)
            all_reports.append({"board": bdoc["board"], "component_count": len(bdoc["components"]), "findings": res})
            status = "PASS (0 缺陷)" if not res else f"FAIL ({len(res)} 项缺陷)"
            print(f"[{status}] {bdoc['board']} (器件数={len(bdoc['components'])})")
            for item in res:
                print(f"  - [{item['rule']}] {item['detail']}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(all_reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if total_err > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
