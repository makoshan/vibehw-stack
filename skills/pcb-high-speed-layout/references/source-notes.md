# 来源和操作边界

- [用户提供的课程](https://www.bilibili.com/video/BV1MTP1e6Euo/)；[官方转载及分集目录](https://www.bilibili.com/video/BV1gyNgeHEWN/)：55 讲、60 集，使用逻辑派 FPGA-G1 案例。可核实目录覆盖原理图和电源、布局与叠层、阻抗规则、扇出、DDR3/HDMI 布线、时序、DRC 和生产资料。本技能依据目录提炼流程，未读取完整字幕或观看全片，不能当作逐讲转录。 [官方资料页](https://wiki.lckfb.com/zh-hans/fpga-ljpi/open-source-hardware/index.html) 指向开源工程；工程页面未能读取，未核实工程内部。
- [嘉立创网络树](https://prodocs.lceda.cn/cn/pcb/side-panel-left-net/index.html)：网络类、差分对、等长网络组各有用途；长度统计仅计导线，填充或普通线条连接部分不计。确认统计的实际端点和遗漏，勿把总网长直接当端到端时序。
- [时延等长](https://prodocs.lceda.cn/cn/pcb/design-time-equal-length/) 与 [等长调节](https://prodocs.lceda.cn/cn/pcb/route-equal-length-tuning/)：可按时延及引脚、焊盘、过孔延迟处理；先确认所装版本是否支持及数据来源。默认 0 ps 不是封装延迟实测为零。根据同组相对预算补偿，不是全部走线都蛇形。
- [官方阻抗计算](https://prodocs.lceda.cn/cn/private/enterprise/impedance-calc.html) 明确仅私有部署支持该功能。不要假定普通专业版有同一功能；选用适合的计算工具并和板厂核实实际叠层及可制造参数。
- [等长规则说明](https://wiki.lceda.cn/zh-hans/design-production/pcb-design/knowledge-point/homometic-regulation.html)：不同传播环境下物理长度相同不必然时延相同。通用或课程案例的公差不能替代器件、拓扑和时序预算。
