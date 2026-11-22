---
name: pcb-high-speed-layout
metadata:
  version: "0.2.0"
description: 设计或评审涉及受控阻抗、差分信号、BGA 扇出或 DDR 时序的 PCB 布局布线；将器件约束、叠层、回流路径与制造验收关联起来。
---

# 高速 PCB 布局与布线

> **来源边界**：已补全商业 EDA 约束管理与前后仿真方法论（Cadence 17.2 Constraint Manager / Model Integrity / PCB SI & SigXplorer 闭环，见 [Allegro SI 与 SigXplorer 仿真要点](references/allegro-si-sigxplorer-notes.md)）；原 B 站六层课仍仅依据目录及补充文档整理（见 [来源和操作边界](references/source-notes.md)），不把单一案例参数当通用规则。

适用于高速接口、密集 FPGA/BGA 或需要时序预算的板。先用 `pcb-design` 处理整板电气与制造验收；KiCad 自动布线交给 `pcb-autoroute`。本流程不要求所有板做六层，也不以未检验模型或默认值替代板厂实测参数。

1. **冻结输入、模型准入与仿真底座。** 核对原理图、网表、板框、接口方向、器件数据手册与目标制造工艺。对参与高速 SI 仿真的芯片，在导出规则前先通过 Model Integrity（或等价检查器 `IBISCHK`）验证 `.ibs` V-I/Ramp 单调性并转换为 `.dml` 库（保留索引不删除，确认无 `Errors` 且完成 Single Buffer 试跑），确定器件 `Class`（`IC/DISCRETE/IO`）与 `Pinuse`，并为电源/地网络赋真 DC 电压（见 [Allegro SI 要点](references/allegro-si-sigxplorer-notes.md)）。
2. **布局、叠层与前仿真规划。** 固定连接器和结构件，按功能块预布局，估算最密 BGA 扇出通道；定义物理叠层材料、厚度、铜厚、导电率与介质 `ε_r/tanδ`，在未布线前对关键高速网络提取拓扑（含串联阻容打通的 `XNet`），在 SigXplorer 做前仿真与 Sweep 参数扫描（评估 `Overshoot / Noise Margin / First Incident / Eye / Crosstalk / SSN`），敲定最优端接与可容许桩长（Stub）；避免高速线跨参考面分割，换层审查回流跨层路径。
3. **先固化 ECSet/分层规则再扇出。** 按 Constraint Manager 分层继承链（`Net Class → Bus → Diff Pair → XNet → Match Group → Pin Pair`，越底层优先级越高）将前仿真最优拓扑输出为可复用电气约束集（`.ecset` / `.top`）并回赋真实网络；明确区分仿真结果 `Simulated Xtalk` 与布线前估算 `Estimated Xtalk`，差分对区分常规 `Primary`、进 BGA `Neck` 线宽间距与出芯片 `Uncoupled length`。核可制造孔径与盘中孔工艺，若用 `.tcfx` 技术文件跨板复用须复核层叠与材料一致。
4. **先关键链路再补全，分清等长与等时延。** 按接口手册和拓扑优先布线 DDR 数据/选通/地址时钟与高速差分对；按 Pin Pair 与相对匹配组（`Min/Max & Rel Prop Delay`）定义组内、对内与组间偏移，考虑 FR-4 外层微带线与内层带状线传播速率差异（例默认 `140 mm/ns` 左右与外内层 140–180 ps/in 差，等长度不等于等时延）。先优化物理路径与回流，再做局部蛇形补偿与全板铺铜。
5. **实板提取后仿真与同版验收交付。** 重新灌注平滑动态铜（`Smooth`）、切 `Routed Interconnect` 提取实际走线几何/过孔寄生与相邻耦合跑后仿真复检过冲、回沟、串扰与 `SSN` 裕量，不通过则返改走线与端接并重跑。交付同一版本的源工程、约束来源表、前后仿真对比报告、原生 DRC 报告（未连通数 = 0）及 Gerber/钻孔（如 Allegro 校验 `Undefined line width` 非零与 `Vector based pad behavior`）、BOM、坐标与装配检查。

肌电等混合信号板只迁移分区、叠层、参考面连续性与回流检查等适用方法；以模拟前端、人体接口安全及实际接口约束为准，不机械套 DDR、BGA 或六层。
