#!/usr/bin/env python3
"""bash 3.2 中文变量检查（2026-11-10 起）。

坑：Mac 自带 /bin/bash 3.2 在 UTF-8 locale 下，会把 "$x（" 里紧跟的全角字节当成变量名的一部分：
开了 set -u 的脚本直接报 unbound 退出，没开的把变量换成空串。Linux 的 bash 5 和 zsh 没这个问题，
所以在 Spark 上写、测过的脚本到 Mac 上才坏。规矩：变量后紧跟非 ASCII 字符，一律写 ${x}。

用法：
  check-bash32-cjk.py              检查仓库里 git 跟踪的全部 *.sh（#!/…zsh 的跳过）
  check-bash32-cjk.py 文件…         只检查给定文件
  check-bash32-cjk.py --selftest    自检（正反例；Mac 上再用真 /bin/bash 证明坑存在）
退出码：0 干净；1 有命中（打印 文件:行号:原文）；2 用法或自检失败。
"""
import os
import re
import subprocess
import sys
import tempfile

# $ 加标识符再紧跟非 ASCII。$1 这种数字参数 bash 只取一位，不受影响，所以标识符必须以字母或下划线开头。
BAD = re.compile(r'\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]')


def scan_text(text):
    """返回 [(行号, 原文)]。整行注释不查。
    ponytail: 不分析引号，单引号里的 $x中 也会报（改成 ${x} 无害）；要更准再上 shell 解析器。"""
    return [(i, l) for i, l in enumerate(text.splitlines(), 1)
            if not l.lstrip().startswith('#') and BAD.search(l)]


def scan_file(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    first = text.split('\n', 1)[0]
    if first.startswith('#!') and 'zsh' in first:
        return []
    return scan_text(text)


def tracked_sh():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run(['git', '-C', root, 'ls-files', '*.sh'], capture_output=True, text=True, check=True).stdout
    return [os.path.join(root, p) for p in out.split('\n') if p]


def selftest():
    bad = ['echo "目录：$WT（干净）"', 'x="新$a中"', 'echo "$a_b。"']
    good = ['echo "目录：${WT}（干净）"', 'echo "第$1中"', 'echo "$(date)中"', 'echo "$a ok"', 'echo "$a"',
            '# 注释里写 $WT（x）不查', "echo '$ 5元'"]
    for s in bad:
        assert scan_text(s), f'该报没报：{s}'
    for s in good:
        assert not scan_text(s), f'不该报却报了：{s}'
    with tempfile.TemporaryDirectory() as d:
        z = os.path.join(d, 'z.sh')
        with open(z, 'w', encoding='utf-8') as f:
            f.write('#!/bin/zsh\necho "$WT（x）"\n')
        assert scan_file(z) == [], 'zsh 脚本应跳过'
        b = os.path.join(d, 'b.sh')
        with open(b, 'w', encoding='utf-8') as f:
            f.write('#!/usr/bin/env bash\necho "$WT（x）"\n')
        assert scan_file(b) == [(2, 'echo "$WT（x）"')], scan_file(b)
    # 真 bash 3.2 上证明这个坑确实存在、而花括号写法正常；其它平台（bash 5）不复现，跳过
    ver = subprocess.run(['/bin/bash', '-c', 'echo "$BASH_VERSION"'], capture_output=True, text=True).stdout
    if ver.startswith('3.'):
        env = {'PATH': '/usr/bin:/bin', 'LC_CTYPE': 'UTF-8'}
        # 出错信息里带被吃掉一半的字节，不是合法 UTF-8，所以按字节读、解码时替换
        def run(s):
            r = subprocess.run(['/bin/bash', '-c', 'set -u; a=1; ' + s], capture_output=True, env=env)
            return r.returncode, r.stdout.decode('utf-8', 'replace').strip(), r.stderr.decode('utf-8', 'replace')
        rc, out, err = run('echo "$a（"')
        assert rc != 0 and 'unbound' in err, ('旧写法应当出错', rc, err)
        rc, out, err = run('echo "${a}（"')
        assert rc == 0 and out == '1（', ('花括号写法应当正常', rc, out, err)
        print('bash 3.2 实测：旧写法出错、花括号写法正常')
    else:
        print(f'/bin/bash 是 {ver.strip() or "未知"}，不是 3.x，跳过真 bash 实测')
    print('自检通过')


def main(argv):
    if argv == ['--selftest']:
        try:
            selftest()
        except AssertionError as e:
            print(f'自检失败：{e}', file=sys.stderr)
            return 2
        return 0
    files = argv or tracked_sh()
    n = 0
    for p in files:
        for i, l in scan_file(p):
            print(f'{p}:{i}:{l.strip()[:120]}')
            n += 1
    print(f'{"有 " + str(n) + " 处，变量后紧跟中文要写成 ${x}" if n else "干净"}（查了 {len(files)} 个文件）')
    return 1 if n else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
