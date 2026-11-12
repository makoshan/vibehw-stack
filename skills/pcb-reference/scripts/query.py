#!/usr/bin/env python3
"""pcb-reference 的查询层。只读，不改库。

三条诚实约束写死在输出里，不靠调用方自觉：
  1. 每个参考板都带许可；没声明就印「许可未声明」，不默认可用。
  2. lift 的样本量必须同时印出来 —— 3 块板算出的 lift 396 是噪声，不是规律。
  3. 覆盖口径每次都印 —— 这个库是 GitHub star 头部的 KiCad 仓 + 提到嘉立创的专业版工程，不是全体开源硬件。
"""
import sqlite3, sys, os, textwrap

DEFAULT_DB = os.path.expanduser('~/go/202609/vibe-hw/work/pcbdb/pcb.db')
DB = os.environ.get('PCBDB') or (
    DEFAULT_DB if os.path.exists(DEFAULT_DB)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pcb.db'))
THIN = 5        # 样本少于这个数的 lift 标为不可信
EDA = "kicad_version IS 'easyeda-pro'"   # 嘉立创专业版并进来的板（merge_easyeda.py）
# 用 IS 不用 =：没解析的 KiCad 板 kicad_version 是 NULL，NOT (NULL = …) 还是 NULL，会被悄悄漏掉（实踩：有板仓 1774 算成 1658）
# 不是「设计」的仓：KiCad 官方组织下的是 EDA 软件自己的测试夹具（issue18119.kicad_pcb 之类的 bug 复现板），
# kicad-source-mirror 一个仓就占已解析板的 4.4%。只排这个有证据的，别的仓不凭感觉猜。
NOT_DESIGN = "r.full_name NOT LIKE 'KiCad/%'"


def db():
    if not os.path.exists(DB):
        sys.exit(f'找不到库：{DB}\n设 PCBDB 环境变量指向 pcb.db')
    c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    return c

def lic(v):
    return v if v else '许可未声明'

def caveat(c):
    n, = c.execute("SELECT count(*) FROM repo WHERE found_by NOT LIKE 'easyeda:%'").fetchone()
    b, = c.execute(f'SELECT count(*) FROM board WHERE parsed_at IS NOT NULL AND NOT {EDA}').fetchone()
    nb, = c.execute(f'SELECT count(*) FROM board WHERE nets_at IS NOT NULL AND NOT {EDA}').fetchone()
    eb, er = c.execute(f'SELECT count(*), count(DISTINCT repo) FROM board WHERE parsed_at IS NOT NULL AND {EDA}').fetchone()
    nets = '网络接法全覆盖' if nb >= b else f'网络接法只覆盖其中 {nb} 块（star 最高的那批）'
    eda = f'另有嘉立创 EDA 专业版 {eb} 块板（{er} 个仓，README/topic 提到嘉立创的 GitHub 仓）；' if eb else ''
    return (f'口径：GitHub topic:kicad/pcb 按 star 前 {n} 个仓，解析 {b} 块 KiCad 板；'
            f'{eda}{nets}。不是全体开源硬件。')

def cmd_like(c, kw):
    print(f'# 含「{kw}」的器件值（库里的原始写法，型号没有归一）\n')
    rows = c.execute('''SELECT COALESCE(value_norm, value) k, count(*) n, count(DISTINCT repo) r
                        FROM part WHERE value LIKE ? AND value NOT IN ('','~')
                        GROUP BY k ORDER BY r DESC, n DESC LIMIT 20''', (f'%{kw}%',)).fetchall()
    if not rows:
        print('没有匹配。注意：型号写法五花八门，换个片段再试（如 esp32 / STM32F1 / 2040）。'); return
    for k, n, r in rows:
        print(f'  {r:4d} 个仓 / {n:6d} 次   {k}')
    print(f'\n{caveat(c)}')

def cmd_part(c, val):
    print(f'# {val}\n')
    n, r = c.execute('''SELECT count(*), count(DISTINCT repo) FROM part
                        WHERE COALESCE(value_norm, value)=?''', (val,)).fetchone()
    if not n:
        print(f'库里没有「{val}」。先用 like 找实际写法：query.py like {val}'); return
    print(f'用量：{n} 次，跨 {r} 个仓\n')

    fps = c.execute('''SELECT footprint, count(*) n, count(DISTINCT repo) r FROM part
                       WHERE COALESCE(value_norm, value)=? AND footprint!=''
                       GROUP BY 1 ORDER BY r DESC LIMIT 6''', (val,)).fetchall()
    if fps:
        print('常用封装：')
        for f, n2, r2 in fps:
            print(f'  {r2:3d} 仓 / {n2:5d} 次   {f}')
        print()

    lc = c.execute('''SELECT lcsc, count(DISTINCT repo) r FROM part WHERE COALESCE(value_norm, value)=?
                      AND lcsc IS NOT NULL AND lcsc!='' GROUP BY 1 ORDER BY r DESC LIMIT 5''', (val,)).fetchall()
    if lc:
        print('作者用的立创料号（设计时的选择，不代表现在有货；库存价格查 jlc-basic-parts）：')
        for code, r3 in lc:
            print(f'  {r3:3d} 仓   {code}')
        print()

    co = c.execute('SELECT b, boards, lift FROM cooccur WHERE a=? ORDER BY lift DESC LIMIT 12', (val,)).fetchall()
    if co:
        print('常配什么（按 lift；lift>1 才算真的经常一起用）：')
        for b, bd, lf in co:
            flag = '  ← 样本太少，当线索不当结论' if bd < THIN else ''
            print(f'  {bd:3d} 板  lift {lf:7.1f}   {b}{flag}')
        print()

    bs = c.execute('''SELECT p.repo, r.stars, r.license, count(*) FROM part p JOIN repo r ON r.full_name=p.repo
                      WHERE COALESCE(p.value_norm, p.value)=? GROUP BY p.repo
                      ORDER BY r.stars DESC LIMIT 6''', (val,)).fetchall()
    if bs:
        print('用过它的参考板（按 star）：')
        for repo, st, li, n3 in bs:
            print(f'  ★{st:<6d} {repo:44s} 用了 {n3:3d} 个   {lic(li)}')
    print(f'\n{caveat(c)}')

def cmd_pins(c, val):
    print(f'# {val} 在真实板上的接法\n')
    rows = c.execute('''SELECT p.repo, p.board, p.ref, r.stars, r.license FROM part p
                        JOIN repo r ON r.full_name=p.repo
                        WHERE COALESCE(p.value_norm,p.value)=?
                          AND EXISTS (SELECT 1 FROM conn n WHERE n.repo=p.repo AND n.board=p.board AND n.ref=p.ref)
                        ORDER BY r.stars DESC LIMIT 3''', (val,)).fetchall()
    if not rows:
        print(f'没有「{val}」的接线记录。可能原因：型号写法不同（先 like），'
              f'或这颗器件所在的板不在已补网络的那批里。\n\n{caveat(c)}')
        return
    for repo, board, ref, st, li in rows:
        print(f'★{st} {repo} :: {board.split("/")[-1]} · {ref}   {lic(li)}')
        # KiCad 给未接引脚自动生成 unconnected-(U5-Pad7) 这种名字，没有信息，不印但要报数
        rows2 = c.execute('''SELECT pad, net FROM conn WHERE repo=? AND board=? AND ref=?
                             ORDER BY CAST(pad AS INTEGER), pad''', (repo, board, ref)).fetchall()
        shown = [(p_, n_) for p_, n_ in rows2 if not n_.startswith('unconnected-')]
        for p_, n_ in shown:
            print(f'    pad {p_:>4s} → {n_}')
        skipped = len(rows2) - len(shown)
        if skipped:
            print(f'    （另有 {skipped} 个引脚在这块板上没接）')
        print()
    print(caveat(c))

def cmd_boards(c, kw):
    print(f'# 含「{kw}」的参考板\n')
    rows = c.execute('''SELECT r.full_name, r.stars, r.license, r.board_count, r.descr,
                          (SELECT count(*) FROM part p WHERE p.repo=r.full_name) parts
                        FROM repo r WHERE r.board_count>0
                          AND (r.full_name LIKE ? OR r.descr LIKE ? OR r.topics LIKE ?)
                        ORDER BY r.stars DESC LIMIT 12''', (f'%{kw}%', f'%{kw}%', f'%{kw}%')).fetchall()
    if not rows:
        print('没有匹配。'); return
    for fn, st, li, bc, de, pt in rows:
        kinds = c.execute('SELECT kind, count(*) FROM label WHERE repo=? GROUP BY kind ORDER BY 2 DESC LIMIT 2', (fn,)).fetchall() if has_label(c) else []
        tag = ' · '.join(f'{k}' for k, _ in kinds) or '未标记'
        print(f'★{st:<6d} {fn}   [{tag}]')
        print(f'        板 {bc} 块 · 器件 {pt} 条 · {lic(li)}')
        if de:
            print('        ' + textwrap.shorten(de, 92))
    print(f'\n{caveat(c)}')

def cmd_stats(c):
    q = lambda s: c.execute(s).fetchone()[0]
    print('# 库的规模与口径\n')
    for lab, v in [('KiCad 候选仓', q("SELECT count(*) FROM repo WHERE found_by NOT LIKE 'easyeda:%'")),
                   ('KiCad 有板仓', q(f'SELECT count(DISTINCT repo) FROM board WHERE NOT {EDA}')),
                   ('KiCad 已解析板', q(f'SELECT count(*) FROM board WHERE parsed_at IS NOT NULL AND NOT {EDA}')),
                   ('嘉立创专业版仓', q(f'SELECT count(DISTINCT repo) FROM board WHERE {EDA}')),
                   ('嘉立创专业版板', q(f'SELECT count(*) FROM board WHERE parsed_at IS NOT NULL AND {EDA}')),
                   ('已解析板合计', q('SELECT count(*) FROM board WHERE parsed_at IS NOT NULL')),
                   ('器件条目', q('SELECT count(*) FROM part')),
                   ('已归一值', q('SELECT count(*) FROM part WHERE value_norm IS NOT NULL')),
                   ('共现对', q('SELECT count(*) FROM cooccur')//2),
                   ('已补网络板', q('SELECT count(*) FROM board WHERE nets_at IS NOT NULL')),
                   ('引脚连接', q('SELECT count(*) FROM conn')),
                   ('带立创料号器件', q("SELECT count(*) FROM part WHERE lcsc IS NOT NULL AND lcsc!=''"))]:
        print(f'  {lab:12s} {v:>9,}')
    print(f'\n{caveat(c)}')
    print('边界：本库只回答「参考什么」。落到嘉立创真实料号用 jlc-basic-parts；布线与 DRC 用 pcb-autoroute。')

def has_label(c):
    return c.execute("SELECT count(*) FROM sqlite_master WHERE name='label'").fetchone()[0] and \
           c.execute('SELECT count(*) FROM label').fetchone()[0] > 0

def label_note(c):
    n, = c.execute('SELECT count(*) FROM label').fetchone()
    b, = c.execute('SELECT count(*) FROM board WHERE parsed_at IS NOT NULL').fetchone()
    m = c.execute('SELECT model FROM label LIMIT 1').fetchone()
    return (f'标签：{n}/{b} 块已标（{m[0] if m else "?"}，Spark 本地模型）。'
            f'分类表固定 13 类，模型信息不够时标「不确定」。标签是模型判断，不是作者声明。')

def cmd_kinds(c, _=''):
    if not has_label(c):
        print('还没有标签。'); return
    print('# 各类别的板数（已排除 KiCad 官方测试夹具）\n')
    for k, n, r in c.execute(f'''SELECT l.kind, count(*), count(DISTINCT l.repo) FROM label l
                                 JOIN repo r ON r.full_name=l.repo WHERE {NOT_DESIGN}
                                 GROUP BY 1 ORDER BY 2 DESC'''):
        print(f'  {n:5d} 块 / {r:4d} 个仓   {k}')
    print(f'\n{label_note(c)}\n{caveat(c)}')

def cmd_kind(c, kind):
    if not has_label(c):
        print('还没有标签。'); return
    rows = c.execute(f'''SELECT l.repo, r.stars, r.license, count(*), group_concat(DISTINCT l.conf)
                          FROM label l JOIN repo r ON r.full_name=l.repo
                          WHERE l.kind LIKE ? AND {NOT_DESIGN}
                          GROUP BY l.repo ORDER BY r.stars DESC LIMIT 15''', (f'%{kind}%',)).fetchall()
    if not rows:
        opts = [k for (k,) in c.execute('SELECT DISTINCT kind FROM label')]
        print(f'没有「{kind}」。现有类别：{"、".join(opts)}'); return
    print(f'# 「{kind}」类参考板（按 star）\n')
    for repo, st, li, n, confs in rows:
        print(f'  ★{st:<6d} {repo:44s} {n:3d} 块 · 置信 {confs or "-"} · {lic(li)}')
    print(f'\n{label_note(c)}\n{caveat(c)}')

CMDS = {'like': cmd_like, 'part': cmd_part, 'pins': cmd_pins, 'boards': cmd_boards, 'kind': cmd_kind}
if __name__ == '__main__':
    a = sys.argv[1:]
    if not a or a[0] not in list(CMDS) + ['stats', 'kinds']:
        sys.exit(__doc__ + '\n用法：query.py {like|part|pins|boards|kind} <关键词>  |  query.py stats  |  query.py kinds')
    c = db()
    {'stats': cmd_stats, 'kinds': cmd_kinds}.get(a[0], lambda c, _='': CMDS[a[0]](c, ' '.join(a[1:])))(c)
