#!/usr/bin/env python3
"""生成真实KiCad正反例并运行专项入口；仅写新证据目录。"""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys


def fixture(path, fault):
    import wx
    app = wx.App(False)
    import pcbnew as k
    b = k.BOARD()
    b.SetCopperLayerCount(4)
    nets = {}
    for name in ('A', 'B', 'GND', 'DEAD', 'PAD_ONLY'):
        n = k.NETINFO_ITEM(b, name)
        b.Add(n)
        nets[name] = n

    def point(x, y):
        return k.VECTOR2I(k.FromMM(x), k.FromMM(y))

    def line(net, a, z, layer=k.F_Cu):
        t = k.PCB_TRACK(b)
        t.SetStart(point(*a))
        t.SetEnd(point(*z))
        t.SetWidth(k.FromMM(.2))
        t.SetLayer(layer)
        t.SetNet(nets[net])
        b.Add(t)

    def pad(ref, xy, net, through=False):
        f = k.FOOTPRINT(b)
        f.SetReference(ref)
        f.SetPosition(point(*xy))
        p = k.PAD(f)
        p.SetNumber('1')
        p.SetPosition(point(*xy))
        p.SetShape(k.PAD_SHAPE_RECT)
        p.SetSize(point(.6, .6))
        p.SetAttribute(k.PAD_ATTRIB_PTH if through else k.PAD_ATTRIB_SMD)
        layers = k.LSET.AllCuMask() if through else k.LSET()
        if not through:
            layers.AddLayer(k.F_Cu)
        p.SetLayerSet(layers)
        if through:
            p.SetDrillSize(point(.3, .3))
        p.SetNet(nets[net])
        f.Add(p)
        b.Add(f)

    pad('U1', (5, 5), 'A')
    pad('C1', (8, 5), 'A')
    pad('J1', (18, 18), 'GND', True)
    pad('J2', (18, 16), 'PAD_ONLY')
    if fault == 'long-route':
        for a, z in [((5, 5), (5, 11)), ((5, 11), (8, 11)), ((8, 11), (8, 5))]:
            line('A', a, z)
    elif fault == 'open-route':
        line('A', (5, 5), (6, 5))
        line('A', (7, 5), (8, 5))
    else:
        line('A', (5, 5), (8, 5))
    line('B', (15, 6), (16, 6))
    if fault == 'near-same-layer':
        line('B', (5, 5.5), (8, 5.5))
    if fault == 'near-other-layer':
        line('B', (5, 5.5), (8, 5.5), k.B_Cu)
    if fault == 'ground-layer-track':
        line('B', (12, 12), (15, 12), k.In1_Cu)
    if fault == 'via-hole':
        v = k.PCB_VIA(b)
        v.SetPosition(point(5, 5))
        v.SetViaType(k.VIATYPE_THROUGH)
        v.SetLayerPair(k.F_Cu, k.B_Cu)
        v.SetWidth(k.FromMM(.6))
        v.SetDrill(k.FromMM(.3))
        v.SetNet(nets['A'])
        b.Add(v)
    if fault == 'keepout-track':
        line('B', (11, 11), (12, 11))
    if fault == 'keepout-graphic':
        s = k.PCB_SHAPE(b)
        s.SetShape(k.SHAPE_T_SEGMENT)
        s.SetStart(point(11, 11))
        s.SetEnd(point(12, 11))
        s.SetWidth(k.FromMM(.2))
        s.SetLayer(k.B_Cu)
        b.Add(s)
    # 参考地保留明确接地点；缺口反例直接改铺铜边界，不靠过期缓存。
    z = k.ZONE(b)
    z.SetLayer(k.In1_Cu)
    z.SetNet(nets['GND'])
    z.SetLocalClearance(k.FromMM(.2))
    z.SetMinThickness(k.FromMM(.1))
    z.SetPadConnection(k.ZONE_CONNECTION_FULL)
    outline = z.Outline()
    outline.NewOutline()
    left = 6.5 if fault == 'ground-gap' else 1
    for x, y in [(left, 1), (19, 1), (19, 19), (left, 19)]:
        outline.Append(point(x, y))
    b.Add(z)
    dead = k.ZONE(b)
    dead.SetLayer(k.F_Cu)
    dead.SetNet(nets['DEAD'])
    dead.SetLocalClearance(k.FromMM(.2))
    dead.SetMinThickness(k.FromMM(.1))
    dead.Outline().NewOutline()
    for x, y in [(23, 23), (24, 23), (24, 24), (23, 24)]:
        dead.Outline().Append(point(x, y))
    b.Add(dead)
    for a, end in [((0, 0), (20, 0)), ((20, 0), (20, 20)), ((20, 20), (0, 20)), ((0, 20), (0, 0))]:
        s = k.PCB_SHAPE(b)
        s.SetShape(k.SHAPE_T_SEGMENT)
        s.SetStart(point(*a))
        s.SetEnd(point(*end))
        s.SetWidth(k.FromMM(.05))
        s.SetLayer(k.Edge_Cuts)
        b.Add(s)
    k.SaveBoard(str(path), b)
    path.with_suffix('.kicad_pro').write_text('{}\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    base = {'version': 1, 'rules': [
        {'id': 'separation', 'type': 'clearance', 'a': ['A'], 'b': ['B'], 'projection': 'all_layers', 'min_mm': 1},
        {'id': 'length', 'type': 'trace_budget', 'nets': ['A'], 'max_length_mm': 5, 'max_vias': 0, 'allowed_layers': ['F.Cu']},
        {'id': 'route', 'type': 'route_bound', 'pads': [['U1', '1'], ['C1', '1']], 'max_bound_mm': 6},
        {'id': 'ground-layer', 'type': 'reserved_layer', 'layer': 'In1.Cu'},
        {'id': 'hole', 'type': 'via_hole_in_pad', 'process': 'forbidden'},
        {'id': 'ground', 'type': 'ground_projection', 'nets': ['A'], 'layer': 'In1.Cu', 'ground_net': 'GND', 'margin_mm': .2, 'max_missing_mm2': 0},
        {'id': 'keepout', 'type': 'copper_keepout', 'box_mm': [10, 10, 3, 3], 'layers': ['F.Cu', 'B.Cu']},
    ]}
    for r in base['rules']:
        r.update(source='测试夹具定义：非产品验收阈值', basis='project_policy')
    cases = [('clean', 0, None, None), ('near-same-layer', 1, 'separation', 'BLOCKED'),
             ('near-other-layer', 1, 'separation', 'BLOCKED'), ('long-route', 1, 'route', 'BLOCKED'),
             ('ground-layer-track', 1, 'ground-layer', 'BLOCKED'), ('via-hole', 1, 'hole', 'BLOCKED'),
             ('ground-gap', 1, 'ground', 'BLOCKED'), ('keepout-track', 1, 'keepout', 'BLOCKED'),
             ('keepout-graphic', 1, 'keepout', 'BLOCKED'), ('open-route', 2, 'route', 'UNVERIFIED'),
             ('unknown-net', 2, 'separation', 'UNVERIFIED'), ('unknown-rule', 2, 'unknown', 'UNVERIFIED'),
             ('pending', 2, 'pending', 'UNVERIFIED'), ('missing-project', 2, None, None),
             ('empty-rules', 2, None, None), ('ground-review', 2, 'ground', 'UNVERIFIED'),
             ('dead-clearance-net', 2, 'separation', 'UNVERIFIED'),
             ('dead-trace-net', 2, 'length', 'UNVERIFIED'), ('pad-only-trace-net', 2, 'length', 'UNVERIFIED'),
             ('dead-ground-net', 2, 'ground', 'UNVERIFIED'),
             ('filled-capped-unchecked', 2, 'hole', 'UNVERIFIED'), ('duplicate-endpoint', 2, 'route', 'UNVERIFIED'),
             ('unknown-dru-constraint', 2, None, None), ('custom-dru-unverified', 2, None, None)]
    reports = []
    for name, code, target, state in cases:
        root = args.out / name
        root.mkdir()
        board = root / 'fixture.kicad_pcb'
        fixture(board, 'ground-gap' if name == 'ground-review' else 'via-hole' if name == 'filled-capped-unchecked' else name)
        rules = copy.deepcopy(base)
        if name == 'unknown-net':
            rules['rules'][0]['a'] = ['DOES_NOT_EXIST']
        elif name in ('pending', 'unknown-rule'):
            rules['rules'].append({'id': target, 'type': 'pending' if name == 'pending' else 'not-implemented',
                                   'source': '负向测试', 'basis': 'unverified', 'reason': '没有配置'})
        elif name == 'empty-rules':
            rules['rules'] = []
        elif name == 'missing-project':
            board.with_suffix('.kicad_pro').unlink()
        elif name == 'ground-review':
            rules['rules'][5]['gap_status'] = 'UNVERIFIED'
        elif name == 'dead-clearance-net':
            rules['rules'][0]['a'] = ['A', 'DEAD']
        elif name in ('dead-trace-net', 'pad-only-trace-net'):
            rules['rules'][1]['nets'] = ['A', 'DEAD' if name == 'dead-trace-net' else 'PAD_ONLY']
        elif name == 'dead-ground-net':
            rules['rules'][5]['nets'] = ['A', 'DEAD']
        elif name == 'filled-capped-unchecked':
            rules['rules'] = [rules['rules'][4]]
            rules['rules'][0].update(process='filled_capped', process_evidence='随便写个字符串不等于工厂证据')
        elif name == 'duplicate-endpoint':
            rules['rules'][2]['pads'] = [['U1', '1'], ['U1', '1']]
        elif name in ('unknown-dru-constraint', 'custom-dru-unverified'):
            constraint = 'made_up_constraint' if name == 'unknown-dru-constraint' else 'clearance'
            board.with_suffix('.kicad_dru').write_text('(version 1)\n(rule custom (constraint ' + constraint + ' (min 0.1mm)))\n')
        manifest = root / 'rules.json'
        manifest.write_text(json.dumps(rules, ensure_ascii=False, indent=2) + '\n')
        cmd = [sys.executable, str(Path(__file__).with_name('layout_gate.py')), str(board), str(manifest), '--out', str(root / 'report')]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        (root / 'run.log').write_text(run.stdout + run.stderr)
        report = json.loads((root / 'report/result.json').read_text())
        assert run.returncode == code, (name, run.returncode, code, report)
        assert report['source_unchanged'] and not report['manufacturing_ready'] and not report['functional_ready'], name
        if target:
            actual = next(r for r in report['rules'] if r['id'] == target)
            assert actual['status'] == state, (name, actual)
        reports.append({'case': name, 'exit': run.returncode, 'target': target, 'expected_state': state, 'status': report['status']})
    (args.out / 'test-summary.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'cases_passed': len(reports), 'positive': 1, 'negative': len(reports) - 1}, ensure_ascii=False))


if __name__ == '__main__':
    main()
