"""出对方板的分层走线图（mm，y 向上），并可放大某区域。用法：python3 plot.py 输出.png [x0 x1 y0 y1]"""
import sys, math, load
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle
MM = 0.0254; zoom = [float(v) for v in sys.argv[2:6]] if len(sys.argv) > 5 else None
pads = load.pads_rect()
plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Heiti SC']
fig, axs = plt.subplots(2, 2, figsize=(16, 14), dpi=110)
col = {1: '#c0392b', 2: '#2e6fd8', 15: '#8e7d2a', 16: '#2a8e4a'}
for ax, L in zip(axs.flat, (1, 2, 15, 16)):
    ax.set_title(load.LAYER[L] + '（灰 = GND 铺铜，黑点 = 过孔）', fontsize=11); ax.set_aspect('equal')
    for net, l, poly in load.poured:
        if l == L and len(poly) > 2: ax.add_patch(Polygon([(x * MM, y * MM) for x, y in poly], closed=True, fc='#dddddd', ec='#bbbbbb', lw=.3))
    for p in pads:
        if p[8] in (L, 12):
            t = math.radians(p[7]); cs = [(p[3] + a * p[5] / 2 * math.cos(t) - b * p[6] / 2 * math.sin(t), p[4] + a * p[5] / 2 * math.sin(t) + b * p[6] / 2 * math.cos(t)) for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            ax.add_patch(Polygon([(x * MM, y * MM) for x, y in cs], closed=True, fc='#f0c060' if p[2] != 'GND' else '#90c090', ec='k', lw=.2))
    for l in load.lines:
        if l['layer'] == L: ax.plot([l['x1'] * MM, l['x2'] * MM], [l['y1'] * MM, l['y2'] * MM], color=col[L], lw=max(.4, l['w'] * MM * 6), solid_capstyle='round')
    for v in load.vias: ax.add_patch(Circle((v['x'] * MM, v['y'] * MM), v['d'] / 2 * MM, fc='k', ec='none'))
    o = load.outline[0][6]; ax.plot([0, o[0] * MM, o[0] * MM, 0, 0], [0, 0, -1437 * MM, -1437 * MM, 0], color='#666', lw=.6)
    if zoom: ax.set_xlim(zoom[0], zoom[1]); ax.set_ylim(zoom[2], zoom[3])
    else: ax.set_xlim(-1, 44.5); ax.set_ylim(-37.5, 1)
fig.tight_layout(); fig.savefig(sys.argv[1], facecolor='white'); print(sys.argv[1])
