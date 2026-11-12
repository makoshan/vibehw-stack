#!/usr/bin/env bash
# 在 Windows(thursday-win) 上跑 KiCad 10.0.6 DRC。用法: win_drc.sh board.kicad_pcb [out.drc.json]
# 板子同目录的 .kicad_pro / fp-lib-table / *.pretty 一起传，保证规则和封装库与本地一致。
set -euo pipefail
pcb=$1; out=${2:-${pcb%.kicad_pcb}.drc.json}
host=${MSTACK_WIN_HOST:-${WIN_HOST:-thursday-win}}; dir=$(cd "$(dirname "$pcb")" && pwd); name=$(basename "$pcb")
rel=codex-tools/kicad-drc/$(date +%Y%m%d-%H%M%S)-$$
/usr/bin/ssh -o BatchMode=yes -o ControlPath=none -o LogLevel=ERROR "$host" "cd & mkdir ${rel//\//\\}" >/dev/null
files=("$pcb"); pro=${pcb%.kicad_pcb}.kicad_pro; [ -f "$pro" ] && files+=("$pro")
for f in "$dir"/fp-lib-table "$dir"/*.pretty; do [ -e "$f" ] && files+=("$f"); done
/usr/bin/scp -q -r -o BatchMode=yes -o ControlPath=none -o LogLevel=ERROR "${files[@]}" "$host:$rel/"
/usr/bin/ssh -o BatchMode=yes -o ControlPath=none -o LogLevel=ERROR "$host" "cd %USERPROFILE%\\${rel//\//\\} & \"C:\\Program Files\\KiCad\\10.0\\bin\\kicad-cli.exe\" pcb drc --format json --output drc.json \"$name\"" | tr -d '\r' | tail -3
/usr/bin/scp -q -o BatchMode=yes -o ControlPath=none -o LogLevel=ERROR "$host:$rel/drc.json" "$out"
/usr/bin/ssh -o BatchMode=yes -o LogLevel=ERROR "$host" "rmdir /s /q ${rel//\//\\}" >/dev/null
python3 -c "import json,collections,sys;r=json.load(open(sys.argv[1]));print('unconnected=',len(r['unconnected_items']),'violations=',dict(collections.Counter(v['type'] for v in r['violations'])))" "$out"
