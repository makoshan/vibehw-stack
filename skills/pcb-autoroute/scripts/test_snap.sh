#!/usr/bin/env bash
# snap.sh 自检：快照 → 改 → 再快照 → 取回旧版内容一致；项目主仓库 git status、HEAD、refs 不受影响。
# 用法：bash test_snap.sh [snap.sh 路径]（默认同目录的 snap.sh；改坏版本自检时传别的路径）
# 只在 mktemp 建的临时目录里干活，结束删掉它。
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SNAP=${1:-$HERE/snap.sh}
T=$(mktemp -d "${TMPDIR:-/tmp}/snaptest.XXXXXX") || exit 2
trap 'rm -rf "${T:?}"' EXIT
# 登记表、锁、全局 git 配置都在 HOME 下：指到临时目录，不碰真家目录。
# 全局配置必须写 $HOME/.gitconfig：snap.sh 会清掉 GIT_CONFIG_GLOBAL 这类 GIT_* 环境变量
export HOME="$T/home"; mkdir -p "$HOME"; unset XDG_STATE_HOME GIT_CONFIG_GLOBAL
GC=$HOME/.gitconfig; : > "$GC"
pass=0; fail=0
ok() { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
no() { fail=$((fail+1)); printf 'FAIL %s\n' "$1"; }
check() { if eval "$2"; then ok "$1"; else no "$1"; fi; }
pg() { git -C "$T/proj" -c user.name=t -c user.email=t@t "$@"; }
hist() { git --git-dir="$T/proj/board/.history.git" "$@"; }

mkdir -p "$T/proj/board/sub" "$T/proj/board/board-backups" "$T/proj/other"
cd "$T/proj" || exit 2
git init -q .
printf '(kicad_pcb v1)\n' > board/a.kicad_pcb
printf '(kicad_sch v1)\n' > board/a.kicad_sch
printf '{}\n' > board/a.kicad_pro
printf '(version 1)\n' > board/a.kicad_dru
printf '(kicad_sch sub)\n' > board/sub/b.kicad_sch
printf 'cache\n' > board/fp-info-cache
printf '(kicad_pcb backup)\n' > board/board-backups/a.kicad_pcb
printf '(kicad_pcb autosave)\n' > board/_autosave-a.kicad_pcb
printf 'x\n' > other/readme.txt
printf '_autosave-*\n*-backups/\n' > .gitignore
pg add -A && pg commit -qm init
head0=$(pg rev-parse HEAD); refs0=$(pg for-each-ref | wc -l)

out=$(bash "$SNAP" board "初版"); code=$?
check "首次快照退出 0" '[ $code -eq 0 ]'
check "首次快照打印版本号和说明" 'printf "%s" "$out" | head -1 | grep -q " 初版$"'
v1=$(hist rev-parse HEAD 2>/dev/null)
check "旁路仓库有一版" '[ "$(hist rev-list --count HEAD 2>/dev/null)" = 1 ]'
files=$(hist ls-tree -r --name-only HEAD 2>/dev/null | tr '\n' ' ')
check "只快照 4 种 KiCad 文件（含子目录），不含备份/自动保存/缓存" '[ "$files" = "a.kicad_dru a.kicad_pcb a.kicad_pro a.kicad_sch sub/b.kicad_sch " ]'
check "项目主仓库 git status 干净" '[ -z "$(pg status --porcelain)" ]'
check "项目主仓库 git status --untracked-files=all 也干净" '[ -z "$(pg status --porcelain --untracked-files=all)" ]'
check "项目 HEAD 没动" '[ "$(pg rev-parse HEAD)" = "$head0" ]'
check "项目 refs 没多" '[ "$(pg for-each-ref | wc -l)" = "$refs0" ]'
check "项目 .gitignore 没动" '[ "$(cat .gitignore)" = "$(printf "_autosave-*\n*-backups/")" ]'

out=$(bash "$SNAP" board "再快照"); code=$?
check "没变化不提交" '[ $code -eq 0 ] && printf "%s" "$out" | grep -q "无变化" && [ "$(hist rev-list --count HEAD)" = 1 ]'

printf '(kicad_pcb v2 改了走线)\n' > board/a.kicad_pcb
rm board/a.kicad_dru
printf '(kicad_pcb new)\n' > board/sub/c.kicad_pcb
bash "$SNAP" board "改走线" >/dev/null; code=$?
check "改后快照退出 0、两版" '[ $code -eq 0 ] && [ "$(hist rev-list --count HEAD)" = 2 ]'
check "删掉的 .kicad_dru 记成删除" '[ "$(hist diff-tree -r --no-commit-id --name-status HEAD | grep a.kicad_dru)" = "$(printf "D\ta.kicad_dru")" ]'
check "新加的子目录文件记上" 'hist ls-tree -r --name-only HEAD | grep -qx sub/c.kicad_pcb'

old=$(bash "$SNAP" --show "$v1" a.kicad_pcb board)
check "--show 取回旧版内容一致" '[ "$old" = "(kicad_pcb v1)" ]'
cur=$(bash "$SNAP" --show HEAD a.kicad_pcb board)
check "--show HEAD 是新版" '[ "$cur" = "(kicad_pcb v2 改了走线)" ]'
dru=$(bash "$SNAP" --show "$v1" a.kicad_dru board)
check "删掉的文件能从旧版取回" '[ "$dru" = "(version 1)" ]'
bash "$SNAP" --show "$v1" nope.kicad_pcb board >/dev/null 2>&1; code=$?
check "取不存在的文件退出 2" '[ $code -eq 2 ]'
log=$(cd board && bash "$SNAP" --log)
check "--log 列出两版说明" 'printf "%s" "$log" | grep -q "改走线" && printf "%s" "$log" | grep -q "初版"'

check "项目主仓库只看到改的 KiCad 文件，看不到 .history.git" '[ "$(pg status --porcelain --untracked-files=all | LC_ALL=C sort | tr "\n" " ")" = " D board/a.kicad_dru  M board/a.kicad_pcb ?? board/sub/c.kicad_pcb " ]'
check "项目 HEAD 仍没动" '[ "$(pg rev-parse HEAD)" = "$head0" ]'

# 调用方带着项目的 GIT_DIR 环境（比如从钩子里调）：照样只写旁路仓库
printf '(kicad_sch v2)\n' > board/a.kicad_sch
idx0=$(git hash-object "$T/proj/.git/index")
GIT_DIR="$T/proj/.git" GIT_INDEX_FILE="$T/proj/.git/index" bash "$SNAP" board "带环境" >/dev/null; code=$?
check "带 GIT_DIR 环境也写旁路仓库" '[ $code -eq 0 ] && [ "$(hist rev-list --count HEAD)" = 3 ]'
check "带 GIT_DIR 环境后项目 HEAD、index 文件逐字节没动" '[ "$(pg rev-parse HEAD)" = "$head0" ] && [ "$(git hash-object "$T/proj/.git/index")" = "$idx0" ]'

# 项目 .gitignore 忽略了 .kicad_pcb，新加的板文件也照样快照
printf '*.kicad_pcb\n' > board/.gitignore
printf '(kicad_pcb v3)\n' > board/a.kicad_pcb
printf '(kicad_pcb ignored new)\n' > board/n.kicad_pcb
bash "$SNAP" board "被忽略也快照" >/dev/null
check "项目 .gitignore 不影响快照（已跟踪的）" '[ "$(bash "$SNAP" --show HEAD a.kicad_pcb board)" = "(kicad_pcb v3)" ]'
check "项目 .gitignore 不影响快照（新加的）" '[ "$(bash "$SNAP" --show HEAD n.kicad_pcb board 2>/dev/null)" = "(kicad_pcb ignored new)" ]'

# 钩子目录（pre-commit 被 --no-verify 跳过，post-commit 只有 hooksPath 能挡）、全局 filter 都配上，旁路提交不跑、不过滤
mkdir -p "$T/hooks"
for h in pre-commit post-commit; do printf '#!/bin/sh\ntouch "%s/hook-ran-%s"\n' "$T" "$h" > "$T/hooks/$h"; chmod +x "$T/hooks/$h"; done
printf '[core]\n\thooksPath = %s/hooks\n[filter "x"]\n\tclean = sed s/REAL/POINTER/\n' "$T" > "$GC"
printf '*.kicad_pcb filter=x\n' > board/.gitattributes
printf '(kicad_pcb REAL)\n' > board/a.kicad_pcb
bash "$SNAP" board "有钩子" >/dev/null; code=$?
: > "$GC"
check "全局钩子不跑、照样提交" '[ $code -eq 0 ] && [ ! -e "$T/hook-ran-pre-commit" ] && [ ! -e "$T/hook-ran-post-commit" ] && [ "$(hist log -1 --format=%s)" = "有钩子" ]'
check "filter 不改存进去的内容" '[ "$(bash "$SNAP" --show HEAD a.kicad_pcb board)" = "(kicad_pcb REAL)" ]'

# 带 GIT_OBJECT_DIRECTORY / GIT_ALTERNATE_OBJECT_DIRECTORIES 调用：对象不写进项目，旁路仓库不缺对象
obj0=$(find "$T/proj/.git/objects" -type f | wc -l)
printf '(kicad_pcb objenv)\n' > board/a.kicad_pcb
GIT_OBJECT_DIRECTORY="$T/proj/.git/objects" GIT_ALTERNATE_OBJECT_DIRECTORIES="$T/proj/.git/objects" bash "$SNAP" board "带对象环境" >/dev/null; code=$?
check "带对象目录环境：项目 objects 数不变" '[ $code -eq 0 ] && [ "$(find "$T/proj/.git/objects" -type f | wc -l)" = "$obj0" ]'
check "带对象目录环境：旁路仓库 fsck 干净" 'hist fsck --no-dangling >/dev/null 2>&1'

# 带空格、中文的文件名，深层子目录
mkdir -p "board/子 目录/a/b/c/d/e"
printf '(kicad_pcb 中文)\n' > "board/子 目录/板 1.kicad_pcb"
printf '(kicad_sch deep)\n' > "board/子 目录/a/b/c/d/e/深.kicad_sch"
bash "$SNAP" board "中文名" >/dev/null; code=$?
check "空格中文文件名能存能取" '[ $code -eq 0 ] && [ "$(bash "$SNAP" --show HEAD "子 目录/板 1.kicad_pcb" board)" = "(kicad_pcb 中文)" ]'
check "深层子目录也快照" '[ "$(bash "$SNAP" --show HEAD "子 目录/a/b/c/d/e/深.kicad_sch" board)" = "(kicad_sch deep)" ]'

# .history.git 是指向项目 .git 的符号链接：拒绝，项目不动
mkdir -p "$T/p2/board"; git -C "$T/p2" init -q; printf '(kicad_pcb)\n' > "$T/p2/board/a.kicad_pcb"
git -C "$T/p2" add -A; git -C "$T/p2" -c user.name=t -c user.email=t@t commit -qm i
h2=$(git -C "$T/p2" rev-parse HEAD); i2=$(git hash-object "$T/p2/.git/index")
ln -s ../.git "$T/p2/board/.history.git"
printf '(kicad_pcb v2)\n' > "$T/p2/board/a.kicad_pcb"
bash "$SNAP" "$T/p2/board" "sym" >/dev/null 2>&1; code=$?
check "符号链接 .history.git 拒绝、项目 HEAD 和 index 不动" '[ $code -eq 1 ] && [ "$(git -C "$T/p2" rev-parse HEAD)" = "$h2" ] && [ "$(git hash-object "$T/p2/.git/index")" = "$i2" ]'

# 别人放进来的 .history.git（带会执行命令的 filter 配置）：不在它上面跑 git，挪开另建
mkdir -p "$T/p3"; printf '(kicad_pcb p3)\n' > "$T/p3/a.kicad_pcb"; printf '* filter=evil\n' > "$T/p3/.gitattributes"
git init -q --bare "$T/p3/.history.git"
git --git-dir="$T/p3/.history.git" config filter.evil.clean "touch $T/PWNED; cat"
git --git-dir="$T/p3/.history.git" config core.pager "touch $T/PWNED; cat"
bash "$SNAP" --log "$T/p3" >/dev/null 2>&1; code=$?
check "不受信仓库 --log 拒绝" '[ $code -eq 1 ] && [ ! -e "$T/PWNED" ]'
bash "$SNAP" "$T/p3" "接手" >/dev/null 2>&1; code=$?
check "不受信仓库被挪开、另建、不执行它的配置" '[ $code -eq 0 ] && [ ! -e "$T/PWNED" ] && ls -d "$T"/p3/.history.git.untrusted-* >/dev/null 2>&1 && [ "$(git --git-dir="$T/p3/.history.git" rev-list --count HEAD)" = 1 ]'
check "另建的仓库受信，--log 能读" 'bash "$SNAP" --log "$T/p3" | grep -q 接手'

# 并发：新目录 4 个同时快照、已有仓库 3 个同时快照，全部成功
mkdir -p "$T/p4"; printf '(kicad_pcb p4)\n' > "$T/p4/a.kicad_pcb"
pids=""; for i in 1 2 3 4; do bash "$SNAP" "$T/p4" "并发$i" >/dev/null 2>&1 & pids="$pids $!"; done
bad=0; for p in $pids; do wait "$p" || bad=$((bad+1)); done
check "新目录 4 个并发全部退出 0、只一版" '[ $bad -eq 0 ] && [ "$(git --git-dir="$T/p4/.history.git" rev-list --count HEAD)" = 1 ]'
pids=""; for i in 1 2 3; do printf '(kicad_pcb p4 %s)\n' "$i" > "$T/p4/c$i.kicad_pcb"; bash "$SNAP" "$T/p4" "再并发$i" >/dev/null 2>&1 & pids="$pids $!"; done
bad=0; for p in $pids; do wait "$p" || bad=$((bad+1)); done
check "已有仓库 3 个并发全部退出 0、文件都进去" '[ $bad -eq 0 ] && [ "$(git --git-dir="$T/p4/.history.git" ls-tree --name-only HEAD | grep -c "^c[123].kicad_pcb$")" = 3 ]'
check "锁都释放了" '[ -z "$(ls "$HOME/.local/state/mstack/locks")" ]'

# 拒绝的情况
mkdir -p "$T/empty"; printf 'x\n' > "$T/empty/readme.txt"
bash "$SNAP" "$T/empty" >/dev/null 2>&1; code=$?
check "没有 KiCad 文件的目录退出 1、不留空仓库" '[ $code -eq 1 ] && [ ! -e "$T/empty/.history.git" ]'
bash "$SNAP" "$T/nope" >/dev/null 2>&1; code=$?
check "目录不存在退出 1" '[ $code -eq 1 ]'
mkdir -p "$T/fakehome"; printf '(kicad_pcb)\n' > "$T/fakehome/h.kicad_pcb"
HOME="$T/fakehome" bash "$SNAP" "$T/fakehome" >/dev/null 2>&1; code=$?
check "拒绝在家目录建仓库" '[ $code -eq 1 ] && [ ! -e "$T/fakehome/.history.git" ]'
bash "$SNAP" / >/dev/null 2>&1; code=$?
check "拒绝在根目录建仓库" '[ $code -eq 1 ]'
bash "$SNAP" >/dev/null 2>&1; code=$?
check "不给参数退出 1" '[ $code -eq 1 ]'

printf '通过 %d，失败 %d\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
