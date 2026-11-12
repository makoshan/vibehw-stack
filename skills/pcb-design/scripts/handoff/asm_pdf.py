"""装配图 PDF：正面一页（F.Fab + F.Courtyard + 板框），背面一页（镜像），不叠丝印、不画焊盘。
ESD（D9xx）自带的阴极条会压住位号首字，放大可读。用法：python3 asm_pdf.py 板 输出.pdf 临时目录"""
import sys, subprocess
from pathlib import Path
src, out, tmp = sys.argv[1], sys.argv[2], Path(sys.argv[3]); tmp.mkdir(parents=True, exist_ok=True)
K = '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli'
pages = []
for side, lay, extra in (('top', 'F.Fab,F.Courtyard,Edge.Cuts', []), ('bot', 'B.Fab,B.Courtyard,Edge.Cuts', ['--mirror'])):
    pdf = tmp / f'asm-{side}.pdf'; pages.append(str(pdf))
    subprocess.run([K, 'pcb', 'export', 'pdf', '-l', lay, '--mode-single', '--scale', '0', '--black-and-white', *extra, '-o', str(pdf), src], check=True, capture_output=True)
Path(out).unlink(missing_ok=True)
subprocess.run(['/System/Library/Automator/Combine PDF Pages.action/Contents/MacOS/join', '-o', out, *pages], check=True)
print(out)
