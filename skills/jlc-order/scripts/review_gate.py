#!/usr/bin/env python3
"""制造复核结论绑设计指纹：设计一改，旧结论自动作废。

借 Backplane `docs/order.md:53-61` 的 review.json（none / stale / current）。门由脚本判，不由人或 agent 口述：
Backplane 原话「Agents skipped listing the issues when told to」，所以有问题的结论必须逐条写问题。

  python3 review_gate.py record <工程目录> --verdict ok|issues [--summary 一句话] [--issue 一条问题]…（最多 8 条）[--exclude 模式]…
  python3 review_gate.py check <工程目录>

check 退出码：0 = current 且 ok；1 = current 且 issues（列出问题）；2 = none（没有记录或记录坏了）；
3 = stale（设计变了，列出哪些文件变了）；4 = 用法错误或读写失败。

设计指纹：目录下板、原理图、工程、规则、STEP、DXF、Gerber、钻孔、BOM、CPL 文件，按相对路径排序，
对每行「路径 \\0 内容 sha256」再算一次 sha256。只看内容不看修改时间；软链（文件和目录）按目标内容算。
上传用的 Gerber zip 也算设计文件。
默认排除目录 `.review/`、`.git/`、出包目录（`fab-<8 位日期>-<8 位 sha>/` 及其 `.staging/`）、KiCad 备份，
排除 `_autosave-*` 和锁文件；`.kicad_prl`（界面状态）不算设计文件。record 时 `--exclude` 追加的模式
（fnmatch，对相对路径或任一级名字）存进 review.json，check 原样沿用，不能在 check 时再加排除；
被排掉的设计文件也记下来，check 每次都列出，免得排掉板文件后没人发现。
review.json 带封签（结论、问题、文件表一起算 sha256），只改 verdict/issues 会按 none 处理。
和 yuanli-band `host/fab_pin.py` 一样按内容 SHA-256 绑定；那边绑的是制造包来源，这里绑的是复核结论。
"""
import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DESIGN = re.compile(
    r'\.(kicad_pcb|kicad_sch|kicad_pro|kicad_dru|pro|sch|'            # KiCad
    r'epro|eprj|eprj2|epcb|epcb2|esch|'                                         # 嘉立创EDA
    r'pcbdoc|schdoc|prjpcb|'                                                    # Altium
    r'step|stp|dxf|'                                                            # 机械
    r'zip|'                                                                     # 上传用的 Gerber 包
    r'gbr|ger|gtl|gbl|gto|gbo|gts|gbs|gtp|gbp|gko|gm\d*|gml|g\d+|gbrjob|'      # Gerber
    r'drl|xln|exc)$', re.I)
# 表格按文件名里的整词认（jlc_smt_bom.csv、top-pos.csv），不按子串，免得 proposal.txt 也算进来
TABLE_WORDS = {'bom', 'cpl', 'pos', 'pnp', 'centroid', 'pick', 'place', 'placement', 'xy'}
TABLE_CN = re.compile(r'坐标|物料|元件清单|贴片')
DEFAULT_DIRS = re.compile(r'^(\.review|\.git|\.history(\.git)?|fab-\d{8}-[0-9a-f]{8}(\.staging)?|.*-backups)$')
DEFAULT_FILES = re.compile(r'^(_autosave-.*|~.*\.lck|.*\.lck)$')
MAX_ISSUES = 8
REVIEW = Path('.review') / 'review.json'


def excluded(rel, patterns):
    """--exclude 追加的模式：对相对路径或任一级名字做 fnmatch。"""
    parts = rel.split('/')
    return any(fnmatch.fnmatch(rel, p) or any(fnmatch.fnmatch(x, p) for x in parts) for p in patterns)


def is_design(rel, path=None):
    name = rel.rsplit('/', 1)[-1]
    if DESIGN.search(name):
        return True
    low = name.lower()
    if low.endswith('.pos'):
        return True
    if low.endswith(('.csv', '.xls', '.xlsx', '.txt', '.tsv')):
        words = set(re.split(r'[^a-z0-9]+', low.rsplit('.', 1)[0]))
        if words & TABLE_WORDS or TABLE_CN.search(name):
            return True
    if low.endswith('.txt') and path is not None:      # Altium 的 .TXT 钻孔
        try:
            with open(path, 'rb') as fh:
                return b'M48' in fh.read(4096)
        except OSError:
            return False
    return False


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for b in iter(lambda: fh.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def design_files(root, extra=(), dropped=None):
    """{相对路径: sha256}。软链按指向的内容算（改了目标也会 stale）；目录软链跟进去，同一真实目录只走一次（防环）。
    dropped 给了列表时，收集被 --exclude 排掉的设计文件。"""
    root = Path(root)
    out = {}
    seen = set()
    for dirpath, dirs, files in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = '' if rel_dir == '.' else rel_dir + '/'
        dirs[:] = sorted(d for d in dirs if not DEFAULT_DIRS.match(d))
        keep = []
        for d in dirs:
            if not excluded(rel_dir + d, extra):
                keep.append(d)
            elif dropped is not None:
                dropped.append(rel_dir + d + '/')
        dirs[:] = keep
        for f in files:
            rel = rel_dir + f
            p = Path(dirpath) / f
            if DEFAULT_FILES.match(f) or not p.is_file() or not is_design(rel, p):
                continue
            if excluded(rel, extra):
                if dropped is not None:
                    dropped.append(rel)
                continue
            out[rel] = sha256_file(p)
    return dict(sorted(out.items()))


def seal(rec):
    """结论、问题、排除、文件表一起封签：只改 verdict 或 issues 会对不上。防手改，不防有意重算伪造。"""
    body = {k: rec[k] for k in ('fingerprint', 'verdict', 'summary', 'issues', 'excludes', 'dropped', 'files',
                                'git_commit', 'time')}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fingerprint(files):
    h = hashlib.sha256()
    for rel, digest in sorted(files.items()):
        h.update(rel.encode('utf-8') + b'\0' + digest.encode() + b'\n')
    return h.hexdigest()


def git_commit(root):
    try:
        r = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True)
        if r.returncode:
            return None, None
        d = subprocess.run(['git', 'status', '--porcelain', '--', '.'], cwd=root, capture_output=True, text=True)
        return r.stdout.strip(), bool(d.stdout.strip())
    except OSError:
        return None, None


def one_line(s, what):
    s = (s or '').strip()
    if '\n' in s or '\r' in s:
        raise ValueError(f'{what}只能写一行：{s[:40]}…')
    return s


def record(root, verdict, summary='', issues=(), extra=()):
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f'{root} 不是目录')
    issues = [one_line(x, '问题') for x in issues]
    issues = [x for x in issues if x]
    if len(issues) > MAX_ISSUES:
        raise ValueError(f'问题最多 {MAX_ISSUES} 条，给了 {len(issues)} 条；合并或只留最要紧的')
    if verdict == 'issues' and not issues:
        raise ValueError('结论是 issues 就要用 --issue 逐条写出问题（至少 1 条）')
    if verdict == 'ok' and issues:
        raise ValueError('结论是 ok 却带了 --issue；有问题就记 issues')
    dropped = []
    files = design_files(root, extra, dropped)
    if dropped:
        print(f'注意：--exclude 排掉了 {len(dropped)} 项：' + '，'.join(dropped[:8]) + ('…' if len(dropped) > 8 else ''), file=sys.stderr)
    if not files:
        raise ValueError(f'{root} 下没有任何设计文件（板/原理图/Gerber/BOM…），不能记录复核')
    commit, dirty = git_commit(root)
    rec = dict(version=1, fingerprint=fingerprint(files), verdict=verdict, summary=one_line(summary, '摘要'),
               issues=issues, time=datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
               git_commit=commit, git_dirty=dirty, excludes=list(extra), dropped=sorted(dropped), files=files)
    rec['seal'] = seal(rec)
    p = root / REVIEW
    p.parent.mkdir(exist_ok=True)
    tmp = p.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, p)
    return rec


def check(root):
    """返回 (状态, 退出码, 说明行)。"""
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f'{root} 不是目录')
    p = root / REVIEW
    if not p.is_file():
        return 'none', 2, ['没有复核记录：先做制造复核再 record，或由 Mako 本人说「不复核直接下」']
    try:
        rec = json.loads(p.read_text())
        old, verdict = rec['files'], rec['verdict']
        good = (isinstance(old, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in old.items())
                and verdict in ('ok', 'issues') and isinstance(rec.get('issues'), list)
                and isinstance(rec.get('excludes'), list) and all(isinstance(x, str) for x in rec['excludes'])
                and isinstance(rec.get('dropped'), list) and rec['fingerprint'] == fingerprint(old)
                and rec.get('seal') == seal(rec) and (verdict == 'ok') == (not rec['issues']))
    except Exception:       # 记录坏到什么样都按没有记录，不能落到 1（current 且 issues）上
        good = False
    if not good:
        return 'none', 2, [f'{p} 读不出或被手改过，按没有记录处理']
    now = design_files(root, rec.get('excludes', []))
    if fingerprint(now) != rec['fingerprint']:
        lines = ['设计在复核之后变了，旧结论作废：']
        lines += [f'  改了 {r}' for r in sorted(set(now) & set(old)) if now[r] != old[r]]
        lines += [f'  新增 {r}' for r in sorted(set(now) - set(old))]
        lines += [f'  删除 {r}' for r in sorted(set(old) - set(now))]
        return 'stale', 3, lines
    head = f'复核 {rec.get("time", "?")}，提交 {(rec.get("git_commit") or "无")[:12]}'
    if rec['dropped']:
        head += f'；记录时 --exclude 排掉了 {len(rec["dropped"])} 项不在复核范围内：' + '，'.join(rec['dropped'][:8]) + \
            ('…' if len(rec['dropped']) > 8 else '')
    if rec.get('git_dirty'):
        head += '；记录时工作区有未提交改动，出包要用含这些改动的提交（fab_stage.sh --review 会再核一次）'
    if verdict == 'ok':
        return 'current', 0, [f'current 且 ok（{head}）' + (f'：{rec["summary"]}' if rec.get('summary') else '')]
    lines = [f'current 但有问题（{head}），先改或由 Mako 本人说照下：']
    lines += [f'  - {x}' for x in rec.get('issues') or []] or [f'  - {rec.get("summary", "")}']
    return 'current', 1, lines


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f'用法错误：{message}', file=sys.stderr)
        sys.exit(4)


def main(argv=None):
    ap = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True, parser_class=Parser)
    r = sub.add_parser('record', help='记录复核结论')
    r.add_argument('dir')
    r.add_argument('--verdict', required=True, choices=['ok', 'issues'])
    r.add_argument('--summary', default='')
    r.add_argument('--issue', action='append', default=[])
    r.add_argument('--exclude', action='append', default=[])
    c = sub.add_parser('check', help='查结论是否还对得上设计')
    c.add_argument('dir')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'record':
            rec = record(a.dir, a.verdict, a.summary, a.issue, a.exclude)
            print(f'已记录 {a.verdict}：{len(rec["files"])} 个设计文件，指纹 {rec["fingerprint"][:16]}… → {Path(a.dir) / REVIEW}')
            return 0
        state, code, lines = check(a.dir)
        print(state)
        print('\n'.join(lines))
        return code
    except Exception as e:      # 任何意外都落到 4，不能和 1（current 且 issues）撞码
        print('未完成：', type(e).__name__, e, file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
