#!/bin/zsh
# 整理 → 写回 → DRC；DRC 新出问题（未连/错误/悬空）的网下一轮整网跳过，直到干净
set -e
# 改板快照（snap.sh，见 SKILL.md「改板留快照」）：失败只提示，不挡整理
_TD=${0:A:h}   # 必须在函数外取：zsh 函数里的 $0 是函数名
snap() { "$_TD/../snap.sh" . "$1" >/dev/null 2>&1 || echo "（快照失败：$1，不影响整理）"; }
snap "tidy run.sh 开始"
K=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
CLI=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
skip="${SKIP0:-}"
for i in 1 2 3 4 5 6; do
  ${GEO:?需要装了 shapely 的 Python，设 GEO=路径} tidy.py base.json tidy-out.json "$skip" | grep 完成
  command cp -f base.kicad_pro t1.kicad_pro; command cp -f base.kicad_dru t1.kicad_dru
  $K -I apply.py base.kicad_pcb tidy-out.json t1.kicad_pcb 2>&1 | grep 删
  $CLI pcb drc --refill-zones --format json -o t1-drc.json t1.kicad_pcb >/dev/null
  bad=$(jq -r '[(.unconnected_items[], (.violations[]|select(.severity=="error" or .type=="track_dangling")))|.items[].description|capture("\\[(?<n>[^\\]]+)\\]").n]|unique|join(",")' t1-drc.json)
  echo "第 $i 轮 未连 $(jq '.unconnected_items|length' t1-drc.json) 错误 $(jq '[.violations[]|select(.severity=="error")]|length' t1-drc.json) 悬空 $(jq '[.violations[]|select(.type=="track_dangling")]|length' t1-drc.json) 问题网: $bad"
  [ -z "$bad" ] && break
  skip=$(echo "$skip,$bad" | sed 's/^,//')
done
echo "跳过的网: $skip"
snap "tidy run.sh 结束（跳过的网: $skip）"
