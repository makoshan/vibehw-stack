"""SES 完整性检查：数 SES 里的 wire/via，再导入原板跑 KiCad DRC 数断网。
用法：check_ses.py <源板.kicad_pcb> <ses>... ；用 KiCad 自带 python 跑。"""
import json, re, subprocess, sys, pathlib, shutil
import pcbnew as k

src = pathlib.Path(sys.argv[1])
for ses in map(pathlib.Path, sys.argv[2:]):
    t = ses.read_text()
    wires, vias = len(re.findall(r'\(wire\b', t)), len(re.findall(r'\(via\b', t))
    out = ses.with_suffix('.imported.kicad_pcb')
    shutil.copy(src.with_suffix('.kicad_pro'), out.with_suffix('.kicad_pro'))  # 规则跟着板走
    b = k.LoadBoard(str(src))
    assert k.ImportSpecctraSES(b, str(ses)), ses
    k.SaveBoard(str(out), b)
    rep = out.with_suffix('.drc.json')
    subprocess.run(['/opt/homebrew/bin/kicad-cli', 'pcb', 'drc', '--format', 'json',
                    '-o', str(rep), str(out)], capture_output=True)
    d = json.loads(rep.read_text())
    tracks = sum(1 for x in b.GetTracks() if x.GetClass() == 'PCB_TRACK')
    vias_b = sum(1 for x in b.GetTracks() if x.GetClass() == 'PCB_VIA')
    print(json.dumps({'ses': ses.name, 'ses_wires': wires, 'ses_vias': vias,
                      'board_tracks': tracks, 'board_vias': vias_b,
                      'unconnected': len(d.get('unconnected_items', [])),
                      'violations': len(d.get('violations', []))}, ensure_ascii=False))
