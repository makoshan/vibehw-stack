"""给板子加泪滴：所有过孔和焊盘打开「允许泪滴」，再用 kicad-cli 重铺并存盘，重铺时 KiCad 按板级默认参数生成泪滴区。
KiCad 10 的 python 不开放泪滴生成工具，但 `pcb drc --refill-zones --save-board` 会生成（C08B D5 实测 708 个）。只加泪滴、不动走线和焊盘。
用法：KiCad python teardrops.py 板.kicad_pcb（就地改）"""
import sys, subprocess, pcbnew as k
p = sys.argv[1]
b = k.LoadBoard(p)
for t in b.GetTracks():
    if t.GetClass() == 'PCB_VIA': t.SetTeardropsEnabled(True)
for f in b.GetFootprints():
    for pad in f.Pads(): pad.SetTeardropsEnabled(True)
b.Save(p)
K = '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli'
subprocess.run([K, 'pcb', 'drc', '--refill-zones', '--save-board', '-o', '/dev/null', p], check=True, capture_output=True)
b = k.LoadBoard(p)
print('泪滴区', sum(1 for z in b.Zones() if z.IsTeardropArea()))
