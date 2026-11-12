#!/bin/bash
# 「只动本项目进程」从文字变成扫源码就红（借 Backplane native/guard/src/scripts.rs 的 kills_by_name）。
# 坑：按名字或模式杀进程会连带别人的进程；模式里的变量为空时（pkill -f -- "$T/"）匹配本用户全部进程。
# 只读源码，绝不执行扫到的任何命令。
#
# 用法：
#   check-no-pkill.sh [仓库根 [目录…]]        扫 <根>/scripts/ 与 <根>/skills/*/scripts/ 下全部文本文件（根默认本脚本上一级）
#                                              给了目录就只扫这些（相对根），项目 CI 照抄时用：check-no-pkill.sh . scripts test deploy tools
#   check-no-pkill.sh --list [仓库根 [目录…]]  另外列出被放行的行
#   check-no-pkill.sh --selftest               正反例自检（只在临时目录写样例文件再扫，不跑样例）
# 退出码：0 干净；1 有命中（打印 文件:行号: 原文）；2 用法错、awk 出错或自检失败。
#
# 判红（同一行内）：
#   - 命令位置的 pkill / killall / killall5：前面不是字母数字 _ -，后面跟空白、引号、; ) | & < > , 或反引号（Python 的 ['pkill', …] 也算）；
#   - kill 和 $(pgrep / `pgrep / $(pidof 同行（不分 -f / -P，确需按父进程号找就写放行）；xargs … kill（管道前面是 pgrep、ps|grep、lsof 都一样）；fuser … -k。
# 不判（已知漏网）：pgrep 和 kill 分两行写；行尾的 K=pkill 这类赋值；kill -9 -1 这类按进程号的误杀归 offload.sh 的守卫管。
# 不算：
#   - 整行注释（# 或 // 开头）；
#   - 假 kill 定义：整行就是一个 kill() / pkill() / killall() { … } 定义（或定义开头、行内没有 }），且行内不经 command / sudo / env / exec / 绝对路径调真命令；
#     export -f 一串函数名。自检必须用假 kill，这是规定写法；
#   - 放行：同一行带「# allow-kill: 理由」，或紧挨的上一行是整行注释「# allow-kill: 理由」；理由不能空（空理由照样判红）。
set -u

ROOT="" LIST=0
[ "${1:-}" = --list ] && { LIST=1; shift; }
case "${1:-}" in
  --selftest) MODE=selftest ;;
  -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
  -*) echo "用法：check-no-pkill.sh [--list] [仓库根 [目录…]] | --selftest" >&2; exit 2 ;;
  *) MODE=scan; ROOT="${1:-}"; [ $# -gt 0 ] && shift ;;
esac

# awk 程序：读一个文件，按上面的规则打印「行号<TAB>标记<TAB>原文」，标记 RED / ALLOW。
# 写成函数里的 here-doc：bash 3.2 解析 $( … ) 里带引号和单个 ) 的 here-doc 会出错。
awk_prog() {
cat <<'AWK'
function boundary_word(s, w,    i, pre, post, rest) {
  rest = s
  while ((i = index(rest, w)) > 0) {
    pre = (i > 1) ? substr(rest, i - 1, 1) : ""
    post = substr(rest, i + length(w), 1)
    if (pre !~ /[A-Za-z0-9_-]/ && post ~ /^[ \t'"`;)|&<>,]$/) return 1
    rest = substr(rest, i + length(w))
  }
  return 0
}
function plain_word(s, w,    i, pre, post, rest) {
  rest = s
  while ((i = index(rest, w)) > 0) {
    pre = (i > 1) ? substr(rest, i - 1, 1) : ""
    post = substr(rest, i + length(w), 1)
    if (pre !~ /[A-Za-z0-9_-]/ && post !~ /[A-Za-z0-9_-]/) return 1
    rest = substr(rest, i + length(w))
  }
  return 0
}
{
  line = $0; t = line; sub(/^[ \t]+/, "", t)
  is_comment = (t ~ /^#/ || t ~ /^\/\//)
  allow_here = (line ~ /#[ \t]*allow-kill:[ \t]*[^ \t]/)
  empty_allow = (line ~ /allow-kill:[ \t]*$/)
  allowed = allow_here || prev_allow
  prev_allow = is_comment && allow_here
  if (is_comment) { if (empty_allow) print FNR "\tRED\t" line " ← allow-kill 缺理由"; next }
  # 假 kill 定义：整行就是一个定义（或定义开头），行内不经 command / sudo / env / exec / 绝对路径调真命令
  if (t ~ /^(kill|pkill|killall)[ \t]*\(\)[ \t]*\{([^}]*\}[ \t;]*|[^}]*)$/ && t !~ /(command|sudo|env|exec)[ \t]+(-[^ \t]+[ \t]+)*(kill|pkill|killall)/ && t !~ /\/(kill|pkill|killall)([^A-Za-z0-9_-]|$)/) next  # allow-kill: 本检查的匹配式
  if (t ~ /^export[ \t]+-f[ \t]+[A-Za-z_ \t]+$/) next
  hit = boundary_word(line, "pkill") || boundary_word(line, "killall") || boundary_word(line, "killall5")  # allow-kill: 本检查的匹配式
  if (!hit && plain_word(line, "kill") && (line ~ /\$\([ \t]*(pgrep|pidof)/ || line ~ /`[ \t]*(pgrep|pidof)/)) hit = 1
  if (!hit && (i = index(line, "xargs")) > 0 && plain_word(substr(line, i), "kill")) hit = 1  # allow-kill: 本检查的匹配式
  if (!hit && plain_word(line, "fuser") && line ~ /fuser[^|;&]*[ \t]-[A-Za-z]*k/) hit = 1
  if (!hit) next
  if (empty_allow) { print FNR "\tRED\t" line " ← allow-kill 缺理由"; next }
  print FNR "\t" (allowed ? "ALLOW" : "RED") "\t" line
}
AWK
}

scan() {  # scan <根> <是否列放行> [目录…]：打印命中，返回 0 干净 / 1 有红 / 2 没扫到或 awk 出错
  _root="$1" _list="$2" _red=0 _n=0; shift 2
  if [ $# = 0 ]; then  # 默认范围；glob 要对着根展开，不是当前目录
    set -- scripts
    for _s in "$_root"/skills/*/scripts; do [ -d "$_s" ] && set -- "$@" "${_s#"$_root"/}"; done
  fi
  _tmp=$(mktemp) _out=$(mktemp) _prog=$(awk_prog)
  for _d in "$@"; do
    case "$_d" in /*) ;; *) _d="$_root/$_d" ;; esac
    [ -d "$_d" ] && find "$_d" -type f ! -path '*/.git/*' ! -path '*/node_modules/*' -print
  done | sort > "$_tmp"
  while IFS= read -r _f; do
    grep -Iq . "$_f" 2>/dev/null || continue   # 跳过二进制和空文件
    _n=$((_n + 1))
    _rel="${_f#"$_root"/}"
    # LC_ALL=C：BSD awk 按字节切到半个中文字符会报错退出，退出码非 0 就整体报错，不能当干净
    LC_ALL=C awk "$_prog" "$_f" > "$_out" || { echo "awk 读 ${_rel} 出错" >&2; rm -f "${_tmp:?}" "${_out:?}"; return 2; }
    while IFS="$(printf '\t')" read -r _ln _tag _txt; do
      [ -n "$_ln" ] || continue
      if [ "$_tag" = RED ]; then _red=1; echo "${_rel}:${_ln}: ${_txt}"
      elif [ "$_list" = 1 ]; then echo "放行 ${_rel}:${_ln}: ${_txt}"; fi
    done < "$_out"
  done < "$_tmp"
  rm -f "${_tmp:?}" "${_out:?}"
  [ "$_n" -gt 0 ] || { echo "一个文件都没扫到（${_root}）" >&2; return 2; }
  if [ "$_red" = 1 ]; then
    echo "上面这些按名字或模式结束进程；改成按自己记下的进程号或进程组停，确需保留就在行尾写「# allow-kill: 理由」" >&2
    return 1
  fi
  echo "干净（扫了 ${_n} 个文件）"
  return 0
}

selftest() {
  _t=$(mktemp -d); _fail=0; _nb=0
  # 样例里的命令名拼出来，免得本脚本自己被扫红
  PK="p""kill"; KA="kill""all"; PG="p""grep"; PO="pid""of"; FU="fu""ser"; XA="xa""rgs"
  mkdir -p "$_t/scripts" "$_t/skills/a/scripts" "$_t/other"
  # 反例：每行单独一个文件，必须判红
  while IFS= read -r _l; do
    _nb=$((_nb + 1)); _r="$_t/r$_nb"; mkdir -p "$_r/scripts"; printf '%s\n' "$_l" > "$_r/scripts/x.sh"
    scan "$_r" 0 >/dev/null 2>&1; _rc=$?
    [ "$_rc" = 1 ] || { echo "反例没判红（退出码 ${_rc}）：$_l"; _fail=1; }
  done <<EOF2
trap '${PK} -f -- "\$T/"' EXIT
${KA} backplane-hub
${KA}5 -9
kill \$HP \$(${PG} -f -- "--home \$O")
kill \$(${PO} node)
subprocess.run(['${PK}','-f',home])
os.system("${PK} -f foo")
kill -9 \`${PG} -f foo\`
${PG} -f foo | ${XA} kill
ps aux | grep hub | awk '{print \$2}' | ${XA} -r kill -9
lsof -ti :3000 | ${XA} kill
${FU} -k 3000/tcp
/usr/bin/${PK} -f foo
${PK}>/dev/null -f foo
${PK}() { command ${PK} "\$@"; }
${PK}() { sudo ${PK} "\$@"; }
kill() { :; }; ${KA} -9 hub
${PK} -f foo   # allow-kill:
echo "allow-kill: 写在字符串里不算"; ${PK} -f hub
echo "见。kill"; ${PK} -f hub
EOF2
  # 「上一行放行」只从整行注释往下传：行尾放行的下一行照样红
  mkdir -p "$_t/n1/scripts"; printf '%s -f a  # allow-kill: x\n%s -9 other\n' "$PK" "$KA" > "$_t/n1/scripts/x.sh"
  _o=$(scan "$_t/n1" 0 2>/dev/null); [ $? = 1 ] && echo "$_o" | grep -q '^scripts/x.sh:2:' || { echo "行尾放行传到了下一行"; _fail=1; }
  # 默认范围含 skills/*/scripts：只在那里放一条红
  mkdir -p "$_t/n2/scripts" "$_t/n2/skills/b/scripts"; echo ok > "$_t/n2/scripts/ok.sh"; printf '%s -f x\n' "$PK" > "$_t/n2/skills/b/scripts/y.py"
  _o=$(scan "$_t/n2" 0 2>/dev/null); [ $? = 1 ] && echo "$_o" | grep -q '^skills/b/scripts/y.py:1:' || { echo "默认范围没扫 skills/*/scripts"; _fail=1; }
  # 正例：放在 scripts/ 与 skills/*/scripts/ 里，整体必须干净
  cat > "$_t/scripts/ok.sh" <<EOF2
# never ${PK} -f
kill -TERM -- "-\$g"
kill -9 "\$HP"
own_stop
un${PK}_x
my-${PK} -x
${PK}() { printf '${PK} %s\n' "\$*" >> "\$KILL_LOG"; }
kill() { printf '%s\n' "\$*" >> "\$KILL_LOG"
export -f kill ${PK} ${KA}
安全：自检一律换上假 kill/${PK}/${KA}
${PK} -f "\$MY_JOB_DIR"   # allow-kill: 样例，只为测放行
# allow-kill: 上一行放行
${KA} sleep
EOF2
  printf 'encoder.kill("SIGTERM")\n中文。kill 说明\n' > "$_t/skills/a/scripts/ok.mjs"
  # 不在扫描范围的目录：有红也不该被扫到
  printf '%s -f x\n' "$PK" > "$_t/other/out.sh"
  _o=$(scan "$_t" 0 2>&1) || { echo "正例被判红："; echo "$_o"; _fail=1; }
  # 显式给目录：只扫给的目录，other/ 里的红要被看到
  _o=$(cd "$_t" && scan . 0 other 2>/dev/null); [ $? = 1 ] && echo "$_o" | grep -q '^other/out.sh:1:' || { echo "给目录 other 没扫到它的红"; _fail=1; }
  # 放行行在 --list 里要列出来
  _c=$(scan "$_t" 1 2>/dev/null | grep -c '^放行 ')
  [ "$_c" = 2 ] || { echo "--list 应列 2 条放行，实际 ${_c}"; _fail=1; }
  rm -rf "${_t:?}"
  [ "$_fail" = 0 ] && { echo "selftest PASS（反例 ${_nb} 条全红，另 2 个范围/放行反例红，正例 15 行干净，放行 2 条，指定目录 1 条）"; return 0; }
  return 2
}

if [ "$MODE" = selftest ]; then selftest; exit $?; fi
[ -n "$ROOT" ] || ROOT=$(cd "$(dirname "$0")/.." && pwd)
scan "$ROOT" "$LIST" "$@"
