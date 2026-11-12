# 嘉立创两层板工艺复核（步 5）

（2026-11-10 从 SKILL.md 原文挪来，只改了相对链接）

对照 https://jlcpcb.com/capabilities/pcb-capabilities，逐项过，低于就是 iterxAI 里的 `AUTOROUTE_RULE_BELOW_JLC` / `AUTOROUTE_VIA_BELOW_JLC`：

- 线宽/线距 ≥ 0.127 mm（5 mil）；我们信号 0.2、电源 0.4，安全
- 过孔钻孔 ≥ 0.2 mm，外径 ≥ 0.45 mm；孔到孔 ≥ 0.254 mm
- 铜到板边 ≥ 0.2 mm（我们规则 0.5）
- 环宽 ≥ 0.13 mm
- 走线角度：新板默认H/V/45作为布局约定；是否限制其他方向按项目/工厂要求核实，非45°不自动等于电气错误
- 上传只放 Gerber/Excellon，不放 DRC JSON、报告、`.nc`
- 花焊盘撑不出两条辐条的小焊盘（0.3 mm FPC 焊盘）改实心连接，**不是放宽 `starved_thermal` 规则**
- **机械高度**：DRC 不看 Z。屏/盖板底下的正面件按封装最大高度过一遍（`check_heights.py <board> <limit>`，表在脚本里）；手焊的焊线盘、通孔件挪到背面，背面件要躲开模块（XIAO）本体轮廓。载板 v0.16 的电池座（4.25 mm）、两颗 SMA、五颗 0805 都是这样在渲染图上才被抓到的，v0.17/18 才改掉。
