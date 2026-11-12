# PCB基础错误与布局经验：官方来源

检索日期：2026-10-08。以下为来源摘要及拟转成的规则，不能当作已实现检查或某块板已验收。没有原文数值的建议，不自行补通用毫米值。

| 对象 | 原文支持的经验 | 写入检查时的边界 |
|---|---|---|
| 走线方向/锐角 | KiCad有自由角度模式；Altium的Acute Angle检查同网铜形状形成的最小夹角 | H/V/45可以作为项目约定；锐角与线段方向分别检查，不根据非45°方向直接判电气故障。[KiCad](https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html#interactive-router-settings)、[Altium制造规则](https://www.altium.com/documentation/altium-designer/pcb/design-rule-types/manufacturing) |
| 悬空支路 | Altium Net Antennae检查未终止走线/圆弧，也包含末端仅接过孔的支路；可声明容许支路长度 | 输出网络、坐标、长度、用途。测试点/支路若有意保留，记录具体例外；不一律删除。[Altium制造规则](https://www.altium.com/documentation/altium-designer/pcb/design-rule-types/manufacturing) |
| 过孔误报 | 多层焊盘、散热盘、安装孔旁的过孔可能引起Net Antenna误报；官方要求核实所有相关层连接后再带说明豁免 | 错误样本必须真被拦；豁免限制到具体对象，不能关闭整个规则。[Altium知识库](https://www.altium.com/documentation/knowledge-base/altium-designer/kb-via-net-antenna-error) |
| ADS1298地回流/去耦 | 整层低阻地、短直接地；接地可用并联过孔；合理分区时无需强行割裂模拟/数字地；去耦近引脚 | 固定参考层后检查敏感网下方连续性、回流及去耦回路。多个接地过孔有明确用途，不按数量判错。[TI ADS1298 Rev K §12.1，印刷页98](https://www.ti.com/lit/ds/symlink/ads1298.pdf#page=98) |
| ADS1298高阻输入/时钟 | 差分输入成对、短直接；模拟输入远离数字线；建议输入滤波用C0G；慢SCLK仍可能受快速边沿振铃影响 | 按实际网络划分禁布区并核匹配；寄生电容、漏电与电阻噪声需要预算及实测。SPI不能只按频率低就忽略边沿。[TI ADS1298 §12.1](https://www.ti.com/lit/ds/symlink/ads1298.pdf#page=98) |
| TPS63802电源布局 | 输入/输出电容靠近芯片，连接短宽；功率与控制地按器件建议汇接；FB线远离L1/L2开关节点 | 参考器件布局检查电容回路、控制地/功率地及FB避让。不得从其他转换器照搬通用地切割方案。[TI TPS63802 Rev D §12.1，印刷页28](https://www.ti.com/lit/ds/symlink/tps63802.pdf#page=28) |
| 焊盘内过孔 | 嘉立创区分盖油、塞孔、树脂填孔盖铜等工艺；普通开口过孔置于焊盘可能吸走焊料 | Via-in-pad必须绑定当前订单工艺；盖油不能视为填孔盖铜的等价物，工艺不明记待核。[嘉立创过孔工艺](https://jlcpcb.com/help/article/pcb-via-covering)、[POFV说明](https://jlcpcb.com/news/free-via-in-pad-6-20-layer-pcbs-pofv) |
| RF/模块净空 | Nordic要求遵循合格RF参考布局；裸芯片天线匹配区下方内层有专门禁布建议；Seeed确认XIAO板载天线并提供原理图/设计资源 | XIAO载板必须依据实际模块几何/官方适用要求声明禁布区，检查所有指定铜层和附近金属。裸芯片匹配网络要求不能直接转成XIAO载板净空毫米值；本次尚未核定该毫米值。[Nordic nRF52840参考电路](https://docs.nordicsemi.com/r/bundle/ps_nrf52840/page/ref_circuitry.html)、[Seeed XIAO](https://wiki.seeedstudio.com/XIAO_BLE/) |

## 建议实施顺序（工程判断）

已有原生断网/悬空门禁之后，先补高阻输入与开关节点的分区避让、器件去耦/反馈回路、实际模块RF与过孔工艺检查。角度整理作为布局约定。规则模板：对象 → 来源 → 阈值/例外 → 失败坐标 → 修复方法 → 错误PCB反例。

来源说明的是一般规则或特定器件要求，不证明噪声或无线性能。后续已实现[专项几何入口](layout-gate.md)：声明网络避让、线长/焊盘路径、参考层/地覆盖、侵孔与铜框净空。自定义规则语法、回流阻抗、去耦完整环路、RF器件/整机金属净空与模拟性能仍未由此验证。
