"""链内局部重布：在链的外包框附近，用"少拐弯"的 A* 在 0/45/90° 网格上重新找一条路。
被 tidy.py 导入使用。障碍判定先用栅格粗筛（快），最后每段仍走 tidy 的精确检查 seg_ok2。"""
import heapq, math
import numpy as np
import shapely
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union
from shapely.prepared import prep

DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


def forbidden(net, L, w, bbox, ctx):
    """该层在 bbox 内、对线中心而言不能去的区域（别网铜/孔/板边/禁布区/同网别的线）。"""
    T = ctx
    win = box(*bbox)
    geoms = []
    tree, nets = T.static[L]
    for i in tree.query(win.buffer(w / 2 + T.CLR + T.MARGIN)):
        if nets[i] != net:
            geoms.append(tree.geometries[i].buffer(w / 2 + T.CLR + T.MARGIN, 8))
    for i in T.hole_tree.query(win.buffer(w / 2 + T.HOLE_CLR + T.MARGIN)):
        if not T.same_net_hole(i, net):
            geoms.append(T.hole_tree.geometries[i].buffer(w / 2 + T.HOLE_CLR + T.MARGIN, 8))
    tt, ts = T.ttrees[L]
    for i in tt.query(win.buffer(w + T.CLR + T.MARGIN + 200000)):
        t = ts[i]
        if t['id'] in T.CUR['ids']:
            continue
        g = tt.geometries[i]
        if t['net'] == net:
            geoms.append(g.buffer(w / 2 + t['w'] / 2, 8))      # 同网：不许碰（两端另放行）
        else:
            geoms.append(g.buffer(w / 2 + t['w'] / 2 + T.CLR + T.MARGIN, 8))
    for k in T.keep[L]:
        if k.intersects(win.buffer(w)):
            geoms.append(k.buffer(w / 2 + T.MARGIN, 8))
    out = win.difference(T.board.buffer(-(T.EDGE_CLR + w / 2 + T.MARGIN)))
    if geoms:
        out = out.union(unary_union(geoms))
    # 链两端各留一个小口，允许接上原来的线/焊盘/过孔
    for e in T.CUR['ends']:
        out = out.difference(Point(e).buffer(w * 1.5))
    return out


def astar(a, b, net, L, w, ctx, step=25000, pad=300000, bend=150000, max_nodes=400000):
    """a、b 为整数 nm 坐标。网格以 a 为原点、步长 step；终点：能用 octi 一跳精确到 b 的网格点。"""
    T = ctx
    x0, x1 = min(a[0], b[0]) - pad, max(a[0], b[0]) + pad
    y0, y1 = min(a[1], b[1]) - pad, max(a[1], b[1]) + pad
    nx, ny = int((x1 - x0) // step) + 3, int((y1 - y0) // step) + 3
    if nx * ny > max_nodes:
        return None
    ox = a[0] - math.ceil((a[0] - x0) / step) * step
    oy = a[1] - math.ceil((a[1] - y0) / step) * step
    xs = ox + np.arange(nx) * step
    ys = oy + np.arange(ny) * step
    bad = forbidden(net, L, w, (ox, oy, ox + nx * step, oy + ny * step), T)
    gx, gy = np.meshgrid(xs, ys, indexing='ij')
    free = ~shapely.contains_xy(bad, gx, gy)
    # 边的中点也要空（防止从两个格点之间穿过细障碍）
    ia, ja = int(round((a[0] - ox) / step)), int(round((a[1] - oy) / step))
    free[ia, ja] = True
    # 终点集合：离 b 不远、且 octi(格点, b) 精确检查通过的格点
    goals = {}
    ib, jb = (b[0] - ox) / step, (b[1] - oy) / step
    for di in range(-4, 5):
        for dj in range(-4, 5):
            i, j = int(round(ib)) + di, int(round(jb)) + dj
            if 0 <= i < nx and 0 <= j < ny and free[i, j]:
                p = [int(xs[i]), int(ys[j])]
                for cand in T.octi(p, list(b)):
                    if all(T.seg_ok2(cand[k], cand[k + 1], net, L, w, T.ttrees) for k in range(len(cand) - 1)):
                        goals[(i, j)] = cand
                        break
    if not goals:
        return None
    prepped = prep(bad)
    # 启发：到 b 的八方向距离
    def h(i, j):
        dx, dy = abs(xs[i] - b[0]), abs(ys[j] - b[1])
        return (max(dx, dy) - min(dx, dy)) + min(dx, dy) * math.sqrt(2)
    start = (ia, ja, -1)
    dist = {start: 0.0}
    prev = {}
    pq = [(h(ia, ja), 0.0, start)]
    end = None
    while pq:
        f, g, s = heapq.heappop(pq)
        if g > dist.get(s, 1e30):
            continue
        i, j, d0 = s
        if (i, j) in goals:
            end = s
            break
        for d, (dx, dy) in enumerate(DIRS):
            if d0 >= 0 and (d - d0) % 8 in (3, 4, 5):   # 不许急转（≥135° 的转向）
                continue
            ni, nj = i + dx, j + dy
            if not (0 <= ni < nx and 0 <= nj < ny) or not free[ni, nj]:
                continue
            mx, my = xs[i] + dx * step / 2, ys[j] + dy * step / 2
            if prepped.contains(Point(mx, my)):
                continue
            c = step * (math.sqrt(2) if dx and dy else 1) + (bend if d0 >= 0 and d != d0 else 0)
            ns = (ni, nj, d)
            ng = g + c
            if ng < dist.get(ns, 1e30):
                dist[ns] = ng
                prev[ns] = s
                heapq.heappush(pq, (ng + h(ni, nj), ng, ns))
    if end is None:
        return None
    path = []
    s = end
    while s in prev:
        path.append([int(xs[s[0]]), int(ys[s[1]])])
        s = prev[s]
    path.append([int(xs[s[0]]), int(ys[s[1]])])
    path.reverse()
    path[0] = list(a)
    tail = goals[(end[0], end[1])]
    return path + tail[1:]
