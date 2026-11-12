"""无界面跑 pcb2blender 导出（.pcb3d）。用 KiCad 自带 Python（3.9）执行。

pcb2blender 的导出器原本是 pcbnew 里的按钮：调 pcbnew.GetBoard()（当前打开的板）和
pcbnew.ExportVRML()（依赖编辑器窗口，无界面不可用）。这里替换两处：
  GetBoard   → pcbnew.LoadBoard(板)
  ExportVRML → kicad-cli pcb export vrml，参数对齐原调用（米、原点 0,0、模型另存）
其余（图层 SVG、焊盘、叠层）走 pcb2blender 原代码。

用法：<KiCad Python> pcb3d_export.py <pcb2blender 仓库> <board.kicad_pcb> <out.pcb3d>
"""
import subprocess, sys, types
from pathlib import Path

repo, board_path, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(repo))

import pcbnew  # noqa: E402

board = pcbnew.LoadBoard(str(board_path))
pcbnew.GetBoard = lambda: board


def export_vrml_cli(wrl, mm_to_unit, include_unspecified, include_dnp, export_3d, relative,
                    subdir, xref, yref):
    # 原调用：ExportVRML(wrl, 0.001, True, False, True, True, components_path, 0, 0)
    assert mm_to_unit == 0.001 and xref == 0 and yref == 0
    cmd = ['kicad-cli', 'pcb', 'export', 'vrml', '-f', '-o', str(wrl), '--units', 'm',
           '--user-origin', '0x0mm', '--models-dir', str(subdir)]
    if relative:
        cmd.append('--models-relative')
    if not include_unspecified:
        cmd.append('--no-unspecified')
    if not include_dnp:
        cmd.append('--no-dnp')
    r = subprocess.run(cmd + [str(board_path)], capture_output=True, text=True)
    if r.returncode != 0 or not Path(wrl).exists():
        sys.exit(f'kicad-cli export vrml 失败 rc={r.returncode}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}')
    return True


pcbnew.ExportVRML = export_vrml_cli

# 包的 __init__ 会 import wx 并注册按钮；无界面跳过它，只加载 export 模块
pkg = types.ModuleType('pcb2blender_exporter')
pkg.__path__ = [str(repo / 'pcb2blender_exporter')]
sys.modules['pcb2blender_exporter'] = pkg
from pcb2blender_exporter import export as ex  # noqa: E402

boarddefs, ignored = ex.get_boarddefs(board)
ex.export_pcb3d(out, boarddefs)
print('PCB3D_OK', out, out.stat().st_size)
