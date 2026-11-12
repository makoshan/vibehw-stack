# 专项布局门禁

先跑`basic_gate.py`的原生DRC/原理图一致性门，再跑本入口。两者是独立结论。

```sh
KPY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
"$KPY" ~/.claude/skills/pcb-autoroute/scripts/layout_gate.py board.kicad_pcb rules.json --out /新的证据目录
"$KPY" ~/.claude/skills/pcb-autoroute/scripts/test_layout_gate.py --out /新的测试目录
```

需要KiCad Python、原生`kicad-cli`和同名`.kicad_pro`。可用`--cli`指定CLI。工具对私有临时副本运行原生重新铺铜并保存副本，然后读取实际铜；源PCB/工程/自定义规则/声明文件留哈希，变化则作废。输出目录不能复用。

## 已实现的范围

| 类型 | 判据 | 不能由此证明 |
|---|---|---|
| `clearance` | 两组声明网的走线/焊盘/孔/实际铺铜形状避让；`projection` 取 `same_layer`（同层）、`adjacent_layers`（同层+叠层紧挨的两层，中间没有平面隔开）或 `all_layers`（任意层投影，最严） | 近场耦合、噪声或EMC达标 |
| `trace_budget` | 声明网的全网线长、过孔数、允许层 | 电源瞬态、最优拓扑 |
| `route_bound` | 两个实际焊盘间，按同层铜形状相接寻路；整段计长并加桥接焊盘包络对角线 | 地平面回流、电源环路面积；上界不是精确最短路径 |
| `reserved_layer` | 指定参考层无走线 | 地平面连续、地阻抗低 |
| `via_hole_in_pad` | 实际孔径与贴片焊盘形状相交；含未编号焊盘和实际穿过的外层 | 工厂已接单填孔盖铜；有侵孔且声明此工艺仍记未验证 |
| `ground_projection` | 走线投影缓冲区减去实际参考层地铜，输出缺口面积与位置；包括反焊盘孔洞 | 回流电流/阻抗、过孔旁回流质量 |
| `copper_keepout` | 明确坐标框与铜层内无铜；含铜字、无网铜图形、实际铺铜 | 器件/电池/外壳金属净空；缺真实RF尺寸不得填写猜测框 |

每个声明网必须存在且有实际铜对象；要求走线的规则还逐网核查实际走线。不存在对象、空规则、重复端点、未知检查类型、缺工程不能通过。

多边形转换误差为0.001mm；检查区域向外取，地铜向内取，保守处理曲线。`ground_projection`可设`gap_status=UNVERIFIED`列待审；不能把缺口豁免成PASS。换层反焊盘不是自动电气故障，须结合具体回流审核。

## 声明与退出码

```json
{"version":1,"rules":[
  {"id":"input-switch","type":"clearance","source":"器件手册章节与项目阈值依据",
   "basis":"project_policy","a":["INP","INN"],"b":["SW"],
   "projection":"all_layers","min_mm":1.0}
]}
```

例中1mm仅说明格式，**假设/未实测，不是通用推荐值**。每条须有`source`与`basis`（`project_policy`/`datasheet`/`factory`/`unverified`）。未知或待核环节用`pending`并写`reason`，不要删掉以获得全绿。

0=`DECLARED_LAYOUT_ONLY_PASS`；1=`BLOCKED`；2=`UNVERIFIED`。存在违规时总状态优先BLOCKED，同时逐项保留未验证。输出`manufacturing_ready`与`functional_ready`始终为false。

原生CLI可能静默跳过未知`.kicad_dru`约束。本工具保留其哈希、实际调用CLI，但**存在自定义规则时总状态至少UNVERIFIED**，不能冒充已经验证规则语法。需要另由支持该版本的原生编辑器核自定义规则。CLI铺铜附带的DRC报告也不能替代完整原理图一致性门。

## 验证证据

`test_layout_gate.py`生成24组真实板：正常1组；跨层/同层避让、过长路径、侵孔、地层走线/缺口、净空铜线/无网图形、断路径、缺网、未知规则、缺工程、空声明、缺口待审、组内无铜网、只有焊盘无走线、填孔声明、重复端点、未知自定义约束等23组负例。每组读取实际入口报告与退出码；不以模拟报告代替原生板测试。

器件建议来源见[官方经验](pcb-layout-sources.md)。几何阈值与去耦元件、增益/输入噪声、电极和RF实测分别验收。

## 叠层门：多层板布线前声明，并接进项目 gate（2026-10-25，原力手环 C07）

- **情形**：多层板的门禁只数原生 DRC 的未连和违规；`layout_gate.py` 已经做好，但项目 gate 脚本没调用它。
- **规则**：
  1. 布线前从板上实测哪些层是平面（不看 KiCad 层类型；碎片地铜不算），再声明三条：敏感网只走紧挨平面的层（`trace_budget.allowed_layers`）、平面下地投影缺口上限（`ground_projection`）、敏感网对时钟/SPI/开关节点在同层和相邻层避让（`clearance`，`projection: adjacent_layers`）。
  2. 规则从「网名正则 + 阈值」的策略文件按板展开，没走线的敏感网列 `pending`，不让门禁悄悄漏掉。
  3. 项目每次回导/收尾的 gate 脚本末尾调用它；BLOCKED 不得晋级。
  4. 阳性对照用原板（应 BLOCKED）；另做一块删掉错层敏感线的副本，确认层规则能转 PASS，证明不是恒拦。
  5. GND 被大量走成线（C07 r4c 927 mm）就是平面不够的信号，先改叠层再布线。
- **证据**：C07 r3/r4c 原生 DRC 只剩丝印违规，但 6 层只有 In1 一层地平面，敏感线 1870 / 1797 段走在无参考平面层，对干扰源冲突 196 / 121 处；对照板层规则转 PASS、冲突降到 13。实现：原力手环 `v12/compact16-proto-20261022/watch-release/{layout-policy.json,make_layout_rules.py,layout_gate.sh}`，提交 7185c580。平面判据（GND 铺铜 ≥ 板面积 70%）、避让 0.3 mm、缺口 0.5 mm² 都是假设、未实测。
- **置信**：中（两块板同一问题；阈值未经实测）。
