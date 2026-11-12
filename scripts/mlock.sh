#!/bin/bash
# 命名锁：独占资源（串口、Altium、Spark GPU……）先拿锁再跑命令（借 Backplane docs/locks.md、scripts/bp-lock.sh 的 flock 兜底）。
#   mlock.sh <名> [--nowait] [--note 说明] -- <命令…>   # 拿到锁才跑命令；锁被占时默认排队等，--nowait 直接失败（退出码 75）
#   mlock.sh --list                                      # 每把锁：占用中 / 空闲，占用者的会话、pid、起始时间、命令
# 底层是 OS 的 flock(2)：锁跟着打开的文件走，持有进程（连同继承了它的子进程）全退出，内核自动释放，进程被杀也一样，不用清理。
# Linux 用 flock(1)；Mac 没有 flock(1)，用 perl -MFcntl=:flock 对同一个文件描述符加锁（锁属于打开的文件，perl 退出后 bash 还拿着这个描述符，锁不丢）。
# 命令是 exec 进来的：旁注里的 pid 就是命令本身，退出码也是命令的（命令自己退 75/2 与「锁被占」「用法错」分不出）。命令自己再起常驻后台进程（daemon）会继承锁，要等它也退出才释放。
# 锁描述符挑一个没被占的（10–99）：命令会继承它，嵌套 mlock.sh 时写死 9 号会把外层的锁关掉（复核实测）。命令自己别关继承来的描述符。
# 旁注 <名>.holder 只是给人看的；锁在不在以 flock 探测为准，持有者被杀后旁注留着也显示「空闲」。
# flock 不保证先来后到：几个人同时排队，谁先拿到由内核定。
# 锁目录：MSTACK_LOCK_DIR，默认 ~/.cache/mstack-locks（同一用户各会话共用）。MSTACK_MLOCK_IMPL=perl 强制走 Mac 的 perl 路径（自检用）。
set -u
DIR=${MSTACK_LOCK_DIR:-${HOME}/.cache/mstack-locks}

usage() { sed -n '2,4p' "$0" >&2; exit 2; }

# freefd：打印一个当前没打开的描述符号（bash 3.2 没有 {fd}> 写法）
freefd() { _f=10; while [ "$_f" -lt 100 ]; do [ -e "/dev/fd/$_f" ] || { echo "$_f"; return 0; }; _f=$((_f + 1)); done; return 1; }

# lockfd <描述符号> <nb：1 不等 / 0 等>：对已打开的描述符加排他锁，拿到返回 0
lockfd() {
  if [ "${MSTACK_MLOCK_IMPL:-}" != perl ] && command -v flock >/dev/null 2>&1; then
    if [ "$2" = 1 ]; then flock -n "$1"; else flock "$1"; fi
  else
    perl -MFcntl=:flock -e 'open(my $f, ">>&=", $ARGV[0]) or die "mlock: 打不开描述符 $ARGV[0]: $!\n";
      flock($f, LOCK_EX | ($ARGV[1] ? LOCK_NB : 0)) or exit 1; exit 0' "$1" "$2"
  fi
}

if [ "${1:-}" = --list ]; then
  [ -d "$DIR" ] || { echo "（没有锁：${DIR} 不存在）"; exit 0; }
  n=0
  for f in "$DIR"/*.lock; do
    [ -e "$f" ] || continue
    n=$((n + 1)); name=$(basename "$f" .lock)
    # 探测：不等地试拿一下，拿到就是空闲，立刻关掉描述符放回去（探测那一瞬间别人的 --nowait 可能撞上失败）
    fd=$(freefd) || exit 2
    eval "exec ${fd}>>\"\$f\""
    if lockfd "$fd" 1; then st=空闲; else st=占用中; fi
    eval "exec ${fd}>&-"
    h=$(cat "$DIR/${name}.holder" 2>/dev/null)
    if [ "$st" = 占用中 ]; then printf '%-20s %s  %s\n' "$name" "$st" "${h:-（无旁注）}"
    else printf '%-20s %s  （上次：%s）\n' "$name" "$st" "${h:-无}"; fi
  done
  [ "$n" -gt 0 ] || echo "（没有锁）"
  exit 0
fi

name=${1:-}
# 名字只认 ASCII 字母数字 _ -，1–64 个（LC_ALL=C：bash 3.2 的 [A-Za-z] 按 locale 排序，可能放进非 ASCII）
printf '%s\n' "$name" | LC_ALL=C grep -Eq '^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$' || usage
shift
NOWAIT=0 NOTE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --nowait) NOWAIT=1 ;;
    --note) [ $# -ge 2 ] || usage; NOTE=$2; shift ;;
    --) shift; break ;;
    *) usage ;;
  esac
  shift
done
[ $# -gt 0 ] || usage

mkdir -p "$DIR" || exit 2
LOCK="$DIR/${name}.lock" HOLD="$DIR/${name}.holder"
FD=$(freefd) || { echo "mlock: 没有空闲描述符" >&2; exit 2; }
eval "exec ${FD}>>\"\$LOCK\"" || exit 2
if ! lockfd "$FD" 1; then
  [ "$NOWAIT" = 1 ] && { echo "mlock: 锁 ${name} 被占：$(cat "$HOLD" 2>/dev/null || echo 无旁注)" >&2; exit 75; }
  echo "mlock: 锁 ${name} 被占：$(cat "$HOLD" 2>/dev/null || echo 无旁注)；排队等……" >&2
  lockfd "$FD" 0 || { echo "mlock: 加锁失败" >&2; exit 2; }
fi
# 拿到锁才写旁注（先写临时文件再改名，--list 不会读到半行）
SESSION=${MSTACK_SESSION:-${CLAUDE_CODE_SESSION_ID:-${CODEX_SESSION_ID:-$(whoami)@$(hostname -s 2>/dev/null)}}}
# 说明和命令里的换行换成空格，旁注保持一行
printf 'session=%s pid=%s since=%s(%s) note=%s cmd=%s\n' "$SESSION" "$$" "$(date +%s)" "$(date '+%Y-%m-%d %H:%M:%S')" \
  "$(printf '%s' "${NOTE:--}" | tr '\n' ' ')" "$(printf '%s' "$*" | tr '\n' ' ')" > "${HOLD}.$$" && mv -f "${HOLD}.$$" "$HOLD"
exec "$@"
