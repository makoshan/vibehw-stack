#!/usr/bin/env python3
"""只读运行 KiCad 基础门禁；原生规则通过不能代替功能或制造验收。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

CRITICAL = {
    'clearance', 'shorting_items', 'tracks_crossing', 'unconnected_items',
    'track_dangling', 'via_dangling', 'isolated_copper', 'net_conflict',
    'track_width', 'annular_width', 'drill_out_of_range', 'hole_clearance',
    'hole_to_hole', 'copper_edge_clearance', 'invalid_outline',
    'items_not_allowed', 'solder_mask_bridge', 'starved_thermal',
    'missing_footprint', 'extra_footprint', 'duplicate_footprints',
    'padstack', 'through_hole_pad_without_hole', 'microvia_drill_out_of_range',
    'item_on_disabled_layer', 'holes_co_located', 'zones_intersect',
    'connection_width', 'creepage', 'malformed_courtyard', 'courtyards_overlap',
}


def inspect_report(report):
    """漏字段或关键规则被关闭时停下，避免空报告给出假通过。"""
    required = ('violations', 'unconnected_items', 'schematic_parity',
                'ignored_checks', 'included_severities')
    if not isinstance(report, dict) or any(
            not isinstance(report.get(key), list) for key in required):
        raise ValueError('原生报告缺字段或格式不符，无法判定')
    if not {'error', 'warning', 'exclusion'} <= set(report['included_severities']):
        raise ValueError('报告没有包含错误、警告和排除项')
    ignored = report['ignored_checks']
    if any(not isinstance(item, dict) or not isinstance(item.get('key'), str)
           for item in ignored):
        raise ValueError('忽略规则列表格式不符')
    disabled = sorted(CRITICAL & {item['key'] for item in ignored})
    counts = {key: len(report[key]) for key in required[:3]}
    passed = not any(counts.values()) and not disabled
    return {
        'status': 'NATIVE_DRC_ONLY_PASS' if passed else 'BLOCKED',
        'counts': counts, 'disabled_critical_rules': disabled,
        'other_ignored_checks': [item for item in ignored if item['key'] not in CRITICAL],
        'functional_ready': False, 'manufacturing_ready': False,
        'scope': '本次原生DRC和关键规则启用状态；未验引脚真源、RF、回流、额定值或实物',
    }


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def schematic_sources(top):
    """层级子图也属于输入；缺子图时不能接受导出器给出的部分网表。"""
    found = set()
    pending = [top]
    while pending:
        path = pending.pop().resolve()
        if path in found:
            continue
        if not path.is_file():
            raise ValueError(f'层级原理图文件缺失：{path}')
        found.add(path)
        for token in re.findall(r'\(property\s+"Sheetfile"\s+("(?:[^"\\]|\\.)*")',
                                path.read_text()):
            name = json.loads(token).replace('${KIPRJMOD}', str(top.parent))
            pending.append(path.parent / name)
    return sorted(found)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', type=Path)
    parser.add_argument('--out', type=Path, required=True, help='必须是新的证据目录')
    parser.add_argument('--cli', default=shutil.which('kicad-cli') or
                        '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli')
    args = parser.parse_args()
    board = args.board.resolve()
    if not board.is_file():
        parser.error('PCB文件不存在')
    # 每次使用新目录，工具失败时不能误读上一次的报告。
    args.out.mkdir(parents=True, exist_ok=False)
    out = args.out.resolve()
    sources = [board] + [p for p in (board.with_suffix('.kicad_pro'),
                                   board.with_suffix('.kicad_sch'),
                                   board.with_suffix('.kicad_dru')) if p.exists()]
    before = {str(p): sha(p) for p in sources}
    report_path = out / 'native-drc.json'
    command = [args.cli, 'pcb', 'drc', '--format', 'json', '--severity-all',
               '--schematic-parity', '--all-track-errors', '--refill-zones',
               '--output', str(report_path), str(board)]
    result = {'status': 'UNVERIFIED', 'source_sha256': before, 'command': command}
    code = 2
    try:
        for suffix in ('.kicad_pro', '.kicad_sch'):
            if not board.with_suffix(suffix).is_file():
                raise ValueError(f'缺同名{suffix}，工程规则或原理图一致性无法核对')
        for path in schematic_sources(board.with_suffix('.kicad_sch')):
            if path not in sources:
                sources.append(path)
                before[str(path)] = sha(path)
        # CLI可能在无法生成原理图网表时仍把空parity列表写进DRC；先独立导出证明它可读。
        netlist = out / 'native-netlist.xml'
        net_command = [args.cli, 'sch', 'export', 'netlist', '--format', 'kicadxml',
                       '--output', str(netlist), str(board.with_suffix('.kicad_sch'))]
        net_process = subprocess.run(net_command, capture_output=True, text=True, timeout=180)
        (out / 'schematic.log').write_text(net_process.stdout + net_process.stderr)
        result['netlist_command'] = net_command
        result['netlist_tool_exit_code'] = net_process.returncode
        if net_process.returncode != 0:
            raise ValueError('原理图网表无法导出，一致性未验证')
        tree = ET.parse(netlist)
        if tree.getroot().tag != 'export' or not tree.findall('./components/comp'):
            raise ValueError('原理图网表格式错误或没有元件，无法判定')
        result['netlist_components'] = len(tree.findall('./components/comp'))
        # refill只在内存执行，绝不加save-board；运行后核对源文件未被改写。
        process = subprocess.run(command, capture_output=True, text=True, timeout=180)
        (out / 'native.log').write_text(process.stdout + process.stderr)
        result['tool_exit_code'] = process.returncode
        if process.returncode != 0:
            raise ValueError('KiCad执行失败，不能给出通过结论')
        result.update(inspect_report(json.loads(report_path.read_text())))
        code = 0 if result['status'] == 'NATIVE_DRC_ONLY_PASS' else 1
    except (OSError, ValueError, ET.ParseError, subprocess.TimeoutExpired) as exc:
        result.update(status='UNVERIFIED', reason=str(exc))
    finally:
        if any(not p.exists() or sha(p) != before[str(p)] for p in sources):
            result.update(status='UNVERIFIED', reason='检查期间源文件变化，结论作废')
            code = 2
        (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
