//! route_grid.py 里 astar() 的 Rust 版，ctypes 调用，算法逐行对应 Python 版：
//! 3 层网格 8 邻域 A*，斜走不切角，换层打孔（孔位许可 + 同一路径孔到孔距离），
//! 启发是到终点外框的八方向距离，代价 = 步长 × 层代价 × (1 + 出口罚分)，打孔 = VIA_COST + 50 × 罚分。
//! 数值照搬 Python 版在 NumPy 2 下的实际行为：pen 是 float32，Python 浮点常量按 NEP 50 先转 float32，
//! 所以代价 g 是 float32；坐标来自 np.nonzero（int64），启发 h 是 numpy float64，f = g + h 升成 float64。
//! 这样两版出堆顺序一致，走出同一条路径。
//! 返回 0 找到路径 / 1 超时 / 2 无路径 / 3 输出缓冲不够。
//! astar_pf：协商拥堵布线（PathFinder）用，走线罚分按层给（[3][h][w]），打孔附加代价单独一张 [h][w]；
//! 其余与 astar 同一个内核。astar 的签名与数值不变（等价门照旧）。
use std::cmp::Ordering;
use std::collections::BinaryHeap;
use std::time::Instant;

#[derive(PartialEq)]
struct Node { f: f64, g: f32, l: usize, y: usize, x: usize }
impl Eq for Node {}
// BinaryHeap 是大顶堆；反过来比，和 Python heapq 的 (f, g, l, y, x) 元组同序出堆
impl Ord for Node {
    fn cmp(&self, o: &Self) -> Ordering {
        o.f.total_cmp(&self.f)
            .then(o.g.total_cmp(&self.g))
            .then(o.l.cmp(&self.l))
            .then(o.y.cmp(&self.y))
            .then(o.x.cmp(&self.x))
    }
}
impl PartialOrd for Node { fn partial_cmp(&self, o: &Self) -> Option<Ordering> { Some(self.cmp(o)) } }

#[no_mangle]
pub unsafe extern "C" fn astar(
    h: usize, w: usize,
    tok: *const u8, vok: *const u8, start: *const u8, goal: *const u8, pen: *const f32,
    layer_cost: *const f64, via_cost: f64, via_gap2: f64, gbox: *const i64, time_limit: f64, max_expand: u64,
    out: *mut i64, out_cap: usize, out_len: *mut usize, expanded: *mut u64,
) -> i32 {
    let pen = std::slice::from_raw_parts(pen, h * w);
    core(h, w, tok, vok, start, goal, |_l, k| pen[k], |k| 50.0f32 * pen[k],
         layer_cost, via_cost, via_gap2, gbox, time_limit, max_expand, out, out_cap, out_len, expanded)
}

#[no_mangle]
pub unsafe extern "C" fn astar_pf(
    h: usize, w: usize,
    tok: *const u8, vok: *const u8, start: *const u8, goal: *const u8,
    pen3: *const f32,   // [3][h][w] 走线罚分（乘在步长上）
    vpen: *const f32,   // [h][w] 打孔附加代价（加在 via_cost 上）
    layer_cost: *const f64, via_cost: f64, via_gap2: f64, gbox: *const i64, time_limit: f64, max_expand: u64,
    out: *mut i64, out_cap: usize, out_len: *mut usize, expanded: *mut u64,
) -> i32 {
    let hw = h * w;
    let pen3 = std::slice::from_raw_parts(pen3, 3 * hw);
    let vpen = std::slice::from_raw_parts(vpen, hw);
    core(h, w, tok, vok, start, goal, |l, k| pen3[l * hw + k], |k| vpen[k],
         layer_cost, via_cost, via_gap2, gbox, time_limit, max_expand, out, out_cap, out_len, expanded)
}

#[inline(always)]
unsafe fn core<P: Fn(usize, usize) -> f32, V: Fn(usize) -> f32>(
    h: usize, w: usize,
    tok: *const u8,     // [3][h][w] 可走线
    vok: *const u8,     // [h][w] 可打孔
    start: *const u8,   // [3][h][w] 起点格
    goal: *const u8,    // [3][h][w] 终点格
    pen: P,             // (层, y*w+x) → 走线罚分
    vpen: V,            // y*w+x → 打孔附加代价
    layer_cost: *const f64, // [3]
    via_cost: f64, via_gap2: f64,
    gbox: *const i64,   // 终点外框 x0 x1 y0 y1
    time_limit: f64,
    max_expand: u64,    // 扩展格数上限，0 = 只按墙钟；扫参/跨机器比结果时用它，结果与机器快慢无关
    out: *mut i64, out_cap: usize, out_len: *mut usize, expanded: *mut u64,
) -> i32 {
    let n = 3 * h * w;
    let tok = std::slice::from_raw_parts(tok, n);
    let vok = std::slice::from_raw_parts(vok, h * w);
    let start = std::slice::from_raw_parts(start, n);
    let goal = std::slice::from_raw_parts(goal, n);
    let lc = std::slice::from_raw_parts(layer_cost, 3);
    let gb = std::slice::from_raw_parts(gbox, 4);
    let hf = |x: usize, y: usize| -> f64 {
        let (x, y) = (x as i64, y as i64);
        let dx = (gb[0] - x).max(0).max(x - gb[1]) as f64;
        let dy = (gb[2] - y).max(0).max(y - gb[3]) as f64;
        dx.max(dy) + (2f64.sqrt() - 1.0) * dx.min(dy)
    };
    let idx = |l: usize, y: usize, x: usize| (l * h + y) * w + x;
    let mut g = vec![f32::INFINITY; n];
    let mut par = vec![-1i64; n];
    let mut lastv = vec![-1i64; n]; // 本路径上一颗孔的 y*w+x
    let mut pq = BinaryHeap::new();
    for l in 0..3 { for y in 0..h { for x in 0..w {
        if start[idx(l, y, x)] != 0 {
            g[idx(l, y, x)] = 0.0;
            pq.push(Node { f: hf(x, y), g: 0.0, l, y, x });
        }
    }}}
    let steps: [(i64, i64, f64); 8] = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
        (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)];
    let t0 = Instant::now();
    let mut cnt: u64 = 0;
    while let Some(Node { g: gc, l, y, x, .. }) = pq.pop() {
        let i = idx(l, y, x);
        if gc > g[i] { continue; }
        if goal[i] != 0 {
            // 回溯路径，起点在前
            let mut p = vec![i as i64];
            let mut c = i;
            while par[c] >= 0 { c = par[c] as usize; p.push(c as i64); }
            p.reverse();
            *expanded = cnt;
            if p.len() > out_cap { return 3; }
            std::ptr::copy_nonoverlapping(p.as_ptr(), out, p.len());
            *out_len = p.len();
            return 0;
        }
        cnt += 1;
        if (max_expand > 0 && cnt > max_expand) || (max_expand == 0 && cnt % 20000 == 0 && t0.elapsed().as_secs_f64() > time_limit) {
            *expanded = cnt;
            return 1;
        }
        let at = |l: usize, y: i64, x: i64| tok[idx(l, y as usize, x as usize)] != 0;
        for &(dx, dy, c) in &steps {
            let (nx, ny) = (x as i64 + dx, y as i64 + dy);
            if nx < 0 || ny < 0 || nx >= w as i64 || ny >= h as i64 || !at(l, ny, nx) { continue; }
            // 斜走要求两个直角邻格也可走，避免切角
            if dx != 0 && dy != 0 && !(at(l, y as i64, nx) && at(l, ny, x as i64)) { continue; }
            let (nxu, nyu) = (nx as usize, ny as usize);
            // Python: gc + c * LAYER_COST[l] * (1 + pen) —— c*LC 是 float64，其余在 float32 里算
            let ng: f32 = gc + ((c * lc[l]) as f32) * (1.0f32 + pen(l, nyu * w + nxu));
            let j = idx(l, nyu, nxu);
            if ng < g[j] {
                g[j] = ng; par[j] = i as i64; lastv[j] = lastv[i];
                pq.push(Node { f: ng as f64 + hf(nxu, nyu), g: ng, l, y: nyu, x: nxu });
            }
        }
        let lv = lastv[i];
        let gap_ok = lv < 0 || {
            let (vy, vx) = ((lv as usize / w) as f64, (lv as usize % w) as f64);
            (vy - y as f64).powi(2) + (vx - x as f64).powi(2) >= via_gap2
        };
        if vok[y * w + x] != 0 && gap_ok {
            for nl in 0..3 {
                if nl == l || tok[idx(nl, y, x)] == 0 { continue; }
                let ng: f32 = (gc + via_cost as f32) + vpen(y * w + x);
                let j = idx(nl, y, x);
                if ng < g[j] {
                    g[j] = ng; par[j] = i as i64; lastv[j] = (y * w + x) as i64;
                    pq.push(Node { f: ng as f64 + hf(x, y), g: ng, l: nl, y, x });
                }
            }
        }
    }
    *expanded = cnt;
    2
}
