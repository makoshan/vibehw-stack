#!/usr/bin/env bash
# 在 Windows 上跑 finish_vNN.py 这类改板脚本，脚本不用改。用法: win_finish.sh path/to/finish.py [args...] [timeout_s 用 WIN_TIMEOUT]
# 脚本所在目录整个上传（win_job.sh 会排除 references/export/图片/3D）；产物（routed 板、drc.json、review json）写回原目录。
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
script=$(cd "$(dirname "$1")" && pwd)/$(basename "$1"); shift
host=${MSTACK_WIN_HOST:-${WIN_HOST:-thursday-win}}
/usr/bin/ssh -o BatchMode=yes -o ControlPath=none -o LogLevel=ERROR "$host" "cd & mkdir codex-tools 2>nul" >/dev/null || true
/usr/bin/scp -q -o BatchMode=yes -o ControlPath=none -o LogLevel=ERROR "$here/win_shim.py" "$host:codex-tools/win_shim.py"
exec "$here/win_job.sh" "$(dirname "$script")" "${WIN_TIMEOUT:-3600}" \
  '"C:\Program Files\KiCad\10.0\bin\python.exe" "%USERPROFILE%\codex-tools\win_shim.py"' "$(basename "$script")" "$@"
