#!/usr/bin/env python3
"""把渲染图和参考图的「质感」量成数字：金色像素占比与颜色、亮度分位数、近黑像素占比。

用法：python3 compare.py <参考图>... -- <渲染图>...
金色判据：R>G>B 且 R−B>45、R>110（参考图沉金实测落在 (174–226, 131–180, 64–78)）。
"""
import sys
import numpy as np
from PIL import Image


def stats(path):
    a = np.asarray(Image.open(path).convert('RGB')).astype(float)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    gold = (r - b > 45) & (r > g) & (g > b) & (r > 110)
    black = (a.max(-1) < 60) & (np.ptp(a, -1) < 15)
    gm = np.median(a[gold], 0).astype(int).tolist() if gold.sum() > 50 else None
    p = np.percentile(a.mean(-1), [5, 50, 95]).astype(int).tolist()
    return dict(gold_pct=round(gold.mean() * 100, 2), gold_rgb=gm, p5_p50_p95=p,
                black_pct=round(black.mean() * 100, 1))


if __name__ == '__main__':
    args = sys.argv[1:]
    cut = args.index('--') if '--' in args else len(args)
    for tag, files in (('参考', args[:cut]), ('渲染', args[cut + 1:])):
        for f in files:
            print(tag, f.rsplit('/', 1)[-1][:32].ljust(32), stats(f))
