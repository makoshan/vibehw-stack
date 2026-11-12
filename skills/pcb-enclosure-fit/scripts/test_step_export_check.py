"""step_export_check.py 自检：现场造板，真 kicad-cli 导出；locale 与失败分支另用假 kicad-cli。

跑法：python3 test_step_export_check.py（要 kicad-cli，可用 KICAD_CLI 指定；
Spark 上用 docker：docker run --rm --network none --user $(id -u):$(id -g) -e HOME=/tmp
  -v $PWD:/w -w /w kicad10-arm64-np python3 test_step_export_check.py）
"""
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / 'step_export_check.py'
sys.path.insert(0, str(HERE))
from step_export_check import find_cli  # noqa: E402

# LANG 是中文；LC_ALL=C.UTF-8 + LANGUAGE=zh_CN 是实测能让容器里的 kicad-cli 真打中文的组合（容器没装 zh_CN locale）
ZH = {'LANG': 'zh_CN.UTF-8', 'LC_ALL': 'C.UTF-8', 'LANGUAGE': 'zh_CN'}


def board(parts, w=20, h=10):
    """最小的板：一个矩形外框，每个件一个焊盘、一个模型路径。"""
    fps = ''.join(f'''  (footprint "T:{ref}" (layer "F.Cu") (at {x} 5)
    (property "Reference" "{ref}" (at 0 -2 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))
    (model "{model}" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))
  )
''' for ref, x, model in parts)
    return f'''(kicad_pcb (version 20240108) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (gr_rect (start 0 0) (end {w} {h}) (layer "Edge.Cuts") (stroke (width 0.1) (type default)) (fill none))
{fps})
'''


def run(args, env_extra=None, env_drop=()):
    env = dict(os.environ, **(env_extra or {}))
    for k in env_drop:
        env.pop(k, None)
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


class RealCli(unittest.TestCase):
    """真 kicad-cli：先导一块无件小板当模型，再给另一块板的件用。"""

    @classmethod
    def setUpClass(cls):
        cli = find_cli()
        if not (Path(cli).is_file() or subprocess.run(['sh', '-c', f'command -v "{cli}"'], capture_output=True).returncode == 0):
            raise RuntimeError(f'没有 kicad-cli（{cli}），本自检要真导出；设 KICAD_CLI 或在 kicad 容器里跑')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls.tmp.name)
        (cls.d / 'small.kicad_pcb').write_text(board([], 3, 2))
        code, out = run([cls.d / 'small.kicad_pcb', cls.d / 'small.step'])
        assert code == 0, out
        cls.model = cls.d / 'small.step'
        (cls.d / 'full.kicad_pcb').write_text(board([('R1', 5, cls.model), ('J2', 12, cls.model)]))
        (cls.d / 'miss.kicad_pcb').write_text(board([('R1', 5, cls.model), ('J2', 10, cls.d / 'nope.step'),
                                                     ('U1.A', 15, cls.d / 'nope3.step')]))
        cn = cls.d / '原力 手环'
        cn.mkdir()
        (cn / '板.kicad_pcb').write_text(board([('R1', 5, cls.model)]))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_full_models_ok(self):
        out_step = self.d / 'full.step'
        code, out = run([self.d / 'full.kicad_pcb', out_step])
        self.assertEqual(code, 0, out)
        self.assertGreater(out_step.stat().st_size, self.model.stat().st_size)  # 模型真进了整板 STEP
        self.assertNotIn('未验证', out)

    def test_missing_model_reported(self):
        code, out = run([self.d / 'miss.kicad_pcb', self.d / 'miss.step'])
        self.assertEqual(code, 1, out)
        self.assertIn('2 个件', out)
        self.assertIn('J2 U1.A', out)
        self.assertNotIn('R1', out)
        self.assertIn('未验证', out)
        self.assertTrue((self.d / 'miss.step').stat().st_size > 0)

    def test_missing_model_under_zh_locale(self):
        # 先确认这个环境真能让 kicad-cli 说中文，否则这条测不到东西
        raw = subprocess.run([find_cli(), 'pcb', 'export', 'step', '-f', '-o', str(self.d / 'raw.step'),
                              str(self.d / 'miss.kicad_pcb')], capture_output=True, text=True, env=dict(os.environ, **ZH))
        self.assertIn('无法为 J2 添加 3D 模型', raw.stdout + raw.stderr)
        code, out = run([self.d / 'miss.kicad_pcb', self.d / 'miss-zh.step'], ZH)
        self.assertEqual(code, 1, out)
        self.assertIn('J2 U1.A', out)
        self.assertNotIn('R1', out)

    def test_chinese_paths(self):
        # 实测 LC_ALL=C 下 kicad-cli 打不开中文路径；板和输出都带中文、带空格
        out_step = self.d / '原力 手环' / '整板 输出.step'
        code, out = run([self.d / '原力 手环' / '板.kicad_pcb', out_step])
        self.assertEqual(code, 0, out)
        self.assertGreater(out_step.stat().st_size, 0)

    def test_output_flag_rejected(self):
        code, out = run([self.d / 'full.kicad_pcb', self.d / 'x.step', '-o', self.d / 'y.step'])
        self.assertEqual(code, 2, out)
        self.assertIn('不要再透传', out)
        self.assertFalse((self.d / 'y.step').exists())

    def test_passthrough_args(self):
        # 透传 --component-filter R1：J2、U1.A 不导，自然也不报缺
        code, out = run([self.d / 'miss.kicad_pcb', self.d / 'filt.step', '--component-filter', 'R1'])
        self.assertEqual(code, 0, out)

    def test_bad_board_is_2(self):
        bad = self.d / 'bad.kicad_pcb'
        bad.write_text('(kicad_pcb (version')
        out_step = self.d / 'bad.step'
        out_step.write_text('上次留下的旧文件')  # 旧文件不能被当成这次的结果
        code, out = run([bad, out_step])
        self.assertEqual(code, 2, out)
        self.assertEqual(out_step.read_text(), '上次留下的旧文件')
        self.assertEqual(run([self.d / 'none.kicad_pcb', self.d / 'none.step'])[0], 2)


FAKE = textwrap.dedent('''\
    #!/usr/bin/env python3
    # 假 kicad-cli：按 FAKE_MODE 演各种情形；把看到的 locale 记到 FAKE_ENV_LOG
    import os, sys
    a = sys.argv; out = a[a.index('-o') + 1]; mode = os.environ['FAKE_MODE']
    open(os.environ['FAKE_ENV_LOG'], 'w').write(repr({k: os.environ.get(k) for k in ('LC_ALL', 'LANG', 'LANGUAGE')}))
    zh = 'zh' in (os.environ.get('LANGUAGE') or '') or 'zh' in (os.environ.get('LC_ALL') or '')
    if mode == 'locale':  # 像 gettext：LC_ALL 不是 C 且有中文就说中文，中文告警故意写成认不出的样子
        print('J2 的 3D 模型加不上。' if zh else 'Could not add 3D model for J2.')
    elif mode == 'zh':    # KiCad 偏好设成中文、环境变量压不住时的真实中文原文；stdout、stderr 各一遍（容器里以 root 跑时见过两边都打）
        print('无法为 J2 添加 3D 模型。\\n找不到文件: /x/nope.step')
        print('无法为 J2 添加 3D 模型。', file=sys.stderr)
    elif mode in ('dup', 'rc'):  # 两颗件位号重名都缺模型；rc 时另演坏模型文件
        print('Could not add 3D model for C3.\\nCould not add 3D model for C3.')
        if mode == 'rc':
            print("No model for filename '/x/bad.step'")
            print('后面还有很长的日志\\n' * 60)  # 把前面几行挤出日志末尾
    if mode == 'nofile':
        sys.exit(0)
    open(out, 'w').write('' if mode == 'empty' else 'ISO-10303-21;')
    if mode == 'rc':  # 文件写出来了但退出码非 0，也算失败
        print('Failed to load board', file=sys.stderr); sys.exit(3)
''')


class FakeCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cli = self.d / 'kicad-cli'
        self.cli.write_text(FAKE)
        self.cli.chmod(self.cli.stat().st_mode | stat.S_IXUSR)
        self.pcb = self.d / 'b.kicad_pcb'
        self.pcb.write_text(board([]))
        self.envlog = self.d / 'env.txt'

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, mode, extra=None):
        env = {'KICAD_CLI': str(self.cli), 'FAKE_MODE': mode, 'FAKE_ENV_LOG': str(self.envlog), **(extra or {})}
        return run([self.pcb, self.d / 'o.step'], env)

    def test_child_locale_forced_to_c(self):
        code, out = self.fake('locale', ZH)
        self.assertEqual(code, 1, out)
        self.assertIn('J2', out)
        seen = eval(self.envlog.read_text())
        # macOS 没有 C.UTF-8，脚本在 Mac 上用 en_US.UTF-8（见 step_export_check.UTF8_C）；写死期望值，不从脚本里取，免得两边一起错
        want = 'en_US.UTF-8' if sys.platform == 'darwin' else 'C.UTF-8'
        self.assertEqual((seen['LC_ALL'], seen['LANG'], seen['LANGUAGE']), (want, want, None))

    def test_chinese_warning_counted(self):
        code, out = self.fake('zh')
        self.assertEqual(code, 1, out)
        self.assertIn('1 个件', out)  # 两路各打一遍只算一个
        self.assertIn('J2', out)

    def test_duplicate_refs_each_counted(self):
        code, out = self.fake('dup')
        self.assertEqual(code, 1, out)
        self.assertIn('2 个件', out)

    def test_failed_export_still_names_models(self):
        code, out = self.fake('rc')
        self.assertEqual(code, 2, out)
        self.assertIn("No model for filename '/x/bad.step'", out)
        self.assertIn('报了 2 个件缺 3D 模型：C3 C3', out)

    def test_no_file_empty_file_failed_rc_are_2(self):
        for mode in ('nofile', 'empty', 'rc'):
            with self.subTest(mode=mode):
                self.assertEqual(self.fake(mode)[0], 2)
                self.assertFalse((self.d / 'o.step').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
