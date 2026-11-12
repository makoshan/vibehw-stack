#!/usr/bin/env bash
# 把本地目录传到 Windows，在当前用户 Interactive 临时计划任务里跑一条命令，取回运行中新建/改动的文件。
# 用法: win_job.sh <local_dir> <timeout_s> <Windows 命令行（在上传后的目录里执行）>
# Mac 端每 10 s 用一条短连接轮询：断网只影响轮询，任务照跑。超时不删任务、不重跑，退出码 124。
# 成功后删除远端工作目录；失败保留在 %USERPROFILE%\codex-tools\jobs\<id> 供检查。
set -euo pipefail
dir=$(cd "$1" && pwd); timeout=$2; shift 2; cmd="$*"
host=${MSTACK_WIN_HOST:-${WIN_HOST:-thursday-win}}
id=job-$(date +%Y%m%d-%H%M%S)-$$; rel=codex-tools/jobs/$id; wrel=${rel//\//\\}
SSH=(/usr/bin/ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none -o ConnectTimeout=20 -o LogLevel=ERROR "$host")
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

COPYFILE_DISABLE=1 tar -czf "$tmp/up.tgz" -C "$dir" --exclude references --exclude export --exclude logs \
  --exclude schematic-preview --exclude '.git' --exclude '*.step' --exclude '*.stp' --exclude '*.wrl' \
  --exclude '*.glb' --exclude '*.stl' --exclude '*.3mf' --exclude '*.png' --exclude '*.jpg' --exclude '*.pdf' \
  --exclude '*.zip' --exclude '*.jar' .
printf '@echo off\r\nset PYTHONUTF8=1\r\ncd /d "%%USERPROFILE%%\\%s\\w"\r\n%s > "..\\log.txt" 2>&1\r\necho %%ERRORLEVEL%% > "..\\done.txt"\r\n' "$wrel" "$cmd" > "$tmp/run.cmd"
"${SSH[@]}" "cd & mkdir $wrel\\w" >/dev/null
/usr/bin/scp -q -o BatchMode=yes -o ControlPath=none -o LogLevel=ERROR "$tmp/up.tgz" "$tmp/run.cmd" "$host:$rel/"
cat > "$tmp/go.ps1" <<EOF
\$ProgressPreference='SilentlyContinue'
\$d=Join-Path \$env:USERPROFILE '$wrel'
tar -xzf "\$d\up.tgz" -C "\$d\w"; Remove-Item "\$d\up.tgz"
Set-Content "\$d\start" ''
\$a=New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "'+\$d+'\run.cmd"')
\$p=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
Register-ScheduledTask -TaskName '$id' -Action \$a -Principal \$p | Out-Null
Start-ScheduledTask '$id'
EOF
"${SSH[@]}" "powershell -NoProfile -EncodedCommand $(iconv -f utf-8 -t utf-16le "$tmp/go.ps1" | base64)" >/dev/null
echo "job=$id started" >&2

code=
for ((t=0; t<timeout; t+=10)); do
  sleep 10
  code=$("${SSH[@]}" "type $wrel\\done.txt" 2>/dev/null | tr -dc '0-9') || true
  [ -n "$code" ] && break
done
if [ -z "$code" ]; then echo "TIMEOUT: task $id left running; log in %USERPROFILE%\\$wrel\\log.txt" >&2; exit 124; fi

"${SSH[@]}" "schtasks /Delete /TN $id /F >nul & cd $wrel\\w & tar -cf ..\\back.tar --newer-mtime-than ..\\start ." >/dev/null
/usr/bin/scp -q -o BatchMode=yes -o ControlPath=none -o LogLevel=ERROR "$host:$rel/back.tar" "$host:$rel/log.txt" "$tmp/"
tar -xf "$tmp/back.tar" -C "$dir"
echo "changed: $(tar -tf "$tmp/back.tar" | grep -v '/$' | grep -v '^\.$' | tr '\n' ' ')" >&2
[ -n "${WIN_JOB_LOG:-}" ] && cp "$tmp/log.txt" "$WIN_JOB_LOG"
tr -d '\r' < "$tmp/log.txt" | tail -15
[ "$code" = 0 ] && "${SSH[@]}" "rmdir /s /q $wrel" >/dev/null || echo "exit=$code; remote dir kept: $wrel" >&2
exit "$code"
