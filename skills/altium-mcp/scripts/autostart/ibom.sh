#!/usr/bin/env bash
# 从 Mac 无人值守生成 Altium 交互式 BOM（iBOM）：写任务文件 → 触发 AltiumStartBridge 冷启动 Altium → 等结果 → 拉回 html。
# 前提：Windows 端已按 setup.ps1 装好，且 Altium 没在运行（开着时 -R 会另起一个卡死实例）。
# 用法: ibom.sh 'C:\path\board.PcbDoc' [本机输出目录]
set -euo pipefail
pcb=${1:?用法: ibom.sh 'C:\\path\\board.PcbDoc' [outdir]}
out=${2:-.}
host=${MSTACK_WIN_HOST:-${WIN_HOST:-thursday-win}}
dir='%USERPROFILE%\codex-tools\altium-autostart'
ps=$(dirname "$0")/../win_ps.sh
pcb_q=${pcb//\'/\'\'}  # PowerShell 单引号串里 ' 要写成 ''，否则路径带 ' 会把脚本截断
res=$(WIN_HOST=$host "$ps" <<PS
\$d='$dir'; \$pcb='$pcb_q'; \$job="\$d\ibom-job.txt"
if (Get-Process X2 -EA 0) { 'fail Altium is running; close it first (with Altium open, X2 -R spawns a stuck second instance)'; exit }
if (-not (Test-Path -LiteralPath \$pcb)) { "fail no such board \$pcb"; exit }
Remove-Item "\$d\ibom-result.txt" -EA 0
# 用系统 ANSI 编码（中文 Windows 是 GBK）写：auto.js 用 FSO 按 ANSI 读，写成 ASCII 中文路径会变成 ?
[IO.File]::WriteAllText(\$job, \$pcb + "\`r\`n", [Text.Encoding]::Default)
schtasks /run /tn AltiumStartBridge | Out-Null
for (\$i=0; \$i -lt 60; \$i++) { Start-Sleep 5; \$r = Get-Content "\$d\ibom-result.txt" -EA 0; if (\$r -and \$r -ne 'started') { break } }
# 任务文件残留会让下一次 AltiumStartBridge 去跑 iBOM 而不是起桥，所以不论成败都清掉
Remove-Item \$job -EA 0
if (-not \$r) { 'fail timeout 300s, script never started' }
elseif (\$r -eq 'started') { 'fail timeout 300s after start (probably a modal dialog in Altium; ask Mako to look)' }
else { \$r }
PS
)
res=${res//$'\r'/}  # Windows 输出带 CRLF，不去掉路径末尾会多个回车，scp 找不到文件
echo "$res"
case $res in ok\ *) html=${res#ok }; /usr/bin/scp -q "$host:$(echo "$html" | tr '\\' '/')" "$out/" && echo "拉回: $out/$(basename "$(echo "$html" | tr '\\' '/')")";; *) exit 1;; esac
