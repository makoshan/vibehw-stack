#!/usr/bin/env python3
"""Wall-thickness check on the medial ridge, with a negative control.

    uv run --python 3.12 --with trimesh --with scipy --with numpy \
        wall_thickness.py new.stl [old.stl] [--limit 1.0] [--pitch 0.1]

Reports every region thinner than --limit, as (worst, median, area, centroid,
bbox). Pass the previous version as the second file: same ruler on both is the
only way to tell "fixed" from "was always there" from "I just broke it".

Why the ridge and not the obvious things:
  ray sampling   - a ray near a fillet exits on the neighbouring face and reports
                   a short "thickness" where material is thick. False positives.
  ball opening   - opening rounds sharp convex edges, so every outside edge leaves
                   residue and they all connect into one blob. False positives.
  medial ridge   - d = distance from an interior voxel to the outside. A real thin
                   wall's mid-surface is a ridge of local maxima of d, thickness =
                   2d. Into a convex corner d rises monotonically, so there is no
                   local maximum and a corner cannot fake a thin wall.

Two limits to carry into whatever you report:
  * This LOCATES, it does not MEASURE. Voxels bias thick by roughly half a pitch;
    measured values run 0.15-0.2mm under the reported ones at pitch 0.1. Confirm
    each hit by marching along the wall normal at 0.005mm before quoting a number.
  * It cannot see feather edges. Where two faces meet at a shallow angle d rises
    monotonically, so a knife edge has no ridge and is reported by construction.
    Check those separately with axis runs through the suspect junction.
"""
import argparse, json, sys, time
from pathlib import Path
import numpy as np, trimesh
from scipy import ndimage


def thin_regions(stl, limit, pitch, min_vox):
    m = trimesh.load(str(stl))
    if not m.is_watertight:
        print(f'warning: {stl} is not watertight; results are unreliable', file=sys.stderr)
    vg = m.voxelized(pitch=pitch)
    solid = ndimage.binary_fill_holes(vg.matrix)
    d = ndimage.distance_transform_edt(solid) * pitch
    ridge = solid & (d >= ndimage.maximum_filter(d, size=3) - 1e-9)
    thin = ridge & (d < limit / 2 - 1e-9)
    lab, n = ndimage.label(thin, np.ones((3, 3, 3)))
    out = []
    for i in range(1, n + 1):
        idx = np.argwhere(lab == i)
        if len(idx) < min_vox:
            continue
        pts = vg.indices_to_points(idx.astype(float))
        t = 2 * d[lab == i]
        out.append(dict(worst=round(float(t.min()), 2), median=round(float(np.median(t)), 2),
                        area=round(float(len(idx) * pitch ** 2), 2),
                        centroid=[round(float(c), 2) for c in pts.mean(0)],
                        bbox=[[round(float(c), 2) for c in pts.min(0)],
                              [round(float(c), 2) for c in pts.max(0)]]))
    return sorted(out, key=lambda c: (c['worst'], -c['area']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('stl', nargs='+', help='new mesh first, previous version second')
    ap.add_argument('--limit', type=float, default=1.0, help='minimum wall, mm')
    ap.add_argument('--pitch', type=float, default=0.1, help='voxel size, mm')
    ap.add_argument('--min-vox', type=int, default=20, help='ignore smaller ridges as noise')
    ap.add_argument('--json', help='write the full report here')
    a = ap.parse_args()
    report = {}
    for f in a.stl:
        t0 = time.time()
        cs = thin_regions(f, a.limit, a.pitch, a.min_vox)
        report[f] = cs
        print(f'\n{f}: {len(cs)} regions under {a.limit}mm ({time.time()-t0:.0f}s)')
        for c in cs:
            print(f"  {c['worst']:.2f}-{c['median']:.2f}mm {c['area']:7.2f}mm2 "
                  f"@{c['centroid']}  bbox {c['bbox']}")
    if len(a.stl) == 1:
        print('\nno negative control given: run the previous version too, or you '
              'cannot tell a fix from a pre-existing hit from a regression')
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1))
