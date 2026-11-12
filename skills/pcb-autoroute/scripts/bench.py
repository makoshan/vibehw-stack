#!/usr/bin/env python3
"""布线工具基准：固定题目、固定标准答案，改了 escape.py / window.py 就跑一遍对账。

用法（root 是放板子和结果的目录，不进仓库）：
  python3 bench.py fetch  --root 目录 [--manifest bench_public.json]    按提交号拉公开板
  python3 bench.py cases  --root 目录 [--per-board 4] [--seed 1]        人工板上删网出题
  python3 bench.py run    --root 目录 [-j 8] [--only 子串] [--timeout 1500]   跑 escape + window，已有结果跳过
  python3 bench.py score  --root 目录 [--baseline 基线.json] [--save 基线.json]  出分；比基线差退出 1
  python3 bench.py strip  --root 目录                                    C 组：按 Zap 的做法去掉线、孔、铺铜
  python3 bench.py tmjob  --root 目录 --job 目录 [--seconds 600] [-j 6]  生成 TraceMaker 批量任务（x86 Linux 才能跑，用 offload.sh 推）
  python3 bench.py finish --root 目录 --routed 目录 [--max-gaps 12] [-j 8]  布完的板用 window.py 逐根收尾
  python3 bench.py full   --root 目录 --routed 目录 [--label 名]          逐板 DRC，和 Zap 公开数字对比
  python3 bench.py --selftest

题目（B 组「删网恢复」）：从人工布好的板上删掉一根网的全部线和孔。人工走线证明这根网一定有路，
所以 escape 判「无路」就是错判。只挑模型覆盖得到的网，否则错判的账算不清：
  正好 2 个焊盘（恰好 1 个缺口）、不是铺铜网、全是通孔、没有盘中孔、平面层上没有走线。
记分：false_no_route（escape 判无路，必须 0）、escape 已证实数、window 已证实数、window 判无解数、超时/崩溃数。
own.json（可选，放 root 下）列自家板的固定答案（A 组）：{"id", "pcb", "net", "escape": 判定, "window": 判定}。
环境：KICAD_PYTHON 指向带 pcbnew 的 Python，PATH 里要有 kicad-cli；工具用 BENCH_PY 指定的 Python 跑（要有 numpy scipy pillow ortools）。
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
KPY = os.environ.get("KICAD_PYTHON", "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3")
BPY = os.environ.get("BENCH_PY", sys.executable)

# 在 KiCad 自带 Python 里跑：挑网、删网、存题
CASEGEN = r'''
import pcbnew as k, sys, json, random, re
src, outdir, per, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
b = k.LoadBoard(src); MM = 1e6
ESC = {"slash": "/", "backslash": "\\", "lt": "<", "gt": ">", "colon": ":", "dblquote": '"', "quote": "'",
       "tab": "\t", "return": "\n", "space": " "}
unesc = lambda s: re.sub(r"\{(\w+)\}", lambda m: ESC.get(m[1], m[0]), s)
L = [b.GetLayerName(l) for l in b.GetEnabledLayers().CuStack()]
ids = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
ol = k.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(ol, True)
area = ol.Area()
zones = [z for z in b.Zones() if not z.GetIsRuleArea()]
plane = {l for l in L[1:-1] if sum(z.Outline().Area() for z in zones if z.IsOnLayer(ids[l])) > 0.5 * area}
zone_nets = {z.GetNetCode() for z in zones}
pads = {}
smd = []
for f in b.GetFootprints():
    for p in f.Pads():
        pads.setdefault(p.GetNetCode(), []).append(p)
        if p.GetAttribute() in (k.PAD_ATTRIB_SMD, k.PAD_ATTRIB_CONN):
            smd.append(p)
items = {}
for t in b.GetTracks():
    items.setdefault(t.GetNetCode(), []).append(t)
ok = []
for code, ts in items.items():
    n = unesc(ts[0].GetNetname())
    if not code or code in zone_nets or len(pads.get(code, [])) != 2:
        continue
    bad, length = False, 0.0
    for t in ts:
        if t.GetClass() == "PCB_VIA":
            if t.GetViaType() != k.VIATYPE_THROUGH or any(p.IsOnLayer(k.F_Cu if p.IsOnLayer(k.F_Cu) else k.B_Cu) and p.HitTest(t.GetPosition(), int(t.GetWidth(k.F_Cu) / 2)) for p in smd):
                bad = True
        else:
            length += t.GetLength() / MM
            if b.GetLayerName(t.GetLayer()) in plane or t.IsLocked():
                bad = True
    if not bad and 2.0 <= length <= 60.0:
        ok.append((n, code, round(length, 2), sum(t.GetClass() == "PCB_VIA" for t in ts)))
ok.sort()
random.Random(seed).shuffle(ok)
out = [{"file": re.sub(r"[^\w.-]", "_", n)[:60] + ".kicad_pcb", "net": n, "code": code, "human_len_mm": length, "human_vias": nv}
       for n, code, length, nv in ok[:per]]
print("JSON" + json.dumps({"eligible": len(ok), "planes": sorted(plane), "cases": out}))
'''


# 同一进程第二次 LoadBoard 拿到的是裸对象，所以删网单独起进程
DELNET = r'''
import pcbnew as k, sys
b = k.LoadBoard(sys.argv[1])
for t in list(b.GetTracks()):
    if t.GetNetCode() == int(sys.argv[3]):
        b.Remove(t)
b.Save(sys.argv[2])
'''


# C 组：保留摆件，去掉线、孔、铺铜（禁布区留着），和 Zap 对比页的处理一致
STRIP = r'''
import pcbnew as k, sys
b = k.LoadBoard(sys.argv[1])
rm = [z for z in b.Zones() if not z.GetIsRuleArea()] + list(b.GetTracks())  # 先取全再删：Remove 之后再取列表拿到的是裸对象
for x in rm:
    b.Remove(x)
b.Save(sys.argv[2])
'''


def sh(cmd, timeout=None, cwd=None):
    """跑命令；超时返回 rc=-9，不抛异常（一题超时不能拖垮整批）。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return -9, "超时"


def manifest(a):
    return json.loads(Path(a.manifest).read_text())


def fetch(a):
    root = Path(a.root)
    for b in manifest(a)["boards"]:
        d = root / "boards" / b["id"]
        if (d / ".done").exists():
            continue
        d.mkdir(parents=True, exist_ok=True)
        steps = [["git", "init", "-q", "."], ["git", "remote", "add", "origin", b["source"]],
                 ["git", "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", b["commit"]],
                 ["git", "config", "core.sparseCheckout", "true"]]
        for c in steps:
            sh(c, 300, d)
        (d / ".git/info/sparse-checkout").write_text("*.kicad_pcb\n*.kicad_pro\n*.kicad_dru\n")
        rc, out = sh(["git", "checkout", "-q", "FETCH_HEAD"], 300, d)
        if rc == 0:
            (d / ".done").touch()
        else:
            print("失败", b["id"], out[-200:])


def main_pcb(d, b):
    if b.get("pcb"):
        return d / b["pcb"]
    fs = [f for f in d.rglob("*.kicad_pcb") if ".git" not in f.parts]
    return max(fs, key=lambda f: f.stat().st_size)  # 同仓多板时取最大的（面板、叠层样例都小）


def cases(a):
    root = Path(a.root)
    tot = 0
    for b in manifest(a)["boards"]:
        pcb = main_pcb(root / "boards" / b["id"], b)
        out = root / "cases" / b["id"]
        if (out / "cases.json").exists():
            tot += len(json.loads((out / "cases.json").read_text())["cases"])
            continue
        out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir="/tmp") as t:
            Path(t, "gen.py").write_text(CASEGEN)
            Path(t, "del.py").write_text(DELNET)
            rc, log = sh([KPY, str(Path(t, "gen.py")), str(pcb.resolve()), str(out.resolve()), str(a.per_board), str(a.seed)], 600)
            m = re.search(r"^JSON(.*)$", log, re.M)
            if not m:
                print("出题失败", b["id"], log[-300:])
                continue
            r = json.loads(m[1])
            for c in r["cases"]:
                sh([KPY, str(Path(t, "del.py")), str(pcb.resolve()), str((out / c["file"]).resolve()), str(c["code"])], 300)
        r["pcb"] = str(pcb)
        r["cases"] = [c for c in r["cases"] if (out / c["file"]).exists()]
        for c in r["cases"]:
            for ext in (".kicad_pro", ".kicad_dru"):
                if pcb.with_suffix(ext).exists():
                    shutil.copy(pcb.with_suffix(ext), (out / c["file"]).with_suffix(ext))
        (out / "cases.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
        tot += len(r["cases"])
        print(b["id"], "可选网", r["eligible"], "出题", len(r["cases"]), "平面层", r["planes"])
    print("题目总数", tot)


def tasks(root, only):
    t = []
    for f in sorted((root / "cases").glob("*/cases.json")):
        for c in json.loads(f.read_text())["cases"]:
            if not only or only in f"{f.parent.name}/{c['net']}":
                t.append({"suite": "B", "board": f.parent.name, "net": c["net"], "pcb": str((f.parent / c["file"]).resolve()),
                          "human_len_mm": c["human_len_mm"], "human_vias": c["human_vias"]})
    own = root / "own.json"
    if own.exists():
        for c in json.loads(own.read_text()):
            if not only or only in f"{c['id']}/{c['net']}":
                t.append({"suite": "A", "board": c["id"], "net": c["net"], "pcb": str(root / c["pcb"]),
                          "expect": {k: c[k] for k in ("escape", "window") if k in c}})
    return t


def run_one(root, t, timeout):
    safe = re.sub(r"[^\w.-]", "_", t["net"])[:60]
    d = root / "results" / t["suite"] / t["board"] / safe
    done = d / "result.json"
    if done.exists():
        return json.loads(done.read_text())
    d.mkdir(parents=True, exist_ok=True)
    r = dict(t)
    t0 = time.time()
    ej = d / "escape.json"
    ej.unlink(missing_ok=True)
    rc, log = sh([BPY, "-I", str(HERE / "escape.py"), t["pcb"], "--json", str(ej), "--prove", str(d / "esc")], timeout)
    (d / "escape.log").write_text(log)
    r["escape_s"] = round(time.time() - t0, 1)
    if ej.exists():
        gaps = [g for g in json.loads(ej.read_text())["gaps"] if g.get("net") == t["net"]]
        r["escape"] = gaps[0]["verdict"] if gaps else "没有这根缺口"
        r["escape_why"] = gaps[0].get("why", "")[:200] if gaps else ""
    else:
        r["escape"] = "超时" if rc == -9 else "崩溃"
    t0 = time.time()
    rc, log = sh([BPY, "-I", str(HERE / "window.py"), t["pcb"], "--net", t["net"], "-o", str(d / "win"), "--time", "120"], timeout)
    (d / "window.log").write_text(log)
    r["window_s"] = round(time.time() - t0, 1)
    wj = d / "win" / "window.json"
    if rc == 0 and wj.exists():
        w = json.loads(wj.read_text())
        r["window"] = w.get("verdict", "?")
        r["window_why"] = str(w.get("why", ""))[:200]
        r["window_new"] = w.get("new_violations", [])[:5]
        r["window_worse"] = w.get("nets_worse", {})
    else:
        r["window"] = "超时" if rc == -9 else "崩溃"
        r["window_why"] = log[-300:]
    for p in list(d.rglob("*.kicad_pcb")):  # 板子副本大，只留判定
        p.unlink()
    done.write_text(json.dumps(r, ensure_ascii=False, indent=1))
    print(f"{t['suite']} {t['board']}/{t['net']}：escape {r['escape']}（{r['escape_s']} s），window {r['window']}（{r['window_s']} s）", flush=True)
    return r


def run(a):
    root = Path(a.root).resolve()
    ts = tasks(root, a.only)
    with ThreadPoolExecutor(a.j) as ex:
        list(ex.map(lambda t: run_one(root, t, a.timeout), ts))


def tally(rs):
    """把逐题结果压成分数。公开板：判无路 = 错判。自家板：和固定答案逐项比。"""
    s = Counter()
    wrong = []
    for r in rs:
        s["题数"] += 1
        e, w = r.get("escape", "?"), r.get("window", "?")
        if r["suite"] == "A":
            for k, v in r.get("expect", {}).items():
                if r.get(k) != v:
                    s["A 组答错"] += 1
                    wrong.append(f"{r['board']}/{r['net']} {k}：应为 {v}，实为 {r.get(k)}")
            continue
        s["B 题数"] += 1
        if e == "无路":
            s["false_no_route"] += 1
            wrong.append(f"{r['board']}/{r['net']} escape 判无路（人工布通过）：{r.get('escape_why', '')}")
        s["escape 已证实"] += e == "有路（已证实）"
        s["escape 临界"] += e == "临界"
        s["escape 有路未证实"] += e == "有路未证实"
        s["escape 没跑完"] += e in ("超时", "崩溃", "没有这根缺口")
        s["window 已证实"] += w == "已证实"
        s["window 无解"] += w == "窗口内无解"
        s["window DRC 不过"] += w == "DRC 不过"
        s["window 没结果"] += w in ("限时内没找到解", "没建起来", "超时", "崩溃", "?")
    return s, wrong


def score(a):
    rs = [json.loads(f.read_text()) for f in sorted(Path(a.root, "results").rglob("result.json"))]
    s, wrong = tally(rs)
    for k, v in sorted(s.items()):
        print(f"{k}: {v}")
    for w in wrong:
        print("  错：", w)
    bad = []
    if s["false_no_route"] or s["A 组答错"]:
        bad.append("有错判")
    if a.baseline:
        base = json.loads(Path(a.baseline).read_text())
        for k in ("escape 已证实", "window 已证实"):
            if s[k] < base.get(k, 0):
                bad.append(f"{k} 退步：{base[k]} → {s[k]}")
    if a.save:
        Path(a.save).write_text(json.dumps(dict(s), ensure_ascii=False, indent=1))
    for x in bad:
        print("退出 1：", x)
    return 1 if bad else 0


def strip(a):
    root = Path(a.root).resolve()
    for b in manifest(a)["boards"]:
        pcb = main_pcb(root / "boards" / b["id"], b)
        d = root / "strip" / b["id"]
        out = d / f"{b['id']}.kicad_pcb"
        if out.exists():
            continue
        d.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir="/tmp") as t:
            Path(t, "s.py").write_text(STRIP)
            rc, log = sh([KPY, str(Path(t, "s.py")), str(pcb.resolve()), str(out)], 300)
        if not out.exists():
            print("去铜失败", b["id"], log[-200:])
            continue
        for ext in (".kicad_pro", ".kicad_dru"):
            if pcb.with_suffix(ext).exists():
                shutil.copy(pcb.with_suffix(ext), out.with_suffix(ext))
    print("去铜完成", len(list((root / "strip").glob("*/*.kicad_pcb"))))


def tmjob(a):
    """整个 strip/ 拷进任务目录，写 run.sh：每块板一个 TraceMaker 进程，1 线程，a.j 路并行。开关同 Zap 对比页。"""
    job = Path(a.job)
    shutil.copytree(Path(a.root) / "strip", job / "strip", dirs_exist_ok=True)
    (job / "run.sh").write_text(f"""set -u
V=0.12.1
grep linux-x86_64.tar.gz SHA256SUMS | sha256sum -c - || {{ echo SHA_FAIL; exit 3; }}
mkdir -p tm out && tar xzf tracemaker-$V-linux-x86_64.tar.gz -C tm
export TM=$(find "$PWD/tm" -type f -name tracemaker -perm -u+x | head -1)
one() {{ i=$1; mkdir -p out/$i; s=$(date +%s)
  timeout {a.seconds * 2} "$TM" route strip/$i/$i.kicad_pcb -o out/$i/$i.kicad_pcb --time {a.seconds} --threads 1 --no-gpu --no-kb \\
    --diff-pairs --keep-vias-off-pads --vias-off-pads-below 1000 > out/$i/log.txt 2>&1
  echo "$? $(( $(date +%s) - s ))" > out/$i/done.txt; cp strip/$i/$i.kicad_pro out/$i/ 2>/dev/null; cp strip/$i/$i.kicad_dru out/$i/ 2>/dev/null; true; }}
export -f one
ls strip | xargs -P {a.j} -I{{}} bash -c 'one {{}}'
""")
    print("任务目录", job, "；把 tracemaker 包和 SHA256SUMS 拷进去，再 offload.sh --detach --dir 任务目录 -- 'bash run.sh'")


def drc_full(board):
    with tempfile.TemporaryDirectory(dir="/tmp") as d:
        o = Path(d, "drc.json")
        sh(["kicad-cli", "pcb", "drc", "--format", "json", "--all-track-errors", "--refill-zones", "-o", str(o), str(board)], 900)
        if not o.exists():
            return None
        dd = json.loads(o.read_text())
    errs = [v for v in dd["violations"] if v["severity"] == "error"]
    return {"unconnected": len(dd["unconnected_items"]), "rule_errors": len(errs),
            "shorts": sum(v["type"] == "shorting_items" for v in errs), "gaps": dd["unconnected_items"]}


def finish_one(board, work, max_gaps, timeout):
    """一根根解：window 证实就接着在结果板上解下一根；解不了的网记下跳过。"""
    cur, skipped, solved = Path(board), set(), []
    work.mkdir(parents=True, exist_ok=True)
    for step in range(max_gaps):
        st = drc_full(cur)
        if st is None:
            break
        nets = [n for u in st["gaps"] for n in set(re.findall(r"\[([^\]]+)\]", " ".join(i["description"] for i in u["items"])))]
        todo = [n for n in dict.fromkeys(nets) if n not in skipped]
        if not todo:
            break
        d = work / f"s{step:02d}"
        rc, log = sh([BPY, "-I", str(HERE / "window.py"), str(cur), "--net", todo[0], "-o", str(d), "--time", "60"], timeout)
        wj = d / "window.json"
        w = json.loads(wj.read_text()) if rc == 0 and wj.exists() else {}
        if w.get("verdict") == "已证实":
            cur = Path(w["final"])
            solved.append(todo[0])
        else:
            skipped.add(todo[0])
    return cur, solved, sorted(skipped)


def finish(a):
    root = Path(a.routed).resolve()

    def one(d):
        src = d / f"{d.name}.kicad_pcb"
        res = d / "finish.json"
        if not src.exists() or res.exists():
            return
        st = drc_full(src)
        if not st or not 0 < st["unconnected"] <= a.max_gaps:
            res.write_text(json.dumps({"skipped": True, "unconnected": st and st["unconnected"]}))
            return
        cur, solved, left = finish_one(src, d / "fin", a.max_gaps, a.timeout)
        if cur != src:
            for ext in (".kicad_pcb", ".kicad_pro", ".kicad_dru"):
                if cur.with_suffix(ext).exists():
                    shutil.copy(cur.with_suffix(ext), (d / f"{d.name}-fin").with_suffix(ext))
        res.write_text(json.dumps({"solved": solved, "left": left}, ensure_ascii=False))
        print(d.name, "收尾", len(solved), "根，没解掉", left, flush=True)
    with ThreadPoolExecutor(a.j) as ex:
        list(ex.map(one, sorted(x for x in root.iterdir() if x.is_dir())))


def full(a):
    root = Path(a.routed).resolve()
    rows, tot = [], Counter()
    boards = manifest(a)["boards"]

    def one(b):
        d = root / b["id"]
        fin = d / f"{b['id']}-fin.kicad_pcb"
        raw = d / f"{b['id']}.kicad_pcb"
        return b, (drc_full(raw) if raw.exists() else None), (drc_full(fin) if fin.exists() else None)
    with ThreadPoolExecutor(a.j) as ex:
        res = list(ex.map(one, boards))
    print(f"{'板':<36}{'Zap':>6}{'TM 60':>7}{'TM 本次':>8}{'收尾后':>7}{'错误(本次)':>11}")
    for b, r, f in res:
        p = b["published"]
        z, t = p["zap"]["unconnected"], p["tracemaker"]["unconnected"]
        mine = r["unconnected"] if r else None
        after = (f or r or {}).get("unconnected")
        print(f"{b['id'][:35]:<36}{z:>6}{t:>7}{str(mine):>8}{str(after):>7}{str((f or r or {}).get('rule_errors')):>11}")
        tot["Zap 未连"] += z
        tot["TM60 未连"] += t
        tot["Zap 全通板"] += z == 0
        tot["TM60 全通板"] += t == 0
        if r:
            tot["本次 未连"] += mine
            tot["收尾后 未连"] += after
            tot["收尾后 全通板"] += after == 0
            tot["收尾后 短路"] += (f or r)["shorts"]
    print(dict(tot))
    if a.save:
        Path(a.save).write_text(json.dumps({"label": a.label, "totals": dict(tot),
                                            "boards": {b["id"]: {"raw": r and r["unconnected"], "fin": f and f["unconnected"]} for b, r, f in res}},
                                           ensure_ascii=False, indent=1))


def selftest():
    r = [{"suite": "B", "escape": "无路", "window": "已证实", "board": "b", "net": "n"},
         {"suite": "B", "escape": "有路（已证实）", "window": "窗口内无解", "board": "b", "net": "m"},
         {"suite": "A", "board": "o", "net": "x", "escape": "无路", "window": "窗口内无解",
          "expect": {"escape": "临界", "window": "窗口内无解"}}]
    s, w = tally(r)
    assert s["false_no_route"] == 1 and s["A 组答错"] == 1 and s["B 题数"] == 2 and s["window 已证实"] == 1 and len(w) == 2, (s, w)
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", choices=["fetch", "cases", "run", "score", "strip", "tmjob", "finish", "full"])
    ap.add_argument("--root", default=os.path.expanduser("~/.cache/pcb-bench"))
    ap.add_argument("--manifest", default=str(HERE / "bench_public.json"))
    ap.add_argument("--per-board", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-j", type=int, default=8)
    ap.add_argument("--only")
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--baseline")
    ap.add_argument("--save")
    ap.add_argument("--job")
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--routed")
    ap.add_argument("--max-gaps", type=int, default=12)
    ap.add_argument("--label", default="")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.cmd:
        ap.error("要给子命令")
    return {"fetch": fetch, "cases": cases, "run": run, "score": score, "strip": strip, "tmjob": tmjob, "finish": finish,
            "full": full}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
