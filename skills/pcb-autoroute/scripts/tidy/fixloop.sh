#!/bin/zsh
# 孤岛修复循环：大于 MIN_MM2 的新增孤岛旁、被拉直过的 (网,层) 整层退回源板走线，重铺铜再算，直到没有新候选
# 改板快照（snap.sh，见 SKILL.md「改板留快照」）：失败只提示，不挡整理
_TD=${0:A:h}   # 必须在函数外取：zsh 函数里的 $0 是函数名
snap() { "$_TD/../snap.sh" . "$1" >/dev/null 2>&1 || echo "（快照失败：$1，不影响整理）"; }
snap "fixloop 开始"
K=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
/bin/rm -f blame.json; command cp -f tidy.kicad_pcb fix.kicad_pcb; command cp -f tidy.kicad_pro fix.kicad_pro; command cp -f tidy.kicad_dru fix.kicad_dru
for i in 1 2 3 4 5 6; do
  $K -I isl_export.py fix.kicad_pcb fix-isl.json 2>/dev/null; $K -I ../extract.py fix.kicad_pcb fix.json >/dev/null 2>&1
  before=$(jq length blame.json 2>/dev/null || echo 0)
  ACC=1 MIN_MM2=${MIN_MM2:-1.0} $GEO isl_blame.py src.json src-isl.json fix.json fix-isl.json | tail -1
  after=$(jq length blame.json)
  echo "第 $i 轮 候选 $before → $after"
  [ "$after" = "$before" ] && break
  $K -I revert_layers.py src.kicad_pcb tidy.kicad_pcb blame.json fix.kicad_pcb 2>&1 | grep 删
done
snap "fixloop 结束"
