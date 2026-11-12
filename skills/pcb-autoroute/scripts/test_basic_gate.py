"""报告边界反例；原生几何识别另用真实错误PCB验证。"""
import copy
from pathlib import Path
import tempfile
import unittest
from basic_gate import inspect_report, schematic_sources


class GateTests(unittest.TestCase):
    def setUp(self):
        self.report = {key: [] for key in ('violations', 'unconnected_items',
                                         'schematic_parity', 'ignored_checks')}
        self.report['included_severities'] = ['error', 'warning', 'exclusion']

    def test_clean(self):
        result = inspect_report(self.report)
        self.assertEqual(result['status'], 'NATIVE_DRC_ONLY_PASS')
        self.assertFalse(result['manufacturing_ready'])

    def test_faults_and_exclusions(self):
        for kind in ('track_dangling', 'via_dangling', 'shorting_items',
                     'clearance', 'isolated_copper'):
            for severity in ('error', 'warning', 'exclusion'):
                with self.subTest(kind=kind, severity=severity):
                    report = copy.deepcopy(self.report)
                    report['violations'] = [{'type': kind, 'severity': severity}]
                    self.assertEqual(inspect_report(report)['status'], 'BLOCKED')

    def test_missing_and_wrong_fields(self):
        for key in self.report:
            for action in ('delete', 'wrong_type'):
                with self.subTest(key=key, action=action):
                    report = copy.deepcopy(self.report)
                    if action == 'delete':
                        del report[key]
                    else:
                        report[key] = None
                    with self.assertRaises(ValueError):
                        inspect_report(report)

    def test_suppressed_severities(self):
        self.report['included_severities'] = ['error']
        with self.assertRaises(ValueError):
            inspect_report(self.report)

    def test_disabled_critical_check(self):
        for key in ('clearance', 'padstack', 'through_hole_pad_without_hole'):
            self.report['ignored_checks'] = [{'key': key}]
            self.assertEqual(inspect_report(self.report)['status'], 'BLOCKED')

    def test_unconnected_and_mismatch(self):
        for key in ('unconnected_items', 'schematic_parity'):
            report = copy.deepcopy(self.report)
            report[key] = [{'description': '故意错误'}]
            self.assertEqual(inspect_report(report)['status'], 'BLOCKED')

    def test_other_ignores_remain_visible(self):
        self.report['ignored_checks'] = [{'key': 'missing_courtyard'}]
        self.assertEqual(len(inspect_report(self.report)['other_ignored_checks']), 1)

    def test_missing_child_schematic(self):
        with tempfile.TemporaryDirectory() as folder:
            top = Path(folder) / 'top.kicad_sch'
            top.write_text('(kicad_sch (sheet (property "Sheetfile" "child.kicad_sch")))')
            with self.assertRaises(ValueError):
                schematic_sources(top)
            child = Path(folder) / 'child.kicad_sch'
            child.write_text('(kicad_sch)')
            self.assertEqual(set(schematic_sources(top)), {top.resolve(), child.resolve()})


if __name__ == '__main__':
    unittest.main()
