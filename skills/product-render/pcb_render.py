#!/usr/bin/env python3
"""KiCad 板 → 产品渲染图（哑光黑阻焊 + 沉金 + 白丝印）。只读源板。

两个引擎：
  kicad   kicad-cli 光追 + Pillow 合成背景。快（俯视约 36 s），金色偏黄、灯光不可细调。
  blender 副本导出 .pcb3d（pcb2blender）→ studio.py 用 Cycles 出图。慢，质感接近参考图。

用法：
  python3 pcb_render.py <board.kicad_pcb> <out_dir> [--engine kicad|blender] [--bg dark|light]
                        [--iso] [--samples 128] [--scale 100]
"""
import argparse, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get('PRODUCT_RENDER_CACHE', Path.home() / '.cache/product-render'))
KICAD_PY = os.environ.get('KICAD_PY', '/Applications/KiCad/KiCad.app/Contents/Frameworks/'
                          'Python.framework/Versions/Current/bin/python3')

# kicad 引擎用的近黑偏冷灰。blender 引擎必须用预设名 "Black"：pcb2blender 导出把 #RRGGBB
# 写成 0–255、导入按 0–1 读，自定义色一律变白（pcb2blender@2a2ac82 实测）。
MASK_KICAD, MASK_BLENDER = '"#16181CF0"', '"Black"'
SILK, FINISH = '"White"', '"ENIG"'
SILK_MAX_H, SILK_STROKE = 0.5, 0.08   # mm；参考图「整洁」主要靠小号统一丝印（目测）
BGS = {'dark': dict(bg=(0, 0, 0), tray=(44, 49, 58), edge=(62, 68, 80), shadow=0.6),
       'light': dict(bg=(226, 229, 234), tray=None, edge=None, shadow=0.35)}


def stackup_block(copper, mask):
    rows = [f'(layer "F.SilkS" (type "Top Silk Screen") (color {SILK}))',
            '(layer "F.Paste" (type "Top Solder Paste"))',
            f'(layer "F.Mask" (type "Top Solder Mask") (color {mask}) (thickness 0.01))']
    for i, cu in enumerate(copper):
        rows.append(f'(layer "{cu}" (type "copper") (thickness 0.035))')
        if i < len(copper) - 1:
            rows.append(f'(layer "dielectric {i+1}" (type "{"core" if i % 2 else "prepreg"}") '
                        f'(color "FR4 natural") (thickness 0.2) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))')
    rows += [f'(layer "B.Mask" (type "Bottom Solder Mask") (color {mask}) (thickness 0.01))',
             '(layer "B.Paste" (type "Bottom Solder Paste"))',
             f'(layer "B.SilkS" (type "Bottom Silk Screen") (color {SILK}))',
             f'(copper_finish {FINISH})', '(dielectric_constraints no)']
    return '(stackup ' + ' '.join(rows) + ')'


MODEL_LIB = Path(os.environ.get('KICAD_3DMODELS', '/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels'))
BODY_H = 0.85   # mm，缺模型时补的芯片体高度（QFN 常见 0.85–0.9）


def block_end(text, i):
    """text[i] 是 '('：返回与之配对的 ')' 之后的位置。"""
    depth = 0
    for j in range(i, len(text)):
        depth += {'(': 1, ')': -1}.get(text[j], 0)
        if depth == 0:
            return j + 1
    raise ValueError('括号不配对')


def resolve(path, board_dir):
    def env(m):
        k = m[1]
        if k == 'KIPRJMOD':
            return str(board_dir)
        if re.fullmatch(r'KICAD\d*_3DMODEL_DIR', k):
            return str(MODEL_LIB)
        return os.environ.get(k, m[0])
    return Path(re.sub(r'\$\{(\w+)\}', env, path))


def repo_find(board_dir, name):
    r = subprocess.run(['git', '-C', str(board_dir), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if r.returncode:
        return []
    root = r.stdout.strip()
    # 不加 --exclude-standard：3D 模型常被 .gitignore 掉（*.step），要连忽略的文件一起找
    r = subprocess.run(['git', '-C', root, 'ls-files', '-co', f'*/{name}', name],
                       capture_output=True, text=True)
    return [Path(root) / p for p in r.stdout.splitlines() if p]


def body_wrl(x0, y0, x1, y1, h):
    """黑色长方体 VRML。KiCad 模型单位是 0.1 inch，模型坐标 Y 朝上（封装坐标 Y 朝下，要取反）。"""
    u = 1 / 2.54
    xa, xb, ya, yb, zt = x0 * u, x1 * u, -y1 * u, -y0 * u, h * u
    v = [(xa, ya, 0), (xb, ya, 0), (xb, yb, 0), (xa, yb, 0), (xa, ya, zt), (xb, ya, zt), (xb, yb, zt), (xa, yb, zt)]
    faces = '0,3,2,1,-1,4,5,6,7,-1,0,1,5,4,-1,1,2,6,5,-1,2,3,7,6,-1,3,0,4,7,-1'
    pts = ', '.join(f'{a:.4f} {b:.4f} {c:.4f}' for a, b, c in v)
    return ('#VRML V2.0 utf8\nShape { appearance Appearance { material Material { '
            'diffuseColor 0.02 0.02 0.022 specularColor 0.05 0.05 0.05 shininess 0.1 } }\n'
            f'geometry IndexedFaceSet {{ coord Coordinate {{ point [ {pts} ] }} coordIndex [ {faces} ] }} }}\n')


def fill_missing_models(text, board_dir, dst_dir):
    """指了 3D 模型但文件找不到的器件，按 F.Fab/B.Fab 器件外形补一个黑色芯片体（只写渲染副本）。
    本来就没写 model 的件（测试点、FPC 焊盘）不碰。"""
    out, pos = [], 0
    for m in re.finditer(r'\(footprint "', text):
        if m.start() < pos:
            continue
        end = block_end(text, m.start())
        blk = text[m.start():end]
        mm = re.search(r'\(model "([^"]+)"', blk)
        if not mm or resolve(mm[1], board_dir).exists():
            continue
        ref = re.search(r'\(property "Reference" "([^"]+)"', blk)
        ref = ref[1] if ref else f'fp{m.start()}'
        ms = m.start() + mm.start()
        # 板子从别的目录复制来时 ${KIPRJMOD} 相对路径会断：先在同一 git 仓库里按文件名找，唯一命中就用
        same = repo_find(board_dir, Path(mm[1]).name)
        if len(same) == 1:
            out.append((ms, ms + len(mm[0]), f'(model "{same[0]}"'))
            print('MODEL_FOUND_IN_REPO', ref, same[0])
            continue
        pts = []
        for g in re.finditer(r'\(fp_(?:line|rect|poly)\s', blk):
            sub = blk[g.start():block_end(blk, g.start())]
            if re.search(r'\(layer "[FB]\.Fab"\)', sub):
                pts += [(float(a), float(b)) for a, b in re.findall(r'\((?:start|end|xy) (-?[\d.]+) (-?[\d.]+)\)', sub)]
        if not pts:
            print('MISSING_MODEL_NO_FAB', ref, mm[1])
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        wrl = dst_dir / f'fallback-{ref}.wrl'
        wrl.write_text(body_wrl(min(xs), min(ys), max(xs), max(ys), BODY_H))
        me =m.start() + block_end(blk, mm.start())
        out.append((ms, me, f'(model "{wrl}" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))'))
        print('FALLBACK_MODEL', ref, f'{max(xs)-min(xs):.2f}x{max(ys)-min(ys):.2f}mm', Path(mm[1]).name)
        pos = end
    for ms, me, rep in reversed(out):
        text = text[:ms] + rep + text[me:]
    return text


def styled_copy(src: Path, dst_dir: Path, mask: str) -> Path:
    """副本里写叠层颜色、缩丝印；源板不动。"""
    text = src.read_text(encoding='utf-8')
    if '(stackup' in text:
        text = re.sub(r'(\(type "(?:Top|Bottom) Solder Mask"\))\s*\(color "[^"]*"\)', rf'\1 (color {mask})', text)
        text = re.sub(r'(\(type "(?:Top|Bottom) Silk Screen"\))\s*\(color "[^"]*"\)', rf'\1 (color {SILK})', text)
        text = re.sub(r'\(copper_finish "[^"]*"\)', f'(copper_finish {FINISH})', text)
        if '(color' not in text.split('(stackup', 1)[1].split('(copper_finish', 1)[0]:
            # 叠层里本来没写颜色：在阻焊/丝印层上补
            text = re.sub(r'(\(type "(?:Top|Bottom) Solder Mask"\))', rf'\1 (color {mask})', text)
            text = re.sub(r'(\(type "(?:Top|Bottom) Silk Screen"\))', rf'\1 (color {SILK})', text)
    else:
        inner = sorted(set(re.findall(r'"(In\d+\.Cu)"', text)), key=lambda s: int(s[2:-3]))
        text = text.replace('(setup', '(setup\n\t\t' + stackup_block(['F.Cu'] + inner + ['B.Cu'], mask), 1)

    def shrink(m):
        w, h, t = float(m[1]), float(m[2]), float(m[3])
        k = min(1.0, SILK_MAX_H / max(w, h))
        return f'(size {w*k:.3f} {h*k:.3f}) (thickness {min(t, SILK_STROKE):.3f})'
    text = re.sub(r'\(size ([\d.]+) ([\d.]+)\)\s*\(thickness ([\d.]+)\)', shrink, text)
    text = fill_missing_models(text, src.parent, dst_dir)
    out = dst_dir / src.name
    out.write_text(text, encoding='utf-8')
    for f in src.parent.iterdir():   # 工程文件和 ${KIPRJMOD} 下的自建 3D 模型跟着走，否则那颗件只剩焊盘
        if f.suffix == '.kicad_pro' or (f.is_dir() and (any(f.glob('**/*.st*p')) or any(f.glob('**/*.wrl')))):
            (shutil.copytree if f.is_dir() else shutil.copy2)(f, dst_dir / f.name)
    return out


def kicad_render(board: Path, png: Path, iso: bool, w=2400, h=1800):
    cmd = ['kicad-cli', 'pcb', 'render', '-o', str(png), '-w', str(w), '-h', str(h),
           '--side', 'top', '--quality', 'high', '--background', 'transparent',
           '--use-board-stackup-colors', '--zoom', '0.70' if iso else '0.92',
           '--light-top', '0.55', '--light-camera', '0.15', '--light-side', '0.35',
           '--light-side-elevation', '35']
    if iso:
        cmd += ['--rotate', '-35,0,30', '--perspective']
    r = subprocess.run(cmd + [str(board)], capture_output=True, text=True)
    if r.returncode != 0 or not png.exists():
        sys.exit(f'kicad-cli render 失败 rc={r.returncode}\n{r.stderr[-2000:]}')


def compose(board_png: Path, out: Path, bg: str):
    from PIL import Image, ImageDraw, ImageFilter
    s = BGS[bg]
    pcb = Image.open(board_png).convert('RGBA')
    fw, fh = pcb.size
    bbox = pcb.getchannel('A').point(lambda a: 255 if a > 8 else 0).getbbox()
    if not bbox:
        sys.exit('渲染图全透明：板子没进画面')
    if bbox[0] <= 1 or bbox[1] <= 1 or bbox[2] >= fw - 1 or bbox[3] >= fh - 1:
        sys.exit(f'板子被画面裁切 bbox={bbox}，调小 --zoom')
    pcb = pcb.crop(bbox)
    W, H = 2400, 1350
    k = min(W * 0.62 / pcb.width, H * 0.70 / pcb.height)
    pcb = pcb.resize((int(pcb.width * k), int(pcb.height * k)), Image.LANCZOS)
    canvas = Image.new('RGBA', (W, H), s['bg'] + (255,))
    pad = int(min(pcb.size) * 0.09)
    x0, y0 = (W - pcb.width) // 2, (H - pcb.height) // 2
    if s['tray']:
        ImageDraw.Draw(canvas).rounded_rectangle(
            [x0 - pad, y0 - pad, x0 + pcb.width + pad * 3, y0 + pcb.height + pad],
            radius=pad, fill=s['tray'] + (255,), outline=s['edge'] + (255,), width=3)
    # 投影只取板子实体（alpha 二值化），半透明边会糊成块状条纹
    solid = pcb.getchannel('A').point(lambda v: 255 if v > 200 else 0)
    sh = Image.new('L', canvas.size, 0)
    sh.paste(solid, (x0 + pad // 6, y0 + pad // 4))
    sh = sh.filter(ImageFilter.GaussianBlur(pad // 2)).point(lambda v: int(v * s['shadow']))
    canvas = Image.composite(Image.new('RGBA', canvas.size, (0, 0, 0, 255)), canvas, sh)
    pcb.putalpha(solid)
    canvas.alpha_composite(pcb, (x0, y0))
    canvas.convert('RGB').save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('board', type=Path)
    ap.add_argument('out_dir', type=Path)
    ap.add_argument('--engine', choices=['kicad', 'blender'], default='kicad')
    ap.add_argument('--bg', choices=list(BGS), default='dark')
    ap.add_argument('--iso', action='store_true', help='kicad 引擎出斜视；blender 引擎总是俯视+斜视两张')
    ap.add_argument('--samples', type=int, default=128)
    ap.add_argument('--scale', type=int, default=100, help='分辨率百分比，调参用 40')
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    src = a.board.resolve()
    before = src.read_bytes()
    with tempfile.TemporaryDirectory(prefix='prender-') as td:
        td = Path(td)
        if a.engine == 'kicad':
            copy = styled_copy(src, td, MASK_KICAD)
            tag = 'iso' if a.iso else 'top'
            raw = td / 'raw.png'
            kicad_render(copy, raw, a.iso)
            out = a.out_dir / f'{src.stem}-{tag}-{a.bg}.png'
            compose(raw, out, a.bg)
            print('RENDERED', out)
        else:
            if not (CACHE / 'pcb2blender').is_dir():
                sys.exit(f'缺 pcb2blender：先跑 {HERE}/setup.sh')
            copy = styled_copy(src, td, MASK_BLENDER)
            pcb3d = td / f'{src.stem}.pcb3d'
            subprocess.run([KICAD_PY, str(HERE / 'pcb3d_export.py'), str(CACHE / 'pcb2blender'),
                            str(copy), str(pcb3d)], check=True)
            subprocess.run(['blender', '-b', '--factory-startup', '--python-exit-code', '1',
                            '--python', str(HERE / 'studio.py'), '--',
                            '--bg', a.bg, '--samples', str(a.samples), '--scale', str(a.scale),
                            '--name', src.stem, str(a.out_dir), str(pcb3d)], check=True)
    assert src.read_bytes() == before, '源板被改动'
    print('SOURCE_UNCHANGED', src)


if __name__ == '__main__':
    main()
