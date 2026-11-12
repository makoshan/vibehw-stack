#!/usr/bin/env python3
"""review_gate.py 和 fab_stage.sh 的自检：在临时 git 仓库里造场景，断言写法。

每条反例都先跑对照（应过），再跑反例（应拒），两头都断言。
  python3 test_review_gate.py      全过退出 0，打印通过条数
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = [sys.executable, str(HERE / 'review_gate.py')]
STAGE = ['bash', str(HERE / 'fab_stage.sh')]


def run(cmd, cwd=None, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=e)


def git(cwd, *a):
    r = run(['git', *a], cwd=cwd)
    assert r.returncode == 0, f'git {a} 失败：{r.stderr}'
    return r.stdout.strip()


def project(t):
    p = t / 'proj'
    (p / 'gerber').mkdir(parents=True)
    (p / 'board.kicad_pcb').write_text('(kicad_pcb (version 1))\n')
    (p / 'board.kicad_sch').write_text('(kicad_sch)\n')
    (p / 'board.kicad_pro').write_text('{}\n')
    (p / 'gerber' / 'board-F_Cu.gtl').write_text('G04 top*\nM02*\n')
    (p / 'gerber' / 'board-PTH.drl').write_text('M48\nM30\n')
    (p / 'jlc_smt_bom.csv').write_text('Comment,Designator\n10k,R1\n')
    (p / 'jlc_smt_cpl.csv').write_text('Designator,Mid X\nR1,1mm\n')
    (p / 'README.md').write_text('说明\n')
    return p


def check(p):
    return run(GATE + ['check', str(p)])


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


# ---------------------------------------------------------------- review_gate

@case
def none_without_record(t):
    p = project(t)
    r = check(p)
    assert r.returncode == 2 and r.stdout.startswith('none'), (r.returncode, r.stdout)


@case
def ok_is_current(t):
    p = project(t)
    r = run(GATE + ['record', str(p), '--verdict', 'ok', '--summary', '过了'])
    assert r.returncode == 0, r.stderr
    rec = json.loads((p / '.review' / 'review.json').read_text())
    assert rec['verdict'] == 'ok' and len(rec['fingerprint']) == 64 and 'board.kicad_pcb' in rec['files']
    assert 'README.md' not in rec['files'], '非设计文件进了指纹'
    r = check(p)
    assert r.returncode == 0 and r.stdout.startswith('current'), (r.returncode, r.stdout)


@case
def one_byte_is_stale(t):
    """反例：改一个字节 → stale，并说出是哪个文件。"""
    p = project(t)
    run(GATE + ['record', str(p), '--verdict', 'ok'])
    assert check(p).returncode == 0
    b = p / 'gerber' / 'board-F_Cu.gtl'
    b.write_bytes(b.read_bytes().replace(b'top', b'tOp'))
    r = check(p)
    assert r.returncode == 3 and r.stdout.startswith('stale'), (r.returncode, r.stdout)
    assert '改了 gerber/board-F_Cu.gtl' in r.stdout, r.stdout
    b.write_bytes(b.read_bytes().replace(b'tOp', b'top'))
    assert check(p).returncode == 0, '改回原样后应恢复 current'


@case
def add_or_delete_is_stale(t):
    p = project(t)
    run(GATE + ['record', str(p), '--verdict', 'ok'])
    (p / 'case.step').write_text('ISO-10303-21;\n')
    r = check(p)
    assert r.returncode == 3 and '新增 case.step' in r.stdout, r.stdout
    (p / 'case.step').unlink()
    (p / 'jlc_smt_cpl.csv').unlink()
    r = check(p)
    assert r.returncode == 3 and '删除 jlc_smt_cpl.csv' in r.stdout, r.stdout


@case
def excluded_changes_ignored(t):
    """对照：输出目录、.review、非设计文件、--exclude 的目录改动不算变。"""
    p = project(t)
    (p / 'old').mkdir()
    (p / 'old' / 'x.kicad_pcb').write_text('a\n')
    run(GATE + ['record', str(p), '--verdict', 'ok', '--exclude', 'old'])
    (p / 'fab-20261110-0123abcd').mkdir()
    (p / 'fab-20261110-0123abcd' / 'b.gtl').write_text('x')
    (p / 'fab-20261110-0123abcd.staging').mkdir()
    (p / 'fab-20261110-0123abcd.staging' / 'b.gbl').write_text('x')
    (p / 'old' / 'x.kicad_pcb').write_text('b\n')
    (p / 'README.md').write_text('改了说明\n')
    r = check(p)
    assert r.returncode == 0, (r.returncode, r.stdout)
    # 但排除不能在 check 时临时加：check 不收 --exclude
    r = run(GATE + ['check', str(p), '--exclude', 'gerber'])
    assert r.returncode == 4, r.returncode


@case
def issues_exit_1(t):
    """反例：有问题的结论 → 1，并逐条列出问题。"""
    p = project(t)
    r = run(GATE + ['record', str(p), '--verdict', 'issues', '--issue', 'U3 焊盘离板边 0.1 mm', '--issue', '最小孔 0.15 要加价'])
    assert r.returncode == 0, r.stderr
    r = check(p)
    assert r.returncode == 1 and 'U3 焊盘离板边 0.1 mm' in r.stdout and '最小孔 0.15' in r.stdout, (r.returncode, r.stdout)


@case
def record_rules(t):
    """issues 不写问题、超过 8 条、ok 带问题、空目录，都不许记录。"""
    p = project(t)
    assert run(GATE + ['record', str(p), '--verdict', 'issues', '--summary', '有几处']).returncode == 4
    nine = sum((['--issue', f'问题 {i}'] for i in range(9)), [])
    assert run(GATE + ['record', str(p), '--verdict', 'issues'] + nine).returncode == 4
    eight = sum((['--issue', f'问题 {i}'] for i in range(8)), [])
    assert run(GATE + ['record', str(p), '--verdict', 'issues'] + eight).returncode == 0
    assert run(GATE + ['record', str(p), '--verdict', 'ok', '--issue', 'x']).returncode == 4
    assert run(GATE + ['record', str(p), '--verdict', 'maybe']).returncode == 4
    (t / 'empty').mkdir()
    assert run(GATE + ['record', str(t / 'empty'), '--verdict', 'ok']).returncode == 4
    assert not (t / 'empty' / '.review').exists()


@case
def tampered_record_is_none(t):
    """review.json 被手改（把 issues 改成 ok、或改坏）→ 按没有记录。"""
    p = project(t)
    run(GATE + ['record', str(p), '--verdict', 'issues', '--issue', 'x'])
    j = p / '.review' / 'review.json'
    rec = json.loads(j.read_text())
    rec['verdict'] = 'ok'
    rec['files']['board.kicad_pcb'] = '0' * 64
    j.write_text(json.dumps(rec))
    assert check(p).returncode == 2
    j.write_text('{坏')
    assert check(p).returncode == 2


@case
def verdict_only_tamper(t):
    """反例：只把 issues 改成 ok、清空 issues（文件表不动）→ 封签对不上，按 none。"""
    p = project(t)
    run(GATE + ['record', str(p), '--verdict', 'issues', '--issue', 'x'])
    assert check(p).returncode == 1
    j = p / '.review' / 'review.json'
    rec = json.loads(j.read_text())
    rec['verdict'], rec['issues'] = 'ok', []
    j.write_text(json.dumps(rec))
    r = check(p)
    assert r.returncode == 2, (r.returncode, r.stdout)
    rec = json.loads(j.read_text())
    rec['files'] = {'board.kicad_pcb': 5}          # 类型坏了也只能落到 none
    j.write_text(json.dumps(rec))
    assert check(p).returncode == 2
    run(GATE + ['record', str(p), '--verdict', 'ok'])
    rec = json.loads(j.read_text())
    rec['excludes'] = ['*.kicad_pcb']                # 只改排除、文件表不动 → 封签对不上
    j.write_text(json.dumps(rec))
    r = check(p)
    assert r.returncode == 2, (r.returncode, r.stdout)
    r = run([sys.executable, '-O'] + GATE[1:] + ['check', str(p)])
    assert r.returncode == 2, '-O 下校验不能失效'


@case
def rename_is_stale(t):
    """反例：内容不变只改名 → stale（指纹含路径）。"""
    p = project(t)
    run(GATE + ['record', str(p), '--verdict', 'ok'])
    (p / 'board.kicad_sch').rename(p / 'board2.kicad_sch')
    r = check(p)
    assert r.returncode == 3 and '新增 board2.kicad_sch' in r.stdout and '删除 board.kicad_sch' in r.stdout, r.stdout


@case
def what_counts(t):
    """嵌套出包目录不算；fab-2-board 这种普通目录要算；.kicad_prl、proposal.txt 不算；.pos 算；软链按目标内容算。"""
    p = project(t)
    (p / 'sub' / 'fab-20261110-0123abcd').mkdir(parents=True)
    (p / 'fab-2-board').mkdir()
    (p / 'fab-2-board' / 'main.kicad_pcb').write_text('m\n')
    (p / 'board.kicad_prl').write_text('{}')
    (p / 'proposal.txt').write_text('p')
    (p / 'top.pos').write_text('R1 1 1\n')
    real = t / 'lib.kicad_sch'
    real.write_text('lib v1\n')
    (p / 'lib.kicad_sch').symlink_to(real)
    run(GATE + ['record', str(p), '--verdict', 'ok'])
    files = json.loads((p / '.review' / 'review.json').read_text())['files']
    assert 'fab-2-board/main.kicad_pcb' in files and 'top.pos' in files and 'lib.kicad_sch' in files, files
    assert 'board.kicad_prl' not in files and 'proposal.txt' not in files, files
    (p / 'sub' / 'fab-20261110-0123abcd' / 'x.gtl').write_text('x')
    (p / 'board.kicad_prl').write_text('{"layers": 1}')
    (p / 'proposal.txt').write_text('q')
    assert check(p).returncode == 0
    real.write_text('lib v2\n')
    r = check(p)
    assert r.returncode == 3 and '改了 lib.kicad_sch' in r.stdout, r.stdout


@case
def zip_is_stale(t):
    """反例：上传用的 Gerber zip 改了 → stale。"""
    p = project(t)
    (p / 'gerbers.zip').write_bytes(b'PK v1')
    run(GATE + ['record', str(p), '--verdict', 'ok'])
    assert check(p).returncode == 0
    (p / 'gerbers.zip').write_bytes(b'PK v2')
    r = check(p)
    assert r.returncode == 3 and '改了 gerbers.zip' in r.stdout, (r.returncode, r.stdout)


@case
def symlink_dir_is_stale(t):
    """反例：层次原理图放在软链目录里，改了 → stale；指回自己的软链目录不死循环。"""
    p = project(t)
    lib = t / 'libdir'
    lib.mkdir()
    (lib / 'power.kicad_sch').write_text('v1\n')
    (p / 'sheets').symlink_to(lib)
    (p / 'loop').symlink_to(p)
    run(GATE + ['record', str(p), '--verdict', 'ok'])
    files = json.loads((p / '.review' / 'review.json').read_text())['files']
    assert 'sheets/power.kicad_sch' in files and not any(k.startswith('loop/') for k in files), files
    assert check(p).returncode == 0
    (lib / 'power.kicad_sch').write_text('v2\n')
    r = check(p)
    assert r.returncode == 3 and '改了 sheets/power.kicad_sch' in r.stdout, (r.returncode, r.stdout)


@case
def exclude_board_is_shown(t):
    """--exclude 排掉了板文件：可以记，但 check 每次都要把排掉的列出来。"""
    p = project(t)
    r = run(GATE + ['record', str(p), '--verdict', 'ok', '--exclude', '*.kicad_pcb'])
    assert r.returncode == 0 and 'board.kicad_pcb' in r.stderr, r.stderr
    r = check(p)
    assert r.returncode == 0 and '排掉了 1 项' in r.stdout and 'board.kicad_pcb' in r.stdout, r.stdout


@case
def odd_record_never_exit_1(t):
    """记录被改成怪类型并重算封签：只能落到 none 或用法错误，不能撞上 1。"""
    sys.path.insert(0, str(HERE))
    import review_gate
    p = project(t)
    run(GATE + ['record', str(p), '--verdict', 'ok'])
    j = p / '.review' / 'review.json'
    for key, val in (('excludes', [1]), ('dropped', 'x'), ('issues', None)):
        rec = json.loads(j.read_text())
        rec[key] = val
        rec['seal'] = review_gate.seal(rec)
        tmp = j.with_name('bad.json')
        tmp.write_text(json.dumps(rec))
        os.replace(tmp, j)
        r = check(p)
        assert r.returncode in (2, 4), (key, r.returncode, r.stdout, r.stderr)
        run(GATE + ['record', str(p), '--verdict', 'ok'])


# ---------------------------------------------------------------- fab_stage

def repo(t):
    """临时 origin（裸库）+ 工作副本，main 上一个提交已推送。"""
    origin = t / 'origin.git'
    git(t, 'init', '-q', '--bare', str(origin))
    w = t / 'w'
    git(t, 'init', '-q', '-b', 'main', str(w))
    git(w, 'config', 'user.email', 't@t')
    git(w, 'config', 'user.name', 't')
    (w / 'board.kicad_pcb').write_text('v1\n')
    git(w, 'add', '.')
    git(w, 'commit', '-qm', 'v1')
    git(w, 'remote', 'add', 'origin', str(origin))
    git(w, 'push', '-q', 'origin', 'main')
    git(w, 'fetch', '-q', 'origin')
    return w, git(w, 'rev-parse', 'HEAD')


EXPORT = 'cp board.kicad_pcb "$FAB_OUT/out.gbr"; test ! -e dirty.txt; echo "$FAB_COMMIT" > "$FAB_OUT/commit.txt"'


def stage(w, rev, out, cmd, *opts):
    return run(STAGE + list(opts) + [rev, str(out), '--', 'bash', '-c', cmd], cwd=w)


@case
def stage_ok(t):
    w, sha = repo(t)
    (w / 'dirty.txt').write_text('工作区里没提交的文件\n')   # 不该出现在 archive 里
    (w / 'board.kicad_pcb').write_text('v2 未提交\n')
    r = stage(w, 'HEAD', t / 'out', EXPORT, '--pin', 'board.kicad_pcb')
    assert r.returncode == 0, (r.returncode, r.stderr)
    outs = [d for d in (t / 'out').iterdir()]
    assert len(outs) == 1 and outs[0].name.startswith('fab-') and outs[0].name.endswith('-' + sha[:8]), outs
    d = outs[0]
    assert (d / 'out.gbr').read_text() == 'v1\n', '出包用的不是提交里的文件'
    src = json.loads((d / 'SOURCE.json').read_text())
    assert src['commit'] == sha and set(src['outputs']) == {'out.gbr', 'commit.txt'}
    import hashlib
    assert src['outputs']['out.gbr']['sha256'] == hashlib.sha256(b'v1\n').hexdigest()
    assert src['files']['board.kicad_pcb']['blob'] == git(w, 'rev-parse', f'{sha}:board.kicad_pcb')
    assert not any(p.name.endswith('.staging') for p in (t / 'out').iterdir())


@case
def stage_target_exists(t):
    """反例：目标已存在 → 拒，原包一个字节都不动，出包命令不跑。"""
    w, sha = repo(t)
    assert stage(w, sha, t / 'out', EXPORT).returncode == 0
    d = next((t / 'out').iterdir())
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    r = stage(w, sha, t / 'out', 'touch "$FAB_OUT/x"; touch ' + str(t / 'ran'))
    assert r.returncode == 2 and '已存在' in r.stderr, (r.returncode, r.stderr)
    assert {p.name: p.read_bytes() for p in d.iterdir()} == before
    assert not (t / 'ran').exists(), '目标已存在时还跑了出包命令'


@case
def stage_empty_output(t):
    """反例：命令退出码 0 但没出文件 → 失败，不留目标和暂存目录。"""
    w, sha = repo(t)
    r = stage(w, sha, t / 'out', 'true')
    assert r.returncode == 1 and '没有非空文件' in r.stderr, (r.returncode, r.stderr)
    assert list((t / 'out').iterdir()) == []
    r = stage(w, sha, t / 'out', 'mkdir "$FAB_OUT/sub"')      # 只有空子目录也算没出文件
    assert r.returncode == 1 and list((t / 'out').iterdir()) == []


@case
def stage_command_fails(t):
    w, sha = repo(t)
    r = stage(w, sha, t / 'out', 'touch "$FAB_OUT/half.gbr"; exit 3')
    assert r.returncode == 1 and '退出码 3' in r.stderr, (r.returncode, r.stderr)
    assert list((t / 'out').iterdir()) == []


@case
def stage_not_on_main(t):
    """反例：提交不在 origin/main（只在本地）→ 拒，出包命令不跑。"""
    w, sha = repo(t)
    (w / 'board.kicad_pcb').write_text('v2\n')
    git(w, 'commit', '-qam', 'v2 未推送')
    local = git(w, 'rev-parse', 'HEAD')
    r = stage(w, local, t / 'out', 'touch "$FAB_OUT/x"; touch ' + str(t / 'ran'))
    assert r.returncode == 2 and 'origin/main' in r.stderr, (r.returncode, r.stderr)
    assert not (t / 'ran').exists(), '提交不在 origin/main 时还跑了出包命令'
    assert not (t / 'out').exists(), '被拒时不该建输出根目录'
    # 对照：推送并 fetch 后就能出
    git(w, 'push', '-q', 'origin', 'main')
    git(w, 'fetch', '-q', 'origin')
    assert stage(w, local, t / 'out', EXPORT).returncode == 0


@case
def stage_leftover_staging(t):
    """上次中断留下的暂存目录 → 拒，不替人删。"""
    w, sha = repo(t)
    out = t / 'out'
    r0 = stage(w, sha, out, EXPORT)
    assert r0.returncode == 0
    d = next(out.iterdir())
    d.rename(d.with_name(d.name + '.staging'))
    r = stage(w, sha, out, EXPORT)
    assert r.returncode == 2 and '暂存目录还在' in r.stderr, r.stderr


@case
def stage_output_symlink(t):
    w, sha = repo(t)
    r = stage(w, sha, t / 'out', 'echo x > "$FAB_OUT/a.gbr"; ln -s /etc/passwd "$FAB_OUT/b.gbr"')
    assert r.returncode == 1 and '软链' in r.stderr, (r.returncode, r.stderr)
    assert list((t / 'out').iterdir()) == []


@case
def stage_pin_is_commit_content(t):
    """--pin 的 sha256 是提交里的内容，不是工作区的。"""
    import hashlib
    w, sha = repo(t)
    (w / 'board.kicad_pcb').write_text('工作区改了\n')
    assert stage(w, sha, t / 'out', EXPORT, '--pin', 'board.kicad_pcb').returncode == 0
    src = json.loads((next((t / 'out').iterdir()) / 'SOURCE.json').read_text())
    assert src['files']['board.kicad_pcb']['sha256'] == hashlib.sha256(b'v1\n').hexdigest(), src['files']


@case
def stage_from_subdir(t):
    """在子目录里跑也打包整个仓库；路径相对仓库根；相对输出目录按调用处算。"""
    w, sha = repo(t)
    (w / 'hw' / 'sub dir').mkdir(parents=True)
    (w / 'hw' / 'sub dir' / 'b.kicad_pcb').write_text('b\n')
    git(w, 'add', '.')
    git(w, 'commit', '-qm', 'sub')
    git(w, 'push', '-q', 'origin', 'main')
    git(w, 'fetch', '-q', 'origin')
    cwd = w / 'hw' / 'sub dir'
    r = run(STAGE + ['--pin', 'hw/sub dir/b.kicad_pcb', 'HEAD', 'out', '--', 'bash', '-c',
                     'cp board.kicad_pcb "hw/sub dir/b.kicad_pcb" "$FAB_OUT/"'], cwd=cwd, env={'CDPATH': '.:/tmp'})
    assert r.returncode == 0, r.stderr
    d = next((cwd / 'out').iterdir())
    assert sorted(x.name for x in d.iterdir()) == ['SOURCE.json', 'b.kicad_pcb', 'board.kicad_pcb']


@case
def stage_shadow_branch(t):
    """反例：本地建个叫 origin/main 的分支顶替远端 → 仍拒。"""
    w, sha = repo(t)
    (w / 'board.kicad_pcb').write_text('v2\n')
    git(w, 'commit', '-qam', 'v2')
    git(w, 'branch', 'origin/main', 'HEAD')
    r = stage(w, 'HEAD', t / 'out', EXPORT)
    assert r.returncode == 2 and 'origin/main' in r.stderr, (r.returncode, r.stderr)


@case
def stage_escaping_symlink(t):
    """反例：提交里有指到仓库外的软链 → 拒；仓库内的软链放行。"""
    w, sha = repo(t)
    os.symlink('board.kicad_pcb', w / 'inner.kicad_pcb')
    git(w, 'add', '.')
    git(w, 'commit', '-qm', 'inner link')
    git(w, 'push', '-q', 'origin', 'main')
    git(w, 'fetch', '-q', 'origin')
    assert stage(w, 'HEAD', t / 'out1', EXPORT).returncode == 0
    os.symlink(str(w / 'board.kicad_pcb'), w / 'abs.kicad_pcb')
    os.symlink('../../outside.kicad_pcb', w / 'up.kicad_pcb')
    git(w, 'add', '.')
    git(w, 'commit', '-qm', 'bad links')
    git(w, 'push', '-q', 'origin', 'main')
    git(w, 'fetch', '-q', 'origin')
    r = stage(w, 'HEAD', t / 'out2', 'touch "$FAB_OUT/x"; touch ' + str(t / 'ran'))
    assert r.returncode == 2 and 'abs.kicad_pcb' in r.stderr and 'up.kicad_pcb' in r.stderr, (r.returncode, r.stderr)
    assert not (t / 'ran').exists()


@case
def stage_race_target(t):
    """反例：出包期间目标被别人建成非空目录 → 失败，不把暂存目录挪进去。"""
    w, sha = repo(t)
    r = stage(w, sha, t / 'out', 'touch "$FAB_OUT/a.gbr"; mkdir "${FAB_OUT%.staging}"; touch "${FAB_OUT%.staging}/other"')
    assert r.returncode == 1, (r.returncode, r.stderr)
    d = next((t / 'out').iterdir())
    assert [x.name for x in d.iterdir()] == ['other'], list(d.iterdir())


@case
def stage_review(t):
    """--review：复核的就是提交里的文件才放行；工作区改过再复核、或结论有问题，都拒。"""
    w, sha = repo(t)
    (w / '.gitignore').write_text('.review/\n')
    git(w, 'add', '.')
    git(w, 'commit', '-qm', 'ignore')
    git(w, 'push', '-q', 'origin', 'main')
    git(w, 'fetch', '-q', 'origin')
    run(GATE + ['record', str(w), '--verdict', 'ok'])
    r = stage(w, 'HEAD', t / 'out', EXPORT, '--review', '.')
    assert r.returncode == 0, r.stderr
    src = json.loads((next((t / 'out').iterdir()) / 'SOURCE.json').read_text())
    assert src['review']['state'] == 'current-ok' and len(src['review']['fingerprint']) == 64
    assert not any('.review' in k for k in src['outputs'])
    (w / 'board.kicad_pcb').write_text('v2 未提交\n')            # 复核的是工作区，提交里还是 v1
    run(GATE + ['record', str(w), '--verdict', 'ok'])
    r = stage(w, 'HEAD', t / 'out2', EXPORT, '--review', '.')
    assert r.returncode == 2 and '复核没过' in r.stderr and 'board.kicad_pcb' in r.stderr, (r.returncode, r.stderr)
    (w / 'board.kicad_pcb').write_text('v1\n')
    run(GATE + ['record', str(w), '--verdict', 'issues', '--issue', '孔到铜 0.15'])
    r = stage(w, 'HEAD', t / 'out3', EXPORT, '--review', '.')
    assert r.returncode == 2 and '孔到铜 0.15' in r.stderr, (r.returncode, r.stderr)


@case
def stage_zero_byte_output(t):
    """反例：只出了 0 字节文件 → 失败。"""
    w, sha = repo(t)
    r = stage(w, sha, t / 'out', ': > "$FAB_OUT/empty.gbr"')
    assert r.returncode == 1 and '非空文件' in r.stderr, (r.returncode, r.stderr)
    assert list((t / 'out').iterdir()) == []


@case
def stage_pin_checked_first(t):
    """--pin 给目录或同名文件 → 出包命令跑之前就拒。"""
    w, sha = repo(t)
    (w / 'hw').mkdir()
    (w / 'hw' / 'board.kicad_pcb').write_text('hw\n')
    git(w, 'add', '.')
    git(w, 'commit', '-qm', 'hw')
    git(w, 'push', '-q', 'origin', 'main')
    git(w, 'fetch', '-q', 'origin')
    ran = 'touch "$FAB_OUT/x"; touch ' + str(t / 'ran')
    r = stage(w, 'HEAD', t / 'out', ran, '--pin', 'hw')
    assert r.returncode == 2 and '不是普通文件' in r.stderr, (r.returncode, r.stderr)
    r = stage(w, 'HEAD', t / 'out', ran, '--pin', 'board.kicad_pcb', '--pin', 'hw/board.kicad_pcb')
    assert r.returncode == 2 and '同名' in r.stderr, (r.returncode, r.stderr)
    assert not (t / 'ran').exists() and not (t / 'out').exists()


@case
def stage_no_var_before_cjk(t):
    """Mac 自带 bash 3.2 在 UTF-8 locale 下把 $x（ 里的全角字节当成变量名，set -u 时直接退出；变量后紧跟非 ASCII 必须写 ${x}。"""
    bad = [f'{i}: {l.strip()}' for i, l in enumerate((HERE / 'fab_stage.sh').read_text().splitlines(), 1)
           if re.search(r'\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]', l)]
    assert not bad, bad


@case
def stage_archive_mismatch(t):
    """反例：提交里有子模块、或 .gitattributes 用了 export-ignore → archive 和提交对不上，拒。"""
    w, sha = repo(t)
    (w / '.gitattributes').write_text('board.kicad_pcb export-ignore\n')
    git(w, 'add', '.')
    git(w, 'commit', '-qm', 'attr')
    git(w, 'push', '-q', 'origin', 'main')
    git(w, 'fetch', '-q', 'origin')
    r = stage(w, 'HEAD', t / 'out', EXPORT)
    assert r.returncode == 2 and 'export-ignore' in r.stderr, (r.returncode, r.stderr)
    git(w, 'rm', '-q', '.gitattributes')
    git(w, 'update-index', '--add', '--cacheinfo', f'160000,{sha},vendor/lib')
    git(w, 'commit', '-qm', 'submodule')
    git(w, 'push', '-q', 'origin', 'main')
    git(w, 'fetch', '-q', 'origin')
    r = stage(w, 'HEAD', t / 'out', EXPORT)
    assert r.returncode == 2 and '子模块' in r.stderr, (r.returncode, r.stderr)
    assert not (t / 'out').exists()


def main():
    if not __debug__:
        sys.exit('自检靠 assert，不要用 python -O 跑')
    passed = 0
    for fn in CASES:
        with tempfile.TemporaryDirectory(prefix='review-gate-') as tmp:
            try:
                fn(Path(tmp))
            except Exception as e:       # 断言失败和用例自己崩了都记不通过
                print(f'不通过 {fn.__name__}: {type(e).__name__} {e}')
                continue
        passed += 1
        print(f'通过 {fn.__name__}')
    print(f'{passed}/{len(CASES)} 通过')
    return 0 if passed == len(CASES) else 1


if __name__ == '__main__':
    sys.exit(main())
