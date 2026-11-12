#!/usr/bin/env python3
# 来源：c08b-ba-54l-from-c08a-20261101/post_route_astar.py（另一会话），本会话改：盲埋孔版——扩 F/In2/In3/In4/B 五信号层 + 过孔按跨层放盲/埋/通孔（只挡跨的层，窄环里落孔位多）+ 预算 300k。
"""Island-to-Island 3-layer (F.Cu, In2.Cu, B.Cu) exact-DRC A* post-router matching .kicad_pro/.kicad_dru rules.
Upgrades for C08B-BA-54L:
1. Exact board polygon outline from b.GetBoardPolygonOutlines(outline, False) covering full [0..45.5, 0..39.5] mm coordinates.
2. Exact signal track radius (0.127/2 = 0.0635 mm) vs power track radius (0.150/2 = 0.075 mm) so 0.40 mm pitch QFN48 pads are not over-inflated.
3. Expanded A* search budget (MAX_VISITS = 300000) to connect long cross-corridor nets.
"""
import os, sys, json, math, heapq, shutil
from pathlib import Path
import pcbnew as k

src_pcb = Path(sys.argv[1])
drc_json = Path(sys.argv[2])
dst_pcb = Path(sys.argv[3])

b = k.LoadBoard(str(src_pcb))
k.ZONE_FILLER(b).Fill(b.Zones())
conn = b.GetConnectivity()
conn.Build(b)
drc = json.load(open(drc_json))

STEP = 0.04  # 40 um grid
bb = b.GetBoardEdgesBoundingBox()
X0, X1 = max(0.0, bb.GetLeft() / 1e6 - 0.5), bb.GetRight() / 1e6 + 0.5
Y0, Y1 = max(0.0, bb.GetTop() / 1e6 - 0.5), bb.GetBottom() / 1e6 + 0.5
NX = int(round((X1 - X0) / STEP)) + 1
NY = int(round((Y1 - Y0) / STEP)) + 1

LAYERS = [k.F_Cu, k.In2_Cu, k.In3_Cu, k.In4_Cu, k.B_Cu]  # 6层：In1 是整层 GND 平面不走信号，其余 5 层都能走
L_IDX = {k.F_Cu: 0, k.In2_Cu: 1, k.In3_Cu: 2, k.In4_Cu: 3, k.B_Cu: 4}
NL = len(LAYERS)

# 拆线重布（RIP=1）：别的网的未锁定信号线/孔算「软障碍」，可以压过但加罚分；走通后删掉被压的那几段，
# 本趟只补一条连接就停（这一条可能拆到好几个网），再由普通补线把被拆的网接回来（驱动见 rip_loop.sh，未连数不降就回退）
RIP = os.environ.get('RIP') == '1'
RIP_COST = float(os.environ.get('RIP_COST', '150'))
THRU = os.environ.get('THRU') == '1'  # 只用通孔(F-B全跨)，不加盲埋孔——fin19 全通孔工艺
# 射频匹配、晶振、AFE 供电、USB CC 的几何是定过的，不许拆（拆了会按 0.127 普通线重走）
# C08B BA (nRF52840) 真实网名（11-02）：差分对/晶振/射频/CC 不许拆
RIP_PROTECT = {"MCU_XL1", "MCU_XL2", "MCU_XC1", "MCU_XC2", "USB_DP", "USB_DM", "MCU_DP", "MCU_DM", "RF_ANT", "ANT_FEED", "CC1", "CC2"}
PWR_NETS = {"GND", "VBAT", "VBAT_SENSE", "VBUS", "3V3", "3V3_AFE", "AVDD_SENSE", "MCU_DCC", "MCU_DCC_L", "MCU_DEC1", "MCU_DEC2", "MCU_DEC3", "MCU_DEC4_6", "MCU_DEC5", "MCU_DECUSB"}

TRACK_W_SIG = 0.127
TRACK_W_PWR = 0.150
TRACK_R_SIG = TRACK_W_SIG / 2.0  # 0.0635 mm
TRACK_R_PWR = TRACK_W_PWR / 2.0  # 0.0750 mm
VIA_DIAM = 0.40
VIA_R = VIA_DIAM / 2.0           # 0.200 mm
VIA_DRILL = 0.30
VIA_DRILL_R = VIA_DRILL / 2.0    # 0.150 mm

CLR = 0.103                      # 0.10 mm min_clearance + 3 um margin
HOLE_CU_CLR = 0.153              # 0.15 mm min_hole_clearance + 3 um margin
HOLE_CLR = 0.253                 # 0.25 mm min_hole_to_hole + 3 um margin

VIA_CU_MARGIN = max(VIA_R + CLR, VIA_DRILL_R + HOLE_CU_CLR)  # 0.303 mm

def gx_of(x): return max(0, min(NX - 1, int(round((x - X0) / STEP))))
def gy_of(y): return max(0, min(NY - 1, int(round((y - Y0) / STEP))))
def x_of(gx): return X0 + gx * STEP
def y_of(gy): return Y0 + gy * STEP
def idx_of(gx, gy): return gx * NY + gy

NCELLS = NX * NY
board_keepout_trk = bytearray(NCELLS)
board_keepout_via = bytearray(NCELLS)

outline = k.SHAPE_POLY_SET()
b.GetBoardPolygonOutlines(outline, False)

edge_segs = []
for d in b.GetDrawings():
    if d.GetLayer() == k.Edge_Cuts and d.GetShape() == k.SHAPE_T_SEGMENT:
        s, e = d.GetStart(), d.GetEnd()
        edge_segs.append((s.x / 1e6, s.y / 1e6, e.x / 1e6, e.y / 1e6))

def pt_seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

for gx in range(NX):
    x = x_of(gx)
    ix = int(round(x * 1e6))
    for gy in range(NY):
        y = y_of(gy)
        idx = idx_of(gx, gy)
        if not outline.Contains(k.VECTOR2I(ix, int(round(y * 1e6)))):
            board_keepout_trk[idx] = 1
            board_keepout_via[idx] = 1

for x1, y1, x2, y2 in edge_segs:
    margin = 0.50
    gx0, gx1 = gx_of(min(x1, x2) - margin), gx_of(max(x1, x2) + margin)
    gy0, gy1 = gy_of(min(y1, y2) - margin), gy_of(max(y1, y2) + margin)
    for gx in range(gx0, gx1 + 1):
        x = x_of(gx)
        for gy in range(gy0, gy1 + 1):
            y = y_of(gy)
            d = pt_seg_dist(x, y, x1, y1, x2, y2)
            idx = idx_of(gx, gy)
            if d < 0.205 + TRACK_R_PWR:
                board_keepout_trk[idx] = 1
            if d < 0.205 + VIA_R:
                board_keepout_via[idx] = 1

# 标记器件与板级 RuleArea 禁布区 (J300, J400 等)
rule_zones = [z for z in b.Zones() if z.GetIsRuleArea()] + [z for f in b.GetFootprints() for z in f.Zones() if z.GetIsRuleArea()]
trk_net1 = [[0] * NCELLS for _ in range(NL)]
trk_net2 = [[0] * NCELLS for _ in range(NL)]
via_net1 = [[0] * NCELLS for _ in range(NL)]
via_net2 = [[0] * NCELLS for _ in range(NL)]

def mark_cell(n1, n2, idx, net):
    v1 = n1[idx]
    if v1 == 0:
        n1[idx] = net
    elif v1 != net or net == -1:
        n2[idx] = -1

for z in rule_zones:
    zbb = z.GetBoundingBox()
    x0_z, x1_z = zbb.GetLeft() / 1e6 - 0.35, zbb.GetRight() / 1e6 + 0.35
    y0_z, y1_z = zbb.GetTop() / 1e6 - 0.35, zbb.GetBottom() / 1e6 + 0.35
    gx0, gx1 = gx_of(x0_z), gx_of(x1_z)
    gy0, gy1 = gy_of(y0_z), gy_of(y1_z)
    no_trk = z.GetDoNotAllowTracks()
    no_via = z.GetDoNotAllowVias()
    zlays = [l for l, kl in enumerate(LAYERS) if z.IsOnLayer(kl)]
    for gx in range(gx0, gx1 + 1):
        ix = int(round(x_of(gx) * 1e6))
        for gy in range(gy0, gy1 + 1):
            pt = k.VECTOR2I(ix, int(round(y_of(gy) * 1e6)))
            idx = idx_of(gx, gy)
            if no_via and z.Outline().Collide(pt, int(round((VIA_R + 0.02) * 1e6))):
                board_keepout_via[idx] = 1
            if no_trk and z.Outline().Collide(pt, int(round((TRACK_R_PWR + 0.02) * 1e6))):
                for l in zlays:
                    mark_cell(trk_net1[l], trk_net2[l], idx, -1)

def add_hole_obstacle(cx, cy, r_hole, net, A=None):
    t1, t2, v1, v2 = A or (trk_net1, trk_net2, via_net1, via_net2)
    rad_hh = r_hole + HOLE_CLR + VIA_DRILL_R
    rad_hc = r_hole + HOLE_CU_CLR + TRACK_R_SIG
    rad_max = max(rad_hh, rad_hc)
    gx0, gx1 = gx_of(cx - rad_max), gx_of(cx + rad_max)
    gy0, gy1 = gy_of(cy - rad_max), gy_of(cy + rad_max)
    for gx in range(gx0, gx1 + 1):
        dx = x_of(gx) - cx
        for gy in range(gy0, gy1 + 1):
            d = math.hypot(dx, y_of(gy) - cy)
            idx = idx_of(gx, gy)
            if d <= rad_hh:
                for l in range(NL):
                    mark_cell(v1[l], v2[l], idx, -1)
            if d <= rad_hc:
                for l in range(NL):
                    mark_cell(t1[l], t2[l], idx, net)

def add_pad_obstacle(p):
    net = p.GetNetCode()
    if net <= 0:
        net = -1
    pos = p.GetPosition()
    cx, cy = pos.x / 1e6, pos.y / 1e6
    sz = p.GetSize()
    hx, hy = sz.x / 2e6, sz.y / 2e6
    ang = p.GetOrientation().AsRadians()
    ca, sa = math.cos(-ang), math.sin(-ang)
    drill = p.GetDrillSize()
    if drill.x > 0:
        r_hole = max(drill.x, drill.y) / 2e6
        add_hole_obstacle(cx, cy, r_hole, -1 if p.GetAttribute() == k.PAD_ATTRIB_NPTH else net)
        if p.GetAttribute() == k.PAD_ATTRIB_NPTH:
            return

    layers = [l for l, kl in enumerate(LAYERS) if p.IsOnLayer(kl)]
    if not layers:
        return
    m_trk = CLR + TRACK_R_SIG
    m_via = VIA_CU_MARGIN
    bound = math.hypot(hx + m_via, hy + m_via)
    gx0, gx1 = gx_of(cx - bound), gx_of(cx + bound)
    gy0, gy1 = gy_of(cy - bound), gy_of(cy + bound)
    for gx in range(gx0, gx1 + 1):
        dx = x_of(gx) - cx
        for gy in range(gy0, gy1 + 1):
            dy = y_of(gy) - cy
            lx = abs(dx * ca - dy * sa)
            ly = abs(dx * sa + dy * ca)
            ex = max(0.0, lx - hx)
            ey = max(0.0, ly - hy)
            dist = math.hypot(ex, ey)
            if dist <= m_via:
                idx = idx_of(gx, gy)
                for l in layers:
                    # 过孔盘与焊盘铜重叠＝盘中孔，同网也禁（Mako 定普通孔，不做盘中孔）
                    mark_cell(via_net1[l], via_net2[l], idx, -1 if dist <= VIA_R + 0.01 else net)
                    if dist <= m_trk:
                        mark_cell(trk_net1[l], trk_net2[l], idx, net)

def add_seg_obstacle(x1, y1, x2, y2, r, layers, net, A=None):
    t1, t2, v1, v2 = A or (trk_net1, trk_net2, via_net1, via_net2)
    m_trk = r + CLR + TRACK_R_SIG
    m_via = r + VIA_CU_MARGIN
    gx0, gx1 = gx_of(min(x1, x2) - m_via), gx_of(max(x1, x2) + m_via)
    gy0, gy1 = gy_of(min(y1, y2) - m_via), gy_of(max(y1, y2) + m_via)
    for gx in range(gx0, gx1 + 1):
        x = x_of(gx)
        for gy in range(gy0, gy1 + 1):
            y = y_of(gy)
            d = pt_seg_dist(x, y, x1, y1, x2, y2)
            if d <= m_via:
                idx = idx_of(gx, gy)
                for l in layers:
                    mark_cell(v1[l], v2[l], idx, net)
                    if d <= m_trk:
                        mark_cell(t1[l], t2[l], idx, net)

for f in b.GetFootprints():
    for p in f.Pads():
        add_pad_obstacle(p)

SOFT = tuple([[0] * NCELLS for _ in range(NL)] for _ in range(4)) if RIP else None
soft_items = []
for t in b.GetTracks():
    net = t.GetNetCode()
    if net <= 0:
        net = -1
    A = None
    if RIP and net > 0 and not t.IsLocked() and t.GetNetname() not in PWR_NETS | RIP_PROTECT:
        A = SOFT
        soft_items.append(t)
    if isinstance(t, k.PCB_VIA):
        pos = t.GetPosition()
        cx, cy = pos.x / 1e6, pos.y / 1e6
        r = t.GetWidth(k.F_Cu) / 2e6
        r_hole = t.GetDrillValue() / 2e6
        a_v, b_v = L_IDX.get(t.TopLayer(), 0), L_IDX.get(t.BottomLayer(), NL - 1)
        a_v, b_v = min(a_v, b_v), max(a_v, b_v)
        add_hole_obstacle(cx, cy, r_hole, net, A)
        add_seg_obstacle(cx, cy, cx, cy, r, list(range(a_v, b_v + 1)), net, A)
    else:
        kl = t.GetLayer()
        if kl in L_IDX:
            s, e = t.GetStart(), t.GetEnd()
            r = t.GetWidth() / 2e6
            add_seg_obstacle(s.x / 1e6, s.y / 1e6, e.x / 1e6, e.y / 1e6, r, [L_IDX[kl]], net, A)

def soft_hit(n1, n2, l, idx, net):
    v1 = n1[l][idx]
    return not (v1 == 0 or (v1 == net and n2[l][idx] == 0))

uuid_map = {}
for f in b.GetFootprints():
    for p in f.Pads():
        uuid_map[p.m_Uuid.AsString()] = p
for t in b.GetTracks():
    uuid_map[t.m_Uuid.AsString()] = t

def can_step_trk(l, idx, net):
    if board_keepout_trk[idx]:
        return False
    v1 = trk_net1[l][idx]
    return v1 == 0 or (v1 == net and trk_net2[l][idx] == 0)

def can_place_via(idx, net, a=0, bb=NL - 1):
    # 盲埋孔：只要求跨的那几层（a..bb）空；孔到孔互斥靠 board_keepout_via（放孔时全层标）
    if board_keepout_via[idx]:
        return False
    for l in range(a, bb + 1):
        v1 = via_net1[l][idx]
        if not (v1 == 0 or (v1 == net and via_net2[l][idx] == 0)):
            return False
    return True

def collect_island_nodes(seed_item, net):
    if seed_item is None:
        return {}
    visited_uids = set()
    q = [seed_item]
    visited_uids.add(seed_item.m_Uuid.AsString())
    items = []
    while q:
        cur = q.pop()
        items.append(cur)
        for nb in conn.GetConnectedTracks(cur):
            u = nb.m_Uuid.AsString()
            if u not in visited_uids:
                visited_uids.add(u)
                q.append(nb)
        for nb in conn.GetConnectedPads(cur):
            u = nb.m_Uuid.AsString()
            if u not in visited_uids:
                visited_uids.add(u)
                q.append(nb)

    nodes = {}
    def try_add_pt(l, px, py, r_search=2):
        gx, gy = gx_of(px), gy_of(py)
        for dx in range(-r_search, r_search + 1):
            for dy in range(-r_search, r_search + 1):
                ngx, ngy = gx + dx, gy + dy
                if 0 <= ngx < NX and 0 <= ngy < NY:
                    nidx = idx_of(ngx, ngy)
                    if can_step_trk(l, nidx, net):
                        nodes.setdefault((l, ngx, ngy), (px, py))

    for it in items:
        if isinstance(it, k.PCB_VIA):
            pos = it.GetPosition()
            px, py = pos.x / 1e6, pos.y / 1e6
            for l in range(NL):
                try_add_pt(l, px, py, r_search=2)
        elif isinstance(it, k.PCB_TRACK):
            kl = it.GetLayer()
            if kl in L_IDX:
                l = L_IDX[kl]
                s, e = it.GetStart(), it.GetEnd()
                x1, y1, x2, y2 = s.x / 1e6, s.y / 1e6, e.x / 1e6, e.y / 1e6
                length = math.hypot(x2 - x1, y2 - y1)
                nsteps = max(1, int(length / STEP))
                for kstep in range(nsteps + 1):
                    t = kstep / nsteps
                    try_add_pt(l, x1 + t * (x2 - x1), y1 + t * (y2 - y1), r_search=1)
        elif isinstance(it, k.PAD):
            pos = it.GetPosition()
            px, py = pos.x / 1e6, pos.y / 1e6
            for l, kl in enumerate(LAYERS):
                if it.IsOnLayer(kl):
                    try_add_pt(l, px, py, r_search=3)
    return nodes

DIRS = [
    (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
    (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142),
]

def astar_islands(starts_dict, goals_dict, net):
    if not starts_dict or not goals_dict:
        return None, f"empty(s={len(starts_dict)},g={len(goals_dict)})"
    g_list = list(goals_dict.keys())
    step_g = max(1, len(g_list) // 16)
    sampled_goals = [g_list[i] for i in range(0, len(g_list), step_g)]

    def h_func(gx, gy):
        best = 1e18
        for _, tx, ty in sampled_goals:
            dx, dy = abs(gx - tx), abs(gy - ty)
            d = (dx + dy) + (1.4142 - 2.0) * min(dx, dy)
            if d < best:
                best = d
        return best

    g_cost = {}
    parent = {}
    pq = []
    for s in starts_dict:
        if s in goals_dict:
            return [s], "already_touching"
        g_cost[s] = 0.0
        parent[s] = None
        h = h_func(s[1], s[2])
        heapq.heappush(pq, (h, 0.0, s))

    def via_too_close(c, cx, cy):
        lim = (VIA_DRILL + HOLE_CLR) / STEP              # 孔心距 ≥ 0.553 mm，按格数比
        while True:
            p = parent.get(c)
            if p is None:
                return False
            if p[0] != c[0] and math.hypot(p[1] - cx, p[2] - cy) < lim:
                return True
            if math.hypot(p[1] - cx, p[2] - cy) > lim + 2:
                return False
            c = p

    visited = 0
    MAX_VISITS = int(os.environ.get('MAX_VISITS', '300000'))
    while pq and visited < MAX_VISITS:
        f, g, cur = heapq.heappop(pq)
        if cur in goals_dict:
            path = []
            c = cur
            while c is not None:
                path.append(c)
                c = parent[c]
            path.reverse()
            return path, f"ok(v={visited})"
        if g > g_cost.get(cur, 1e18):
            continue
        visited += 1
        l, cx, cy = cur
        cidx = idx_of(cx, cy)

        for dx, dy, step_c in DIRS:
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < NX and 0 <= ny < NY):
                continue
            nidx = idx_of(nx, ny)
            if not can_step_trk(l, nidx, net):
                continue
            if dx != 0 and dy != 0:
                if not (can_step_trk(l, idx_of(cx + dx, cy), net) and can_step_trk(l, idx_of(cx, cy + dy), net)):
                    continue
            ng = g + step_c * (1.03 if l == 1 else 1.0)
            if RIP and soft_hit(SOFT[0], SOFT[1], l, nidx, net):
                ng += RIP_COST
            nxt = (l, nx, ny)
            if ng < g_cost.get(nxt, 1e18):
                g_cost[nxt] = ng
                parent[nxt] = cur
                heapq.heappush(pq, (ng + h_func(nx, ny), ng, nxt))

        for nl in range(NL):
            if nl == l or not can_step_trk(nl, cidx, net):
                continue
            a_s, b_s = (l, nl) if l < nl else (nl, l)
            if via_too_close(cur, cx, cy):                # 本路径刚放的孔还没登记进障碍，孔到孔 0.55 要自己查
                continue
            va, vb = (0, NL - 1) if THRU else (a_s, b_s)  # 通孔占全层栈，落孔位要全层空
            if not can_place_via(cidx, net, va, vb):
                continue
            ng = g + 4.0 + 3.0 * (b_s - a_s)             # 跨层越短越便宜，鼓励就近下内层
            if RIP and any(soft_hit(SOFT[2], SOFT[3], q, cidx, net) for q in range(a_s, b_s + 1)):
                ng += RIP_COST * 5
            nxt = (nl, cx, cy)
            if ng < g_cost.get(nxt, 1e18):
                g_cost[nxt] = ng
                parent[nxt] = cur
                heapq.heappush(pq, (ng + h_func(cx, cy), ng, nxt))
    return None, f"exhausted(v={visited},s={len(starts_dict)},g={len(goals_dict)})"

def _caps(t, hole=False):
    """线 / 孔都当胶囊体（两端点 + 半径，mm）"""
    if isinstance(t, k.PCB_VIA):
        p = t.GetPosition()
        r = (t.GetDrillValue() if hole else t.GetWidth(k.F_Cu)) / 2e6
        return (p.x / 1e6, p.y / 1e6, p.x / 1e6, p.y / 1e6, r)
    s_, e_ = t.GetStart(), t.GetEnd()
    return (s_.x / 1e6, s_.y / 1e6, e_.x / 1e6, e_.y / 1e6, t.GetWidth() / 2e6)

def _gap(a, c):
    """两胶囊体铜边距；线段相交时取 0"""
    ax1, ay1, ax2, ay2, ra = a
    cx1, cy1, cx2, cy2, rc = c
    def cross(ox, oy, px, py, qx, qy): return (px - ox) * (qy - oy) - (py - oy) * (qx - ox)
    d1, d2 = cross(cx1, cy1, cx2, cy2, ax1, ay1), cross(cx1, cy1, cx2, cy2, ax2, ay2)
    d3, d4 = cross(ax1, ay1, ax2, ay2, cx1, cy1), cross(ax1, ay1, ax2, ay2, cx2, cy2)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return -ra - rc
    d = min(pt_seg_dist(ax1, ay1, cx1, cy1, cx2, cy2), pt_seg_dist(ax2, ay2, cx1, cy1, cx2, cy2),
            pt_seg_dist(cx1, cy1, ax1, ay1, ax2, ay2), pt_seg_dist(cx2, cy2, ax1, ay1, ax2, ay2))
    return d - ra - rc

def rip_victims(new_items, net):
    """新路径压到的别网软障碍（铜距 0.1、孔到铜 0.15、孔到孔 0.25，多留 2 um），只拆这些段"""
    out = []
    for t in soft_items:
        if t.GetNetCode() == net:
            continue
        t_via = isinstance(t, k.PCB_VIA)
        for n in new_items:
            n_via = isinstance(n, k.PCB_VIA)
            share = any(t.IsOnLayer(L) and n.IsOnLayer(L) for L in LAYERS)
            hit = share and _gap(_caps(n), _caps(t)) < 0.102
            if n_via and not hit:
                hit = _gap(_caps(n, True), _caps(t)) < 0.152 or (t_via and _gap(_caps(n, True), _caps(t, True)) < 0.252)
            if t_via and not hit:
                hit = _gap(_caps(t, True), _caps(n)) < 0.152
            if hit:
                out.append(t)
                break
    return out

unc_list = []
for u in drc.get("unconnected_items", []):
    it0, it1 = u["items"][0], u["items"][1]
    p0, p1 = it0["pos"], it1["pos"]
    dist = math.hypot(p0["x"] - p1["x"], p0["y"] - p1["y"])
    is_gnd = 1 if "[GND]" in it0["description"] or "[GND]" in it1["description"] else 0
    unc_list.append((is_gnd, dist, it0, it1))
unc_list.sort(key=lambda x: (x[0], x[1]))

routed_count = 0
for _, dist, it0, it1 in unc_list:
    obj0 = uuid_map.get(it0["uuid"])
    obj1 = uuid_map.get(it1["uuid"])
    if obj0 is None or obj1 is None:
        continue
    net = obj0.GetNetCode() or obj1.GetNetCode()
    if net <= 0:
        continue
    netname = obj0.GetNetname() or obj1.GetNetname()
    if RIP and os.environ.get('RIP_NET') and netname != os.environ['RIP_NET']:
        continue
    if os.environ.get('ONLY_NET') and netname not in os.environ['ONLY_NET'].split(','):  # 只布指定网（逗号分隔）
        continue
    tw = TRACK_W_SIG
    tr = tw / 2.0

    starts_dict = collect_island_nodes(obj0, net)
    goals_dict = collect_island_nodes(obj1, net)
    path, info = astar_islands(starts_dict, goals_dict, net)
    if path is None:
        print(f"MISS ({dist:5.2f} mm, {info}): {it0['description']} <-> {it1['description']}")
        continue

    x0_exact, y0_exact = starts_dict[path[0]]
    x1_exact, y1_exact = goals_dict[path[-1]]
    pts = [(path[0][0], x0_exact, y0_exact)]
    for l, gx, gy in path:
        pts.append((l, x_of(gx), y_of(gy)))
    pts.append((path[-1][0], x1_exact, y1_exact))

    new_items = []
    i = 0
    while i < len(pts) - 1:
        l_cur, xa, ya = pts[i]
        l_nxt, xb, yb = pts[i + 1]
        if l_cur != l_nxt:
            a_s, b_s = (l_cur, l_nxt) if l_cur < l_nxt else (l_nxt, l_cur)
            if THRU:
                vt, oa, ob = k.VIATYPE_THROUGH, 0, NL - 1  # 通孔 F-B 全跨
            else:
                vt = k.VIATYPE_THROUGH if (a_s == 0 and b_s == NL - 1) else (k.VIATYPE_BLIND if (a_s == 0 or b_s == NL - 1) else k.VIATYPE_BURIED)
                oa, ob = a_s, b_s
            v = k.PCB_VIA(b)
            v.SetPosition(k.VECTOR2I(int(round(xa * 1e6)), int(round(ya * 1e6))))
            v.SetViaType(vt)
            v.SetLayerPair(LAYERS[oa], LAYERS[ob])
            v.SetWidth(int(round(VIA_DIAM * 1e6)))
            v.SetDrill(int(round(VIA_DRILL * 1e6)))
            v.SetNetCode(net)
            b.Add(v)
            new_items.append(v)
            add_hole_obstacle(xa, ya, VIA_DRILL_R, net)                        # 孔到孔仍全层互斥，不做同 xy 叠孔
            add_seg_obstacle(xa, ya, xa, ya, VIA_R, list(range(oa, ob + 1)), net)  # 通孔盘铜挡全层
            i += 1
        else:
            j = i + 1
            while j + 1 < len(pts) and pts[j + 1][0] == l_cur:
                dx1, dy1 = pts[j][1] - xa, pts[j][2] - ya
                dx2, dy2 = pts[j + 1][1] - pts[j][1], pts[j + 1][2] - pts[j][2]
                if abs(dx1 * dy2 - dy1 * dx2) < 1e-6 and (dx1 * dx2 + dy1 * dy2) >= 0:
                    j += 1
                else:
                    break
            xb, yb = pts[j][1], pts[j][2]
            if math.hypot(xb - xa, yb - ya) > 1e-4:
                t = k.PCB_TRACK(b)
                t.SetStart(k.VECTOR2I(int(round(xa * 1e6)), int(round(ya * 1e6))))
                t.SetEnd(k.VECTOR2I(int(round(xb * 1e6)), int(round(yb * 1e6))))
                t.SetWidth(int(round(tw * 1e6)))
                t.SetLayer(LAYERS[l_cur])
                t.SetNetCode(net)
                b.Add(t)
                new_items.append(t)
                add_seg_obstacle(xa, ya, xb, yb, tr, [l_cur], net)
            i = j

    for ni in new_items:
        uuid_map[ni.m_Uuid.AsString()] = ni
    conn.Build(b)
    routed_count += 1
    print(f"HIT  ({dist:5.2f} mm, {info}, steps={len(path)}): {it0['description']} <-> {it1['description']}")
    if RIP:
        for ni in new_items:                  # 拆线走通的线锁定，后面给被拆网拆线时不会把它再拆回去
            ni.SetLocked(True)
        victims = rip_victims(new_items, net)
        for t in victims:
            b.Remove(t)
        print(f"RIP {netname}: 拆 {len(victims)} 段 {sorted({t.GetNetname() for t in victims})}")
        break

print(f"Total Island-A* hits: {routed_count} / {len(unc_list)}")
k.ZONE_FILLER(b).Fill(b.Zones())
k.SaveBoard(str(dst_pcb), b)
for ext in (".kicad_pro", ".kicad_dru"):
    if src_pcb.with_suffix(ext).exists() and src_pcb.resolve() != dst_pcb.resolve():
        shutil.copyfile(src_pcb.with_suffix(ext), dst_pcb.with_suffix(ext))
