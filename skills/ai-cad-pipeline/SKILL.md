---
name: ai-cad-pipeline
description: |
  从参考图/渲染稿到可打印 STEP+3MF 套件的完整 AI CAD 流水线（硬件外壳、桌面机器人壳、PCB 盒）：
  尺寸源分级 → 让 AI 出带尺寸的正交参考图（Codex/genimg）→ 母曲面选路（Fusion MCP 变半径圆角+抽壳 vs build123d/OCC）
  → build123d 切件开孔 → validate/interfere/探针/DfAM/3MF 水密闭环 → kit 交付。
  汇总 openclaw-cart、lobster-box 两条产品线踩过的所有坑。当用户说「一模一样」「不够圆润」「按这张图建模」
  「装 XX 板的壳」「Fusion 建模」「让 AI 出参考图」「打印件套件」时触发；叠加 cad、pcb-enclosure-fit、genimg、dfam-check 使用。
user-invokable: true
argument-hint: "[要建什么 + 参考图/尺寸来源]"
metadata:
  author: makoshan
  origin: 2026-09 龙虾盒子 / OpenClaw 机器人盒 实战
---

# AI CAD 流水线（参考图 → 打印套件）

这是**总控**skill：只讲流程、选路和坑。几何操作看 `cad`（text-to-cad），配合法则看 `pcb-enclosure-fit`，
生图看 `genimg`，网格体检看 `dfam-check`。细节全在 `references/`，按需读。

## 0. 先定尺寸源（不可跳级）

EDA 源文件 > 官方 DXF/STP > 卡尺实测 > **带尺寸的正交图** > 渲染图目测。
渲染图只能定"形态"，不能定尺寸；每个数字都要能说出来自哪一级。
AI 出的尺寸图是第 4 级：它会**编造**结构（分模线、内部布局），凡与硬件事实（屏模块实际尺寸、板孔距）冲突，硬件赢。

## 1. 用户说"不一样/不够像"时：先要图，别猜

自己肉眼估半径再迭代是最贵的循环（龙虾盒子 v4→v4.2 三轮）。正确做法：
1. 列出你看到的差别（体型/屏/背面/附件，按影响排序）让用户确认；
2. 给用户一份**正交参考图需求清单**（模板见 `references/reference-drawing-request.md`），用户用 AI 出图回传；
3. 逐面板读数，写进参数表，标注来源。

用户手头有 Codex 订阅：`codex exec -C "$(pwd)" -s workspace-write --skip-git-repo-check "请使用 image generation tool 生成…存成 ./ref/x.png"`
（1–7 分钟/张，Bash 超时给 ≥7 min）。要参考图风格迁移用 `genimg --provider gemini`（codex 不吃参考图）。

## 1.5 量几何用 `scripts/fitprobe.py`，别每次手写探针

把「这里撞了吗 / 装得下吗 / 壁在哪 / 是不是一个实体」固化成带类型的查询，
理由和用法见 `references/adam-plugin-lessons.md`（拆自 Adam 的 Fusion 插件：它把模型操作切成
read/create/update/delete/execute 五个动词，任意脚本只是逃生舱）。

```python
import os, sys; sys.path.insert(0, os.path.expanduser("~/.claude/skills/ai-cad-pipeline/scripts"))
from fitprobe import clash, fits, envelope, wall_at, solids, outside, sag

clash(frame, cap, detail=True)        # 干涉量 + 每块重叠的 bbox（只给总量等于让人猜）
fits([fr, cap, ch], 69, 4, 48, (0, 21.5, 31))   # 真模块尺寸的方块能不能进
envelope([...], 69, 4, 48, y_range=[...], z_range=[...])  # 扫退距×高度，返回所有可行位；无解时给最接近的
wall_at(body, z=40)                   # 该高度真实半宽/前后面 y —— 斜壁曲面壳必须用它定位贴壁特征
solids(part, "frame")                 # 一个打印件必须是一个实体，否则列出碎块
outside(part, body)                   # 肋/柱有没有捅出外表面（同一件内，interfere 查不到）
sag(body, z=34, x_half=34)            # 曲面前脸的弧垂，平屏要 < 0.5
```

**铁律**：探针答不上来要抛错说缺什么，绝不返回 0。退化的布尔切片返回空集会让探针「通过」，
这是最危险的假阳性（本轮量前脸平面宽度返回 0mm 就是）。fitprobe 里所有查询都按这条写。

## 2. 母曲面选路（外形壳体）

| 外形 | 用什么 | 原因 |
|---|---|---|
| 直棱 + 常规圆角（R ≤ 壁厚×4，定半径） | build123d `fillet` + `offset()` | 全脚本、免费、可探针 |
| 变半径圆角 / 大半径枕头体 / 前后不同半径 | **Fusion MCP** 出母曲面 → 导 STEP 给 build123d | OCC 变半径法则在内壳 inset 处成批失效；`offset()` 在 B 样条圆角面上退化 |
| 自由曲面 / T-spline | Fusion 全程 | build123d 不擅长 |

Fusion 路径固定做法（模板 `references/fusion_pillow_template.py`）：一个脚本建 **外实体 + 每档内缩壳**（壁厚、壁厚+配合间隙、+插环厚）各导一个 STEP；
build123d 侧 `_pillow(inset)` 查表读 STEP，内枕头 = 外实体 − 对应壳。改半径 = 改脚本重跑 + 改参数表，两处同步。
Fusion API 的坑见 `references/fusion-mcp.md`（零件文档单零部件、变半径签名、抽壳输入、导出粒度、脚本整体事务回滚）。

## 3. 切件与特征（build123d）

- 一个母曲面按高度切成多件，接缝 = 渲染分模线；**跨分模线的特征（口排、屏窗）必须落在一件里** → 给那件加裙边下探。
- 每个打印件收尾 `_finish()`：融合成**单实体** + `clean().fix()`；多实体各自 valid 也会导出不水密网格。
- 插环与壳内面之间有配合间隙 → 插环是悬空的：加 0.4 高**焊接环**（inset = 壁厚，共面即可 fuse）。
- 腔内螺柱/立柱必须有肋连壁；开口（口排/格栅）**最后切**，肋不会堵口。
- 特征落在圆角起点（tangent line）附近必出薄片/断连：卡扣凸点、屏窗边、肩盘都要留在平直带内（平带宽 = 边长 − 2R）。
- 其余 build123d/OCP 坑：`references/build123d-pitfalls.md`。

## 4. 校验闭环（每次改动全跑，缺一不交付）

```
gen <parts> --force --write            # 显式 --write path，装配偶发不落盘
artifact <asm.step> --force            # 不刷缓存 refs/interfere 会看旧几何
inspect validate  每个打印件            # 单实体、closed、正体积
inspect interfere <asm> --refs …       # 排除厂商 STEP 自带瑕疵子件
python check_*.py                      # 布尔探针：开口通到腔、销入孔、孔不被柱堵、口排跨两件时两件一起探
export --3mf --mesh-tolerance 0.01     # 变半径曲面 STL 四角接缝会留开边，3MF 水密 → 套件发 3MF
dfam_tool measure                      # min/p05 壁厚、watertight；min 异常先查是不是多实体/悬空件
snapshot 正/侧/背                        # 亲眼看：口有没有被另一件挡、槽有没有爬上圆角
```

## 4.5 切片与交接（OrcaSlicer CLI → Bambu LAN）

切片也会**静默出错**：Orca 机型预设默认冷板，PETG 被拒后回退成 PLA 温度而返回码仍是 0——
切完必看 gcode 头的 `curr_bed_type / filament_type` 和 `M190/M109`。`--load-settings` 的用户预设要压平 `inherits`，
STL 位置就是床位置（先居中），A1 mini 的开机行程要进 `motion_bounds_mm`，Orca 导的 3mf 要补 `printer_model_id`。
打印机发现用 8883+990 端口探（Bambu Studio 占了 SSDP 端口），序列号从 TLS 证书取，access code 只能从触摸屏读。
细节与脚本路径：`references/amac1984-taishan-lessons.md`。

## 5. 交付节奏

每轮：kit/（3mf、step、src 含 Fusion 脚本与母曲面 STEP、snap、README：尺寸源、本轮改动、未做项、校验结果）→ zip → 发图 + zip → 更新 memory（坑写进 skill 而不是只写 memory）。
README 里明写"**未做**"项（例：前脸后仰 5° 需要 Fusion draft），不要静默降级。
