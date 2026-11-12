#!/usr/bin/env bash
# product-render 依赖：pcb2blender（钉提交）+ Blender 5.2 的 Python 3.13 依赖。只装进 ~/.cache，不碰 Blender 扩展目录。
set -euo pipefail
C="${PRODUCT_RENDER_CACHE:-$HOME/.cache/product-render}"
REV=2a2ac82   # 2026-04-21 版；换版本要重跑 SKILL.md 的验收（颜色 bug 可能已修/又变）
mkdir -p "$C"
if [ ! -d "$C/pcb2blender/.git" ]; then
  git clone -q https://github.com/30350n/pcb2blender.git "$C/pcb2blender"
fi
git -C "$C/pcb2blender" fetch -q --depth 50 origin 2>/dev/null || true
git -C "$C/pcb2blender" checkout -q "$REV"
# io_scene_x3d / mat4cad / custom_node_utils 是子模块，漏拉会在导入时报 ModuleNotFoundError
git -C "$C/pcb2blender" submodule update -q --init --recursive --depth 1
if [ ! -f "$C/deps/skia.cpython-313-darwin.so" ]; then
  uv pip install -q --target "$C/deps" --python-version 3.13 --python-platform aarch64-apple-darwin \
    error-helper==1.4 pillow==11.3 skia-python==138
  # numpy 用 Blender 自带的；带进来会和 Blender 的 ABI 打架
  /bin/rm -rf "$C"/deps/numpy* "$C/deps/bin"
fi
echo "OK $C pcb2blender@$(git -C "$C/pcb2blender" rev-parse --short HEAD)"
