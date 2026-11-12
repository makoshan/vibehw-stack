#!/usr/bin/env python3
"""fab_facts.py 自检：在临时目录里现场造小 Gerber/钻孔/BOM/CPL，断言写法。

每条反例都和对照一起跑：对照（好文件）不报，反例（坏文件）必须报，两头都断言。
  python3 test_fab_facts.py      全过退出 0，打印通过条数
"""
import contextlib
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fab_facts as F  # noqa: E402

HEAD = '%FSLAX46Y46*%\n%MOMM*%\n%LPD*%\n%ADD10C,0.100000*%\nD10*\n'


def gbr(function, body):
    tf = f'%TF.FileFunction,{function}*%\n' if function else ''
    return tf + HEAD + body + 'M02*\n'


def um(v):
    return str(int(round(v * 1e6)))


def rect(x0, y0, x1, y1):
    return (f'X{um(x0)}Y{um(y0)}D02*\nX{um(x1)}Y{um(y0)}D01*\nX{um(x1)}Y{um(y1)}D01*\n'
            f'X{um(x0)}Y{um(y1)}D01*\nX{um(x0)}Y{um(y0)}D01*\n')


PASTE = 'X1000000Y1000000D03*\nX2000000Y1000000D03*\nX3000000Y1000000D03*\nG36*\nX5000000Y5000000D02*\nX6000000Y5000000D01*\nX6000000Y6000000D01*\nX5000000Y5000000D01*\nG37*\n'

PTH_MM = """M48
; #@! TF.FileFunction,Plated,1,4,PTH
METRIC
T1C0.300
T2C0.800
T3C0.600
%
G90
G05
T1
X12.7Y25.4
X15.24Y25.4
X17.78Y25.4
T2
X20.0Y10.0
T3
X30.0Y10.0G85X31.0Y10.0
G00X30.0Y20.0
M15
G01X31.5Y20.0
M16
G05
M30
"""

NPTH_MM = """M48
; #@! TF.FileFunction,NonPlated,1,4,NPTH
METRIC
T1C3.000
%
G90
T1
X3.0Y3.0
X37.0Y3.0
M30
"""

# 同一组镀铜孔，英寸、LZ（省末尾零）整数坐标；T1 = 0.0118 in = 0.29972 mm
PTH_INCH = """M48
;TYPE=PLATED
INCH,LZ,00.0000
T01C0.0118
T02C0.0315
%
T01
X005Y01
X006Y01
X007Y01
T02
X007874Y003937
M30
"""


def kicad_set(d, drop=()):
    files = {
        'b-F_Cu.gtl': gbr('Copper,L1,Top', rect(0, 0, 40, 30)),
        'b-In1_Cu.g1': gbr('Copper,L2,Inr', rect(0, 0, 40, 30)),
        'b-In2_Cu.g2': gbr('Copper,L3,Inr', rect(0, 0, 40, 30)),
        'b-B_Cu.gbl': gbr('Copper,L4,Bot', rect(0, 0, 40, 30)),
        'b-Edge_Cuts.gm1': gbr('Profile,NP', rect(0, 0, 40, 30)),
        'b-F_Paste.gtp': gbr('Paste,Top', PASTE),
        'b-B_Paste.gbp': gbr('Paste,Bot', 'X1000000Y1000000D03*\n'),
        'b-F_Silkscreen.gto': gbr('Legend,Top', rect(1, 1, 2, 2)),
        'b-PTH.drl': PTH_MM,
        'b-NPTH.drl': NPTH_MM,
        'b-job.gbrjob': '{}',
    }
    d.mkdir(parents=True, exist_ok=True)
    for n, t in files.items():
        if n not in drop:
            (d / n).write_text(t)
    return d


def easyeda_set(d, drop=()):
    """嘉立创EDA 命名、无 X2；PTH_Through 已含 Via 文件的孔。"""
    files = {
        'Gerber_TopLayer.GTL': gbr('', rect(0, 0, 40, 30)),
        'Gerber_InnerLayer1.G1': gbr('', rect(0, 0, 40, 30)),
        'Gerber_InnerLayer2.G2': gbr('', rect(0, 0, 40, 30)),
        'Gerber_BottomLayer.GBL': gbr('', rect(0, 0, 40, 30)),
        'Gerber_BoardOutlineLayer.GKO': gbr('', rect(0, 0, 40, 30)),
        'Gerber_TopPasteMaskLayer.GTP': gbr('', PASTE),
        'Gerber_TopAssemblyLayer.GTA': gbr('', rect(-50, -50, 90, 90)),
        'Drill_PTH_Through.DRL': ';TYPE=PLATED\n;Layer: PTH_Through\nM48\nMETRIC,LZ,0000.00000\nT01C0.30000\nT02C0.60000\n%\nG05\nG90\nT01\nX5.0Y-5.0\nX6.0Y-5.0\nT02\nX8.0Y-5.0G85X9.0Y-5.0\nM30\n',
        'Drill_PTH_Through_Via.DRL': ';TYPE=PLATED\n;Layer: PTH_Through_Via\nM48\nMETRIC,LZ,0000.00000\nT01C0.30000\n%\nG05\nG90\nT01\nX5.0Y-5.0\nX6.0Y-5.0\nM30\n',
        'Drill_NPTH_Through.DRL': ';TYPE=NON_PLATED\nM48\nMETRIC,LZ,0000.00000\nT01C1.70000\n%\nT01\nX3.95Y-4.25\nM30\n',
        'PCB下单必读.txt': '请看说明',
    }
    d.mkdir(parents=True, exist_ok=True)
    for n, t in files.items():
        if n not in drop:
            (d / n).write_text(t)
    return d


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a)


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def kicad_baseline(t):
    f = F.facts(kicad_set(t / 'k'))
    assert f['copper_layers'] == 4, f['copper_layers']
    assert f['problems'] == [], f['problems']
    assert (f['outline']['width_mm'], f['outline']['height_mm']) == (40.0, 30.0), f['outline']
    d = f['drill']
    assert d['min_pth_mm'] == 0.3 and d['pth_holes'] == 4 and d['npth_holes'] == 2, d
    assert d['pth_slots'] == 2 and d['npth_slots'] == 0, d          # G85 一条、M15…M16 一条
    assert f['paste_pads'] == {'top': 4, 'bottom': 1}, f['paste_pads']   # 3 闪光 + 1 区域


@case
def zip_same_as_dir(t):
    d = kicad_set(t / 'k')
    z = t / 'k.zip'
    with zipfile.ZipFile(z, 'w') as zf:
        for p in d.iterdir():
            zf.write(p, p.name)
    a, b = F.facts(d), F.facts(z)
    for k in ('copper_layers', 'outline', 'drill', 'paste_pads'):
        assert a[k] == b[k], (k, a[k], b[k])


@case
def drop_copper_kicad(t):
    """反例：删一层铜（X2 有 L 号）→ 层数 3 且报编号断档。"""
    good = F.facts(kicad_set(t / 'g'))
    bad = F.facts(kicad_set(t / 'b', drop=('b-In1_Cu.g1',)))
    assert good['copper_layers'] == 4 and not good['problems']
    assert bad['copper_layers'] == 3, bad['copper_layers']
    assert any('断档' in p and 'L2' in p for p in bad['problems']), bad['problems']


@case
def drop_copper_easyeda(t):
    """反例：嘉立创EDA 命名删 InnerLayer1 → 层数 3 且报内层断档。"""
    good = F.facts(easyeda_set(t / 'g'))
    bad = F.facts(easyeda_set(t / 'b', drop=('Gerber_InnerLayer1.G1',)))
    assert good['copper_layers'] == 4 and not good['problems'], good['problems']
    assert bad['copper_layers'] == 3
    assert any('断档' in p for p in bad['problems']), bad['problems']
    bad2 = F.facts(easyeda_set(t / 'b2', drop=('Gerber_BottomLayer.GBL',)))
    assert any('底层铜 0' in p for p in bad2['problems']), bad2['problems']


@case
def easyeda_dedupe(t):
    """嘉立创EDA：Via 文件的孔已在 PTH_Through 里，只算一次；装配层不当板框。"""
    f = F.facts(easyeda_set(t / 'e'))
    d = f['drill']
    assert d['pth_holes'] == 2 and d['duplicates_removed'] == 2, d
    assert d['pth_slots'] == 1 and d['npth_holes'] == 1 and d['min_npth_mm'] == 1.7, d
    assert (f['outline']['width_mm'], f['outline']['height_mm']) == (40.0, 30.0), f['outline']
    assert f['paste_pads']['top'] == 4


@case
def inch_drill(t):
    """反例：英寸钻孔。0.0118 in 必须换成 0.2997 mm，坐标 LZ 补末尾零后和公制同一个孔。"""
    m = kicad_set(t / 'mm')
    i = kicad_set(t / 'in', drop=('b-PTH.drl',))
    (i / 'b-PTH.drl').write_text(PTH_INCH)
    fm, fi = F.facts(m), F.facts(i)
    assert fm['drill']['min_pth_mm'] == 0.3
    assert abs(fi['drill']['min_pth_mm'] - 0.29972) < 1e-4, fi['drill']
    assert fi['drill']['pth_by_dia'] == {'0.300': 3, '0.800': 1}, fi['drill']['pth_by_dia']
    hs, _ = F.excellon(PTH_INCH, 'b-PTH.drl')
    assert (round(hs[0]['x'], 3), round(hs[0]['y'], 3)) == (12.7, 25.4), hs[0]
    assert (round(hs[3]['x'], 3), round(hs[3]['y'], 3)) == (20.0, 10.0), hs[3]
    # 两份同一组孔放一起要去重成同一批
    both = kicad_set(t / 'both')
    (both / 'b-PTH-inch.drl').write_text(PTH_INCH)
    fb = F.facts(both)
    assert fb['drill']['duplicates_removed'] == 4, fb['drill']


@case
def arc_outline(t):
    """反例：圆板。只取端点会量成 20×0，按真实弧要 20×20；顺逆时针方向要对。"""
    circle = 'G75*\nX10000000Y0D02*\nG03X-10000000Y0I-10000000J0D01*\nG03X10000000Y0I10000000J0D01*\n'
    d = kicad_set(t / 'c', drop=('b-Edge_Cuts.gm1',))
    (d / 'b-Edge_Cuts.gm1').write_text(gbr('Profile,NP', circle))
    f = F.facts(d)
    assert (f['outline']['width_mm'], f['outline']['height_mm']) == (20.0, 20.0), f['outline']
    # 半圆：G02 从 (10,0) 顺时针到 (-10,0) 经过 (0,-10)
    half = 'G75*\nX10000000Y0D02*\nG02X-10000000Y0I-10000000J0D01*\nG01X10000000Y0D01*\n'
    (d / 'b-Edge_Cuts.gm1').write_text(gbr('Profile,NP', half))
    o = F.facts(d)['outline']
    assert (o['y_min'], o['y_max'], o['height_mm']) == (-10.0, 0.0, 10.0), o
    # G74 单象限：I/J 不带符号，四分之一圆从 (10,0) 逆时针到 (0,10)，包络 0..10
    quarter = 'G74*\nX10000000Y0D02*\nG03X0Y10000000I10000000J0D01*\nG01X0Y0D01*\nX10000000Y0D01*\n'
    (d / 'b-Edge_Cuts.gm1').write_text(gbr('Profile,NP', quarter))
    o = F.facts(d)['outline']
    assert (o['x_min'], o['y_min'], o['x_max'], o['y_max']) == (0.0, 0.0, 10.0, 10.0), o
    # 圆角矩形（嘉立创EDA 的写法：FSLAX45、G02 带负 I/J）：圆角不外凸，包络 = 外框
    rr = ('%FSLAX45Y45*%\n%MOMM*%\n%ADD10C,0.15*%\nG75*\nG54D10*\n'
          'G01X4455000Y-3365000D02*\nG02X3995000Y-3825000I-460000J0D01*\nG01X605000Y-3825000D01*\n'
          'G02X145000Y-3365000I0J460000D01*\nG01X145000Y-635000D01*\nG02X605000Y-175000I460000J0D01*\n'
          'G01X3995000Y-175000D01*\nG02X4455000Y-635000I0J-460000D01*\nG01X4455000Y-3365000D01*\nM02*\n')
    (d / 'b-Edge_Cuts.gm1').write_text(rr)
    o = F.facts(d)['outline']
    assert (o['width_mm'], o['height_mm']) == (43.1, 36.5), o


@case
def bom_extra_ref(t):
    """反例：BOM 多一个位号 → 列出；CPL 重复、区间写法也要认。"""
    d = kicad_set(t / 'k')
    bom, cpl = t / 'bom.csv', t / 'cpl.csv'
    cpl.write_text('Designator,Mid X,Mid Y,Layer,Rotation\nR1,1mm,1mm,Top,0\nR2,2mm,1mm,Top,0\nC3,1mm,2mm,Top,0\nC4,1mm,3mm,Top,0\nC5,1mm,4mm,Top,0\n')
    bom.write_text('Comment,Designator,Footprint,LCSC Part #,Quantity\n10k,"R1,R2",0402,C25744,2\n100nF,C3-5,0402,C1525,3\n')
    good = F.facts(d, bom, cpl)
    assert good['refs']['bom_not_in_cpl'] == [] and good['refs']['cpl_not_in_bom'] == [] and not good['problems'], good['refs']
    bom.write_text('Comment,Designator,Footprint,LCSC Part #,Quantity\n10k,"R1,R2,R9",0402,C25744,3\n100nF,C3-5,0402,C1525,3\n')
    bad = F.facts(d, bom, cpl)
    assert bad['refs']['bom_not_in_cpl'] == ['R9'], bad['refs']
    assert any('R9' in p for p in bad['problems'])
    cpl.write_text(cpl.read_text() + 'R1,1mm,1mm,Top,0\nU7,5mm,5mm,Bottom,0\n')
    bad2 = F.facts(d, bom, cpl)
    assert bad2['refs']['cpl_dups'] == ['R1'] and bad2['refs']['cpl_not_in_bom'] == ['U7'], bad2['refs']
    # 嘉立创EDA 导出的 UTF-16 制表符表
    cpl.write_bytes('Designator\tMid X\nR1\t1\nR2\t1\nC3\t1\nC4\t1\nC5\t1\nR9\t1\n'.encode('utf-16'))
    ok = F.facts(d, bom, cpl)
    assert ok['refs']['bom_not_in_cpl'] == [] and ok['refs']['cpl_refs'] == 6, ok['refs']


@case
def exit_codes(t):
    d = kicad_set(t / 'k')
    assert quiet(F.main, [str(d)]) == 0
    bad = kicad_set(t / 'b', drop=('b-Edge_Cuts.gm1',))
    assert quiet(F.main, [str(bad)]) == 1
    assert quiet(F.main, [str(t / 'missing')]) == 2
    out = t / 'f.json'
    quiet(F.main, [str(d), '--out', str(out)])
    assert '"copper_layers": 4' in out.read_text()


@case
def review_gaps(t):
    """复核补的反例：刀具级镀铜、单段整圆、PTH/NPTH 同坐标、英寸 Gerber、G04 写的 X2、G04 注释带 %。"""
    # KiCad 合并钻孔：文件级 Plated，刀具级把 3.0 标成 NonPlated
    mixed = ('M48\n; #@! TF.FileFunction,MixedPlating,1,4\nMETRIC\n'
             '; #@! TA.AperFunction,Plated,PTH,ViaDrill\nT1C0.300\n'
             '; #@! TA.AperFunction,NonPlated,NPTH,ComponentDrill\nT2C3.000\n%\nG90\nT1\nX1Y1\nT2\nX5Y5\nX5Y5\nM30\n')
    hs, _ = F.excellon(mixed, 'b.drl')
    assert [h['plated'] for h in hs] == [True, False, False], hs     # 刀具级属性：T1 镀铜、T2 非镀铜
    d = kicad_set(t / 'm', drop=('b-PTH.drl', 'b-NPTH.drl'))
    (d / 'b-PTH.drl').write_text(PTH_MM)
    (d / 'b-NPTH.drl').write_text(NPTH_MM.replace('X3.0Y3.0', 'X12.7Y25.4').replace('T1C3.000', 'T1C0.300'))
    f = F.facts(d)
    assert f['drill']['npth_holes'] == 2 and f['drill']['pth_holes'] == 4, f['drill']   # 同坐标同孔径但镀铜性不同，不算重复
    # 单段 G75 整圆
    (d / 'b-Edge_Cuts.gm1').write_text(gbr('Profile,NP', 'G75*\nX10000000Y0D02*\nG03X10000000Y0I-10000000J0D01*\n'))
    o = F.facts(d)['outline']
    assert (o['width_mm'], o['height_mm']) == (20.0, 20.0), o
    # G74 起止重合 = 零长弧，不当整圆
    (d / 'b-Edge_Cuts.gm1').write_text(gbr('Profile,NP', rect(0, 0, 40, 30) + 'G74*\nX10000000Y0D02*\nG03X10000000Y0I10000000J0D01*\n'))
    o = F.facts(d)['outline']
    assert (o['width_mm'], o['height_mm']) == (40.0, 30.0), o
    # 英寸 Gerber：1.7 x 1.4 in = 43.18 x 35.56 mm
    inch = '%TF.FileFunction,Profile,NP*%\n%FSLAX24Y24*%\n%MOIN*%\n%ADD10C,0.01*%\nD10*\nX0Y0D02*\nX17000Y0D01*\nX17000Y14000D01*\nX0Y14000D01*\nX0Y0D01*\nM02*\n'
    (d / 'b-Edge_Cuts.gm1').write_text(inch)
    o = F.facts(d)['outline']
    assert (o['width_mm'], o['height_mm']) == (43.18, 35.56), o
    # G04 #@! 写的 X2：文件名看不出是铜层
    e = t / 'g04'
    e.mkdir()
    for n, fn in (('a.gbr', 'Copper,L1,Top'), ('b.gbr', 'Copper,L2,Bot')):
        (e / n).write_text(f'G04 #@! TF.FileFunction,{fn}*\n' + HEAD + rect(0, 0, 1, 1) + 'M02*\n')
    (e / 'c.gbr').write_text('G04 #@! TF.FileFunction,Profile,NP*\n' + HEAD + rect(0, 0, 10, 5) + 'M02*\n')
    f = F.facts(e)
    assert f['copper_layers'] == 2 and f['outline']['width_mm'] == 10.0, f
    # G04 注释里带 % 不能吞掉后面的 %FS
    (d / 'b-Edge_Cuts.gm1').write_text('G04 Scale: 100%, ok*\n' + gbr('Profile,NP', rect(0, 0, 40, 30)).replace('%FSLAX46Y46*%', '%FSLAX45Y45*%').replace('40000000', '4000000').replace('30000000', '3000000'))
    o = F.facts(d)['outline']
    assert (o['width_mm'], o['height_mm']) == (40.0, 30.0), o


@case
def review_gaps_2(t):
    """同文件叠孔照算；缺底层（X2）报；C6-C8 区间；DNP 行跳过；Gerber 钻孔、未定义刀具报问题；--out 写不了退出 2。"""
    d = kicad_set(t / 'k', drop=('b-PTH.drl',))
    (d / 'b-PTH.drl').write_text(PTH_MM.replace('X12.7Y25.4\n', 'X12.7Y25.4\nX12.7Y25.4\n'))
    f = F.facts(d)
    assert f['drill']['pth_holes'] == 5 and f['drill']['duplicates_removed'] == 0, f['drill']
    assert any('叠孔' in w for w in f['warnings']), f['warnings']
    bad = F.facts(kicad_set(t / 'nb', drop=('b-In2_Cu.g2', 'b-B_Cu.gbl')))
    assert bad['copper_layers'] == 2 and any('Bot' in p for p in bad['problems']), bad['problems']
    assert F.split_refs('C6-C8,R1') == ['C6', 'C7', 'C8', 'R1']
    assert F.split_refs('V4_C3-5') == ['V4_C3', 'V4_C4', 'V4_C5']
    bom, cpl = t / 'bom.csv', t / 'cpl.csv'
    bom.write_text('Comment,Designator,Footprint,Quantity\n10k,R1,0402,1\nDNP,R2,0402,1\n')
    cpl.write_text('Designator,Mid X\nR1,1\n')
    f = F.facts(d, bom, cpl)
    assert f['refs']['bom_not_in_cpl'] == [] and f['refs']['bom_dnp_lines'] == 1, f['refs']
    g = kicad_set(t / 'gd', drop=('b-PTH.drl', 'b-NPTH.drl'))
    (g / 'b-PTH-drl.gbr').write_text(gbr('Plated,1,4,PTH', 'X1000000Y1000000D03*\n'))
    assert any('Gerber 格式的钻孔' in p for p in F.facts(g)['problems'])
    (g / 'b-PTH.drl').write_text('M48\nMETRIC\nT1C0.3\n%\nT1\nX1Y1\nT9\nX2Y2\nM30\n')
    assert any('没在工具表' in p for p in F.facts(g)['problems'])
    assert quiet(F.main, [str(d), '--out', str(t / 'no' / 'such' / 'x.json')]) == 2


def main():
    passed = 0
    for fn in CASES:
        with tempfile.TemporaryDirectory(prefix='fab-facts-') as tmp:
            try:
                fn(Path(tmp))
            except AssertionError as e:
                print(f'不通过 {fn.__name__}: {e}')
                continue
        passed += 1
        print(f'通过 {fn.__name__}')
    print(f'{passed}/{len(CASES)} 通过')
    return 0 if passed == len(CASES) else 1


if __name__ == '__main__':
    sys.exit(main())
