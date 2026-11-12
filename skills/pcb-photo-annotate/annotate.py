"""把 KiCad 设计坐标套到实物照片上，判朝向/正反面，并在照片上标焊盘名。

两步（两个 Python）：
  dump：KiCad 自带 Python 读 .kicad_pcb，导出焊盘与过孔的板坐标 JSON（pcbnew 自己算旋转，不手推）
    $KPY -B annotate.py dump board.kicad_pcb U1 > design.json
  draw：任意带 numpy + Pillow 的 Python，读 design.json + 配置，试 8 种对应、出标注图
    python -I annotate.py draw design.json config.json out.png

配置（像素都在 photo 这张图里量；照片先按 EXIF 摆正、裁好再量）：
  {"photo": "crop.jpg",
   "corners_px": [[x,y]*4],   # 封装四个角上的焊盘，沿板子一圈依次点，从哪个角、顺逆时针都行
   "vias_px": [[x,y],...],    # 照片里看得见的过孔（越多越稳，至少 5 个）
   "labels": {"2": "D1"},     # 可选：按焊盘号改字；默认用网络名，没网络写「空」
   "colors": {"GND": "#009600", "3V3": "#dc0000"},   # 可选：按显示的字着色并画圈
   "title": "...", "notes": ["..."], "scale": 1.6,
   "arrow": {"text": "USB-C 朝这头", "from": [x,y], "to": [x,y]}}   # 可选：板坐标 mm
"""
import json
import sys


def dump(pcb, ref):
    import pcbnew
    b = pcbnew.LoadBoard(pcb)
    mm = pcbnew.ToMM
    fp = b.FindFootprintByReference(ref)
    if fp is None:
        sys.exit(f'找不到位号 {ref}')
    pads = [{'num': p.GetNumber(), 'net': p.GetNetname(),
             'x': mm(p.GetPosition().x), 'y': mm(p.GetPosition().y)} for p in fp.Pads()]
    vias = [[mm(t.GetPosition().x), mm(t.GetPosition().y)] for t in b.GetTracks()
            if t.GetClass() == 'PCB_VIA']
    json.dump({'ref': ref, 'side': 'B' if fp.IsFlipped() else 'F', 'pads': pads, 'vias': vias},
              sys.stdout, ensure_ascii=False, indent=1)


def corner_pads(pads):
    # 板坐标 y 朝下，从正面看顺时针：左上、右上、右下、左下
    keys = (lambda p: p['x'] + p['y'], lambda p: -p['x'] + p['y'],
            lambda p: -p['x'] - p['y'], lambda p: p['x'] - p['y'])
    return [min(pads, key=k) for k in keys]


def fit(design, corners_px, vias_px):
    """8 种角对应各拟一次仿射，按「设计过孔投到照片后离最近可见过孔多远」打分。"""
    import numpy as np
    cs = corner_pads(design['pads'])
    if len({p['num'] for p in cs}) < 4:
        sys.exit('判不了：封装四角取到重复焊盘（单排或转 45°），换 ref')
    src = np.array([[p['x'], p['y'], 1] for p in cs])
    seen = np.array(vias_px, float)
    V = np.array([[x, y, 1] for x, y in design['vias']])
    out = []
    for rev in (False, True):
        order = cs[::-1] if rev else cs
        for k in range(4):
            o = order[k:] + order[:k]
            A = np.array([[p['x'], p['y'], 1] for p in o])
            T, *_ = np.linalg.lstsq(A, np.array(corners_px, float), rcond=None)
            # 每个可见过孔找最近的设计过孔：照片里漏看几个不扣分，看到的必须都对得上
            proj = V @ T
            err = float(np.mean([np.min(np.hypot(*(proj - s).T)) for s in seen]))
            mirrored = np.linalg.det(T[:2, :2]) < 0
            out.append({'T': T, 'err': err, 'mirrored': bool(mirrored),
                        'start': o[0]['num'], 'rev': rev})
    return sorted(out, key=lambda r: r['err'])


def draw(design_path, cfg_path, out_png):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    design = json.load(open(design_path))
    cfg = json.load(open(cfg_path))
    if len(cfg.get('vias_px', [])) < 5:
        sys.exit('判不了：可见过孔少于 5 个，补点后再跑')
    res = fit(design, cfg['corners_px'], cfg['vias_px'])
    best, second = res[0], res[1]
    print('对应（起始焊盘, 反向, 镜像）→ 平均偏差像素：')
    for r in res:
        print(f"  {r['start']:>4} {str(r['rev']):5} {str(r['mirrored']):5} → {r['err']:.1f}")
    # 判不了就说判不了：最好和次好差不到 2 倍，不出图
    if second['err'] < 2 * best['err']:
        sys.exit(f"判不了：最好 {best['err']:.1f} 与次好 {second['err']:.1f} 差不到 2 倍；多点几个过孔或换角")
    # pcbnew 坐标一律按从正面看；镜像 = 照片拍的是板子背面，与封装在哪面无关
    face = 'B' if best['mirrored'] else 'F'
    face_cn = '正面（F）' if face == 'F' else '背面（B）'
    print(f'照片是 {face_cn}，偏差 {best["err"]:.1f}，次好 {second["err"]:.1f}')

    k = cfg.get('scale', 1.6)
    im = ImageOps.exif_transpose(Image.open(cfg['photo'])).convert('RGB')
    im = im.resize((int(im.width * k), int(im.height * k)))
    pad = int(min(im.size) * 0.12)
    can = Image.new('RGB', (im.width + 2 * pad, im.height + 2 * pad), 'white')
    can.paste(im, (pad, pad))
    d = ImageDraw.Draw(can)
    fonts = ['/System/Library/Fonts/STHeiti Medium.ttc', '/System/Library/Fonts/PingFang.ttc',
             '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']
    fp = next((f for f in fonts if __import__('os').path.exists(f)), None)
    if fp is None:
        sys.exit('找不到中文字体：装 Noto CJK 或改 fonts 列表')
    F = lambda s: ImageFont.truetype(fp, s)
    T = best['T']
    P = lambda x, y: tuple((np.array([x, y, 1]) @ T) * k + pad)
    s = max(18, int(can.width / 55))
    cx = np.mean([p['x'] for p in design['pads']])
    cy = np.mean([p['y'] for p in design['pads']])
    colors, labels = cfg.get('colors', {}), cfg.get('labels', {})
    for p in design['pads']:
        name = labels.get(p['num']) or p['net'].lstrip('/') or '空'
        if name.startswith('unconnected-') or name.startswith('Net-('):
            name = '空'
        c = P(p['x'], p['y'])
        # 字往封装外侧放，免得压住别的焊盘
        vx, vy = p['x'] - cx, p['y'] - cy
        n = (vx * vx + vy * vy) ** .5 or 1
        t = P(p['x'] + vx / n * 4, p['y'] + vy / n * 4)
        col = colors.get(name, '#787878' if name == '空' else '#0046c8')
        if name == '空' or name in colors:
            r = s * 1.1
            d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], outline=col, width=max(3, s // 6))
        d.line([c, t], fill=col, width=max(2, s // 12))
        w = d.textlength(name, font=F(s))
        d.text((t[0] - w / 2, t[1] - s / 2), name, fill=col, font=F(s))
    a = cfg.get('arrow')
    if a:
        p0, p1 = P(*a['from']), P(*a['to'])
        d.line([p0, p1], fill='#e67800', width=max(4, s // 4))
        r = s * .6
        d.ellipse([p1[0] - r, p1[1] - r, p1[0] + r, p1[1] + r], fill='#e67800')
        w = d.textlength(a['text'], font=F(int(s * 1.3)))
        d.text((p0[0] - w / 2, p0[1] - s * 1.6), a['text'], fill='#e67800', font=F(int(s * 1.3)))
    d.text((s, s // 2), cfg.get('title', f"{design['ref']} 焊盘标注：照片是{face_cn}"), fill='black', font=F(int(s * 1.5)))
    notes = cfg.get('notes', []) + [f"依据：{len(design['vias'])} 个设计过孔套到照片，平均偏差 {best['err']:.0f} 像素；次好对应 {second['err']:.0f} 像素"]
    for i, line in enumerate(notes):
        d.text((s, can.height - (len(notes) - i) * s * 1.5 - s // 2), line, fill='black' if i < len(notes) - 1 else '#5a5a5a', font=F(s))
    can.save(out_png)
    print(out_png)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'dump' and len(sys.argv) == 4:
        dump(sys.argv[2], sys.argv[3])
    elif cmd == 'draw' and len(sys.argv) == 5:
        draw(*sys.argv[2:])
    else:
        sys.exit(__doc__)
