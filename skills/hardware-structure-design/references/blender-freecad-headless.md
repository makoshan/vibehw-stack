# FreeCAD / Blender 无头跑：装配动画 glb 一条线（2026-09-28 实打）

来源：vibe-hw #143「FreeCAD 出壳，Blender 沿高度装上」本机验收。环境 macOS，`brew install --cask blender freecad`
→ Blender **5.2.2 LTS**（`/Applications/Blender.app/Contents/MacOS/Blender`）、FreeCAD **1.1.3**
（`/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`，`Contents/MacOS/` 下只有 GUI 的 `FreeCAD`）。
本机 KiCad 是 10.0.6，pcb2blender 2.17.5 钉的是 KiCad 9，没装；板子直接用工程产物 `board.glb`。

## FreeCAD 无头

- `freecadcmd script.py -- --a x --b y` **不行**：freecadcmd 把每个位置参数都当文件去打开（`.json` 送进 FEM 网格导入器报
  `'str' object has no attribute 'items'`，`.step` 报 `Cannot read STEP file`），脚本的 `__main__` 也不跑。
  能用的写法：
  ```bash
  PYTHONUTF8=1 freecadcmd -c "import sys,runpy; sys.argv=['x','--','--case','case.json','--out','case.step']; runpy.run_path('script.py', run_name='__main__')"
  ```
- freecadcmd 的 Python 默认 locale 是 ascii：`Path.read_text()` 读到中文就 `'ascii' codec can't decode`。
  脚本里写 `encoding='utf-8'`，或环境给 `PYTHONUTF8=1`。
- `Import.export([objs], 'x.step')` 出的 STEP 用 `Part.Shape().read()` 读回量 `BoundBox`、`Volume` 就是验收；
  `Mesh.export(objs, 'x.obj')` 顺手出 OBJ 给 Blender（Blender 不吃 STEP）。OBJ 里物体名落在 `g` 行。

## Blender 无头

- OBJ 导入默认**不按组拆物体**：`bpy.ops.wm.obj_import(..., use_split_objects=True, use_split_groups=True)`，否则全并成一个叫文件名的物体，按名字标角色就对不上。
- glb 导入米制、Y 朝上；Blender 场景 1 BU = 1 m（`scene.unit_settings.scale_length`），毫米几何进来乘 0.001。
- **glTF 导出的动画名不是 action 名。** `export_animation_mode='ACTIVE_ACTIONS'` 下多个物体共用一个 action，导出合成一段动画，名字取
  `export_nla_strips_merged_animation_name`（默认 `"Animation"`），action 叫什么都没用。前端按名字找 clip 的话必须传这个参数。
- `export_frame_range=True` 时从第 0 帧开始采样：关键帧打在 1..49、fps 30，导出的时长是 49/30 = 1.633 s 而不是 1.6 s，开头多一帧静止。
  按时长线性映射进度的前端会整体慢一帧。关键帧从 0 开始打，或导出后核时长。
- 核 glb 不用开 Blender：读 JSON chunk 的 `animations[].name`、`channels`，按 `accessors` 解 `bufferViews` 出时间 / 平移轨，
  自己插值到要查的进度点，比截图靠谱。

## three.js 播这段动画

`action.play(); action.paused = true;` 之后 `mixer.setTime(t)` **不动**：paused 让有效 timeScale 变 0，
`clipTime` 停在原地，画面永远是第 0 帧。要拖时间轴就 `action.paused = false; action.time = t; mixer.update(0)`
。别用 `mixer.setTime(t)`：它先把每个 action 的 time 清零再 update，LoopOnce + clampWhenFinished 的动作一旦到过末尾就变 paused，之后 setTime 永远停在第 0 帧。症状：滑杆 0 和 1 的截图一模一样、按钮点了状态变了但画面不变、或装上之后再也拖不动。

进度的两套口径别混：产品页滑杆是「拉开量」（1 拉开 0 合上），Blender 脚本的 `settle_gaps(u)` 是「收回进度」（0 拉开 1 合上），u = 1 − 拉开量。核 glb 时先说清在查哪个。

无头复验网页 WebGL：Browser 面板被藏起来时截不了图，改用 playwright-core + `chromium.launch({ channel: 'chrome', args: ['--use-angle=swiftshader'] })`，截图后用 PIL 数像素差，比肉眼稳。
