#!/usr/bin/env bash
# 在 Windows 上跑一段 PowerShell（从 stdin 读），UTF-8 输出。
# 用 -EncodedCommand，所以 $、引号、% 都不用转义；中文 Windows 默认 GBK 输出，这里切成 UTF-8。
# 用法: win_ps.sh <<'PS'
#         Get-Process X2 | Select Id,StartTime
#       PS
set -euo pipefail
host=${MSTACK_WIN_HOST:-${WIN_HOST:-thursday-win}}
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
{ echo '[Console]::OutputEncoding=[Text.Encoding]::UTF8;$ProgressPreference="SilentlyContinue"'; cat; } > "$tmp"
exec /usr/bin/ssh -o BatchMode=yes -o LogLevel=ERROR -o ControlPath=none -T "$host" \
  "powershell -NoProfile -EncodedCommand $(iconv -f utf-8 -t utf-16le "$tmp" | base64)"
