#!/usr/bin/env python3
"""导整板 STEP，并把缺 3D 模型的件报出来。

用法：step_export_check.py <板>.kicad_pcb <输出>.step [透传给 kicad-cli 的参数…]

kicad-cli 找不到某个件的模型时只打一行「Could not add 3D model for J2.」，退出码照样 0，
导出的 STEP 里就没有这颗件，拿它查碰壳永远「不碰」。这里把那一行数出来。
做法借自 Backplane native/host/src/kicad/solidread.rs 的 missing_note。

退出码：0 = 全部有模型；1 = 有缺，列出位号（这些件的碰壳结论记「未验证」）；
        2 = 导出失败、没出文件，或输出为空。
kicad-cli：环境变量 KICAD_CLI > PATH 里的 kicad-cli > macOS 默认安装位置。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAC_CLI = '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli'
# 子进程强制英文。不用纯 C：实测 LC_ALL=C 下 kicad-cli 打不开中文路径（「原力/板.kicad_pcb」读成 ??????），
# 所以用英文 + UTF-8：Linux 是 C.UTF-8，macOS 没有 C.UTF-8，用必有的 en_US.UTF-8
UTF8_C = 'en_US.UTF-8' if sys.platform == 'darwin' else 'C.UTF-8'
# KiCad 偏好里设成中文时环境变量可能压不住，所以中文告警也认
# （中文原文取自 KiCad 10 的 zh_CN/kicad.mo：「无法为 %s 添加 3D 模型。」）
MISSING = (re.compile(r'^Could not add 3D model for (.+?)\.\s*$'),
           re.compile(r'^无法为 (.+?) 添加 3D 模型'))
# 模型文件在但读不了（坏文件）时 kicad-cli 打这类行，退出码 2，失败时原样给出来
MODEL_TROUBLE = re.compile(r'3D model|3D 模型|No model for|Cannot identify|找不到文件|File not found')


def find_cli():
    return os.environ.get('KICAD_CLI') or shutil.which('kicad-cli') or MAC_CLI


def refs_in(text):
    refs = []
    for line in text.splitlines():
        for pat in MISSING:
            m = pat.match(line.strip())
            if m:
                refs.append(m.group(1))
    return refs


def missing_refs(out, err):
    """缺模型的位号，一行算一个（位号重名的两颗件各算一次，同 Backplane 按行数）。
    stdout、stderr 可能各打一遍（2026-11-10 在容器里以 root 跑时见过 stderr 也打），只取打得多的那一路，不两路相加。"""
    a, b = refs_in(out), refs_in(err)
    return a if len(a) >= len(b) else b


def export(board, out, extra):
    """导出到同目录的临时子目录，成功才挪到 out：失败时不会把上次留下的旧文件当成这次的结果。"""
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, LC_ALL=UTF8_C, LANG=UTF8_C)
    env.pop('LANGUAGE', None)  # 实测只设 LC_ALL 不去 LANGUAGE=zh_CN，kicad-cli 照样出中文
    with tempfile.TemporaryDirectory(prefix='.step-export-', dir=out.parent) as tmp:
        stage = Path(tmp) / out.name  # 同名：kicad-cli 用输出文件名给板子命名
        cmd = [find_cli(), 'pcb', 'export', 'step', '-f', '-o', str(stage), *extra, str(board)]
        try:
            r = subprocess.run(cmd, capture_output=True, env=env, timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as e:
            return 2, [], f'kicad-cli 没跑起来：{e}'
        so, se = r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8', 'replace')
        log = so + '\n' + se
        refs = missing_refs(so, se)
        if r.returncode != 0:
            trouble = [ln for ln in log.splitlines() if MODEL_TROUBLE.search(ln)]
            return 2, refs, '\n'.join([f'kicad-cli 退出码 {r.returncode}', *trouble, '--- 日志末尾', log.strip()[-400:]])
        if not stage.is_file():
            return 2, refs, f'kicad-cli 说成功，但没出文件：{log.strip()[-400:]}'
        if stage.stat().st_size == 0:
            return 2, refs, '导出的 STEP 是空文件'
        os.replace(stage, out)
    return (1 if refs else 0), refs, log


def main(argv):
    if len(argv) < 2 or not argv[0].endswith('.kicad_pcb'):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    board, out, extra = argv[0], argv[1], argv[2:]
    if any(a in ('-o', '--output') or a.startswith('--output=') for a in extra):
        print('输出路径用第二个参数给，不要再透传 -o/--output', file=sys.stderr)
        return 2
    if not Path(board).is_file():
        print(f'找不到板文件：{board}', file=sys.stderr)
        return 2
    code, refs, log = export(board, out, extra)
    if code == 2:
        print(f'导出失败：{log}', file=sys.stderr)
        if refs:
            print(f'另外 kicad-cli 报了 {len(refs)} 个件缺 3D 模型：{" ".join(refs)}', file=sys.stderr)
    elif refs:
        print(f'已导出 {out}，但 {len(refs)} 个件没有 3D 模型（STEP 里没有它们）：{" ".join(refs)}')
        print('这些件的碰壳结论记「未验证」：补上模型路径（或 KiCad 3D 库）后重导再查。')
    else:
        # 封装里压根没写 model、或模型标了 hide 的件，kicad-cli 不导也不报，这里也查不到
        print(f'已导出 {out}，kicad-cli 没报缺模型（封装里没写 model、模型设了隐藏的件不在此列）')
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
