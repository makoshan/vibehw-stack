# Cadence Allegro PCB SI / Constraint Manager / SigXplorer 仿真与约束闭环要点

## 来源与定位

- 来源：《Cadence 17.2 电路设计与仿真 从入门到精通（云课版）》（李鹏、吴荣 等，人民邮电出版社，2020.1，ISBN 978-7-115-50880-5）第 12.1 节（`p.345–353`）与第 15 章（`p.436–468`），经全章节 Apple Vision OCR + 图表/参数界面样张核对整理，标注 `[Cadence17.2 §x.x p.xxx]`（正文书页）。
- 定位：补全 `pcb-high-speed-layout` 原缺的**真正商业 EDA（Allegro Constraint Manager + Model Integrity + PCB SI / SigXplorer）约束驱动前后仿真与拓扑回赋全闭环方法论**，与 `source-notes.md` 的公开视频目录定位互补。所有参数以实板叠层、芯片 IBIS 模型与板厂制造能力为准。

---

## 一、Constraint Manager（约束管理器）分层继承与电气指标体系（§12.1）

1. **八级约束对象与覆盖优先级** [Cadence17.2 §12.1 p.345–346]：
   - 继承链由粗到细：`System（系统）→ Design（单板）→ Net Class（网络类）→ Bus（总线）→ Differential Pair（差分对）→ XNet（扩展网络，串联阻容感打通两侧的合并信号通路）→ Match Group（匹配群组，相对目标或绝对偏差）→ Pin Pair（精确驱动端对接收端管脚对）`。
   - **冲突裁决铁律**：上层设定向下继承，**越底层的独立定义优先级越高（覆盖上层设置）**；含串联端接电阻的高速线必须生成 **`XNet`**（靠赋予离散阻容模型或设信号属性建立连接关系）后再设全路径等长与前仿真。
2. **电气（Electrical）核心规则分组语义** [Cadence17.2 §12.1.1 p.346–351]：
   - **`Signal Integrity` 6 表**：
     - `Electrical Properties`：设定网络频率/周期、占空比、抖动（Jitter）、测量周期。
     - `Reflection`：最大允许过冲 `Overshoot Max` 与最小高低电平噪声裕量 `Noise Margin Min`。
     - `Edge Distortions`：边沿单调性敏感度标记与 `First Incident Switch`（首波即达有效跳变，防时钟重触发）。
     - **`Estimated Xtalk` vs `Simulated Xtalk`**：两者字段相同（`Active/Sensitive Window` 翻转窗口、`Ignore Nets` 排除静态网、`Xtalk / Peak Xtalk`），但前者用于**布线前按几何估算预判**，后者用于**提取拓扑仿真后的实测判读**，不可混淆 [p.348]。
     - **`SSN`（同步开关噪声）**：同时填写关联的 `Power Bus Name`、`Ground Bus Name` 与最大允许 `Max SSN (mV)`，负裕量即报警。
   - **`Timing` 2 表**：`Switch/Settle Delays`（最小首次翻转 `Min First Switch` 与最大稳定建落 `Max Final Settle`）与 `Setup/Hold`（绑定参考时钟名、周期、时钟偏斜 Skew）。
   - **`Routing` 6 表（实战重点）**：
     - `Wiring`：定义预设拓扑（`Daisy-chain` 菊花链 / `Star` 星形）、**`Stub length`（最大分叉桩长）**、`Via count`、平行并行限长。
     - `Impedance`：依赖精确叠层介质 `ε_r` 与厚度的单端/差分阻抗与偏差容限。
     - `Min/Max Propagation Delay` 与 `Relative Propagation Delay`：可细选 `Longest/Shortest Pin Pair`、`Longest/Shortest Driver/Receiver` 或 `All Drivers/All Receivers`，支持全局/局部组匹配与精确 `Delta / Tolerance`（单位支持 `% / ns / mil`）。
     - `Total Etch Length`：布线前以 `Unrouted net length`（曼哈顿预估比例 `Manhattan ratio`）检查布局可行性，布线后校验实际线长。
     - `Differential Pair`：明确区分 **`Primary width/gap`**（常规走线对宽/间距）、**`Neck width/gap`**（进密封装 BGA 区域的颈缩允许值）、**`Uncoupled length`**（`gather control` 设为 `include` 计入出芯片扇出非耦合段、`ignore` 则只算主干不耦合）、**`Phase tolerance`**（动态/静态相位长度偏差）与对间 `Line spacing`。

---

## 二、Model Integrity 模型转化与准入校验（§15.3）

3. **IBIS 模型解析校验（使用前强前置门禁）** [Cadence17.2 §15.3 p.438–445]：
   - Allegro PCB SI 不直接运行原始 `.ibs` 文本，必须用 **Model Integrity** 转为专有 **`DML`（Device Model Library）格式**。
   - 打开 `.ibs` 后软件自动运行 **`IBISCHK`** 检查语法、上拉/下拉 V-I 曲线、上升/下降 `Ramp` 速率单调性及数据合理性；必须核查 `Errors: 0`（对单调性轻度警告需核清或修补后再 `IBIS to DML`），并与 `.ibs` 存在同级目录并保持同名一 一对应，避免多版本混乱。
   - 内置 **`Simulate Buffer`（IO Cell 独立仿真）** 可在挂进整板前先单颗加 `Rseries` / `Rref` / `Cref` / `Vref` 检查波形是否正常起振；支持用 **`ESpice to Spice`** 将 Cadence 行为模型转为通用 `Generic Spice`。

---

## 三、前仿真 → ECSet 拓扑约束 → 后仿真完整闭环（§15.2, §15.5–15.9）

4. **抽取网络拓扑前的 5 项数据库准备（缺一即报模型或电压缺失错）** [Cadence17.2 §15.5 p.446–451]：
   - **① `Cross Section` 物理叠层**：设置介质厚、铜厚、导电率（例铜 `595900 mho/cm`）、介电常数 `ε_r` 与损耗角正切（前仿真虽可用理想传输线预估，涉及损耗/真实阻抗/后仿真必设真实叠层 [§15.5.1 p.447]）。
   - **② `Identify DC Nets`（直流供电网络赋值）**：为各电源/地网络与电压源管脚赋真实电压（例 `3.3 V / 1.5 V / 0 V`）。原书 `p.448` 注意框笔误写“都不是必需”，但上文实操与工业界明确：若要仿真终端上下拉电压、IO 摆幅阈值比对、PDN 或 EMI，必须正确赋予电源和地网络电压。
   - **③ `Manage Library` 挂载 DML**：打开后会在工作区自动生成 **`devices.dml` 与 `cds_models.ndx`** 模型索引文件，**不可随意手工删除**，否则波形仿真立刻报错；并通过 **`Device Setup`** 逐一标定 **`Class`（有源 `IC` / 无源 `DISCRETE` / 接口 `IO`）** 与 **`Pinuse`（`IN / OUT / BI / TRI / POWER / GROUND / OCA / OCL`）** [§15.5.3 p.448]。
   - **④ `Model Assignment` 模型分配**：阻容感（已填 `Value` 值的无源 `DISCRETE` 器件）可由工具自动生成 `ESpiceDevice` 模型（但须核对 `Value` 与实板一致，无参与仿真的器件可不赋）；缺原厂 IBIS 的器件可用 **`Create IBIS Model`** 暂赋默认 `CDSDefaultIO_2p5v` 等级缓冲器作预估；支持导出/复用 `.map` 模型映射表。
   - **⑤ 互连抽取偏好（`Analysis Preferences > Interconnect Models`）**：核对未布线默认速度（默认差分/传输默认传播速率 ~`1.4142×10⁸ m/s` 即书内 FR-4 换算基准 `140 mm/ns`，`Default Impedance 50–70 Ω` / `Diff 100 Ω`）及耦合仿真抓取窗口（`Coupling Window`、`Min Coupled Length`、`Bypass Buffer Delays: From Library / On the Fly`）。
5. **前仿真闭环：Extract → Sweep → Set Constraints → 回赋 PCB（§15.6–15.8）** [Cadence17.2 p.451–468]：
   - **拓扑提取与版本控制**：从 `Analyze > Probe > View Topology` 或 `Constraint Manager` 把典型网络/XNet 抽入 **SigXplorer**；调整预布局间距、端接方式（串联/戴维南/交流并联阻容）与拓扑分叉（T 型/菊花链）。修改同一拓扑模板后递增 **`userRevision`**，Constraint Manager 可原位更新而不必逐条重指网络。
   - **激励组合与三大测量维度**：按引脚性质给驱动与邻近干扰线设 **`To Cell Stimulus Edit`**（`Pulse / Rise / Fall / Quiet Hi / Quiet Lo / Tristate / Custom` 码流）；可在 `Reflection`（过冲 `Overshoot`、回沟单调 `Monotonic/Ringback`、`Noise Margin`、眼高/眼宽/抖动 `EyeHeight/Width/Jitter`、首波翻转 `FirstIncident` 与 `SettleDelay`）、`Crosstalk`（邻线常高/常低看受扰净峰串扰，支持同步耦合多线评估）及 `EMI` 切换观察。
   - **Sweep 参数扫描与目标收敛**：利用 `Parameters`（`Linear Range` / `Multiple Values` 扫描串阻值、线宽、线长、`traceConductivity`）配合自定义 `Expression`（如观测 `TL3.impedance`）找出过冲满足且单调切换的最优端接电阻值与最大可容许桩长（Stub）。
   - **【关键衔接点】`ECSet` 回赋给 Allegro 实际网络**：在 SigXplorer 中用 `Set > Constraints`（共 10 类选项卡：`Switch-Settle / Prop Delay / Impedance / Rel Prop Delay / Diff Pair / Max Parallel / Wiring / User-Defined / Signal Integrity / Usage`）固化的规则保存为拓扑/电气约束集 **`Electrical CSets (.ecset / .top)`**；在 `Allegro Constraint Manager` 用 **`File > Import > Electrical CSets`** 导回板级工程，选中一组实际同类网络右键执行 **`Constraint Sets Reference`** 批量引用，直接将前仿真结果化作布线实时 DRC 围栏！
6. **布线后实板提取与后仿真复检（§15.9）** [Cadence17.2 p.468]：
   - 全板布线并重灌动态铜皮、确定真实叠层后，把分析模式切至基于实际走线几何、过孔寄生与相邻耦合提取（`Routed Interconnect Models`）再进 SigXplorer 重跑 `Reflection + Crosstalk + EMI + SSN`。
   - 对未过阈值的具体网络必须回到 Allegro PCB Editor 中微调邻近线距、补包地/改换层孔或重修端接，然后重新灌铜复测，直至后仿真全部收敛为正裕量再归档投板。
