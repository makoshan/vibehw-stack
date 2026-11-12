#!/usr/bin/env python3
"""从制造文件本身量出下单要核的事实：铜层数、外形尺寸、钻孔、钢网焊盘数，以及 BOM/CPL 位号对不对得上。

思路借 Backplane `order/fabsrv/analysis.py:195-239`（board）和 `:487-515`（BOM 与坐标对位号）。
D20N 包里的 `gerber_read.py`/`check_fab.py` 只认 KiCad 子集、遇圆弧就报错，这里补上嘉立创EDA 命名、
圆弧包络、英寸钻孔、槽孔和跨文件去重。只用标准库，KiCad 自带的 Python 3.9 能跑。

  python3 fab_facts.py <gerber 目录或 zip> [--bom bom.csv] [--cpl cpl.csv] [--out facts.json]

退出码：0 = 量完且没发现问题；1 = 量完但有问题（缺外形、缺铜层、铜层编号断档、位号对不上等，逐条打印）；
2 = 参数或文件读不了。警告（如钻孔文件没标明镀铜）不影响退出码。

量法：
- 铜层：先看 X2 `TF.FileFunction,Copper,L<n>`（也认 `G04 #@! TF...` 写法），没有再按文件名/扩展名
  （KiCad：F_Cu/B_Cu/In<n>_Cu、.gtl/.gbl/.g<n>；嘉立创EDA：TopLayer/BottomLayer/InnerLayer<n>、.GTL/.GBL/.G<n>）。
- 外形：Profile / Edge_Cuts / BoardOutlineLayer / .gko/.gm1/.gml 上所有线段和圆弧的中心线包络；
  G02/G03 按圆心算真实弧（含跨过 0/90/180/270° 的极值点），G74 单象限按半径一致挑圆心。
- 钻孔：Excellon 的 METRIC/INCH、LZ/TZ 整数坐标、工具表（头里和正文里定义都认）；镀铜看
  文件级 X2/`;TYPE=`、刀具级 `TA.AperFunction`、文件名（PTH/NPTH）；G85 和 M15…M16 记槽孔。
  同一个孔（镀铜性、孔径、坐标都相同）出现在几个文件里只算一次——嘉立创EDA 的
  `PTH_Through.DRL` 已含 `PTH_Through_Via.DRL` 的全部过孔。
- 钢网焊盘：Paste 层的 D03 闪光数 + G36 区域数（嘉立创EDA 圆角焊盘画成区域）。
"""
import argparse
import csv
import io
import json
import math
import re
import sys
import zipfile
from pathlib import Path

GERBER_EXT = re.compile(r'\.(gbr|ger|pho|art|g[a-z0-9]{1,3}|gko|gm\d*|gml)$', re.I)
DRILL_EXT = re.compile(r'\.(drl|xln|exc|drd|nc|txt)$', re.I)


# ---------------------------------------------------------------- 读入

def load_files(src):
    """{相对名: 字节}；src 是目录或 zip。"""
    p = Path(src)
    out = {}
    if p.is_dir():
        for f in sorted(p.rglob('*')):
            if f.is_file():
                out[f.relative_to(p).as_posix()] = f.read_bytes()
    elif zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            for i in z.infolist():
                if not i.is_dir():
                    out[i.filename] = z.read(i)
    else:
        raise ValueError(f'{src} 不是目录也不是 zip')
    return out


def text_of(data):
    for enc in ('utf-8', 'gbk', 'latin-1'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode('latin-1')


def is_excellon(name, text):
    head = [ln.strip() for ln in text.splitlines()[:40]]
    if 'M48' in head:
        return True
    return bool(DRILL_EXT.search(name)) and any(re.match(r'T\d+C[\d.]+', s) for s in head)


# ---------------------------------------------------------------- Gerber

def x2_function(text):
    m = re.search(r'%TF\.FileFunction,([^*%]*)\*?%', text) or re.search(r'G04 #@! TF\.FileFunction,([^*]*)\*', text)
    return m.group(1).strip() if m else ''


def layer_role(name, function):
    """('copper', n) n=1 顶层、内层 1+k、底层 99 待定；('outline',)；('paste', 'top'|'bottom')；或 None。
    返回第三项说明依据：'X2' 或 '文件名'。"""
    f = function.lower()
    if f:
        if f.startswith('copper'):
            m = re.search(r'copper,l(\d+)', f)
            side = 'bot' if ',bot' in f else ('top' if ',top' in f else 'inr')
            return ('copper', int(m.group(1)) if m else None, side, 'X2')
        if f.startswith('profile'):
            return ('outline', None, None, 'X2')
        if f.startswith('paste'):
            return ('paste', None, 'bottom' if 'bot' in f else 'top', 'X2')
        return None
    base = name.rsplit('/', 1)[-1].lower()
    stem = base.rsplit('.', 1)[0]
    ext = base.rsplit('.', 1)[-1] if '.' in base else ''
    by = '文件名'
    if re.search(r'(^|[-_.])f[._]cu$', stem) or re.search(r'toplayer$', stem) or ext == 'gtl':
        return ('copper', 1, 'top', by)
    if re.search(r'(^|[-_.])b[._]cu$', stem) or re.search(r'bottomlayer$', stem) or ext == 'gbl':
        return ('copper', None, 'bot', by)
    m = re.search(r'in(\d+)[._]cu$', stem) or re.search(r'innerlayer(\d+)$', stem)
    if m:
        return ('copper', int(m.group(1)) + 1, 'inr', by)
    m = re.fullmatch(r'gp?(\d+)', ext)
    if m:
        return ('copper', int(m.group(1)) + 1, 'inr', by)
    if re.search(r'edge[._]cuts$', stem) or re.search(r'boardoutline(layer)?$', stem) or ext in ('gko', 'gm1', 'gml', 'gm'):
        return ('outline', None, None, by)
    if re.search(r'(^|[-_.])f[._]paste$', stem) or re.search(r'toppaste(mask)?layer$', stem) or ext == 'gtp':
        return ('paste', None, 'top', by)
    if re.search(r'(^|[-_.])b[._]paste$', stem) or re.search(r'bottompaste(mask)?layer$', stem) or ext == 'gbp':
        return ('paste', None, 'bottom', by)
    return None


def arc_bbox(sx, sy, ex, ey, cx, cy, cw, full=True):
    """圆弧的真实包络。cw=True 顺时针。起止重合时 full=True（G75）视为整圆，否则（G74）是零长弧。"""
    r = math.hypot(sx - cx, sy - cy)
    a0 = math.atan2(sy - cy, sx - cx)
    a1 = math.atan2(ey - cy, ex - cx)
    if math.hypot(sx - ex, sy - ey) < 1e-9:
        sweep = 2 * math.pi if full else 0.0
    elif cw:
        sweep = (a0 - a1) % (2 * math.pi)
    else:
        sweep = (a1 - a0) % (2 * math.pi)
    xs, ys = [sx, ex], [sy, ey]
    for k in range(4):
        q = k * math.pi / 2
        d = ((a0 - q) if cw else (q - a0)) % (2 * math.pi)
        if d <= sweep + 1e-12:
            xs.append(cx + r * math.cos(q))
            ys.append(cy + r * math.sin(q))
    return min(xs), min(ys), max(xs), max(ys)


class Gerber:
    """够用的 RS-274X：坐标格式与单位、D01/D02/D03、G01/G02/G03、G74/G75、G36/G37。"""

    def __init__(self, text):
        self.unit = 1.0
        self.int_dec = (3, 6)
        self.omit = 'L'
        self.flashes = 0
        self.regions = 0
        self.strokes = 0
        self.bbox = None
        self.warnings = []
        self._parse(text)

    def _num(self, s):
        neg = s.startswith('-')
        s = s.lstrip('+-')
        if '.' in s:
            v = float(s)
        else:
            i, d = self.int_dec
            if self.omit == 'T':
                s = s.ljust(i + d, '0')
            v = int(s) / 10 ** d if s else 0.0
        return (-v if neg else v) * self.unit

    def _grow(self, b):
        self.bbox = b if self.bbox is None else (min(self.bbox[0], b[0]), min(self.bbox[1], b[1]),
                                                 max(self.bbox[2], b[2]), max(self.bbox[3], b[3]))

    def _parse(self, text):
        x = y = 0.0
        interp, quadrant, last_op, in_region, contours = 1, 'multi', 2, False, 0
        text = re.sub(r'(?m)^\s*G0?4\b[^*]*\*', '', text)     # 先删注释：注释里的 % 会打乱 %…% 的切分
        for m in re.finditer(r'%([^%]*)%|([^%*]*)\*', text):
            ext, word = m.group(1), m.group(2)
            if ext is not None:
                for e in ext.split('*'):
                    e = e.strip()
                    if e.startswith('FS'):
                        f = re.search(r'FS([LTD]?)[AI]?X(\d)(\d)Y(\d)(\d)', e)
                        if f:
                            self.omit = f.group(1) or 'L'
                            self.int_dec = (int(f.group(2)), int(f.group(3)))
                    elif e.startswith('MOIN'):
                        self.unit = 25.4
                    elif e.startswith('MOMM'):
                        self.unit = 1.0
                    elif e[:2] in ('SR', 'AB') and len(e) > 2:     # 只有收尾的 %SR*%/%AB*% 不算
                        self.warnings.append(f'含 %{e[:2]}（阶梯重复/块孔径），焊盘数可能不准')
                continue
            w = (word or '').strip()
            if not w or w.startswith('G04') or w.startswith('M02') or w.startswith('M00'):
                continue
            while True:
                g = re.match(r'G0*(\d+)', w)
                if not g:
                    break
                n = int(g.group(1))
                if n in (1, 2, 3):
                    interp = n
                elif n == 74:
                    quadrant = 'single'
                elif n == 75:
                    quadrant = 'multi'
                elif n == 36:
                    in_region = True
                    contours = 0
                elif n == 37:
                    in_region = False
                    self.regions += max(contours, 1)
                elif n == 70:
                    self.unit = 25.4
                elif n == 71:
                    self.unit = 1.0
                w = w[g.end():]
            if re.fullmatch(r'D\d+', w) and int(w[1:]) >= 10:
                continue    # 选孔径
            if re.match(r'G54D\d+', w):
                continue
            c = dict(re.findall(r'([XYIJ])([+-]?[\d.]+)', w))
            d = re.search(r'D0*([123])$', w)
            if d:
                last_op = int(d.group(1))
            elif not c:
                continue
            nx = self._num(c['X']) if 'X' in c else x
            ny = self._num(c['Y']) if 'Y' in c else y
            if in_region and last_op == 2:
                contours += 1
            if last_op == 1 and not in_region:
                self.strokes += 1
            if last_op == 1:
                if interp in (2, 3):
                    i = self._num(c['I']) if 'I' in c else 0.0
                    j = self._num(c['J']) if 'J' in c else 0.0
                    cx, cy = self._center(x, y, nx, ny, i, j, interp == 2, quadrant)
                    self._grow(arc_bbox(x, y, nx, ny, cx, cy, interp == 2, quadrant == 'multi'))
                else:
                    self._grow((min(x, nx), min(y, ny), max(x, nx), max(y, ny)))
            elif last_op == 3 and not in_region:
                self.flashes += 1
            x, y = nx, ny

    @staticmethod
    def _center(x, y, nx, ny, i, j, cw, quadrant):
        if quadrant == 'multi':
            return x + i, y + j
        best = None
        for si in (1, -1):
            for sj in (1, -1):
                cx, cy = x + si * abs(i), y + sj * abs(j)
                r0, r1 = math.hypot(x - cx, y - cy), math.hypot(nx - cx, ny - cy)
                a0, a1 = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
                sweep = ((a0 - a1) if cw else (a1 - a0)) % (2 * math.pi)
                if sweep <= math.pi / 2 + 1e-6:
                    key = abs(r0 - r1)
                    if best is None or key < best[0]:
                        best = (key, cx, cy)
        return (best[1], best[2]) if best else (x + i, y + j)


# ---------------------------------------------------------------- Excellon

def excellon(text, name):
    """返回 (孔列表, 警告)。孔 = dict(dia, x, y, slot, plated)；plated 为 None 表示文件没说。"""
    unit, int_dec, zeros = 1.0, None, None
    tools, cur, holes, warns = {}, None, [], []
    low = name.rsplit('/', 1)[-1].lower()
    file_plated = None
    has_np = bool(re.search(r'(^|[^a-z])npth|non[-_ ]?plated|unplated', low))
    has_p = bool(re.search(r'(^|[^a-z])pth([^a-z]|$)|(^|[^n-])plated', low))
    if has_np != has_p:                           # 名字里两样都有（PTH_NPTH）就不猜
        file_plated = has_p
    tool_plated, pending_attr = {}, None
    x = y = 0.0
    route_from, routing, header = None, False, True

    def num(s):
        neg = s.startswith('-')
        s = s.lstrip('+-')
        if '.' in s:
            v = float(s)
        else:
            i, d = int_dec or ((2, 4) if unit != 1.0 else (3, 3))
            if zeros == 'LZ':        # 留前导零 = 省末尾零：从左边对齐
                s = s.ljust(i + d, '0')
            v = int(s) / 10 ** d if s else 0.0
        return (-v if neg else v) * unit

    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith(';'):
            u = s.upper().replace(' ', '')
            m = re.search(r'TF\.FILEFUNCTION,(NONPLATED|PLATED)', u)
            if m:
                file_plated = m.group(1) == 'PLATED'
            if u.startswith(';TYPE=NON_PLATED') or u.startswith(';TYPE=NPTH'):
                file_plated = False
            elif u.startswith(';TYPE=PLATED') or u.startswith(';TYPE=PTH'):
                file_plated = True
            m = re.search(r'TA\.APERFUNCTION,(NONPLATED|PLATED)', u)
            if m:
                pending_attr = m.group(1) == 'PLATED'
            continue
        u = s.upper()
        m = re.match(r'(METRIC|INCH)(?:,(LZ|TZ))?(?:,(0*)\.(0*))?', u)
        if m:
            unit = 25.4 if m.group(1) == 'INCH' else 1.0
            zeros = m.group(2) or zeros
            if m.group(3) is not None:
                int_dec = (len(m.group(3)), len(m.group(4)))
            continue
        if u in ('M71',):
            unit = 1.0
            continue
        if u in ('M72',):
            unit = 25.4
            continue
        if u in ('%', 'M95'):
            header = False
            continue
        m = re.match(r'T0*(\d+)((?:[FSBHZ][\d.]+)*)C([\d.]+)', u)
        if m:
            tools[m.group(1)] = float(m.group(3)) * unit
            if pending_attr is not None:
                tool_plated[m.group(1)] = pending_attr
                pending_attr = None
            if not header:
                cur = m.group(1)
            continue
        m = re.fullmatch(r'T0*(\d+)', u)
        if m:
            cur = m.group(1) if m.group(1) != '0' else None
            continue
        if u == 'M15':
            routing = True
            continue
        if u in ('M16', 'M17'):
            if routing and route_from is not None and cur:
                holes.append(dict(dia=tools.get(cur), x=route_from[0], y=route_from[1], x2=x, y2=y, slot=True,
                                  plated=tool_plated.get(cur, file_plated)))
            routing, route_from = False, None
            continue
        if u.startswith('R') and re.match(r'R\d+', u):
            warns.append(f'{name}: 有重复码 {u}，没展开，孔数可能偏少')
            continue
        g85 = re.match(r'(.*?)G85(.*)', u)
        if g85:
            c1 = dict(re.findall(r'([XY])([+-]?[\d.]+)', g85.group(1)))
            c2 = dict(re.findall(r'([XY])([+-]?[\d.]+)', g85.group(2)))
            x1 = num(c1['X']) if 'X' in c1 else x
            y1 = num(c1['Y']) if 'Y' in c1 else y
            x2 = num(c2['X']) if 'X' in c2 else x1
            y2 = num(c2['Y']) if 'Y' in c2 else y1
            if cur:
                holes.append(dict(dia=tools.get(cur), x=x1, y=y1, x2=x2, y2=y2, slot=True,
                                  plated=tool_plated.get(cur, file_plated)))
            x, y = x2, y2
            continue
        gm = re.match(r'G0*([0-3])(.*)', u)
        body = gm.group(2) if gm else u
        c = dict(re.findall(r'([XY])([+-]?[\d.]+)', body))
        if not c:
            continue
        nx = num(c['X']) if 'X' in c else x
        ny = num(c['Y']) if 'Y' in c else y
        if gm and gm.group(1) == '0':          # 进入铣槽前的定位
            route_from = (nx, ny)
        elif gm or routing:
            pass                                 # 铣槽轨迹中间点，M16 收尾时记一条槽
        elif cur:
            holes.append(dict(dia=tools.get(cur), x=nx, y=ny, slot=False, plated=tool_plated.get(cur, file_plated)))
        x, y = nx, ny
    if any(h['dia'] is None for h in holes):
        warns.append(f'未定义刀具:{name}: {sum(h["dia"] is None for h in holes)} 个孔用的刀具没在工具表里定义，没算进去')
    return [h for h in holes if h['dia'] is not None], warns


# ---------------------------------------------------------------- BOM / CPL

def read_table(path):
    data = Path(path).read_bytes()
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        t = data.decode('utf-16')
    elif data[:3] == b'\xef\xbb\xbf':
        t = data[3:].decode('utf-8')
    else:
        t = text_of(data)
    first = t.splitlines()[0] if t else ''
    delim = max([',', '\t', ';'], key=first.count)
    rows = list(csv.reader(io.StringIO(t), delimiter=delim))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise ValueError(f'{path} 是空表')
    head = [h.strip().lower() for h in rows[0]]
    for key in ('designator', '位号', 'reference', 'references', 'ref', 'refdes'):
        if key in head:
            k = head.index(key)
            return [(r[k] if k < len(r) else '', r) for r in rows[1:]]
    raise ValueError(f'{path} 表头里找不到位号列（Designator/位号/Reference）：{rows[0]}')


def split_refs(cell):
    out = []
    for tok in re.split(r'[,\s;]+', cell.strip()):
        if not tok:
            continue
        m = re.fullmatch(r'(.*?[A-Za-z_])(\d+)-(?:\1)?(\d+)', tok)     # 区间写法 C3-5、C3-C5
        if m and int(m.group(2)) <= int(m.group(3)) and int(m.group(3)) - int(m.group(2)) < 1000:
            out += [f'{m.group(1)}{n}' for n in range(int(m.group(2)), int(m.group(3)) + 1)]
        else:
            out.append(tok)
    return out


def dups(seq):
    seen, d = set(), []
    for r in seq:
        if r in seen and r not in d:
            d.append(r)
        seen.add(r)
    return d


def refs_check(bom, cpl):
    dnp = {'dnp', 'dni', 'nc', 'no fit', 'nofit', 'not fitted', '不贴', '不焊'}
    rows = read_table(bom)
    cells = [cell for cell, row in rows if not any(c.strip().lower() in dnp for c in row)]
    b = [r for cell in cells for r in split_refs(cell)]
    c = [cell.strip() for cell, _ in read_table(cpl) if cell.strip()]
    sb, sc = set(b), set(c)
    key = lambda r: (re.sub(r'\d+', '', r), int((re.findall(r'\d+', r) or ['0'])[-1]), r)
    return dict(bom_dnp_lines=len(rows) - len(cells), bom_lines=sum(1 for cell in cells if cell.strip()), bom_refs=len(sb), cpl_refs=len(sc),
                bom_not_in_cpl=sorted(sb - sc, key=key), cpl_not_in_bom=sorted(sc - sb, key=key),
                bom_dups=dups(b), cpl_dups=dups(c))


# ---------------------------------------------------------------- 汇总

def facts(src, bom=None, cpl=None):
    files = load_files(src)
    problems, warnings = [], []
    copper, outline, paste = {}, None, {'top': 0, 'bottom': 0}
    used = {'copper': [], 'outline': [], 'paste': [], 'drill': []}
    all_holes = []
    for name, data in files.items():
        text = text_of(data)
        if name.lower().endswith('.gbrjob'):
            continue
        if is_excellon(name, text):
            hs, w = excellon(text, name)
            problems += [x.split(':', 1)[1] for x in w if x.startswith('未定义刀具:')]
            warnings += [x for x in w if not x.startswith('未定义刀具:')]
            if any(h['plated'] is None for h in hs):
                warnings.append(f'{name}: 没标明镀铜/非镀铜，按镀铜算')
                for h in hs:
                    if h['plated'] is None:
                        h['plated'] = True
            for h in hs:
                h['file'] = name
            all_holes += hs
            used['drill'].append(name)
            continue
        if not (GERBER_EXT.search(name) and re.search(r'%FS|%MO|D0?[123]\*', text)):
            continue
        fn = x2_function(text)
        if re.match(r'(non)?plated', fn, re.I):
            problems.append(f'{name}: 是 Gerber 格式的钻孔文件，本脚本不量；请出 Excellon 钻孔再量')
            continue
        role = layer_role(name, fn)
        if not role:
            continue
        kind = role[0]
        g = Gerber(text)
        warnings += [f'{name}: {w}' for w in g.warnings]
        if kind == 'copper':
            copper[name] = role
            used['copper'].append(f'{name}（{role[3]}）')
        elif kind == 'outline':
            used['outline'].append(name)
            if g.bbox:
                outline = g.bbox if outline is None else (min(outline[0], g.bbox[0]), min(outline[1], g.bbox[1]),
                                                          max(outline[2], g.bbox[2]), max(outline[3], g.bbox[3]))
        elif kind == 'paste':
            used['paste'].append(name)
            paste[role[2]] += g.flashes + g.regions
            if g.strokes:
                warnings.append(f'{name}: 钢网层有 {g.strokes} 段画线（D01），没算进焊盘数')

    # 铜层：X2 给了 L 号就用；文件名推断的底层排最后
    n_cu = len(copper)
    nums = sorted(r[1] for r in copper.values() if r[1] is not None)
    if len(nums) != len(set(nums)):
        problems.append(f'铜层编号重复：{nums}')
    x2 = [r for r in copper.values() if r[3] == 'X2']
    if x2 and len(x2) == n_cu:
        want = set(range(1, max(nums) + 1)) if nums else set()
        sides = {r[1]: r[2] for r in copper.values()}
        if nums and (sides.get(1) != 'top' or sides.get(max(nums)) != 'bot'):
            problems.append(f'X2 里 L1 应是 Top、L{max(nums)} 应是 Bot，实际 L1={sides.get(1)}、L{max(nums)}={sides.get(max(nums))}：缺顶层或底层')
        if set(nums) != want:
            problems.append('铜层编号断档：有 ' + '、'.join(f'L{n}' for n in nums) + '，缺 ' + '、'.join(f'L{n}' for n in sorted(want - set(nums))))
    else:
        inner = sorted(r[1] - 1 for r in copper.values() if r[2] == 'inr' and r[1])
        if inner and inner != list(range(1, len(inner) + 1)):
            problems.append(f'内层编号断档：有内层 {inner}，缺 {sorted(set(range(1, max(inner) + 1)) - set(inner))}')
        tops = sum(r[2] == 'top' for r in copper.values())
        bots = sum(r[2] == 'bot' for r in copper.values())
        if n_cu and (tops != 1 or bots != 1):
            problems.append(f'顶层铜 {tops} 个、底层铜 {bots} 个（应各 1）')
    if n_cu == 0:
        problems.append('文件里没有铜层')
    if n_cu % 2 and n_cu > 1:
        problems.append(f'铜层数 {n_cu} 是奇数，可能缺一层')

    size = None
    if outline is None:
        problems.append('没有板框（Edge_Cuts / GKO / GM1 / Profile）')
    else:
        size = dict(width_mm=round(outline[2] - outline[0], 3), height_mm=round(outline[3] - outline[1], 3),
                    x_min=round(outline[0], 4), y_min=round(outline[1], 4), x_max=round(outline[2], 4), y_max=round(outline[3], 4))

    # 钻孔去重：镀铜性、孔径、起止坐标都相同的视为同一个孔
    seen, holes, dup, stacked = {}, [], 0, 0
    for h in all_holes:
        k = (h['plated'], round(h['dia'], 3), round(h['x'], 3), round(h['y'], 3),
             round(h.get('x2', h['x']), 3), round(h.get('y2', h['y']), 3), h['slot'])
        if k in seen and seen[k] != h['file']:
            dup += 1
            continue
        if k in seen:
            stacked += 1
        seen.setdefault(k, h['file'])
        holes.append(h)
    if stacked:
        warnings.append(f'同一钻孔文件里有 {stacked} 个孔和别的孔同位置同孔径（叠孔），照算')
    pth = [h for h in holes if h['plated'] and not h['slot']]
    npth = [h for h in holes if not h['plated'] and not h['slot']]
    pslot = [h for h in holes if h['plated'] and h['slot']]
    nslot = [h for h in holes if not h['plated'] and h['slot']]
    plated_all = pth + pslot
    drill = dict(pth_holes=len(pth), npth_holes=len(npth), pth_slots=len(pslot), npth_slots=len(nslot),
                 min_pth_mm=round(min(h['dia'] for h in pth), 4) if pth else None,
                 min_plated_incl_slots_mm=round(min(h['dia'] for h in plated_all), 4) if plated_all else None,
                 min_npth_mm=round(min(h['dia'] for h in npth + nslot), 4) if npth + nslot else None,
                 duplicates_removed=dup,
                 pth_by_dia={f'{d:.3f}': sum(1 for h in pth if round(h['dia'], 3) == d)
                             for d in sorted({round(h['dia'], 3) for h in pth})})
    if not used['drill']:
        warnings.append('没有钻孔文件')

    out = dict(source=str(src), copper_layers=n_cu,
               copper_files=sorted(used['copper']), outline=size, outline_files=sorted(used['outline']),
               drill=drill, drill_files=sorted(used['drill']),
               paste_pads=paste, paste_files=sorted(used['paste']))
    if bom or cpl:
        if not (bom and cpl):
            raise ValueError('核位号要同时给 --bom 和 --cpl')
        r = refs_check(bom, cpl)
        out['refs'] = r
        for key, label in (('bom_not_in_cpl', 'BOM 有而 CPL 没有'), ('cpl_not_in_bom', 'CPL 有而 BOM 没有'),
                           ('bom_dups', 'BOM 里重复'), ('cpl_dups', 'CPL 里重复')):
            if r[key]:
                problems.append(f'{label} {len(r[key])} 个：{", ".join(r[key])}')
    out['problems'], out['warnings'] = problems, warnings
    return out


def table(f):
    d, o = f['drill'], f['outline']
    rows = [('铜层数', f'{f["copper_layers"]}', '；'.join(f['copper_files']) or '—'),
            ('外形尺寸 (mm)', f'{o["width_mm"]:.2f} × {o["height_mm"]:.2f}' if o else '—', '，'.join(f['outline_files']) or '—'),
            ('最小镀铜孔 (mm)', f'{d["min_pth_mm"]:.3f}' if d['min_pth_mm'] is not None else '—',
             f'含槽 {d["min_plated_incl_slots_mm"]:.3f}' if d['min_plated_incl_slots_mm'] is not None else ''),
            ('镀铜孔数', str(d['pth_holes']), '按孔径 ' + '，'.join(f'{k}×{v}' for k, v in d['pth_by_dia'].items())),
            ('非镀铜孔数', str(d['npth_holes']), f'最小 {d["min_npth_mm"]:.3f}' if d['min_npth_mm'] is not None else ''),
            ('槽孔数', f'镀铜 {d["pth_slots"]} / 非镀铜 {d["npth_slots"]}', ''),
            ('跨文件重复孔（已去掉）', str(d['duplicates_removed']), '，'.join(f['drill_files'])),
            ('钢网焊盘 正/反', f'{f["paste_pads"]["top"]} / {f["paste_pads"]["bottom"]}', '，'.join(f['paste_files']) or '—')]
    if 'refs' in f:
        r = f['refs']
        rows += [('位号 BOM / CPL', f'{r["bom_refs"]} / {r["cpl_refs"]}', f'BOM {r["bom_lines"]} 行'),
                 ('BOM 有 CPL 无', str(len(r['bom_not_in_cpl'])), ', '.join(r['bom_not_in_cpl'])),
                 ('CPL 有 BOM 无', str(len(r['cpl_not_in_bom'])), ', '.join(r['cpl_not_in_bom'])),
                 ('重复位号 BOM / CPL', f'{len(r["bom_dups"])} / {len(r["cpl_dups"])}', ', '.join(r['bom_dups'] + r['cpl_dups']))]
    lines = [f'# 制造文件事实表：{f["source"]}', '', '| 项 | 量到的 | 依据 / 明细 |', '|---|---|---|']
    lines += [f'| {a} | {b} | {c} |' for a, b, c in rows]
    lines.append('')
    lines += [f'问题：{p}' for p in f['problems']] or ['问题：无']
    lines += [f'警告：{w}' for w in f['warnings']]
    lines.append('板厚、颜色、表面处理这类文件里没有的，按下单选项核。')
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src', help='Gerber 目录或 zip')
    ap.add_argument('--bom')
    ap.add_argument('--cpl')
    ap.add_argument('--out', help='写 JSON')
    a = ap.parse_args(argv)
    try:
        f = facts(a.src, a.bom, a.cpl)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        print('读不了：', e, file=sys.stderr)
        return 2
    print(table(f))
    if a.out:
        try:
            Path(a.out).write_text(json.dumps(f, ensure_ascii=False, indent=1) + '\n')
        except OSError as e:
            print('写不了 --out：', e, file=sys.stderr)
            return 2
    return 1 if f['problems'] else 0


if __name__ == '__main__':
    sys.exit(main())
