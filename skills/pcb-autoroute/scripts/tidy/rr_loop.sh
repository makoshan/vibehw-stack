#!/bin/zsh
# 逐网拆布：每个网重布 → 写回 → KiCad DRC；未连/错误/悬空不多于基线、且没有新锐角/折回、模拟网不新增长平行段才留下，再布下一个
# 用法：GEO=… ./rr_loop.sh 起始板前缀 网1,网2,...    （起始板前缀.kicad_pcb/.kicad_pro/.kicad_dru）
# 改板快照（snap.sh，见 SKILL.md「改板留快照」）：失败只提示，不挡整理
_TD=${0:A:h}   # 必须在函数外取：zsh 函数里的 $0 是函数名
snap() { "$_TD/../snap.sh" . "$1" >/dev/null 2>&1 || echo "（快照失败：$1，不影响整理）"; }
snap "rr_loop 开始"
K=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
CLI=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
src=$1; nets=(${(s:,:)2})
for e in kicad_pcb kicad_pro kicad_dru; do command cp -f $src.$e cur.$e; done
$CLI pcb drc --refill-zones --format json -o cur-drc.json cur.kicad_pcb >/dev/null
score() { jq '[(.unconnected_items|length), ([.violations[]|select(.severity=="error")]|length), ([.violations[]|select(.type=="track_dangling")]|length)]|join(",")' -r $1; }
base=$(score cur-drc.json); echo "基线 未连,错误,悬空 = $base"
for n in $nets; do
  $K -I extract.py cur.kicad_pcb cur.json >/dev/null 2>&1
  $GEO router.py cur.json one.json "$n" | sed 's/^/  /'
  if [ "$(jq '.added|length' one.json)" = "0" ]; then continue; fi
  for e in kicad_pro kicad_dru; do command cp -f cur.$e cand.$e; done
  $K -I apply2.py cur.kicad_pcb one.json cand.kicad_pcb >/dev/null 2>&1
  $CLI pcb drc --refill-zones --format json -o cand-drc.json cand.kicad_pcb >/dev/null
  s=$(score cand-drc.json)
  $K -I extract.py cand.kicad_pcb cand.json >/dev/null 2>&1
  $GEO par.py cur.json cand.json "$n" | sed 's/^/    /'; cp_ok=${pipestatus[1]}
  if [ "$n" = "${n#V4_}" ]; then cp_ok=0; fi    # 耦合关只管模拟网（V4_ 开头）
  na=$($K -I joints.py cur.kicad_pcb cand.kicad_pcb 2>/dev/null | sed -n 's/.*新板才有 \([0-9]*\).*/\1/p')
  if [ "$s" = "$base" ] && [ "$na" = "0" ] && [ "$cp_ok" = "0" ]; then
    for e in kicad_pcb; do command cp -f cand.$e cur.$e; done; command cp -f cand-drc.json cur-drc.json
    echo "  ✓ 留下 $n"
  else
    echo "  ✗ 退回 $n（DRC $s，新锐角 $na，耦合关 $cp_ok）"; jq -r '[(.unconnected_items[], (.violations[]|select(.severity=="error" or .type=="track_dangling")))|"\(.type // "未连") \([.items[].description]|join(" | "))"]|.[:3][]' cand-drc.json | cut -c1-160 | sed 's/^/      /'
  fi
done
snap "rr_loop 结束"
