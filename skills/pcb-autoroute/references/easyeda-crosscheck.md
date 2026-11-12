# 嘉立创 EDA 对照检查（可选，步 5 的补充）

（2026-11-10 从 SKILL.md 原文挪来，只改了相对链接）

用 `easyeda-api` skill 的桥 + run-api-gateway 扩展，`SYS_FileManager.importProjectByProjectFile(zipFile,'KiCad',{importOption:'ImportDocument'},{operation:'New Project',newProjectOwnerTeamUuid:<本地 projects 目录>})` 能把 KiCad 工程 zip 导进去；枚举值在桥里不可见，直接传字符串。**实测（JLCEDA 3.2.186，KiCad 10 文件）：封装和过孔进来了，走线和网络全丢**——所以它的电气 DRC 无效，但几何检查有效。它查了一条 KiCad 不查的：**元件外形到通孔焊盘 ≥10 mil**（"Device to TH Pad"）。载板上电池座骑在 XIAO VBUS 通孔上（0 mil）就是这么发现的。跑法：`dmt_EditorControl.openDocument(pcbUuid)` → `activateDocument(tabId)` → 等 5 秒 → `pcb_Drc.check(true,true,true)` 返回分类数组；不激活文档会报"画布内没有相关订阅"。

下单（算价、制造包、嘉立创网页流程、SMT BOM/坐标）在 `jlc-order` skill。
