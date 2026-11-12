#!/usr/bin/env python3
"""只读专项布局检查；阈值由带来源的项目声明提供，不能据此放行制造。"""
import argparse
import hashlib
import heapq
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile


def number(rule, key, positive=False):
    value = rule[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(key + '必须为有限数字')
    if value < 0 or (positive and value == 0):
        raise ValueError(key + '范围错误')
    return value


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Layout:
    def __init__(self, board, out, cli=None):
        import wx
        self.app = wx.App(False)
        import pcbnew as k
        self.k = k
        project = board.with_suffix('.kicad_pro')
        if not project.is_file():
            raise ValueError('缺同名工程或工程无法加载；不能核定铺铜规则')
        cli = cli or shutil.which('kicad-cli') or '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli'
        # Python绑定没有暴露DRC规则引擎，不能保证.dru生效。
        # 用原生CLI对私有副本铺铜，加载结果后只读分析；源板永远不保存。
        with tempfile.TemporaryDirectory(prefix='layout-refill-') as temp:
            root = Path(temp).resolve()
            copy = root / board.name
            for p in [board, project, board.with_suffix('.kicad_dru')]:
                if p.exists():
                    shutil.copyfile(p, root / p.name)
            command = [str(cli), 'pcb', 'drc', '--format', 'json', '--refill-zones', '--save-board',
                       '--output', str(out / 'native-refill.json'), str(copy)]
            run = subprocess.run(command, capture_output=True, text=True, timeout=120)
            (out / 'native-refill.log').write_text(run.stdout + run.stderr)
            (out / 'native-refill-command.json').write_text(json.dumps(command, ensure_ascii=False) + '\n')
            if run.returncode != 0 or not (out / 'native-refill.json').is_file():
                raise ValueError('原生副本铺铜失败：退出码' + str(run.returncode))
            self.b = k.LoadBoard(str(copy))
        self.layers = list(self.b.GetEnabledLayers().CuStack())
        self.layer_names = {self.b.GetLayerName(l): l for l in self.layers}
        self.tracks = list(self.b.GetTracks())
        self.pads = [p for f in self.b.GetFootprints() for p in f.Pads()]
        self.items = self.tracks + self.pads
        self.nets = {n.GetNetname() for n in self.b.GetNetsByNetcode().values()}
        self.objects = []
        for item in self.items:
            for layer in self.layers:
                if item.IsOnLayer(layer):
                    self.objects.append((item.GetNetname(), layer, item.GetEffectiveShape(layer), self.describe(item)))
        for z in self.b.Zones():
            if z.GetIsRuleArea():
                continue
            for layer in self.layers:
                if z.IsOnLayer(layer):
                    shape = k.SHAPE_POLY_SET(z.GetFilledPolysList(layer))
                    if shape.OutlineCount():
                        self.objects.append((z.GetNetname(), layer, shape, self.describe(z)))
        # 无网络的铜线、铜字也会占天线区，不能只检查电气对象。
        graphics = list(self.b.GetDrawings())
        for f in self.b.GetFootprints():
            graphics.extend(list(f.GraphicalItems()))
            graphics.extend([f.Reference(), f.Value()])
        for g in graphics:
            if g.GetLayer() in self.layers:
                self.objects.append(('', g.GetLayer(), g.GetEffectiveShape(), self.describe(g)))

    def describe(self, item):
        k = self.k
        pos = item.GetPosition()
        result = {'uuid': item.m_Uuid.AsString(), 'kind': type(item).__name__,
                  'net': item.GetNetname() if hasattr(item, 'GetNetname') else '',
                  'xy_mm': [round(k.ToMM(pos.x), 6), round(k.ToMM(pos.y), 6)]}
        if type(item).__name__ == 'PAD':
            result.update(ref=item.GetParentFootprint().GetReference(), pad=item.GetNumber())
        return result

    def select(self, rule, key='nets'):
        nets = rule[key]
        if not isinstance(nets, list) or not nets or any(not isinstance(n, str) or not n for n in nets):
            raise ValueError(key + '必须为非空网名列表')
        missing = set(nets) - self.nets
        if missing:
            raise ValueError('网络不存在：' + ','.join(sorted(missing)))
        result = [o for o in self.objects if o[0] in nets]
        empty = set(nets) - {o[0] for o in result}
        if empty:
            raise ValueError('声明网络没有实际铜对象：' + ','.join(sorted(empty)))
        return result

    def layer(self, name):
        if name not in self.layer_names:
            raise ValueError('铜层不存在：' + name)
        return self.layer_names[name]

    def polygon(self, shape, inside=False):
        p = self.k.SHAPE_POLY_SET()
        shape.TransformToPolygon(p, self.k.FromMM(.001), self.k.ERROR_INSIDE if inside else self.k.ERROR_OUTSIDE)
        return p

    def union(self, shapes, inside=False):
        result = self.k.SHAPE_POLY_SET()
        for s in shapes:
            result.BooleanAdd(self.polygon(s, inside))
        return result

    def check(self, r):
        k = self.k
        kind = r['type']
        if kind == 'pending':
            return 'UNVERIFIED', {'reason': r['reason']}
        if kind == 'clearance':
            a, b = self.select(r, 'a'), self.select(r, 'b')
            if set(r['a']) & set(r['b']):
                raise ValueError('避让的两组网络不能重叠')
            if r.get('projection') not in ('all_layers', 'same_layer', 'adjacent_layers'):
                raise ValueError('projection必须明确为all_layers、same_layer或adjacent_layers')
            # adjacent_layers：同层或叠层里紧挨的两层（中间没有别的铜层，所以也没有平面隔开）。
            rank = {l: i for i, l in enumerate(self.layers)}
            clearance = k.FromMM(number(r, 'min_mm', True))
            hits = []
            for x in a:
                for y in b:
                    if r['projection'] == 'same_layer' and x[1] != y[1]:
                        continue
                    if r['projection'] == 'adjacent_layers' and abs(rank[x[1]] - rank[y[1]]) > 1:
                        continue
                    if x[2].Collide(y[2], clearance):
                        hits.append({'a': x[3], 'b': y[3], 'layers': [self.b.GetLayerName(x[1]), self.b.GetLayerName(y[1])]})
            return ('BLOCKED' if hits else 'PASS'), {'conflicts': hits, 'min_required_mm': r['min_mm']}
        if kind == 'trace_budget':
            self.select(r)
            ts = [t for t in self.tracks if t.GetNetname() in r['nets']]
            lines = [t for t in ts if type(t).__name__ != 'PCB_VIA']
            empty = set(r['nets']) - {t.GetNetname() for t in lines}
            if empty:
                raise ValueError('声明网络没有实际走线：' + ','.join(sorted(empty)))
            length = sum(k.ToMM(t.GetLength()) for t in lines)
            vias = sum(type(t).__name__ == 'PCB_VIA' for t in ts)
            allowed = [self.layer(x) for x in r['allowed_layers']]
            wrong = [self.describe(t) for t in lines if t.GetLayer() not in allowed]
            limit = number(r, 'max_length_mm', True)
            via_limit = number(r, 'max_vias')
            if int(via_limit) != via_limit:
                raise ValueError('max_vias必须为整数')
            return ('BLOCKED' if length > limit or vias > via_limit or wrong else 'PASS'), {
                'total_length_mm': round(length, 6), 'vias': vias, 'wrong_layer_items': wrong}
        if kind == 'route_bound':
            # 只走有明确铜连接的线/焊盘/孔，不把几何直线距离当布线长度。
            # 整段计长+每个桥接焊盘对角线，得到保守上界；不含地平面捷径。
            endpoints = []
            for ref, pin in r['pads']:
                matches = [p for p in self.pads if p.GetParentFootprint().GetReference() == ref and p.GetNumber() == pin]
                if len(matches) != 1:
                    raise ValueError('端点焊盘缺失或有多个：' + ref + '.' + pin)
                endpoints.append(matches[0])
            if len(endpoints) != 2 or endpoints[0].GetNetname() != endpoints[1].GetNetname():
                raise ValueError('route_bound需要同网两个端点')
            if endpoints[0].m_Uuid == endpoints[1].m_Uuid:
                raise ValueError('两个端点必须是不同焊盘')
            net = endpoints[0].GetNetname()
            if not net or net.startswith('unconnected-'):
                raise ValueError('端点未分配有效网络')
            items = [i for i in self.items if i.GetNetname() == net]
            shapes = [{l: i.GetEffectiveShape(l) for l in self.layers if i.IsOnLayer(l)} for i in items]
            start = next(j for j, i in enumerate(items) if i.m_Uuid == endpoints[0].m_Uuid)
            end = next(j for j, i in enumerate(items) if i.m_Uuid == endpoints[1].m_Uuid)
            costs = []
            for i in items:
                if type(i).__name__ == 'PCB_VIA':
                    costs.append(0.)
                elif type(i).__name__ == 'PAD':
                    box = i.GetBoundingBox()
                    costs.append(math.hypot(k.ToMM(box.GetWidth()), k.ToMM(box.GetHeight())))
                else:
                    costs.append(k.ToMM(i.GetLength()))
            graph = [[] for _ in items]
            for x in range(len(items)):
                for y in range(x):
                    if any(shapes[x][l].Collide(shapes[y][l], 0) for l in shapes[x].keys() & shapes[y].keys()):
                        graph[x].append(y)
                        graph[y].append(x)
            queue = [(costs[start], start)]
            dist, prev = {start: costs[start]}, {}
            while queue:
                d, x = heapq.heappop(queue)
                if d != dist[x]:
                    continue
                if x == end:
                    break
                for y in graph[x]:
                    nd = d + costs[y]
                    if nd < dist.get(y, math.inf):
                        dist[y], prev[y] = nd, x
                        heapq.heappush(queue, (nd, y))
            if end not in dist:
                return 'UNVERIFIED', {'reason': '没有显式铜路径；本检查不经铺铜平面寻路', 'net': net}
            route, x = [], end
            while True:
                route.append(self.describe(items[x]))
                if x == start:
                    break
                x = prev[x]
            return ('BLOCKED' if dist[end] > number(r, 'max_bound_mm', True) else 'PASS'), {
                'net': net, 'route_upper_bound_mm': round(dist[end], 6), 'route': route[::-1], 'includes_zone_paths': False}
        if kind == 'reserved_layer':
            layer = self.layer(r['layer'])
            hits = [self.describe(t) for t in self.tracks if type(t).__name__ != 'PCB_VIA' and t.GetLayer() == layer]
            return ('BLOCKED' if hits else 'PASS'), {'signal_tracks': hits, 'proves_ground_continuity': False}
        if kind == 'via_hole_in_pad':
            process = r['process']
            if process not in ('forbidden', 'filled_capped', 'unknown'):
                raise ValueError('未知via-in-pad工艺')
            hits = []
            for v in self.tracks:
                if type(v).__name__ != 'PCB_VIA':
                    continue
                probe = k.PCB_VIA(self.b)
                probe.SetPosition(v.GetPosition())
                probe.SetWidth(v.GetDrillValue())
                probe.SetLayerPair(k.F_Cu, k.B_Cu)
                for p in self.pads:
                    if p.GetAttribute() != k.PAD_ATTRIB_SMD:
                        continue
                    layers = [l for l in (k.F_Cu, k.B_Cu) if p.IsOnLayer(l) and v.IsOnLayer(l)]
                    if any(p.GetEffectiveShape(l).Collide(probe.GetEffectiveShape(l), 0) for l in layers):
                        hits.append({'via': self.describe(v), 'pad': self.describe(p)})
            status = 'PASS'
            if hits:
                status = 'BLOCKED' if process == 'forbidden' else 'UNVERIFIED'
            return status, {'intrusions': hits, 'declared_process': process,
                            'process_evidence': r.get('process_evidence'), 'proves_factory_acceptance': False}
        if kind == 'ground_projection':
            self.select(r)
            layer = self.layer(r['layer'])
            ground = r['ground_net']
            if ground not in self.nets:
                raise ValueError('参考地网缺失')
            lines = [t for t in self.tracks if t.GetNetname() in r['nets'] and type(t).__name__ != 'PCB_VIA']
            empty = set(r['nets']) - {t.GetNetname() for t in lines}
            if empty:
                raise ValueError('声明网络没有敏感走线：' + ','.join(sorted(empty)))
            corridor = self.union([t.GetEffectiveShape(t.GetLayer()) for t in lines])
            margin = number(r, 'margin_mm')
            if margin:
                corridor.Inflate(k.FromMM(margin), k.CORNER_STRATEGY_ROUND_ALL_CORNERS, k.FromMM(.001))
            area = corridor.Area() / k.FromMM(1) ** 2
            # 地铜取原生CLI重新铺铜后的实际多边形，孔洞也参与布尔差集。
            copper = self.union([o[2] for o in self.objects if o[0] == ground and o[1] == layer], inside=True)
            missing = k.SHAPE_POLY_SET(corridor)
            missing.BooleanSubtract(copper)
            uncovered = missing.Area() / k.FromMM(1) ** 2
            bounds = []
            for i in range(missing.OutlineCount()):
                box = missing.Outline(i).BBox()
                bounds.append([k.ToMM(box.GetX()), k.ToMM(box.GetY()), k.ToMM(box.GetWidth()), k.ToMM(box.GetHeight())])
            gap_status = r.get('gap_status', 'BLOCKED')
            if gap_status not in ('BLOCKED', 'UNVERIFIED'):
                raise ValueError('地铜缺口只能拦截或待审，不能豁免成通过')
            status = gap_status if uncovered > number(r, 'max_missing_mm2') else 'PASS'
            return status, {'corridor_area_mm2': area, 'missing_area_mm2': uncovered, 'missing_boxes_mm': bounds,
                            'polygon_error_mm': .001, 'proves_return_current_or_impedance': False}
        if kind == 'copper_keepout':
            coords = r['box_mm']
            if len(coords) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in coords):
                raise ValueError('净空框须为[x,y,width,height]')
            x, y, w, h = coords
            if w <= 0 or h <= 0:
                raise ValueError('净空框尺寸必须为正')
            shape = k.SHAPE_RECT(k.FromMM(x), k.FromMM(y), k.FromMM(w), k.FromMM(h))
            layers = {self.layer(l) for l in r['layers']}
            if not layers:
                raise ValueError('净空框铜层不能为空')
            hits = [dict(o[3], layer=self.b.GetLayerName(o[1])) for o in self.objects if o[1] in layers and o[2].Collide(shape, 0)]
            return ('BLOCKED' if hits else 'PASS'), {'copper_items': hits, 'proves_component_or_enclosure_clearance': False}
        raise ValueError('未实现的规则类型：' + kind)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('board', type=Path)
    ap.add_argument('rules', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--cli', help='可选：原生kicad-cli路径')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = {'status': 'UNVERIFIED', 'rules': [], 'manufacturing_ready': False, 'functional_ready': False,
              'scope': '只验证声明的几何规则；需另跑原生DRC/网表一致性', 'source_sha256': {}}
    paths = [args.board.resolve(), args.rules.resolve()]
    # 项目规则影响内存铺铜；如果同目录存在也须留哈希，不能悄悄变版。
    paths += [p.resolve() for p in [args.board.with_suffix('.kicad_pro'), args.board.with_suffix('.kicad_dru')] if p.exists()]
    try:
        result['source_sha256'] = {str(p): digest(p) for p in paths}
        result['implementation_sha256'] = digest(Path(__file__).resolve())
        custom = args.board.with_suffix('.kicad_dru').is_file()
        result['project_rule_validation'] = {
            'status': 'UNVERIFIED' if custom else 'NOT_PRESENT',
            'reason': '原生CLI可能静默跳过未知自定义约束；此入口不验证.dru语法' if custom else '无同名自定义.dru'}
        manifest = json.loads(args.rules.read_text())
        if manifest.get('version') != 1 or not isinstance(manifest.get('rules'), list) or not manifest['rules']:
            raise ValueError('需要version=1且规则非空')
        ids = [r['id'] for r in manifest['rules']]
        if len(set(ids)) != len(ids):
            raise ValueError('规则id重复')
        layout = Layout(args.board.resolve(), args.out.resolve(), args.cli)
        result['kicad_version'] = layout.k.Version()
        for r in manifest['rules']:
            entry = {'id': r['id'], 'type': r.get('type'), 'source': r.get('source'), 'basis': r.get('basis')}
            try:
                if not isinstance(r.get('source'), str) or not r['source'].strip():
                    raise ValueError('每条规则必须有来源')
                if r.get('basis') not in ('project_policy', 'datasheet', 'factory', 'unverified'):
                    raise ValueError('basis必须明确为项目策略/手册/工厂/未核定')
                entry['status'], entry['details'] = layout.check(r)
            except Exception as e:
                entry.update(status='UNVERIFIED', reason=str(e))
            result['rules'].append(entry)
        states = [r['status'] for r in result['rules']]
        if custom:
            states.append('UNVERIFIED')
        result['status'] = 'BLOCKED' if 'BLOCKED' in states else 'UNVERIFIED' if 'UNVERIFIED' in states else 'DECLARED_LAYOUT_ONLY_PASS'
    except Exception as e:
        result['error'] = str(e)
    result['source_unchanged'] = bool(result['source_sha256']) and all(p.exists() and digest(p) == result['source_sha256'].get(str(p)) for p in paths)
    if not result['source_unchanged']:
        result['status'] = 'UNVERIFIED'
    (args.out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'rules': [(r['id'], r['status']) for r in result['rules']], 'source_unchanged': result['source_unchanged']}, ensure_ascii=False))
    return {'DECLARED_LAYOUT_ONLY_PASS': 0, 'BLOCKED': 1, 'UNVERIFIED': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
