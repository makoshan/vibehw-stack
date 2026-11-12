#!/usr/bin/env bash
# 在 Windows 上跑 Freerouting 1.9.0，省 Mac 内存。用法: win_route.sh in.dsn out.ses [passes=500] [timeout_s=3600]
# 1.9.0 没有 headless，要桌面：win_job.sh 用 Interactive 临时计划任务，窗口开在 Windows 桌面。
set -euo pipefail
dsn=$1; ses=$2; passes=${3:-500}; timeout=${4:-3600}
here=$(cd "$(dirname "$0")" && pwd)
java='C:\Program Files\Eclipse Adoptium\jre-25.0.4.101-hotspot\bin\java.exe'
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
cp "$dsn" "$tmp/in.dsn"
WIN_JOB_LOG="${ses%.ses}.freerouting.log" "$here/win_job.sh" "$tmp" "$timeout" \
  "\"$java\" -jar \"%USERPROFILE%\\codex-tools\\freerouting\\freerouting-1.9.0.jar\" -de in.dsn -do out.ses -mp $passes -da -dct 1 -l en" >/dev/null
cp "$tmp/out.ses" "$ses"
echo "ses=$ses ($(grep -c '(wire' "$ses") wires, $(grep -c '(via' "$ses") vias) — 仍须 KiCad DRC 复核"
