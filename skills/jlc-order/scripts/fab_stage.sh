#!/usr/bin/env bash
# 从已推到 origin/main 的提交出制造包，不从工作区直出。
# 借 Backplane docs/design-review.md:69-101（在提交的 archive 上导出、先写暂存目录、成功再改名、已有的包不重写、
# 软链不许指出仓库）和 native/host/src/kicad/release.rs:78-87（manifest 记提交号）。
#
#   fab_stage.sh [--review <工程目录>] [--pin <路径>]... <提交> <输出根目录> -- <出包命令…>
#
# <工程目录>、--pin 路径都相对仓库根写；在仓库任何子目录里运行都一样（脚本先 cd 到仓库根）。
# 顺序：
#   1. 该提交必须在 origin/main 上（git merge-base --is-ancestor，比的是 refs/remotes/origin/main），不在就拒。先自己 git fetch。
#   2. git archive 整个仓库到临时目录。软链指到 archive 外（绝对路径或 ../ 出界）、LFS 指针文件都拒；
#      提交里有子模块（archive 不带内容）或 .gitattributes 用了 export-ignore/export-subst（archive 和提交不一致）也拒。
#      --review 时把工作区 <工程目录>/.review/review.json 放进 archive 副本，用 review_gate.py check 核：
#      必须 current 且 ok，即复核过的就是这个提交里的文件。不复核直接下（Mako 本人说的）就不加 --review。
#   3. 在 archive 根跑出包命令；环境变量 FAB_OUT 指向 <目标>.staging，FAB_COMMIT 是完整提交号，FAB_SRC 是 archive 目录。
#      命令退出码非 0，或退出码 0 但 FAB_OUT 里没有非空文件，或输出里有软链，都算失败，删掉暂存目录。
#   4. 成功后写 SOURCE.json（提交号、树号、命令、每个输出文件的 sha256 和字节数、复核指纹），再改名成
#      <输出根目录>/fab-<YYYYMMDD>-<sha8>。目标或暂存目录已存在就拒，不覆盖、不续写。
#   --pin 把源文件（如板文件）的 git blob 号和 sha256 记进 SOURCE.json 的 files 字段，格式同 yuanli-band
#   host/fab_pin.py：加了 --pin 才能 `fab_pin.py verify|fetch|pushed SOURCE.json`（verify 不带目录，比仓库原路径）。
# 退出码：0 成功；1 出包失败；2 用法错误或被拒（不在 origin/main、目标已存在、复核不是 current 且 ok 等）。
set -euo pipefail

die() { echo "fab_stage: $1" >&2; exit "${2:-2}"; }
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)

pins=()
review=''
while [ $# -gt 0 ]; do
  case "$1" in
    --pin) [ $# -ge 2 ] || die "--pin 要跟路径"; pins+=("$2"); shift 2 ;;
    --review) [ $# -ge 2 ] || die "--review 要跟工程目录"; review=$2; shift 2 ;;
    --) die "缺 <提交> <输出根目录>" ;;
    -*) die "不认识的选项 $1" ;;
    *) break ;;
  esac
done
[ $# -ge 4 ] && [ "$3" = "--" ] || die "用法：fab_stage.sh [--review 工程目录] [--pin 路径]... <提交> <输出根目录> -- <出包命令…>"
rev=$1; outroot=$2; shift 3

git rev-parse --git-dir >/dev/null 2>&1 || die "当前目录不在 git 仓库里"
case "$outroot" in /*) ;; *) outroot="$PWD/$outroot" ;; esac   # 先按调用处解析，再去仓库根；过了检查才建
cd -- "$(git rev-parse --show-toplevel)"

main=refs/remotes/origin/main                            # 写全，本地叫 origin/main 的分支顶替不了
sha=$(git rev-parse --verify --quiet "${rev}^{commit}") || die "找不到提交 $rev"
git rev-parse --verify --quiet "$main" >/dev/null || die "没有 origin/main（先 git fetch）"
git merge-base --is-ancestor "$sha" "$main" || die "提交 ${sha:0:12} 不在 origin/main 上：先推送并 git fetch，再出包"
tree=$(git rev-parse "${sha}^{tree}")
for p in ${pins[@]+"${pins[@]}"}; do
  mode=$(git ls-tree "$sha" -- "$p" | awk '{print $1}')
  [ -n "$mode" ] || die "--pin $p 不在提交 ${sha:0:12} 里（路径相对仓库根）"
  [ "$mode" != 120000 ] || die "--pin $p 是软链，blob 记的是链接文本，换成它指向的文件"
  [ "$mode" = 100644 ] || [ "$mode" = 100755 ] || die "--pin $p 不是普通文件（git 模式 ${mode}），要逐个写文件"
done
if [ -n "${pins[*]+x}" ]; then
  dup=$(printf '%s\n' "${pins[@]##*/}" | sort | uniq -d | head -1)
  [ -z "$dup" ] || die "--pin 里有同名文件 ${dup}（fab_pin.py 平铺取回会互相覆盖）"
fi
[ -z "$(git ls-tree -r "$sha" | awk '$1 == "160000"' | head -1)" ] || die "提交 ${sha:0:12} 里有子模块，git archive 不带它的内容"
attrs=$( { git ls-tree -r --name-only "$sha" | grep -E '(^|/)\.gitattributes$' | while IFS= read -r f; do git show "$sha:$f"; done
           cat "$(git rev-parse --git-path info/attributes)" 2>/dev/null; } | grep -E 'export-(ignore|subst)' | head -1 || true)
[ -z "$attrs" ] || die "属性里有 export-ignore/export-subst，git archive 出来的和提交不一致：$attrs"
if [ -n "$review" ]; then
  [ -n "$(git ls-tree "$sha" -- "$review")" ] || die "--review $review 不在提交 ${sha:0:12} 里（路径相对仓库根）"
  [ -f "$review/.review/review.json" ] || die "没有 $review/.review/review.json：先 review_gate.py record"
fi

mkdir -p -- "$outroot"
outroot=$(CDPATH='' cd -- "$outroot" && pwd -P)
target="$outroot/fab-$(date +%Y%m%d)-${sha:0:8}"
staging="$target.staging"
[ ! -e "$target" ] || die "目标已存在，不覆盖：$target"
[ ! -e "$staging" ] || die "暂存目录还在（上次中断？先看一眼再删）：$staging"

src=$(mktemp -d "${TMPDIR:-/tmp}/fab-stage-src.XXXXXX")
cleanup() { rm -rf -- "${src:?}"; }
trap cleanup EXIT
git archive --format=tar "$sha" | tar -x -C "$src"

python3 - "$src" <<'PY' || die "archive 检查没过"
import os, sys
root = os.path.realpath(sys.argv[1])
bad, lfs = [], []
for d, dirs, fs in os.walk(root):
    for n in dirs + fs:
        p = os.path.join(d, n)
        rel = os.path.relpath(p, root)
        if os.path.islink(p):
            t = os.readlink(p)
            dest = os.path.normpath(os.path.join(os.path.dirname(p), t))
            if os.path.isabs(t) or not (dest == root or dest.startswith(root + os.sep)):
                bad.append(f'{rel} -> {t}')
        elif n in fs and os.path.getsize(p) < 1024:
            with open(p, 'rb') as fh:
                if fh.read(40).startswith(b'version https://git-lfs.github.com/spec/'):
                    lfs.append(rel)
for x in bad[:10]:
    print(f'fab_stage: 软链指到仓库外：{x}', file=sys.stderr)
for x in lfs[:10]:
    print(f'fab_stage: LFS 指针文件（git archive 不取内容）：{x}', file=sys.stderr)
sys.exit(1 if bad or lfs else 0)
PY

review_fp=''
if [ -n "$review" ]; then
  mkdir -p "$src/$review/.review"
  cp "$review/.review/review.json" "$src/$review/.review/review.json"
  set +e
  msg=$(python3 "$here/review_gate.py" check "$src/$review" 2>&1)
  rc=$?
  set -e
  [ "$rc" -eq 0 ] || die "复核没过（review_gate check 退出 ${rc}，按提交 ${sha:0:12} 里的文件核；
工作区里没提交或被 gitignore 的设计文件也会让这里报「删除」，没提交的先提交，不该算的 record 时用 --exclude）：
$msg"
  review_fp=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["fingerprint"])' "$review/.review/review.json")
  rm -rf -- "${src:?}/${review:?}/.review"
fi

mkdir "$staging"
set +e
(cd "$src" && FAB_OUT="$staging" FAB_COMMIT="$sha" FAB_SRC="$src" "$@")
rc=$?
set -e
fail() { rm -rf -- "${staging:?}"; die "$1" 1; }
[ "$rc" -eq 0 ] || fail "出包命令退出码 ${rc}，已删暂存目录"
[ -n "$(find "$staging" -type f -size +0 -print -quit)" ] || fail "出包命令退出码 0 但 FAB_OUT 里没有非空文件，已删暂存目录"
[ -z "$(find "$staging" -type l -print -quit)" ] || fail "输出里有软链，已删暂存目录"
[ ! -e "$staging/SOURCE.json" ] || fail "出包命令自己写了 SOURCE.json，和来源记录冲突"

python3 - "$staging" "$target" "$sha" "$tree" "$rev" "$src" "$review" "$review_fp" ${pins[@]+"${pins[@]}"} -- "$@" <<'PY' || fail "写 SOURCE.json 或改名失败"
import datetime, hashlib, json, os, subprocess, sys
staging, target, sha, tree, rev, src, review, review_fp = sys.argv[1:9]
rest = sys.argv[9:]
cut = rest.index('--')
pins, cmd = rest[:cut], rest[cut + 1:]
def digest(p):
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for b in iter(lambda: fh.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()
outputs = {}
for d, _, fs in os.walk(staging):
    for f in fs:
        p = os.path.join(d, f)
        outputs[os.path.relpath(p, staging).replace(os.sep, '/')] = dict(sha256=digest(p), bytes=os.path.getsize(p))
files = {}
for rel in pins:
    blob = subprocess.run(['git', 'rev-parse', f'{sha}:{rel}'], capture_output=True, text=True, check=True).stdout.strip()
    body = subprocess.run(['git', 'cat-file', 'blob', blob], capture_output=True, check=True).stdout
    files[rel] = dict(blob=blob, sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
names = [os.path.basename(r) for r in files]
if len(set(names)) != len(names):
    sys.exit('--pin 里有同名文件（fab_pin.py 平铺取回会互相覆盖）')
rec = dict(commit=sha, tree=tree, ref=rev, ref_checked='refs/remotes/origin/main',
           date=datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
           command=cmd, outputs=dict(sorted(outputs.items())))
if review:
    rec['review'] = dict(dir=review, fingerprint=review_fp, state='current-ok')
if files:
    rec['files'] = files
with open(os.path.join(staging, 'SOURCE.json'), 'w') as fh:
    json.dump(rec, fh, ensure_ascii=False, indent=1)
    fh.write('\n')
# 改名：目标若被别人抢先建成非空目录，rename 失败而不是把暂存目录挪进去
if os.path.lexists(target):
    sys.exit(f'目标在出包期间被别人建了，不覆盖：{target}')
os.rename(staging, target)
PY

echo "已出包：${target}（提交 ${sha:0:12}，$(find "$target" -type f | wc -l | tr -d ' ') 个文件，含 SOURCE.json）"
