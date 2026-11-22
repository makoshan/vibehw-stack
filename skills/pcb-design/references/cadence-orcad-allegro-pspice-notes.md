# 《Cadence 17.2 电路设计与仿真从入门到精通》核心工程摘录

## 来源与口径

- 书名：《Cadence 17.2 电路设计与仿真 从入门到精通（云课版）》，李鹏、吴荣 等编著，人民邮电出版社，2020.1（ISBN 978-7-115-50880-5，492 页扫描本；正文页码 `p ≈ PDF 页 − 19`，以下标注 `[Cadence17.2 §x.x p.xxx]` 均为正文书页）。
- 读法：提取 §1、§6、§7、§9、§10.3–10.6、§11–§15 及附录 1–2（共 264 页核心工程章节）Apple Vision OCR 文字层，并用 24 页高分辨率原图样张交叉核对公式、对话框参数与表项。已对照实图纠出几处原书印刷笔误（`.pad` 误印 `.psd`、`Mils` 误印 `mm`、`tcfx` XML 技术文件等）。
- 适用边界：入门篇里的基础走线/间距示例偏宽松（如 `10/12 mil` 基础举例，而板向导默认 `5/5 mil`），高速/高频/工艺极限仍以器件手册、板厂及场求解器为准。

---

## 一、OrCAD Capture CIS 原理图工程治理（§1, §6, §7）

1. **工具选型与 SPB 17.2 新特征** [Cadence17.2 §1.1.2 p.3–5 / §1.4.1 p.10–11]：
   - Capture 启动选择组件时选 **`OrCAD Capture CIS`**（含元器件数据库管理，功能完整），不要选简化缺库管理功能的 `OrCAD Capture`。
   - 17.2 新增关键能力：`DesignTrue` 在线 DFM 实时核查、DRC 浏览器、`IDX` 格式 MCAD-ECAD 双向协作、`Interactive3D Canvas` 刚柔结合板折叠碰撞检查、支持 **多层动态铜属性分级 (`Dyn_*`) 与多重叠构 (`Cross Section`)**、以及允许局部 **`Replace Padstack`** 时保留设计内焊盘名免被刷新的开关（`Keep design padstack names for symbol pins`）。
   - `Design Cache` 更新分工：**`Replace Cache`** 可跨库换零件或刷新引脚信息（可选 `Preserve schematic part properties` 保留现有属性，或 `Replace schematic properties` 重置回库默认）；**`Update Cache`** 仅就地同步属性更新，不改变器件与原库的链接绑定 [§6.1.2 p.127–128]。
2. **平坦式 vs 层次式连接符严禁混用** [Cadence17.2 §7.2–7.5 p.164–171]：
   - **平坦式多页图纸**：各页属于同一 `Schematic` 文件夹层级，页间信号互联使用 **`Off-Page Connector`（页间连接符）**，同目录同名即通。
   - **层次式图纸（自上而下 `Descend/Synchronize` 或自下而上 `Generate Part`）**：父方框图（Hierarchical Block）放 **Pin（图纸入口）**，子页对应放置同名 **Hierarchical Port（电路端口）** 才能垂直贯通；若将 `Off-Page Connector` 与 `Hierarchical Port` 混接或跨页不匹配，须在原理图 DRC 勾选 **`Check off-page connector connection`**（平坦式必勾）和 **`Check hierarchical port connection`**（层次式必勾）抓断网。
3. **物理驱动属性与规则检查（ERC/DRC）** [Cadence17.2 §6.2–6.5 p.129–136]：
   - `DIFF` 差分对只能在 **Flat 网络** 上建立（自动批配按公共前缀与 `+`/`−` 后缀过滤）；`PROPAGATION_DELAY`、`RELATIVE_PROPAGATION_DELAY`、`RATSNEST_SCHEDULE` 与 `Room`、`PCB Footprint` 均可在 Capture 属性表提前注入，随 **`pstchip.dat` / `pstxnet.dat` / `pstxprt.dat`** 三网表导入 Allegro。
   - **Instances vs Occurrences 模式切换**：普通平坦式/简单层次式原理图优先选 **`Use Instances (Preferred)`**（直接改写页内实体）；只有同一子模块被多次调用的**复合层次电路**，必须切到 **`Occurrences`** 才能为每次实例化分配独立位号而不撞号。
   - ERC/DRC 关键查错点：`Browse > Nets` 人工核对**电源网络名称是否赋对/未漏名**（DRC 往往不对电源错名直接报错）；`ERC Matrix` 行列交叉方格控制连线冲突等级（空白不报 / 黄色 `W` 警告 / 红色 `E` 报错）。
4. **自动编号与精确反向标注（Back Annotation）** [Cadence17.2 §6.6 p.136–139 / §11.10 p.332–334]：
   - `Annotate` 重排默认字母分类（表 `6-1`：`R/C/L/D/Q/M/J/K/U/E/F/G/H/V/I/X/STIM` 等），支持按 PM 页顺序、增量补号或 `PCB Editor Reuse` 模块复用来编；重编前未定号位设 `?`。
   - 需要修改特定前缀序号或 PCB 调换引脚/门电路后回传原理图：在 Allegro 用 `Export Logic` 导出后，由 Capture **`Back Annotation` 生成/执行 `*.swp` 文本文件**，核心三指令：
     - `CHANGEREF U1A U2A`（改位号）
     - `GATESWAP U1A U1D`（同一物理芯片内门交换）
     - `PINSWAP U1A 1 2`（同组等效功能引脚交换）

---

## 二、Allegro 文件体系与 Padstack 焊盘设计（§9, §11.1）

5. **Allegro 核心文件后缀清单（对照实图纠偏 `p.297` 印错）** [Cadence17.2 §9.1.2 p.212 / §9.9 p.237 / §11.1.3 p.297]：
   - `.brd`：PCB 电路板主文件；`.dra`：可编辑封装/图形源码图纸；`.psm`：编译后封装符号（普通贴片/插件体）；`.pad`：焊盘栈文件（注：书 `p.297` 正文误印作 `.psd`，实际与前文 `p.237` 一致为 `.pad`）；`.bsm`：带板框/安装孔的机械符号（Mechanical Symbol）；`.osm`：图框/格式符号（书 `p.297` 漏印小数点前缀）；`.fsm`：负片花焊盘（Flash）符号；`.ssm`：异形焊盘几何外形（Shape Symbol）；`.mdd`：复用模块定义（Module）；`.tap`：NC 钻孔带文件；`.art`：光绘底片输出文件；`.tcfx` / `.tcf`：可移植技术规则与叠层模板；`.scr`（书误印 `.ser`）：动作脚本；`.color`：视图层颜色方案；`.jrl`（书误印 `j`）：操作事件日志。
   - **强约束关系**：给原理图导入 PCB 用 `.psm` + `.pad`，日后修改则改 `.dra` 再编译覆盖 `.psm`；`.dra` 必须含 `Package Geometry` 下的 **`Place_Bound_Top`（必设零件实体边界与 `Package Height` 高度供 3D 干涉检测）**、`Assembly_Top` 装配外形、`Silkscreen_Top` 丝印、`RefDes/Silkscreen_Top` 与 `RefDes/Display_Top` 位号，及 `Body_Center` 原点中心 [§9.6.2 p.224–227]。
6. **焊盘设计经验准则与 6 层结构定义** [Cadence17.2 §9.7.2 p.229 / §9.8–9.10 p.234–253]：
   - 可靠性关键：**表贴焊盘焊点机械可靠性主要取决于长度而非宽度**；焊盘按元件封装尺寸公差上/下限联算，同一器件对称焊盘必须形状尺寸完全一致；接大面积电源/地铜皮的散热隔离导线宽 **`0.2–0.4 mm`**、长约 **`0.6 mm`**；波峰焊胶固元件可比再流焊适当加大焊盘防遮蔽漏焊（不过大导致拉薄不润湿）。
   - **`Padstack Editor`（17.2 新界面）核心层定义**：
     - **`Regular Pad`**（规则铜焊盘：`BEGIN / DEFAULT INTERNAL / END` 三区可分别定圆/方/矩形/异形；表贴仅设 `BEGIN LAYER`，热焊盘与隔离焊盘设 `Null`）。
     - **`Thermal Relief`（热风/花焊盘 `.fsm`）** 与 **`Anti Pad`（负片隔离盘）**：仅直插通孔及连接负片内层的过孔必须定义；书 `p.238` 经验关系：`阻焊 Soldermask = 常规焊盘 + ~0.1 mm`，内层热风焊盘内径 `≈ 钻孔直径 + 0.5 mm（或 16–20 mil）`、开口幅宽 `≈ (外径 − 内径)/2 + 10 mil`、多为 4 辐条 `45°`（或 `90°`）交叉连接。
     - **Mask Layers 极性本质**：`SolderMask` 出**负片**（画出来的区域代表去绿油开窗露铜）；`PasteMask` 出**正片**（画出来的区域代表钢网开窗刷锡膏）。
     - **`Options` 关键选项**：`Suppress unconnected internal pad`（隐藏内层无连线多余铜环，节省内层走线间隙、光绘按需抑制）；**`Lock layer span`**（锁定盲/埋孔起始/终止层跨度，防止日后层叠插入新内层时误把盲埋孔跨度撑穿新层！）。
     - **实图勘误**：§9.10.2 标题及正文误将 `20 mil / 40 mil` 盲孔（`b_v40_20.pad`）写成了 `20 mm / 40 mm`，同节参数栏截图清楚显示 `Units: Mils`。

---

## 三、Allegro 布局约束、正负片覆铜、扇出与技术复用（§10.3–13.10）

7. **板框、三大区隔离与软硬结合多叠构** [Cadence17.2 §10.3 p.278–285 / §11.3 p.302–311]：
   - **三道边界层次**：`Board Geometry / Outline`（17.2 起新增 `Design_Outline` 与 `CUTOUTS` 专用镂空层）是实物物理边；`Package Keepin` 是器件允许放置框（通常比物理外框内缩 **`≥ 2 mm`**，用 `Z-Copy > Contract` 偏移一键生成）；`Route Keepin` 是允许走线与铺铜外缘。若外框由散线段绘制，`Z-Copy` 时右侧 `Find` 面板须选 `Line` 而非 `Shape`。
   - **17.2 动态铜分层覆盖参数（`Dyn_*`）**：支持按单个 Pin/Via 或分层覆写热风连接方式与避让扩展（如 `Dyn_thermal_con_type`, `Dyn_fixed_therm_width_array`, `Dyn_clearance_oversize_array`, `Dyn_max_thermal_conns`），**仅对动态铜（Dynamic Copper）有效，对静态实铜（Static Solid）无效** [§1.1.2 p.3 / §12.2.1 p.354]。
   - **显示排障必开项**：若 Allegro 中过孔/焊盘只显示空心圆环或拐角有断差视觉，在 `Setup > Design Parameters > Display` 中勾选 **`Filled pads`**、**`Plated holes`**、**`Thermal pads`** 与 **`Connect line endcaps`** 并缩放刷新 [§10.3.1 p.279]。
   - **盲埋孔命名与生成**：通过 `Setup > B/B Via Definition` 设起始/终止层与 `Max Signal Layer Span`；规范命名格式为 `<前缀><起始层>-<结束层>`，必须预先建好焊盘并指派物理规则才可调用 [§10.3.3 p.284–285]。
8. **正片 Dynamic Copper vs 负片 `Anti Etch` 分割平面** [Cadence17.2 §12.2–12.3 p.354–364]：
   - **Dynamic Fill 三状态**：精细布线与最终光绘前必须设为 **`Smooth`**；大改铺铜前切为 `Disabled`（或 `Rough`）避免每次拉线卡死重铺；动态删孤铜用 **`Shape > Delete Islands`**。
   - **负片内层 `Anti Etch` 割地规则**：① **分割必须在布线之前完成**；② 切换 Class/Subclass 至 **`Anti Etch > All`**，设线宽（例 `15 mil`）画闭合分割线或**起止端点伸出 `Route Keepin` 边界之外**的切线；③ 执行 `Edit > Split Plane` 生成各分区并 `Assign Net`；④ 使用负片时**所有过孔与通孔引脚都必须在 Padstack 里建好合法的 Flash（`.fsm`）和 Anti-Pad**，否则光绘报错或死锁成整板短路 [§12.2.2 p.354 / §12.3.1 p.359]。
9. **扇出、自动/交互布线与泪滴时机** [Cadence17.2 §12.4–12.5 p.364–383]：
   - 布线顺序遵循 **先手布高频/时钟/电源/BGA 扇出 → 再局部群组或自动布线 → 最后人工精修**。
   - `SPECCTRA / PCB Router` 扇出可设 `Direction`（向内/向外/任意）、`Via Location`（器件外/内/任意）、`Fanout Grid`（`1 Wire` / `2 Wire Between Vias` 限定孔间通道线数）。
   - **补泪滴时机**：`Route > Gloss > Add Teardrops` **必须留在全部布线、改线彻底定稿之后最后一步操作**；如若加了泪滴又需微调走线，必须先删除局部或全部泪滴再动线，否则无法直接光滑编辑走线与过孔 [§12.5 p.382]。
10. **`.tcfx` 技术文件与 `env` 全局复用** [Cadence17.2 §10.6 p.288–292 / §13.10–13.11 p.409–414]：
    - `File > Export > Techfile` 导出 **`*.tcfx`**（XML 结构）保存全套 **`CrossSection` 叠层 + `Electrical/Physical/Spacing` 约束 + 用户属性**，在新板开板时一键 `Import > Techfile` 直接套用全司标准规范（注意自动备份：Allegro 的 **`autosave` 定时备份只在实际无键盘鼠标操作空闲时触发**，忙碌画板期间需手工 Ctrl+S [§10.5 p.287–288]）。
    - `<SPB安装目录>/share/pcb/text/env` 统一维护个人快捷键 `funckey` 与环境变量；批处理复现动作可用 Script 录制（`.scr`）或鼠标手势 `Strokes`（按住 `Ctrl + 右键拖动` 执行预定义轨迹命令，如 `C/M/U/W/A`）。

---

## 四、后期验证（DFA / 测试点 / 光绘 / 钻孔 / 迁移）（§13, 附录 1–2）

11. **按物理坐标重排位号 + DFA 装配审查 + 自动测试点（Testprep）** [Cadence17.2 §13.2–13.6 p.394–405]：
    - 布局定稿后通过 `Rename RefDes` 按 Top/Bottom 分层与坐标顺序重排位号，随即执行 **`Export Logic` + OrCAD `Back Annotate (.swp)`** 使原理图同步更新，绝不把板级重排后的位号留成原理图双轨。
    - **DFA（可装配性检查）**：调用约束规则核查器件体间物理间距（支持按 `Place_Bound` 或 `Assembly` 边算）、摆放角度与轴向跨距（`IPC-CM-770A` / `MIL-STD-275C`）、悬空线及**无网络属性孤立过孔（`orphan_via_audit`）**，输出 `dfa.msg` / `.log` / `.mkr`。
    - **Testprep 测试点与夹具文件**：统一生成贴底面或双面测试焊盘/过孔并标 `Probe_Top/Bottom`，`Fixed` 锁定探针点防后期改线移动导致针床报废，并单独导出给治具厂用的 **`probe_drill.log` / 测试钻孔**；确认丝印**字符高 `≥ 40 mil (1.016 mm)`、线宽 `≥ 6 mil (0.1524 mm)`**，严禁压焊盘、导电触点及定位反光点。
12. **【极易踩坑】Artwork（RS-274X）光绘胶片导出核心选项** [Cadence17.2 §13.7 p.405–406]：
    - 制板胶片格式默认 **`Gerber RS274X`**（坐标精度按公/英制设，例 `5.5`）；导出前务必勾选 **`Check database before artwork`**。
    - **`Undefined line width` 严禁留 `0`**：用于给光绘底片上未设线宽的零宽图形/文字/板框线赋予实体线宽，填 `0` 会导致丝印框或板框在 Gerber 里消失！一般设为 `4–6 mil`（或板框规范线宽）。
    - **必勾 `Vector based pad behavior`**（基于矢量渲染复杂 Flash 焊盘，避免传统光圈旋转失效导致热焊盘/矩形盘方向错乱）；**内信号层可按需勾 `Suppress unconnected pads`** 削掉无用空焊盘扩展走线空间（**外层焊盘不可抑制**）；`Negative` 负片默认 `Shape bounding box = 100 mil` 会在铜区外缘绘制 `2.54 mm` 黑色边界区；出底片的同时必须在 `NC > NC Drill` 与 **`NC > Drill Legend` 生成钻孔文件与实体钻孔分孔表（核对 Plated/Non-Plated 属性）**。
13. **PADS / DXF 向 Allegro 无损转化铁律** [Cadence17.2 附录 1–2 p.470–472]：
    - **PADS → Allegro**：PADS Layout 导出 ASCII `*.asc` 时，在格式设置下拉框里**必须选 `PowerPCB V3.0` 以下版本格式**，全选所有段（`Select All`），否则 Allegro `PADS IN`（需指向 `<SPB安装目录>/tools/bin/pads_in.ini`）无法识别导入；导完必须重构约束、层叠并逐个检查封装。
    - **DXF → Allegro 板框/结构对齐**：在 `DXF In > Edit/View Layers` 显式将 CAD 外形层映射到 Allegro 精确的 `Class: BOARD GEOMETRY` / `Subclass: OUTLINE`（或 `DESIGN_OUTLINE`）及精确单位，避免导入尺度缩偏十倍或落入错误绘图层。

---

## 五、PSpice 原理图仿真与收敛策略（§14）

14. **仿真电路硬规则与 9 大分析类型** [Cadence17.2 §14.2–14.3 p.417–429]：
    - 仿真库路径 `<SPB>/tools/capture/library/pspice/`；自建模型必须**`.olb`（符号）与 `.lib`（网表模型）成对存在**且符号含 `PSpice Template` 属性；全原理图**必须包含至少一个网络名 `0`（PSpice 接地符号）**；**禁构纯电压源+电感闭合回路、纯电流源/电压源与电容构成的纯直流割集**（实操可给电容并极大电阻 `100 MΩ–1 GΩ`、给电感串极小电阻 `1 mΩ` 破除拓扑奇异）；避免使用负值 R/L/C 以防求解器不收敛。
    - **9 类分析**：`DC Sweep`（直流传输特性）、`AC Sweep/Noise`（频响与等效输入噪声密度 `V²/Hz`，**无源电容/电感与受控源不产噪声**，交流小信号中需设信号源 `AC>0` 且 **`M` 表示毫、`MEG` 才表兆，PSpice 不区分大小写！**）、`Time Domain (Transient)` + 末周期 **`Fourier`（计算基波至第 9 次谐波，总仿真时长必须大于等于基波周期）**、`Bias Point`（含灵敏度 `SENS` 与小信号传递函数 `TF`）、**`Monte Carlo`（按公差概率抽样统计分散性）**、**`Worst-Case`（参数容差极限组合最坏偏移）**、`Parameter Sweep`（不覆盖子电路内部嵌套变量，注意抽提参数）、`Temperature Sweep`。
    - 17.2 仿真器新增 **`DelayT()` / `DelayTI()`** 行为延迟函数取代传统 `TLINE/Laplace` 以提速降不收敛，以及 `OPTIONS` 中设 `SKIPTOPO=1` 跳过冗余拓扑预检 [§1.1.2 p.4–5]。
15. **【核心收敛机理】`IC` vs `NODESET` 与储能器件初值等效** [Cadence17.2 §14.6 p.433–434]：
    - **`NODESET`（辅助预猜，易释放）**：仅作为 AC/TRAN 求静态偏置解或 DC Sweep **第一步求解直流初值的迭代初猜起点**，一旦推进后续计算步或稳态求解失败就失效释放（专治双稳态/震荡器起振锁死或收敛卡壳）。
    - **`IC`（强制钳位偏置）**：在 AC 与 TRAN 的整个偏置求解期间强置节点于给定初始电压（内部等效接入 **`0.00025 Ω` 内阻理想电压源**，**在 DC 扫描分析中不起作用**）。当同一节点**同时存在 `IC` 与 `NODESET` 时，`IC` 优先级高于 `NODESET`**。
    - **电容与电感自带 `IC` 属性（配合瞬态 `Skip initial transient bias point calculation / SKIPBP`）**：勾选 `SKIPBP` 后跳过直流工作点预求，未设初值的储能元件一律归 `0`；电容 `IC` 等效于求解时串联 `0.002 Ω` 直流电压源，电感 `IC` 等效于并联 `1 GΩ` 的恒流源。
