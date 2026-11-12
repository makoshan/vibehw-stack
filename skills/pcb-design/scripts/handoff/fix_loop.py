"""斜出焊盘多轮局部重布，带退步保护：KiCad 自带 Python 运行，每轮调 fix_exits.py（uv）。
用法：KPY fix_loop.py 源板 输出板 [轮数=3]
每轮：route_lint → extract → fix_exits → apply2 → route_lint。除斜出外任何一项变多（怪角度、尖角、锐角 T 接、线宽突变、悬空），
就找出新问题点附近的那几处改动，把它们的焊盘拉进不改名单，从本轮起点重跑；斜出没减少就停。"""
import json, os, shutil, subprocess, sys
src, dst = sys.argv[1], sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 3
HERE = os.path.dirname(os.path.abspath(__file__)); KPY = os.environ.get('KPY', sys.executable)  # Spark 上 KiCad Python 在容器里，用 KPY 环境变量指向包装脚本
TMP = dst + '.loop'; os.makedirs(TMP, exist_ok=True)
HARD = ('odd_angle', 'sharp', 'tee_acute', 'width_jump', 'dangling', 'pad_exit')   # 斜出也不许新增（终点焊盘斜着进）


def run(*a, env=None):
    subprocess.run(list(a), check=True, capture_output=True, env={**os.environ, **(env or {})})


def lint(board, out):
    subprocess.run([KPY, f'{HERE}/route_lint.py', board, out], capture_output=True)
    return json.load(open(out))


def keys(l, k):
    return {(x.get('net'), x['layer'], tuple(x['at'])) for x in l[k]}


cur, deny, log = src, set(), []
run(KPY, f'{HERE}/extract.py', src, f'{TMP}/b0.json')      # 两脚件名单从导出的 JSON 拿，本脚本自己不 import pcbnew（Spark 上 KiCad 在容器里）
TWO = {f['ref'] for f in json.load(open(f'{TMP}/b0.json'))['fps'] if f['n'] <= 3}   # 两脚件的长边出不算
base = lint(cur, f'{TMP}/lint-0.json')
for i in range(1, N + 1):
    for attempt in range(6):
        run(KPY, f'{HERE}/extract.py', cur, f'{TMP}/b{i}.json')
        run('uv', 'run', '--no-project', '--with', 'numpy', '--with', 'shapely', 'python', '-I', f'{HERE}/fix_exits.py',
            f'{TMP}/b{i}.json', f'{TMP}/lint-{i - 1}.json', f'{TMP}/fix{i}.json', env={'DENY': ','.join(sorted(deny))})
        run(KPY, f'{HERE}/apply2.py', cur, f'{TMP}/fix{i}.json', f'{TMP}/p{i}.kicad_pcb')
        for ext in ('kicad_pro', 'kicad_dru'):                  # 中间板旁边没有工程文件，KiCad 按默认规则加载、存盘还会写回默认值（D9 踩过）
            pj = os.path.splitext(src)[0] + '.' + ext
            if os.path.exists(pj):
                shutil.copy(pj, f'{TMP}/p{i}.{ext}')
        new = lint(f'{TMP}/p{i}.kicad_pcb', f'{TMP}/lint-{i}.json')
        bad = [(k, p) for k in HARD for p in keys(new, k) - keys(base, k)]
        if not bad:
            break
        fx = json.load(open(f'{TMP}/fix{i}.json'))
        # 新问题点附近（0.6 mm）有新线的那几处改动，拉进不改名单
        culprits = set()
        for _, (net, L, at) in bad:
            for a in fx['added']:
                if a['layer'] == L and a['net'] == net and min(abs(a['a'][0] / 1e6 - at[0]) + abs(a['a'][1] / 1e6 - at[1]),
                                                               abs(a['b'][0] / 1e6 - at[0]) + abs(a['b'][1] / 1e6 - at[1])) < .6:
                    culprits |= {d['pad'] for d in fx['done'] + fx.get('moves', []) if d['net'] == net and d['layer'] == L}
        log.append({'pass': i, 'attempt': attempt, 'bad': [list(b) for b in bad], 'deny_add': sorted(culprits - deny)})
        if not culprits - deny:
            raise SystemExit(f'第 {i} 轮退步但找不到是哪处改动：{bad}')
        deny |= culprits
    else:
        raise SystemExit(f'第 {i} 轮重试 6 次仍退步')
    side = lambda l: sum(1 for x in l.get('pad_exit_side', []) if not any(x['pad'].startswith(r + '.') for r in TWO))
    fixed = len(base['pad_exit']) + side(base) - len(new['pad_exit']) - side(new)
    log.append({'pass': i, 'pad_exit': len(new['pad_exit']), 'fixed': fixed, 'deny': sorted(deny)})
    print('第', i, '轮：斜出', len(base['pad_exit']), '→', len(new['pad_exit']), '不改名单', len(deny), flush=True)
    if fixed <= 0:
        break
    cur, base = f'{TMP}/p{i}.kicad_pcb', new
shutil.copy(cur, dst)
json.dump(log, open(dst + '.fixloop.json', 'w'), ensure_ascii=False, indent=1)
print('输出', dst, '斜出', len(base['pad_exit']))
