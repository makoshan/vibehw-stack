# vibehw-stack

我是 [Mako](https://github.com/makoshan)。在 [Lauren Tan 的 `pstack`](https://github.com/cursor/plugins/tree/main/pstack) 把软件工程纪律带进 AI Agent 之后，我遇到了下一个问题：**当 AI 开始画原理图、摆 PCB、选嘉立创料号、导出 Gerber、切 3D 打印外壳时，「代码能跑 / DRC 为 0」距离「实物能点亮、能装进去」还差十万八千里。**

商品图的尺寸不等于卡尺量出来的包络；嘉立创 EDA 报着 3 条 DRC 照样允许导出下单；一颗没锁 C 号的 7.68kΩ 电阻会在贴片匹配页多收你 ¥20 换料费；STEP 水密且两两干涉为 0 的外壳，排线座子朝向反了照样装不进去。

**`vibehw-stack` 是我的答案。** 这套技能诞生于我一个人 + 一群 AI Agent（Claude Code / Codex / Cursor）从零做出智能手表与 8 通道肌电手环（OpenEMG）的实战：39 版 3D 打印外壳、16 版载板与高密度采集板、真实嘉立创 PCB+SMT 打样与三维猴树脂外发。

它不教 AI 空谈电子学，而是把**从想法到实物的七段路**、每一段的**硬门禁（Done 谓词）**、以及**嘉立创生态（LCSC 基础库选料、Gerber/BOM/CPL 物理事实核查、KiCad 自动布线、外壳装入路径检验）**写死成可执行的脚本与 Skill。

---

## 安装（Install）

### 一键脚本安装（Claude Code / Codex / Agent Skills 通用）

```bash
git clone https://github.com/makoshan/vibehw-stack.git ~/.local/share/vibehw-stack
bash ~/.local/share/vibehw-stack/scripts/install.sh
```

脚本会将 `skills/*` 下的 22 个硬件技能软链到 `~/.claude/skills`、`~/.codex/skills` 与 `~/.agents/skills`。

### Cursor / Claude Code 插件市场安装

仓库根目录自带符合 Agent Plugins 规范的 [`plugin.json`](./plugin.json) 与 [`.cursor-plugin/plugin.json`](./.cursor-plugin/plugin.json)：

```bash
/add-plugin vibehw-stack
```

---

## 快速开始（Get Started）

任何硬件任务开工前，直接调用总控入口 [`/vibehw-stack`](./skills/vibehw-stack/SKILL.md)：

```text
/vibehw-stack 我想用 nRF52840 + ADS1298 做一块 8 通道肌电采集板，走嘉立创 4 层经济型 SMT，先过选型和基础库料号。
```

`/vibehw-stack` 会按当前所处阶段，自动调度下面七段路里的子技能，并在每段结束时执行对应的门禁脚本。

---

## 0. 硬件四条铁律（Core Principles）

| 铁律 | 含义与实测反例 |
|---|---|
| **1. 实物为准** | 商品图尺寸 ≠ 卡尺实测外包络；ERC 0 / DRC 0 ≠ 板能用；STEP 水密 + 两两干涉 0 ≠ 装得进去（必须验装入路径、焊点高度、排线弯折半径与插头深度）。 |
| **2. Agent 不碰钱** | 打样、SMT 贴片、3D 打印外发、面板与元件采购，**全部停在付款前**。付款、地址与账号登录永远由人完成。 |
| **3. 通电前先断电核对** | 供电/背光电流未确认前禁止点屏；满电 4.2V 锂电禁直连 3V3；焊接全程断电，焊完目视 + 万用表通断，锂电永远最后接。 |
| **4. 门禁是自己的，平台不会拦你** | 嘉立创 EDA 带着未解决的 DRC 照样能导出下单。每次外发前必须跑通物理事实脚本并生成放行审核单（写清过了什么、缺什么、哪几条等实物）。 |

---

## 1. 从零到实物的七段路与 22 个子技能（The Seven Stages）

| 阶段 | 做什么 | 主力 Skill | 门禁（Done 谓词） |
|---|---|---|---|
| **0. 总控路由** | 判断当前阶段、派发子技能、守住四条铁律 | [`vibehw-stack`](./skills/vibehw-stack/SKILL.md) | 阶段门禁通过 + 证据落盘 |
| **① 拆解与参考选型** | 查真实开源板怎么接、芯片常配什么外围与立创 C 号 | [`pcb-reference`](./skills/pcb-reference/SKILL.md) · [`schematik`](./skills/schematik/SKILL.md) · [`fcc-teardown`](./skills/fcc-teardown/SKILL.md) | 拿到原厂 Datasheet 与真板接法，真机/开发板跑通 |
| **② 原理图、嘉立创选料与 PCB 布线** | 设计时锁死立创基础库 C 号、KiCad/Altium 布局布线、高速与模拟分区 | [`jlc-basic-parts`](./skills/jlc-basic-parts/SKILL.md) · [`pcb-design`](./skills/pcb-design/SKILL.md) · [`pcb-autoroute`](./skills/pcb-autoroute/SKILL.md) · [`pcb-high-speed-layout`](./skills/pcb-high-speed-layout/SKILL.md) · [`altium-mcp`](./skills/altium-mcp/SKILL.md) | `bom_rules.py` 0 错 + `basic_gate.py` / `layout_gate.py` 全绿 |
| **③ 打板与 SMT 下单门禁** | 独立核对 Gerber/Drill/BOM/CPL 物理事实、出厂审查与面板丝印 | [`jlc-order`](./skills/jlc-order/SKILL.md) · [`panel-artwork`](./skills/panel-artwork/SKILL.md) | `fab_facts.py` + `review_gate.py` 通过 + 放行签字 |
| **④ 结构、外壳与 3D 打印** | 参数化建模、验证板壳装入路径与限高、FDM/三维猴切片下单 | [`product-structure-design`](./skills/product-structure-design/SKILL.md) · [`hardware-structure-design`](./skills/hardware-structure-design/SKILL.md) · [`pcb-enclosure-fit`](./skills/pcb-enclosure-fit/SKILL.md) · [`mako-cad-fit`](./skills/mako-cad-fit/SKILL.md) · [`ai-cad-pipeline`](./skills/ai-cad-pipeline/SKILL.md) · [`fdm-slicing`](./skills/fdm-slicing/SKILL.md) · [`bambu-x2d-slice`](./skills/bambu-x2d-slice/SKILL.md) · [`jlc-3dp`](./skills/jlc-3dp/SKILL.md) | `step_export_check.py` 通过 + 装入扫掠无碰撞 + 切片可打 |
| **⑤ 到板检验、标注与尺寸图** | 实拍板卡引脚标注、Apple 风格工程尺寸图 | [`pcb-photo-annotate`](./skills/pcb-photo-annotate/SKILL.md) · [`dimensional-drawing`](./skills/dimensional-drawing/SKILL.md) · [`dimensional-drawing-motion`](./skills/dimensional-drawing-motion/SKILL.md) | 实物万用表通断与供电验收通过 |

---

## 2. 嘉立创（JLCPCB / LCSC）工具链亮点

1. **[`jlc-basic-parts`](./skills/jlc-basic-parts/SKILL.md)（设计期锁死基础库 C 号）**
   - 优先选用嘉立创 SMT **基础库**（免换料费），扩展库每种料号加收 ¥20。
   - 自带 [`bom_rules.py`](./skills/jlc-basic-parts/scripts/bom_rules.py)（检查重号、缺封装/值、同 MPN 不同封装、缺 C 号）与 [`bom_diff.py`](./skills/jlc-basic-parts/scripts/bom_diff.py)（版本间 BOM 变更对比）。
2. **[`jlc-order`](./skills/jlc-order/SKILL.md)（Gerber / BOM / CPL 出厂门禁）**
   - 自带 [`fab_facts.py`](./skills/jlc-order/scripts/fab_facts.py) 与 [`review_gate.py`](./skills/jlc-order/scripts/review_gate.py)：从导出的制造包直接反读板框尺寸、层数、最小孔径、内层孤铜、封装 courtyard 间距，并绑定 Git commit hash 防篡改。
3. **[`pcb-reference`](./skills/pcb-reference/SKILL.md)（6,197 块开源真板 + 5.6 万立创 C 号共现查询）**
   - 自带只读查询器 [`query.py`](./skills/pcb-reference/scripts/query.py)：按芯片型号查开源真板最常搭配的外围阻容、封装与作者实选的立创 C 号（按 `lift` 去重排序）。

---

## 3. 环境变量与可选外部依赖（Configuration & Not Shipped Here）

本仓库**不包含任何硬编码私有地址**。如需使用跨机算力卸载（Windows EDA / 远程 Linux），可通过环境变量覆盖：

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `MSTACK_WIN_HOST` / `WIN_HOST` | `thursday-win` | 运行 Windows 版 KiCad / Altium / Freerouting 的 SSH 主机别名 |
| `PCBDB` | `~/go/202609/vibe-hw/work/pcbdb/pcb.db` | `pcb-reference` 的本地 SQLite 数据库路径（约 489 MB，不随 Git 分发） |

推荐配合使用的上游开源 Skill（各自独立安装）：
- [`pstack`](https://github.com/cursor/plugins/tree/main/pstack)（Lauren Tan 的通用软件工程 Skill 栈）
- `easyeda-eprj3-skill` / `easyeda-pro-format-skill`（嘉立创 EDA 专业版官方 MIT 格式生成与校验 Skill）

---

## 4. 自检与测试（Verification）

在仓库根目录运行全套自检与单元测试：

```bash
bash scripts/check-no-pkill.sh
python3 scripts/check-bash32-cjk.py
python3 skills/jlc-basic-parts/scripts/test_bom_tools.py
python3 skills/jlc-order/scripts/test_fab_facts.py
python3 skills/jlc-order/scripts/test_review_gate.py
```

---

## License

[MIT](./LICENSE) © Mako Shan
