#!/usr/bin/env python3
"""多后端补布线、统一打分、留最好的一版（本地版「Zap 式」择优）。

用法：
  route.py 板.kicad_pcb [-o 输出目录] [--minutes 10] [--through-only] [--allow-cloud] [--host win]
  route.py --selftest

做什么：
  1. 把板（连同同名 .kicad_pro / .kicad_dru）拷进输出目录，本机 kicad-cli 跑基线 DRC。
  2. 后端并行出候选（都只补缺、不动已有铜）：
     - tracemaker：推到 thursday-win（offload.sh），按 TM_VARIANTS 几组开关各跑一次；
     - zap：只有加 --allow-cloud 才跑（板子会上传到 Zap! 云端，先问 Mako）；要 ZAP_PY 指向 zap.py 且已 zap.py login。
  3. 每个候选用本机 kicad-cli 判：未连、相对基线新增的铜层违规、删掉的已有铜、非通孔（--through-only 时）、
     新增过孔数和线长；删了已有铜或违反 --through-only 的直接出局。
  4. 排名：未连 → 新增违规 → 新增过孔×ROUTE_VIA_MM（默认 10）+ 新增线长；
     同时对基线板跑出口预检（escape.py），报告里标出哪些缺口只看固定铜就无路；最好的一版存成 best.kicad_pcb，报告写 report.json / report.md。
"""
import argparse, collections, concurrent.futures as cf, json, math, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

OFFLOAD = Path.home() / ".claude/skills/mstack/scripts/offload.sh"
TM_VERSION = "0.12.1"
# TraceMaker 自己每次就并行跑 8 个变体；这里再按 README 里针对密集封装的开关多开几组（都默认关）
TM_VARIANTS = {
    "base": "",
    "escape": "--escape-plan",
    "bundle": "--bundle-escape --package-tunnels",
    "supply": "--supply-first --via-drops",
    "persist": "--escape-persist --package-tunnels",
}
NON_COPPER = ("silk_", "lib_footprint", "courtyard", "text_", "footprint_")  # 按子串匹配（含 missing_courtyard）


def inventory(path):
    """板上走线/过孔/铺铜的几何指纹。按几何比（不按 uuid），因为后端可能重写 uuid。
    只认 KiCad 10 的写法（net 写网名）；旧版 `(net 1)` 的板会被误判成删了铜，转存成 KiCad 10 再喂。"""
    t = Path(path).read_text()
    r = lambda v: round(float(v), 4)

    def pt(tag, body):
        m = re.search(rf"\({tag} ([\d.\-]+) ([\d.\-]+)\)", body)
        return (r(m[1]), r(m[2])) if m else None

    def net(body):
        m = re.search(r'\(net "?([^")]*)"?\)', body)
        return m[1] if m else ""

    segs, vias, zones = collections.Counter(), [], collections.Counter()
    for kind, body in re.findall(r"\n\t\((segment|arc)\s+(.*?)\n\t\)", t, re.S):
        a, b = pt("start", body), pt("end", body)
        layer = re.search(r'\(layer "([^"]+)"\)', body).group(1)
        width = re.search(r"\(width ([\d.]+)\)", body)
        # 线宽和圆弧中点也进指纹：后端把已有线改细或改弧，也算动了已有铜
        segs[(kind, min(a, b), max(a, b), layer, net(body), width and r(width.group(1)), pt("mid", body))] += 1
    for body in re.findall(r"\n\t\(via\s+(.*?)\n\t\)", t, re.S):
        layers = tuple(re.search(r"\(layers ([^)]*)\)", body).group(1).replace('"', "").split())
        kind = (re.match(r"(blind|micro|buried)\b", body.strip()) or [None])[0]
        through = kind is None and layers[:1] == ("F.Cu",) and layers[-1:] == ("B.Cu",)
        vias.append((pt("at", body), net(body), layers, kind, through))
    for body in re.findall(r"\n\t\(zone\s+(.*?)\n\t\)", t, re.S):
        layers = re.search(r"\(layers? ([^)]*)\)", body)
        outline = re.search(r"\(polygon\s+\(pts(.*?)\)\s*\)", body, re.S)  # 只取轮廓，不取填充（填充随重铺变）
        zones[(net(body), layers.group(1) if layers else "",
               " ".join(outline.group(1).split()) if outline else "")] += 1
    return {"segs": segs, "vias": collections.Counter(v[:4] for v in vias),
            "non_through": sum(not v[4] for v in vias), "zones": zones}


def seg_len(key):
    # ponytail: 圆弧按弦长算，略短；样本板没有圆弧，有了再按 mid 求弧长
    return math.dist(key[1], key[2])


def drc(board):
    """kicad-cli DRC（先重铺铜再判，不存盘）；读同名 .kicad_pro / .kicad_dru，调用前要拷到候选旁边。"""
    out = Path(board).with_suffix(".drc.json")
    out.unlink(missing_ok=True)  # 不让上次的 JSON 冒充这次
    p = subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "--all-track-errors", "--refill-zones",
                        "-o", str(out), str(board)], capture_output=True, text=True)
    if p.returncode not in (0, 5) or not out.exists():
        raise RuntimeError(f"kicad-cli 读不进 {board}（rc={p.returncode}）：{p.stderr.strip()[-200:]}")
    d = json.loads(out.read_text())
    viol = collections.Counter(
        (v["type"],) + tuple(round(v["items"][0]["pos"][k], 2) for k in "xy")  # 按「类型+位置」，修一处新一处不抵消
        for v in d["violations"] if not any(s in v["type"] for s in NON_COPPER))
    unc = [{"items": [i["description"] for i in u["items"]], "pos": u["items"][0].get("pos")}
           for u in d["unconnected_items"]]
    return viol, unc


def score(base_inv, base_viol, cand_inv, cand_viol, unconnected, through_only, allow_new_pours=False):
    removed_segs = sum((base_inv["segs"] - cand_inv["segs"]).values())
    removed_vias = sum((base_inv["vias"] - cand_inv["vias"]).values())
    removed_zones = sum((base_inv["zones"] - cand_inv["zones"]).values())
    added = cand_inv["segs"] - base_inv["segs"]
    new_viol = cand_viol - base_viol
    out = {
        "unconnected": len(unconnected),
        "new_violations": sum(new_viol.values()),
        "new_violations_by_type": dict(collections.Counter(k[0] for k in new_viol.elements())),
        "vias_added": sum((cand_inv["vias"] - base_inv["vias"]).values()),
        "length_added_mm": round(sum(seg_len(k) * n for k, n in added.items()), 2),
        "segs_added": sum(added.values()),
        "zones_added": sum((cand_inv["zones"] - base_inv["zones"]).values()),
        "removed_existing": removed_segs + removed_vias + removed_zones,
        "non_through_vias": cand_inv["non_through"],
    }
    why = []
    if out["removed_existing"]:
        why.append(f"删改了已有铜 {removed_segs} 段 / {removed_vias} 孔 / {removed_zones} 块铺铜")
    if through_only and out["non_through_vias"]:
        why.append(f"有 {out['non_through_vias']} 个非通孔")
    if out["zones_added"] and not allow_new_pours:  # 只补缺：新铺整层地会改板，默认出局
        why.append(f"新铺了 {out['zones_added']} 块铜")
    out["disqualified"] = "；".join(why)
    return out


# 一个新孔折算多少毫米线长。只按孔数排时，0 孔但绕 126 mm 的会赢过 3 孔 20 mm 的（s6e 实测），所以合成一个代价
VIA_MM = float(os.environ.get("ROUTE_VIA_MM", 10))


def rank_key(s):
    return (bool(s["disqualified"]), s["unconnected"], s["new_violations"],
            round(s["vias_added"] * VIA_MM + s["length_added_mm"], 2))


def tm_package():
    """本机下载并缓存 TraceMaker 官方包（WSL 直连 github 常 TLS 断连，所以不在远端下载）。"""
    d = Path.home() / ".cache/mstack/tracemaker" / TM_VERSION
    tar = d / f"tracemaker-{TM_VERSION}-linux-x86_64.tar.gz"
    if not tar.exists() or not (d / "SHA256SUMS").exists():
        d.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/DingoOz/TraceMaker/releases/download/v{TM_VERSION}"
        for name in (tar.name, "SHA256SUMS"):
            subprocess.run(["curl", "-sSLf", "-o", str(d / name), f"{url}/{name}"], check=True)
    sums = (d / "SHA256SUMS").read_text()
    if subprocess.run(["shasum", "-a", "256", "-c", "-"], cwd=d, capture_output=True,
                      input="".join(l + "\n" for l in sums.splitlines() if tar.name in l), text=True).returncode:
        tar.unlink()
        raise SystemExit(f"TraceMaker 包 SHA256 不符，已删，重跑会重新下载：{tar}")
    return tar, d / "SHA256SUMS"


def run_tracemaker(work, host, seconds):
    """一单推到远端：带上本机校验过的官方包（远端再校验一次），几组开关两两并行跑，结果取回 work/tm/out/。"""
    job = work / "tm"
    shutil.rmtree(job / "out", ignore_errors=True)  # 不让上次留下的板冒充这次的结果
    (job / "out").mkdir(parents=True)
    for f in work.glob("in.*"):
        shutil.copy(f, job / f.name)
    for f in tm_package():
        shutil.copy(f, job / f.name)
    lines = [
        "set -u", f"V={TM_VERSION}",
        "grep linux-x86_64.tar.gz SHA256SUMS | sha256sum -c - || { echo SHA_FAIL; exit 3; }",
        "mkdir -p tm && tar xzf tracemaker-$V-linux-x86_64.tar.gz -C tm",
        'TM=$(find "$PWD/tm" -type f -name tracemaker -perm -u+x | head -1)',
        '"$TM" escape in.kicad_pcb > out/escape.txt 2>&1',
    ]
    for i, (name, flags) in enumerate(TM_VARIANTS.items()):
        lines.append(f'timeout {seconds * 4} "$TM" route in.kicad_pcb -o out/{name}.kicad_pcb --time {seconds} '
                     f'--threads 8 {flags} --stuck-report out/{name}.stuck.json > out/{name}.log 2>&1 &')
        if i % 2:
            lines.append("wait")
    lines += ["wait", "true"]
    (job / "run.sh").write_text("\n".join(lines) + "\n")
    p = subprocess.run([str(OFFLOAD), "--host", host, "--dir", str(job), "--get", "out/", "--", "bash run.sh"],
                       capture_output=True, text=True)
    (work / "tracemaker.offload.log").write_text(p.stdout + p.stderr)
    m = re.search(r"job=(\S+)", p.stdout)
    if m:  # 日志和 out/ 已取回，成败都清远端这单
        subprocess.run([str(OFFLOAD), "--clean", "--host", host, "--job", m.group(1)], capture_output=True)
    return {f"tm-{n}": job / "out" / f"{n}.kicad_pcb" for n in TM_VARIANTS}


def run_escape(work):
    """出口预检（escape.py，只看固定铜）：哪些缺口物理上无路，换布线器没用，要改孔位或挪件。"""
    out = work / "escape.json"
    out.unlink(missing_ok=True)
    try:  # 预检只是附加信息：缺 uv、超时、输出坏了都退回 None，不拖垮整轮择优
        p = subprocess.run(["uv", "run", "-q", "--no-project", "--with", "numpy", "--with", "scipy", "--with", "pillow",
                            "python", "-I", str(Path(__file__).with_name("escape.py")), str(work / "in.kicad_pcb"),
                            "--json", str(out)], capture_output=True, text=True, timeout=1800)
        (work / "escape.log").write_text(p.stdout + p.stderr)
        return json.loads(out.read_text())["gaps"]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as e:
        (work / "escape.log").write_text(f"出口预检失败：{e!r}")
        return None


def run_zap(work, zap, minutes):
    d = work / "zap"
    shutil.rmtree(d, ignore_errors=True)  # zap.py 失败时不让上次的板冒充
    d.mkdir()
    for f in work.glob("in.*"):
        shutil.copy(f, d / f.name)
    # ground_pour off 只关新铺整层地；电源网（VBAT/3V3/VBUS…）的铜块没有开关可关，
    # 再加 size_rails_from_current off 也照铺（thru3 实测 29 块，2026-11-03），所以 Zap 候选默认按「新铺了铜」出局
    p = subprocess.run([sys.executable, "-I", zap, "run", "in.kicad_pcb", "--keep-existing", "--effort", "thorough",
                        "--ground-pour", "off", "--minutes", str(minutes), "-o", "zap.kicad_pcb"],
                       cwd=d, capture_output=True, text=True)
    (work / "zap.log").write_text(p.stdout + p.stderr)
    return {"zap": d / "zap.kicad_pcb"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", nargs="?")
    ap.add_argument("-o", "--out")
    ap.add_argument("--minutes", type=int, default=10, help="每个后端的大致时间预算")
    ap.add_argument("--through-only", action="store_true", help="出现非通孔的候选直接出局")
    ap.add_argument("--allow-cloud", action="store_true", help="允许把板子传给 Zap!（云端）")
    ap.add_argument("--host", default="win", help="TraceMaker 跑在哪台（只有 linux-x86_64 发布包）")
    ap.add_argument("--candidate", action="append", default=[], metavar="名=路径",
                    help="把别处已出的板（Freerouting、旧 Zap 结果等）一起打分排名，可重复")
    ap.add_argument("--no-tracemaker", action="store_true")
    ap.add_argument("--allow-new-pours", action="store_true", help="新铺铜不出局（默认出局）")
    ap.add_argument("--no-escape", action="store_true", help="不跑出口预检")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.board:
        ap.error("要给板子路径")
    zap = os.environ.get("ZAP_PY")
    if a.allow_cloud and not (zap and Path(zap).exists()):  # 开跑前查，别等 TraceMaker 跑完才报
        ap.error("--allow-cloud 需要 ZAP_PY 指向 zap.py（curl -sO https://zap.mutual.solutions/downloads/zap.py），并先 zap.py login")
    ext_cands = {}
    for c in a.candidate:
        name, sep, path = c.partition("=")
        if not sep or not re.fullmatch(r"[\w.-]+", name) or name in ext_cands or name.startswith("tm-") or name == "zap":
            ap.error(f"--candidate 要写成 名=路径，名只用字母数字 . _ -，不重名、不用 tm-* / zap：{c}")
        ext_cands[name] = Path(path)
    src = Path(a.board).resolve()
    work = Path(a.out or src.with_suffix(".route")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    for f in work.glob("best.*"):  # 本轮没有合格候选时，不留上轮的 best
        f.unlink()
    for ext in (".kicad_pcb", ".kicad_pro", ".kicad_dru"):
        if src.with_suffix(ext).exists():
            shutil.copy(src.with_suffix(ext), work / f"in{ext}")

    base_inv = inventory(work / "in.kicad_pcb")
    base_viol, base_unc = drc(work / "in.kicad_pcb")
    base_by_type = dict(collections.Counter(k[0] for k in base_viol.elements()))
    print(f"基线：未连 {len(base_unc)}，铜层违规 {sum(base_viol.values())} {base_by_type}")

    # TraceMaker 的 --time 是单轮秒数，有未连时还会各救两轮：总时长约 3 轮，5 组两两并行再约 3 批
    tm_seconds = max(30, a.minutes * 60 // 9)
    cands = {}
    shutil.rmtree(work / "ext", ignore_errors=True)
    for name, path in ext_cands.items():  # 拷进 work，免得往别人的目录旁边写 .kicad_pro / .drc.json
        (work / "ext").mkdir(exist_ok=True)
        cands[name] = work / "ext" / f"{name}.kicad_pcb"
        shutil.copy(path, cands[name])
    with cf.ThreadPoolExecutor() as ex:
        esc = None if a.no_escape else ex.submit(run_escape, work)
        jobs = [] if a.no_tracemaker else [ex.submit(run_tracemaker, work, a.host, tm_seconds)]
        if a.allow_cloud:
            jobs.append(ex.submit(run_zap, work, zap, a.minutes))
        for j in jobs:
            cands.update(j.result())
        escape = esc.result() if esc else None

    results = {}
    for name, path in cands.items():
        if not path.exists():
            results[name] = {"error": "后端没出板（看对应日志）"}
            continue
        for ext in (".kicad_pro", ".kicad_dru"):  # kicad-cli 按同名工程读规则
            if (work / f"in{ext}").exists():
                shutil.copy(work / f"in{ext}", path.with_suffix(ext))
        try:
            viol, unc = drc(path)
        except RuntimeError as e:  # 半截板（被 timeout 杀掉时会有）记成这个候选的错误，不拖垮整轮
            results[name] = {"error": str(e)}
            continue
        s = score(base_inv, base_viol, inventory(path), viol, unc, a.through_only, a.allow_new_pours)
        s["unconnected_items"], s["board"] = unc, str(path)
        results[name] = s
        print(f"{name:12} 未连 {s['unconnected']:3}  新增违规 {s['new_violations']:3}  +孔 {s['vias_added']:3}  "
              f"+线 {s['length_added_mm']:7.2f} mm  {s['disqualified'] or ''}")

    ok = sorted((n for n in results if "error" not in results[n]), key=lambda n: rank_key(results[n]))
    best = ok[0] if ok and not results[ok[0]]["disqualified"] else None
    if best:
        shutil.copy(results[best]["board"], work / "best.kicad_pcb")
        for ext in (".kicad_pro", ".kicad_dru"):
            if (work / f"in{ext}").exists():
                shutil.copy(work / f"in{ext}", work / f"best{ext}")
    report = {"board": str(src), "baseline": {"unconnected": len(base_unc), "copper_violations": base_by_type,
                                              "unconnected_items": base_unc, "escape": escape},
              "best": best, "ranking": ok, "candidates": results}
    (work / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    md = [f"# 补布线择优：{src.name}", "", f"基线未连 {len(base_unc)}；最好：**{best or '无合格候选'}**"
          + (f"（比基线少 {len(base_unc) - results[best]['unconnected']} 个未连）" if best else ""), "",
          "| 候选 | 未连 | 新增违规 | +孔 | +线 mm | 出局原因 |", "|---|--:|--:|--:|--:|---|"]
    for n in ok:
        s = results[n]
        md.append(f"| {n} | {s['unconnected']} | {s['new_violations']} | {s['vias_added']} | {s['length_added_mm']} | {s['disqualified']} |")
    for n, s in results.items():
        if "error" in s:
            md.append(f"| {n} | — | — | — | — | {s['error']} |")
    if best:
        md += ["", "剩余未连（坐标 mm）："] + [f"- {u['pos']}：{' ↔ '.join(u['items'])}"
                                         for u in results[best]["unconnected_items"]]
    if escape == []:
        md += ["", "出口预检：基线没有未连。"]
    elif escape:
        md += ["", "出口预检（基线板，只看固定铜；无路 = 换布线器没用，要改孔位或挪件）："]
        md += [f"- [{e['verdict']}] {e['net']}：{e['why']}" for e in escape]
    elif not a.no_escape:
        md += ["", "出口预检没出结果，看 escape.log"]
    md += ["", "仍须人工过目走线并做制造前复核；DRC 0 不等于能用。"]
    (work / "report.md").write_text("\n".join(md) + "\n")
    print(f"最好：{best}；报告 {work / 'report.md'}")


def selftest():
    """打分与排名的最小自检：删改已有铜、新铺铜要出局；违规按位置不抵消；排名先未连再新增违规再代价。"""
    C = collections.Counter
    seg = lambda s, e, w="0.1", lyr="F.Cu": (f'\n\t(segment\n\t\t(start {s})\n\t\t(end {e})\n\t\t(width {w})'
                                          f'\n\t\t(layer "{lyr}")\n\t\t(net "A")\n\t)')
    via = lambda at, kind="", lyrs='"F.Cu" "B.Cu"': (f'\n\t(via{kind}\n\t\t(at {at})\n\t\t(size 0.4)\n\t\t(drill 0.2)'
                                                   f'\n\t\t(layers {lyrs})\n\t\t(net "A")\n\t)')
    zone = '\n\t(zone\n\t\t(net "GND")\n\t\t(layer "In1.Cu")\n\t\t(polygon\n\t\t\t(pts\n\t\t\t\t(xy 0 0) (xy 5 0) (xy 5 5)\n\t\t\t)\n\t\t)\n\t)'
    boards = {
        "base": seg("0 0", "1 0") + via("1 0"),
        "added": seg("0 0", "1 0") + via("1 0") + seg("1 0", "1 3", lyr="B.Cu") + via("1 3", " blind", '"F.Cu" "In1.Cu"'),
        "removed": seg("0 0", "1 0"),
        "thinner": seg("0 0", "1 0", w="0.05") + via("1 0"),        # 已有线改细
        "reblind": seg("0 0", "1 0") + via("1 0", " blind", '"F.Cu" "In1.Cu"'),  # 已有孔原地改盲孔
        "poured": seg("0 0", "1 0") + via("1 0") + zone,
    }
    with tempfile.TemporaryDirectory() as d:
        inv = {}
        for n, body in boards.items():
            Path(d, n).write_text("(kicad_pcb" + body + "\n)")
            inv[n] = inventory(Path(d, n))
    bi = inv["base"]
    sc = lambda n, **k: score(bi, C(), inv[n], C(), [], k.get("through_only", False))
    assert sum(bi["segs"].values()) == 1 and bi["non_through"] == 0
    s = sc("added")
    assert (s["segs_added"], s["vias_added"], s["length_added_mm"], s["disqualified"]) == (1, 1, 3.0, ""), s
    assert sc("added", through_only=True)["disqualified"]  # 盲孔出局
    for n in ("removed", "thinner", "reblind"):
        assert sc(n)["removed_existing"] == 1 and sc(n)["disqualified"], n
    assert sc("poured")["zones_added"] == 1 and sc("poured")["disqualified"]
    assert not score(bi, C(), inv["poured"], C(), [], False, allow_new_pours=True)["disqualified"]
    # 修好一处、别处新增一处同类违规：按位置比，算 1 个新增
    assert score(bi, C({("clearance", 1, 1): 1}), bi, C({("clearance", 9, 9): 1}), [], False)["new_violations"] == 1
    a = {"disqualified": "", "unconnected": 2, "new_violations": 5, "vias_added": 0, "length_added_mm": 0}
    b = dict(a, unconnected=1, new_violations=9)
    c = dict(a, unconnected=0, disqualified="删了已有铜")
    assert sorted([a, b, c], key=rank_key) == [b, a, c]
    zap_like, tm_like = dict(a, vias_added=3, length_added_mm=20.0), dict(a, length_added_mm=125.7)
    assert sorted([tm_like, zap_like], key=rank_key) == [zap_like, tm_like]  # 少绕路胜过少 3 个孔
    print("selftest ok")

if __name__ == "__main__":
    main()
