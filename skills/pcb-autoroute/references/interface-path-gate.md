# 接口路径门（2026-09-16，载板 v0.19B 放反 FPC 座的根因）

（2026-11-10 从 SKILL.md 原文挪来，只改了相对链接）

DRC 0/0、屏下高度、Fusion 体积叠放全过，板子照样错，**而且是付款排产之后才发现的——¥182.18 打了水漂（v0.19B，PCB Y12 + SMT2609162805）**：FH12 屏排线座的嘴朝板内，排线从板边折过来进不去；电池/马达/按键的焊线盘也都放在"有空的地方"而不是"线从哪来的地方"。**这三道门查的都是"放不放得下"，没有一道查"线从哪边来、座子从哪边接"。** KiCad 封装没有"插入方向"属性，DRC 不知道屏在哪；渲染图和 3D 里能看出来，但没人对着看。

对策（已抽成通用脚本 `scripts/interface_gate.py`，门名 `INTERFACES_PASS`，自检 `scripts/test_interface_gate.py`）：
- 每个对外接口在项目的 `interfaces.json` 里写一条声明：连接器写**嘴的方向**（FH12 下接触：从触点脚行指向金属固定耳那一侧就是嘴；Hirose FH12 目录 p.4）+ 对手从哪边来 `mate_from` + 接触面 `contact` 与对手脚表 `panel`（可选背靠背单调轴 `monotonic_axis`）；焊线盘写对手区域 `region` 或板边 `edge` + 最大距离 `max_mm`；模块写 `usb_pad` / `far_pad` 要朝哪面墙 `usb_toward`。
- 板子改版先改这张表，再用 KiCad Python 跑 `scripts/interface_gate.py <board.kicad_pcb> --spec <interfaces.json>`（自检：`scripts/interface_gate.py --selftest`），和 `verify_board.py`、`check_heights.py` 一起过才算 ROUTING_ONLY_PASS。缺少必选接口直接判 `FAIL`（不静默 `SKIP`）。v0.19B 回放：J1/J2/J3/SW1-3 六项 FAIL；v0.19C 仅当时的路径门通过；2026-09-26 加入独立屏端定义的脚序检查后，B/C 均失败，D 倒序网络后通过设计层检查，实物验收另列。
- 堆叠模型里**把排线和导线画进去**（Fusion 文档「MAKO Watch 2.0 v0.19B in V36」的 body `8 FPC tail`：0.3×6.5，屏 +x 边折 180°，外径 0.95）：只有画了路径才看得出折弯伸出屏边 0.97 mm、和 V36 按键座打架 0.39 mm 这种事。方块模型"省略排线/导线"就是省略了这次的错误。
- 渲染图不是给人"看一眼"的：连接器类逐个对着渲染图问"排线/导线从哪来、嘴朝哪"，写进 REVIEW。
