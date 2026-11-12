import json, sys, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Heiti SC', 'Arial Unicode MS']
A, B = json.load(open('base.json')), json.load(open('after.json'))
COL = {'F.Cu': '#c0392b', 'B.Cu': '#2e6fd8', 'In1.Cu': '#b8860b', 'In2.Cu': '#2a9d4b'}
def draw(ax, d, L, box=None, title=''):
    segs = [[(t['a'][0]/1e6, t['a'][1]/1e6), (t['b'][0]/1e6, t['b'][1]/1e6)] for t in d['tracks'] if t['layer'] == L]
    ws = [t['w']/1e6 for t in d['tracks'] if t['layer'] == L]
    sc = 72 * (3.2 if box else 1.0) / 25.4 * (8 if box else 3)
    ax.add_collection(LineCollection(segs, linewidths=[w * sc for w in ws], colors=COL[L], capstyle='round'))
    for p in d['pads']:
        for poly in p['polys'].get(L, []):
            xs = [q[0]/1e6 for q in poly] + [poly[0][0]/1e6]; ys = [q[1]/1e6 for q in poly] + [poly[0][1]/1e6]
            ax.fill(xs, ys, color='#888', lw=0)
    for v in d['vias']:
        ax.add_patch(plt.Circle((v['p'][0]/1e6, v['p'][1]/1e6), v['d']/2e6, color='#444'))
    e = d['edge'][0]; ax.plot([q[0]/1e6 for q in e] + [e[0][0]/1e6], [q[1]/1e6 for q in e] + [e[0][1]/1e6], 'k-', lw=0.6)
    if box: ax.set_xlim(box[0], box[1]); ax.set_ylim(box[3], box[2])
    else: ax.autoscale(); ax.invert_yaxis()
    n = sum(1 for t in d['tracks'] if t['layer'] == L)
    ax.set_aspect('equal'); ax.set_xticks([]); ax.set_yticks([]); ax.set_title(f'{title}  {L}  {n} 段', fontsize=11)
for L in ['B.Cu', 'F.Cu', 'In1.Cu', 'In2.Cu']:
    fig, axs = plt.subplots(1, 2, figsize=(16, 7.4))
    draw(axs[0], A, L, title='整理前'); draw(axs[1], B, L, title='整理后')
    fig.tight_layout(); fig.savefig(f'cmp-{L}.png', dpi=110); plt.close(fig)
box = (8, 20, 18, 28)
fig, axs = plt.subplots(1, 2, figsize=(16, 7.4))
draw(axs[0], A, 'B.Cu', box, '整理前 局部'); draw(axs[1], B, 'B.Cu', box, '整理后 局部')
fig.tight_layout(); fig.savefig('cmp-zoom.png', dpi=110)
print('ok')
