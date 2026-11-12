---
name: product-render
version: 0.1.4
description: 硬件产品宣传渲染图：把 KiCad 板子或整机模型（GLB/STL/OBJ）渲染成 OSSW / Apple 拆解风的影棚图——哑光黑阻焊、沉金焊盘、白色小号丝印，浅灰无缝背景或纯黑背景加深蓝灰托盘，正交俯视 + 斜视透视，多件自动平铺成分解图。两个引擎：kicad-cli 光追（快，零依赖）和 Blender Cycles + pcb2blender（慢，质感接近参考图）。只读源板，自带过曝/出画/源板未改三道检查。Use when 用户要「渲染一张产品图 / 像这张图那样的质感 / 板子宣传图 / 拆解图 / 爆炸平铺图 / PCB 美图 / 给 deck 配板子图」，或发来产品渲染参考图要学风格。要尺寸标注图走 dimensional-drawing，要实拍照片标注走 pcb-photo-annotate，要 AI 生图走 genimg。
---

# product-render

**渲染图不是实物，也不是工艺验收。** 图好看不代表丝印合格、件装得下；对外用时标注「渲染」。

## A. 参考风格拆成可控参数

| 参考图里看到的 | 本 skill 怎么做 | 改哪里 |
|---|---|---|
| 板面黑而不反光 | 阻焊 `#16181C`（kicad）/ 预设 `Black`（blender） | `pcb_render.py` 顶部常量 |
| 焊盘、安装孔金色 | 叠层写 `copper_finish "ENIG"`；blender 引擎再改材质为橙金缎面 | `--gold` `--gold-rough` |
| 高调、低对比、亮 | AgX Base Contrast + 曝光预设 | `--exposure` |
| 芯片外壳黑色哑光 | 中性深灰塑料 + 名字像芯片封装的件顶面，统一改近黑哑光环氧 | `--ic-black`（0 = 保留模型原色）、`--ic-names` |
| 白字小而整齐 | 渲染副本里丝印字高封顶 0.5 mm、线宽 0.08 mm | `SILK_MAX_H` |
| 近白无缝背景（图 1–3） | `--bg light`：冷白地面 + 同色世界光，曝光 +1.9，金色 `e89a10` | `--floor` `--exposure` `--gold` |
| 纯黑背景 + 托盘（图 4） | `--bg dark`：黑世界 + 圆角深蓝灰托盘（低反光） | `--tray` `--tray-spec` |
| 立体但不过曝 | 顶部大柔光 + 低角度侧光 + 弱轮廓光，功率按照度 `P≈E·π·d²` 反推 | `--expo` |
| 零件一字排开（图 2） | 多个输入沿 X 平铺 | `studio.py` 导入循环 |
| 正俯视 / 斜视 | 正交俯视 + 85 mm 透视斜视，自动拉近到刚好完整入画 | `shoot()` |

做不了、要改源设计的：`DISP` 这类反白标签、logo、安装孔、`BAT CONN` 贴纸——得在 KiCad 丝印层或 3D 模型里真画出来，渲染不会凭空生成。

## B. 跑法

```bash
S=~/.claude/skills/mstack/skills/product-render
$S/setup.sh                       # 只有 blender 引擎要；装进 ~/.cache/product-render
PY=<arm64 的 Python，带 Pillow>     # 原力手环用 host/.venv-emg/bin/python
$PY $S/pcb_render.py board.kicad_pcb out/ --engine kicad --bg light          # 俯视
$PY $S/pcb_render.py board.kicad_pcb out/ --engine kicad --bg dark --iso     # 斜视
$PY $S/pcb_render.py board.kicad_pcb out/ --engine blender --bg dark --samples 32 --scale 40   # 先低配试
# 整机/多件：模型先转 GLB（STEP 走 pcb-enclosure-fit 的 GLB 管线），再直接调影棚
blender -b --factory-startup --python-exit-code 1 --python $S/studio.py -- \
    --bg light --name watch out/ case.glb pcb.pcb3d battery.glb
```

| 引擎 | 时间（MacBook Air，单视图，实测） | 长处 | 短处 |
|---|---|---|---|
| kicad | 约 40 s（arm64 Python 起）；x86 `python3` 起要 3 分多 | 零依赖，板子朝向和 KiCad 一致 | 金色偏黄偏艳，灯光只有 4 个旋钮，背景是 Pillow 贴的 |
| blender | 单板 459 个网格：32 采样 40% 分辨率两张约 45 s；2400×1350、128 采样两张约 3–6 分钟（含导出，实测 161–369 s，同机有别的活时偏慢） | 真实材质与柔光，可放整机、多件平铺 | 首次 Metal 编译内核要几分钟；依赖 pcb2blender |

## C. 检查（每次都跑，不靠肉眼）

1. **源板未改**：`pcb_render.py` 末尾比对源板字节，打印 `SOURCE_UNCHANGED`。
2. **出画**：kicad 引擎看 alpha 外框是否贴边；blender 引擎把所有包围盒角投到相机，有一个出界就报错。
3. **过曝**：blender 出图后统计近白像素（三通道 ≥0.995），超过 1% 报错退出。反例：`--expo 24` 必须红（实测 1.8%，退出码 1；正常出图 0.0%）。
4. **质感对数字**：`python3 compare.py 参考图... -- 渲染图...`，比金色像素占比与颜色、亮度 p5/p50/p95、近黑占比。Mako 说「质感不一样」时先跑这个，把「哪里不一样」变成数字再调；目测会漏（第一版焊盘根本不是金色，肉眼只觉得「偏暗」）。
5. 出图后用 Read 看一眼，和参考图并排放；风格到不到位由 Mako 判，报告写「已实现未验收」。

## D. 坑（都踩过）

- **pcb2blender 自定义阻焊色变白**：导出把 `#RRGGBB` 写成 0–255，导入按 0–1 读（@2a2ac82 源码 `export.py hex2rgb` 对 `materials.py srgb2lin`）。blender 引擎只用预设色名 `Black`/`White`。升级 pcb2blender 后先用自定义色试一张再放开。
- **pcb2blender 有三个 git 子模块**（io_scene_x3d、mat4cad、custom_node_utils）：只 clone 不拉子模块，导入时报 `No module named 'pcb2blender_importer.io_scene_x3d.source'`。`setup.sh` 已带 `submodule update`。
- **pcb2blender 的 ENIG 不是参考图那种金**：它是 `#efdfbb` 浅香槟色、粗糙度 0.1 的镜面，渲出来发灰发褐，`compare.py` 测金色像素 0.04%。`studio.py` 导入后把表面处理节点改 CUSTOM：`--gold c8964a`、`--gold-rough 0.3`（缎面）。改后黑底俯视金色 (179,141,85)，参考图 4 实测 (174,139,78)。
- **KiCad / 嘉立创模型的芯片外壳是中灰**：SOT-23 外壳线性 0.147（≈ sRGB 107）、半哑光塑料，再映出灰环境就成浅灰；参考图是近黑哑光。`studio.py` 两条规则一起用：(1) 中性灰的深色塑料改黑，带色的（棕电容、绿 LED）和浅色的（白连接器）不动；(2) 有的模型外壳被 mat4cad 判成「金属」（TI VQFN24 `RHL0024A` 四个材质全是 METAL），按塑料挑不出来，就按器件名（`--ic-names`）找它最顶层朝上面积最大的材质，复制后改黑，免得连累共用材质的连接器。C08B 实测 11 个塑料节点 + 18 个器件顶面，USB-C 壳、侧按键、天线座保持原色。
- **参考图是高调低对比**：用 `AgX - Base Contrast` + 曝光预设（黑底 +1.2、浅色 +1.9 档），别用 Medium High Contrast。三个按数字校准过的点：(1) 黑底托盘的亮度来自它反射的灰环境，不在底色——底色压到 1/4 只从 92 降到 85，反光（Specular IOR Level）压到 0.1、粗糙 0.8 才降到 (41,47,56)，参考图 4 约 (45,52,62)。(2) 浅色背景提地面反照率几乎没用（0.80→0.90 只 +6），AgX 会压高光，要靠曝光：+1.9 档到 230，参考图 231。(3) 曝光一高金色被冲淡（蓝分量 111–127，参考 65），浅色模式金色换更饱和的 `e89a10`，俯视测 (205,153,76)，参考 (226,180,65)，斜视仍偏淡。调参先 `--samples 32 --scale 40`，每张 15–20 s。
- **沉金看不出金色有两个原因**：(1) 纯黑世界让金属只反射黑色；暗色背景改成「相机看到黑、反射和补光看到中灰」（Light Path 的 Is Camera Ray 混合）。(2) 主光放正上方时，俯视正交里平的焊盘把它镜面反射成白点；主光偏离竖直约 40°。判断方法：先解开 `.pcb3d` 看 `layers/stackup.toml` 是不是 `surface_finish = "ENIG"`，是就是光的问题。
- **pcb2blender 导出器无界面跑不了**：它调 `pcbnew.GetBoard()` 和 `ExportVRML()`。`pcb3d_export.py` 换成 `LoadBoard` + `kicad-cli pcb export vrml`，用 KiCad 自带 Python 3.9 执行。
- **manifest 写 `blender_version_max = 5.2.0`**：本机 Blender 5.2.2 装扩展会被拒；这里不装扩展，直接 `sys.path` 加载，导入正常。
- **依赖里别带 numpy**：Blender 自带一份，`--target` 装进来的会抢先被 import。`setup.sh` 装完删掉。
- **竖直视线别用 `to_track_quat`**：方向退化，俯视图随机转 90°。俯视相机直接 `rotation_euler = (0,0,0)`。
- **灯光功率按照度算**：第一版按「看着差不多」给 30 W，整张过曝约 20 倍；Blender 里 1–5 W/m² 就是日光量级。
- **CPU 低 ≠ 卡死**：首次 Metal 渲染时 `MTLCompilerService` 在编内核（实测占 237% CPU、1.36 GB，数分钟），之后走缓存。看日志进度，别杀。
- **x86 的 `python3` 起 kicad-cli 慢 5 倍**：本机 `/usr/bin/env python3` 是 x86，子进程走 Rosetta。用 arm64 Python。
- **缺 3D 模型的件会渲染成裸焊盘**（C08B 主控 U13 就是：`QFN-48-1EP_6x6mm_P0.4mm_EP4.6x4.6mm.step` 在 KiCad 10 自带库和 GitLab 官方仓库 master/10.0/9.0 都不存在）。`pcb_render.py` 在渲染副本里按顺序补：(1) 路径能解析就不动（`${KIPRJMOD}` = 板子目录，`${KICADn_3DMODEL_DIR}` = KiCad.app 自带库）；(2) 找不到就在同一 git 仓库按文件名找，唯一命中就换路径——板子从别处复制来时 `${KIPRJMOD}` 会断，而且模型常被 `.gitignore` 掉，所以 `git ls-files -co` 不能加 `--exclude-standard`；(3) 还找不到就按封装的 F.Fab/B.Fab 外形生成黑色长方体 VRML（高 0.85 mm）。本来就没写 model 的件（测试点、FPC 焊盘）不碰。日志 `FALLBACK_MODEL` / `MODEL_FOUND_IN_REPO` / `MISSING_MODEL_NO_FAB` 逐个列出。C08B 实测：3 个缺失（U13、U3、J_AFE）→ 0，网格 459 → 462。补的是占位体，不是真实封装外形（没有引脚、没有丝印标记）。
