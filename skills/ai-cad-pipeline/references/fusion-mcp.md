# Fusion MCP（Autodesk 官方）接入与 API 坑

## 接入
- Fusion → 首选项 → 常规 → API → 勾「Fusion MCP 服务器（在此设备上本地运行）」，端口 27182。
- 端点 `http://127.0.0.1:27182/mcp`（**必须带 /mcp**，Streamable HTTP）。
- Claude Code：`claude mcp add --transport http --scope user fusion http://127.0.0.1:27182/mcp`。Fusion 必须在跑并开着一个设计文件。
- 会话内 MCP 客户端在服务器起来之前失败就不重试 → 工具要新会话（或 ToolSearch `select:mcp__fusion__fusion_mcp_execute,…`）。直连 curl：initialize 响应头拿 `Mcp-Session-Id`，后续带上，响应可能是 SSE `data:` 行。
- 4 个工具：`fusion_mcp_read`（apiDocumentation / screenshot / document / projects / activeCommand）、`fusion_mcp_execute`（featureType `script`，Python 需 `def run(_context: str)`，print 即返回）、`fusion_mcp_update`（undo/redo）、`fusion_mcp_electronics_read`。

## 脚本纪律
- 脚本整体是**一个事务**：任一异常全部回滚（连前面建好的 body 一起消失）。先用 `apiDocumentation` 查签名再写；写成幂等（开头删旧 body / 按名字找）。
- 孔特征的草图要建在目标面上（`root.sketches.add(face)` + `modelToSketchSpace`），建在 XY 基准面上报"未找到要剪切的目标实体"。
- 截图前 `app.activeViewport.fit()`；空设计截图是纯白 7KB，不是失败。
- 单位：API 内部 **cm**，所有 mm 值 ×0.1。

## 实测 API 坑（2026-09-03）
1. **零件(Part)文档只允许一个零部件**：`occurrences.addNewComponent` 报错 → 全部在 `rootComponent` 建 body，用 `body.name` 区分。
2. 变半径圆角：`FilletFeatureInput.addVariableRadiusEdgeSet(edges, startRadius, endRadius, positions: list[ValueInput], radii: list[ValueInput])`——没有 `setMidRadii`。传 `[V(0.5)], [V(r_mid)]` 即可。
3. 边的方向：变半径 start/end 对应 `edge.startVertex`，用 `startVertex.geometry.y` 判断哪头是前。
4. 一个 fillet feature 可混多组 edge set（前沿定半径 R10、后沿定半径 R22、侧边 10→22 变半径），共享顶点处半径要一致。
5. 抽壳成空心体：`shellFeatures.createInput(ObjectCollection(bodies), True)` + `insideThickness = V(t)`（不传要删的面）。
6. `exportManager.createSTEPExportOptions(path, root)` 导出整个 root → 一次只留一个 body：建 → 导出 → `body.deleteMe()`，最后留外实体给截图。
7. 顶圈边识别：`bb.minPoint.z ≈ bb.maxPoint.z ≈ z_top`；圆弧 `e.geometry.curveType == Curve3DTypes.Arc3DCurveType`；沿 X 的直边 `bb.maxPoint.y − bb.minPoint.y < 1e-4`。
8. 变半径圆角 + 抽壳 Fusion 一次成功，OCC 同参数在内壳 inset 全灭——这就是选 Fusion 的理由。

## 安全与变更纪律（吸收自 Autodesk 官方指引，2026-09-03）
- **27182 端点无鉴权**，仅限本机。别把端口暴露到局域网/家庭多 agent 网络——能连上就能驱动 Fusion 全 API。
- **事务回滚只保护"异常"**：成功执行但改错了的脚本不会回滚，undo 也只是单步。**首次写操作前先存版本**（云文档另存版本 / 本地另存），几何跑歪时回上一版比修特征树便宜。
- **工具面是动态发现的**：Fusion 更新后工具名/参数可能变。新版本第一个调用永远是只读自检（列工具 + apiDocumentation + document 读取），别假设旧脚本的工具名还在。
- Autodesk 自己对可靠性的定位：批量改参数、报表、重复几何、导出这类**有界任务**稳；复杂装配/完整设计不稳——和本管线"Fusion 只做母曲面"的分工一致。

## 从 Adam 插件逆向学到的对策（2026-09-04，读其 bundle 源码/pyc）
Adam add-in 与官方 MCP 是同一套 read/execute/update 工具面（palette 桥接替代 HTTP），但多四个工程化组件，对应我们已踩的坑：
- **watchdog（对症"适配器假死"）**：ping 间隔探活 → 判死 → 冷却后重建桥。我们在 MCP 客户端侧无法重建适配器，纪律化替代：**每批写操作前先发一个廉价 read（document）当 ping**；ping 失败直接请用户重开 API 勾选，不要拿长脚本去试。
- **modal_guard**：Fusion 有模态命令挂着时执行脚本是真实故障源（Adam 为此单写一个模块）。执行前 `fusion_mcp_read queryType=activeCommand`，非默认选择态就先停。
- **文档生命周期在工具面里**（execute handler 内置 document_open/save/close/按 id 找文件）：印证"改前存版本"，落地方式就是 execute 脚本第一步调 `doc.save('checkpoint before AI edits')`。
- **结果 stash（TTL+recover）**：跨 UI 重载恢复在途结果 → 等价于我们的"长脚本拆步、每步产物落盘导 STEP"，方向再获印证。
- 顺带一个 UI 事实：Adam 面板路径 `/home/adam/workspace/` 是其云端沙箱，生成脚本不落本机；要拿脚本得从面板复制导出。

## 实测更新（2026-09-04，纪律跑通 + function 建模范式）
- **工具面已扩**：`fusion_mcp_execute` 新增 `featureType:"document"`（open/close/save）——9-03 时还没有，动态工具发现实锤。**checkpoint 存版本现在是一等工具调用**，写操作前先 `{featureType:"document", object:{operation:"save"}}`（未保存的空白试验文档可跳过）。
- **适配器新 bug**：`fusion_mcp_read queryType=activeCommand` 在文档名含中文时报 `invalid UTF-8 byte` → 模态检查改在脚本里做：`ui.activeCommand == 'SelectCommand'`。
- **function 建模范式**（已验证：建 90×64×64 → 改参数 → 几何自动变 110×64×64，timeline 健康）：
  `userParameters.add(name, createByString('90 mm'), 'mm', comment)`（幂等用 itemByName 判存在）；
  草图尺寸 `sketchDimensions.addDistanceDimension(...).parameter.expression = 'pw'`；
  特征值用 `createByString('ph')` / `createByString('r_corner')`；
  **无单位标量（变半径 positions 0..1）仍用 createByReal**；
  改参数后 `adsk.doEvents()` 再读几何；健康检查 `all(not i.isSuppressed for i in des.timeline)`。
  价值：AI 建完的模型在 Fusion 参数表里可继续人肉调，不再是烤死数值的一次性脚本产物。
- 预检序列已写进 fusion_pillow_template.py 头部（modal guard → checkpoint → 建模）。

## 分工
build123d/text-to-cad：切件、开孔、探针、装配、导出（免费、可脚本、可回归）。Fusion：母曲面、T-spline、装配运动、CAM、以后 draft（前脸后仰）。

## 适配器假死（2026-09-03）
放样 + 变半径 + 抽壳脚本跑到一半，会话 MCP 客户端报 socket closed；Fusion 进程活、27182 在 LISTEN、CPU 1%，但任何 HTTP（含裸 initialize）被 reset/空响应 → 适配器已死，脚本层无法恢复。只能用户在首选项里关再开 MCP 勾选或重启 Fusion。**对策**：能用 OCC 放样+两点法则做的先在 OCC 做，Fusion 留给真做不了的；长脚本拆成小步（先 loft 导出，再 fillet），别一次跑 4 个 body。

## 写 Fusion 插件（2026-09-04，Lobster Shell）
样板在 `~/go/202608/lobster-box/fusion-addin/LobsterShell`：`LobsterShell.py`（UI/命令）+ `shell_core.py`（纯几何，无 UI，可经 MCP 直接 import 跑）+ `.manifest`。装法是软链进 `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/`，用户 Shift+S 运行。
把几何和 UI 分开是关键：同一份 `shell_core.py` 既是插件的核心，也能当 MCP 一次性脚本用，改完 `importlib.reload` 立刻生效，不用停/起插件。
新增 API 坑：**删 body 会级联失效其它 body 的句柄**（参数化文档删的是特征）→ 清场要反复 `item(0)` + try/except，不能缓存列表；大量草图点会盖满视口，建完设 `isLightBulbOn = False`（不能删，删了 loft 就废）。

## 换总装里的零件（2026-11-09，C08B R12 总装换表带）
- **端口不开先看首选项有没有被重置**：Fusion 启动日志里一串 `PREF: …OptionGroup was set to defaults`、MCP 只有 `Registered … handler` 没有服务启动时，MCP 开关是关的；勾上后要**重启 Fusion** 端口才开。选项存档在 `~/Library/Application Support/Autodesk/Neutron Platform/Options/<用户ID>/NGlobalOptions.xml`（UTF-16，先 iconv 再 grep）。
- **会话 MCP 判失败后不重连**：端口开了照样用 HTTP 直连（`initialize` → 带 `MCP-Session-Id` → `tools/call fusion_mcp_execute`），客户端样例 `~/go/202608/智能手表/fusion_call.py`。
- **中文三个坑**：`createFusionArchiveImportOptions` 路径含中文报「file does not exist」→ 拷到英文路径再导；STEP 导入后中文产品名被读成乱码 → 导入后按尺寸认件、`component.name` 重命名；脚本源码里写中文字符串去比对名字会对不上 → 用 ASCII 前缀匹配，中文名写 `\uXXXX` 转义（`json.dumps(ensure_ascii=True)` 生成）。
- **换件做法**：build123d 导出带 label 的 Compound STEP → `importManager.importToTarget(opt, root)` 零位移进总装 → 旧件 `isLightBulbOn=False`（不删）→ `exportManager` 导 `.f3d` 和整装 STEP（STEP 只含可见件）→ 再 `importToNewDocument` 回读核零件数、体积、隐藏状态。外观库名也是本地化的（「Fusion 外观库」），按英文外观名找不到。
