#!/usr/bin/env bash
# 改板自动快照：把板目录里的 KiCad 文件提交到目录下的旁路仓库 .history.git，不碰项目自己的 git。
# 借自 Backplane native/host/src/files/scripts/step.sh（agents/hist.rs 每次工具调用后快照、kicad/histsteps.rs 按步回看）。
#
# 用法：
#   snap.sh <板目录> [说明]            快照；没变化就不提交，打印「无变化」
#   snap.sh --log [板目录]             看历史（每版改了哪些文件）
#   snap.sh --show <版本> <文件> [板目录]  把某版的文件打到标准输出（文件按板目录相对路径写）
# 快照的文件：*.kicad_pcb *.kicad_sch *.kicad_pro *.kicad_dru（含子目录；跳过 *-backups/、*_autosave-*）。
# .history.git 里放一个只写 * 的 .gitignore，项目主仓库 git status 看不到它；项目自己的 .git 和 .gitignore 都不动。
# 退出码：0 成功（含无变化）；1 参数或目录不对；2 git 失败或等锁超时。
set -u

die() { printf '%s\n' "snap.sh: $1" >&2; exit "${2:-1}"; }

# 调用方的 GIT_* 环境（钩子里常带 GIT_DIR、GIT_INDEX_FILE、GIT_OBJECT_DIRECTORY……）一律不用，免得写进项目仓库
for v in $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$v"; done

# 信任登记表：只认本脚本建的 .history.git（inode + 绝对路径）；别人放进来的（解压、拷贝来的）可能带着会执行命令的配置
STATE=${XDG_STATE_HOME:-${HOME:-/nonexistent}/.local/state}/mstack
REG=$STATE/snap.trusted

# 旁路仓库专用的 git：不跑钩子，不签名，不用分页器
hg() {
  git -c core.hooksPath=/dev/null -c commit.gpgsign=false -c core.fsmonitor=false \
      -c core.autocrlf=false -c core.quotepath=false -c core.pager=cat \
      --git-dir=.history.git --work-tree=. "$@"
}

enter() {
  [ -d "$1" ] || die "目录不存在：$1"
  cd "$1" || die "进不去目录：$1"
  [ -n "${HOME:-}" ] || die "没有 HOME，不知道家目录在哪"
  case "$(pwd -P)" in
    /|"$(cd "$HOME" && pwd -P)") die "不在根目录或家目录建快照仓库：$(pwd)" ;;
  esac
  [ -L .history.git ] && die ".history.git 是符号链接，不用：$(pwd)/.history.git"
  [ ! -e .history.git ] || [ -d .history.git ] || die ".history.git 存在但不是目录：$(pwd)"
}

ino() { ls -di "$1" | awk '{print $1}'; }
trusted() { [ -f "$REG" ] && grep -qxF "$(ino .history.git) $(pwd -P)/.history.git" "$REG"; }

# 只读命令：仓库不受信就不碰
need_trusted() {
  [ -d .history.git ] || die "这里还没有快照：$(pwd)"
  trusted || die "$(pwd)/.history.git 不是本机 snap.sh 建的，不读（先跑一次 snap.sh 会把它挪开另建）"
}

case "${1:-}" in
  --log)
    enter "${2:-.}"; need_trusted
    hg log --date=format:'%Y-%m-%d %H:%M:%S' --format='%h %ad %s' --name-status || exit 2
    exit 0 ;;
  --show)
    [ $# -ge 3 ] || die "用法：snap.sh --show <版本> <文件> [板目录]"
    v=$2; f=$3
    enter "${4:-.}"; need_trusted
    hg cat-file blob "${v}:${f}" || die "取不到 ${v} 里的 ${f}" 2
    exit 0 ;;
  ""|-h|--help)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    [ -n "${1:-}" ] && exit 0 || exit 1 ;;
  -*)
    die "不认识的选项：$1" ;;
esac

dir=$1; msg=${2:-快照}
enter "$dir"
here=$(pwd -P)

# 同一个板目录同时只许一个 snap.sh：mkdir 锁放在家目录状态区（不放进板目录，免得项目 git status 看到）。
# 不靠进程号判锁的主人死没死：锁目录超过 10 分钟没动就当残留清掉。
mkdir -p "$STATE/locks" || die "建不了 ${STATE}/locks" 2
lock=$STATE/locks/$(printf '%s' "$here" | cksum | awk '{print $1}')
n=0
until mkdir "$lock" 2>/dev/null; do
  if [ -n "$(find "$lock" -maxdepth 0 -mmin +10 2>/dev/null)" ]; then rm -rf "${lock:?}"; continue; fi
  n=$((n+1)); [ $n -gt 600 ] && die "等锁超过 60 秒：${lock}" 2
  sleep 0.1
done
trap 'rm -rf "${lock:?}"' EXIT

list=$lock/snap.list
find . \( -name .history.git -o -name '.history.git.untrusted-*' -o -name .git -o -name '*-backups' -o -name node_modules \) -prune -o -type f \
  \( -name '*.kicad_pcb' -o -name '*.kicad_sch' -o -name '*.kicad_pro' -o -name '*.kicad_dru' \) \
  ! -name '*_autosave-*' -print0 > "$list" || die "列文件失败" 2

if [ -d .history.git ] && ! trusted; then
  aside=.history.git.untrusted-$(date +%Y%m%d%H%M%S)
  mv .history.git "$aside" || die "挪不开不受信的 .history.git" 2
  printf '%s\n' "snap.sh: $(pwd)/.history.git 不是本机 snap.sh 建的，已挪到 ${aside}，另建新的" >&2
fi
if [ ! -d .history.git ]; then
  [ -s "$list" ] || die "目录里没有 KiCad 文件：$(pwd)"
  hg init -q || die "建旁路仓库失败" 2
  printf '*\n' > .history.git/.gitignore          # 项目主仓库看不到这个目录
  printf '/.history.git/\n' >> .history.git/info/exclude
  printf '%s %s\n' "$(ino .history.git)" "$here/.history.git" >> "$REG" || die "写不了登记表 ${REG}" 2
fi
# 存原样字节：不跑任何 clean/smudge filter（项目 .gitattributes 里的 lfs 之类），不转换换行
printf '* -filter -text -ident\n' > .history.git/info/attributes

# 已跟踪的文件：改动和删除都记上；再把目录里现有的 KiCad 文件强制加进来（-f：不受项目 .gitignore 影响）
hg add -u || die "git add -u 失败" 2
while IFS= read -r -d '' f; do
  hg add -f -- "$f" || die "加不进 ${f}" 2
done < "$list"
if hg rev-parse -q --verify HEAD >/dev/null; then
  if hg diff --cached --quiet --no-ext-diff; then
    printf '无变化（%s）\n' "$(hg rev-parse --short HEAD)"
    exit 0
  fi
else
  [ -n "$(hg ls-files)" ] || die "目录里没有 KiCad 文件：$(pwd)"
fi
GIT_AUTHOR_NAME=snap GIT_AUTHOR_EMAIL=snap@localhost GIT_COMMITTER_NAME=snap GIT_COMMITTER_EMAIL=snap@localhost \
  hg commit -q --no-verify -m "$msg" || die "提交失败" 2
printf '%s %s\n' "$(hg rev-parse --short HEAD)" "$msg"
hg diff-tree -r --no-commit-id --name-status --root HEAD
