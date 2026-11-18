---
name: product-render
version: 0.3.0
description: 真实硬件产品渲染。把 KiCad PCB 或整机模型（GLB/BLEND/STL/OBJ）做成影棚图。保留板、壳、排线、表带的装配关系，从同一场景输出正交俯视、三分之四斜视、接口微距；也可多件平铺。支持纯黑背景与深蓝灰托盘、浅灰无缝背景、哑光阻焊及缎面金属。用 kicad-cli 快速看板，或用 Blender Cycles 拍宣传图。适用「产品图、PCB 美图、拆解图、像参考图的质感、给 deck 配板子图」。尺寸图走 dimensional-drawing，实拍标注走 pcb-photo-annotate。
---

# product-render

**渲染图不是实物，也不是工艺验收。** 图好看不代表丝印合格、件装得下；对外用时标注「渲染」。

## A. 参考风格拆成可控参数

同一场景先确定装配状态，再选镜头。整机与调试板相连、两板通过排线相连等场景，用 `--layout assembled` 保留共同坐标。已摆好位置的整机优先导出为一个带材质的 GLB。需要展示零件清单或分解平铺时用默认 `spread`。

| 镜头 | 每张图要说明什么 | 检查什么 |
|---|---|---|
| `top` 正交俯视 | 板、壳、接口和表带的相对位置 | 主体完整入画，接口、版本丝印和板面方向可辨 |
| `iso` 三分之四斜视 | 壳体深度、板边厚度、连接器插口与排线路径 | 主体完整入画，暗色零件靠柔和的侧光和轮廓光分开 |
| `detail` 微距 | 一个真实的接口、锁扣、排线端头或器件细节 | 关键接合面在焦内，外围可裁切，背景轻微虚化 |

板形、器件、丝印和标志取自自己的模型。按图的用途选构图。

| 图的用途 | 场景与光线 |
|---|---|
| 深底整机主图 | 纯黑背景、深蓝灰底座、低亮度阻焊。用侧光分开壳边、表带和芯片外壳，用大柔光留住接口金属表面的细节。 |
| 浅底拆解与台架近照 | 浅灰或冷白地面，保留柔和接触阴影。壳、后盖、FPC、电池、PCB 按拆装顺序排开，保持真实比例，俯视用等距留白。装配特写可沿表带对角线构图，先确认目标接合处无遮挡。 |
| PCB 工程图 | 走线、过孔、板框直接从 EDA 导出正投影视图。全层图用于交代复杂度，单层图用一组高对比色突出当层铜，保留板框、层名与板版号。与产品照同页时标清「实拍」「渲染」或「PCB 图层」。 |

实拍参考可以借取手套、镊子和夹具提供比例感。渲染中只有已建模且需要交代操作时才加道具，保持工具与板的真实比例。FPC 的琥珀覆盖膜、铜箔、补强片，以及绿阻焊、沉金、焊锡、环氧封装分别保留各自颜色和反射；避免用全局提亮补救单个材质。对已有材质的 GLB 在源场景配置表面。`--gold` 与 `--ic-black` 作用于 pcb2blender 材质节点。

| 参考图里看到的 | 本 skill 怎么做 | 改哪里 |
|---|---|---|
| 板面黑而不反光 | 阻焊 `#16181C`（kicad）/ 预设 `Black`（blender） | `pcb_render.py` 顶部常量 |
| 焊盘、安装孔金色 | 叠层写 `copper_finish "ENIG"`；blender 引擎再改材质为橙金缎面 | `--gold` `--gold-rough` |
| 高调、低对比、亮 | AgX Base Contrast + 曝光预设 | `--exposure` |
| 芯片外壳黑色哑光 | 中性深灰塑料 + 名字像芯片封装的件顶面，统一改近黑哑光环氧 | `--ic-black`（0 = 保留模型原色）、`--ic-names` |
| 白字小而整齐 | 渲染副本里丝印字高封顶 0.5 mm、线宽 0.08 mm | `SILK_MAX_H` |
| 近白无缝背景 | `--bg light`：冷白地面 + 同色世界光，曝光 +1.9，金色 `e89a10` | `--floor` `--exposure` `--gold` |
| 纯黑背景 + 托盘 | `--bg dark`：黑世界 + 圆角深蓝灰托盘（低反光） | `--tray` `--tray-spec`；已有底座用 `--no-tray` |
| 立体但不过曝 | 顶部大柔光 + 低角度侧光 + 弱轮廓光，功率按照度 `P≈E·π·d²` 反推 | `--expo` |
| 零件一字排开 | 多个输入沿 X 平铺，可按毫米固定间距 | `--layout spread --spread-gap-mm 5` |

做不了、要改源设计的：`DISP` 这类反白标签、logo、安装孔、`BAT CONN` 贴纸——得在 KiCad 丝印层或 3D 模型里真画出来，渲染不会凭空生成。

装配 STEP 导入发白、需要用实际 Gerber 恢复黑色板面，或需要补接口低机位和反侧细节时，读 [黑色阻焊装配与多角度](references/assembled-pcb.md)。

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
# 装配影棚：以下特写坐标仅作示例，应换成模型里接合面的实测位置
blender -b --factory-startup --python-exit-code 1 --python $S/studio.py -- \
    --layout assembled --bg dark --no-tray --views top,iso,detail \
    --detail-target-mm=12,-6,3 --detail-width-mm 18 --detail-fstop 14 \
    --detail-azimuth=-120 --detail-elevation 49 \
    --samples 32 --scale 40 --name watch out/ assembly.glb
```

先用 `--views top,iso` 出预览。特写中心按导入后 Blender 的世界坐标填写，单位是毫米，Z 要扣掉装配最低点。用 `CONTENT_BOUNDS_MM` 核对坐标系与大小，并在模型里量取实际焦点。分文件输入须共享轴向、单位及原点，异构来源优先导出同一整机 GLB。STL/OBJ 的坐标数值要求为米。特写方位角 0° 从 +X 看，90° 从 +Y 看；仰角从水平面向上量。需要同时看清接口和板面时，用 `--detail-deep-focus` 关闭景深，或收小光圈（增大 f 值）。正式交付可用 `--width 3840 --height 2160 --scale 100`。

| 引擎 | 时间（MacBook Air，单视图，实测） | 长处 | 短处 |
|---|---|---|---|
| kicad | 约 40 s（arm64 Python 起）；x86 `python3` 起要 3 分多 | 零依赖，板子朝向和 KiCad 一致 | 金色偏黄偏艳，灯光只有 4 个旋钮，背景是 Pillow 贴的 |
| blender | 单板 459 个网格：32 采样 40% 分辨率两张约 45 s；2400×1350、128 采样两张约 3–6 分钟（含导出，实测 161–369 s，同机有别的活时偏慢） | 真实材质与柔光，可放整机、多件平铺 | 首次 Metal 编译内核要几分钟；依赖 pcb2blender |

## C. 检查（每次都跑，不靠肉眼）

1. **源板未改**：`pcb_render.py` 末尾比对源板字节，打印 `SOURCE_UNCHANGED`。
2. **出画**：kicad 引擎看 alpha 外框是否贴边；Blender 俯视与斜视检查所有包围盒角。微距允许裁外围，脚本校验焦点坐标在主体包围盒内，实际落在哪个零件及焦点清晰度要看成片。
3. **过曝**：blender 出图后统计近白像素（三通道 ≥0.995），超过 1% 报错退出。反例：`--expo 24` 必须红（实测 1.8%，退出码 1；正常出图 0.0%）。
4. **质感对数字**：`python3 compare.py 参考图... -- 渲染图...`。同镜头、同类区域才直接比较金色、亮度分位数、近黑占比。黑底面积会拉低整图亮度，橙色 FPC 也可能落入金色筛选；要看板面或触点时，先取画面里对应区域再对照。
5. 以 100% 缩放复核板名、丝印、接口开口朝向、针脚和排线。真实模型与占位件分别说明，宣传图显眼处的接口若缺模型，先补真实模型或在交付时标出缺失；高光剪切比例低只能说明整图没有大面积过曝。

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
