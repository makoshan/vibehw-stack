# Fusion 工作流与已遇到的坑

当连接 Fusion 或重用 `v4/fusion_build.py` 时阅读。先检查当前工具及插件状态，端点、版本和文件层级都可能变化。

## 工具接入

本次通过 Fusion 插件显示的本地 MCP 使用 `fusion_mcp_execute`，`featureType=script`，`object.script`。运行脚本需定义 `run(context)`；只读探针才标 `readOnly=true`。不要猜测系统上一定存在同端口服务，优先使用当前可发现的工具。

无目的工具而已有确认的本地MCP时，按 initialize→notifications/initialized→tools/call，保持返回的会话头。使用JSON编码脚本及请求体，避免shell字符串拼接。历史临时helper可能被清除，不能依赖`/tmp`永久存在。

执行修改前检查 `app.userInterface.activeCommand`；正常闲置为 `SelectCommand`。本案例曾遇到读取工具直接输出中文导致UTF8异常，可用只读脚本返回所需字段。不要中断用户无关交互。

## 原生后盖

Fusion内部长度单位cm，项目参数mm：Point3D数值除以10，ValueInput字符串使用单位。

STEP导入后实体可能在root，也可能嵌套多层occurrence。先检查实际层级，再定位目标单实体组件，不能固定“首个occurrence就是实体”。若出现多个候选，按名称/几何定位，不能任意修改。

本次V4流程：
1. V1后盖导入新文档，开启参数化设计；不覆盖当前用户模型。
2. 六个参数：ring_od、ring_width、ring_thickness、ring_clearance、floor_wall、ring_top。
3. 偏移XY基准面建立同心双圆。圆心固定；半径标注.expression绑定参数；选profileLoops.count==2的环形轮廓。
4. 从新外底向旧实体Join凸台，再从槽底向上Cut；participantBodies明确目标实体。隐藏草图与基准面。
5. 导出原生f3d及STEP，比对基准双向布尔差、实体有效性与数量。用文件重新导入检查时不要覆盖唯一原件。

`SketchPoint.isFixed=True`可用；不要调用不存在的`geometricConstraints.addFixed`。矩形标注可用`sketchDimensions.addDistanceDimension`，圆用`addRadialDimension`。

## 装配、爆炸与验证

需要约束装配时用asBuiltJoints刚性连接、固定后盖；静态爆炸时在副本中抑制刚性约束再移occurrence.transform2；存在pending snapshot时记录。V4另以STEP导入装配和爆炸快照，须说明这不提供参数联动或动画时间线。

导出使用exportManager的createFusionArchiveExportOptions、createSTEPExportOptions。检查timeline健康状态时跳过group，group的非Healthy枚举不能当成特征故障。保存成功与CAD匹配是两项检查。

CAD Viewer本次启动因缺少agent:start失败，已退回Fusion与CAD静态快照。以后可重新检查可用性，不把历史失败变成永久禁用规则。

## Adam 错误的边界

曾有selection watcher调用sendInfoToHTML出现“Cannot send information to native palette”。它是插件选中状态到界面的同步异常，不自动证明实体损坏或MCP全部失效。先区分插件面板和建模API结果；本次没有修复该插件，不能记录成已修好。


## Windows 实机建模（Codex / Claude 共用）

用户指定 Windows Fusion 或希望卸载 Mac 建模负载时使用这一流程。建模发生在 Windows Fusion 进程内，SSH 只负责传文件和读取结果；不要把 Mac 的 Fusion MCP 当作 Windows 接口。只迁移本次需要的生成/导出/验证步骤，不因内存问题重建整个工具链。

### 已验证入口与预检

2026-09-24 实测主机别名 `thursday-win`，当前桌面用户。别名是本机配置，不保证其他机器可用；先检查现有 SSH 配置，复用各客户端自己的认证，不复制密钥。Windows 已登录且 Fusion 已打开。检查 Fusion 进程、版本、系统可用内存与插件；可执行文件位于 `%LOCALAPPDATA%\Autodesk\webdeploy\production\<版本目录>\Fusion360.exe`，不可固定版本目录。

当前 Windows 仅验证 **上传脚本 → 用户在 Fusion 运行 → SSH 取回结果**。未接通常驻 MCP 或远程自动启动。SOLIDWORKS 的 COM/计划任务执行器不能用于 Fusion。发现监听端口不代表发现 MCP，不猜测端口协议。

### 可复用最小试跑

已实跑脚本及 manifest 随 Skill 提供：`scripts/MakoWindowsProbe/`。脚本必须在 Fusion 的 Python 环境内调用 `run(context)`；系统 Python 没有 `adsk` 运行时，不能直接运行此文件。

1. 核对 Windows 对应用户的实际 Scripts 目录。本机验证目录为 `%APPDATA%\Autodesk\Autodesk Fusion 360\API\Scripts`；其他版本以 Scripts and Add-Ins 显示的目录为准。
2. 把整个 `MakoWindowsProbe` 文件夹上传到 Scripts 下。已有同名文件先比对、备份，不覆盖其他会话正在运行的脚本。脚本和 manifest 同名，保留目录层级。
3. Fusion 中 **Shift+S → 脚本 / Scripts → MakoWindowsProbe → 运行**；未自动显示时从对话框添加该脚本目录。当前无已验证控制接口时，需要用户执行此 UI 步骤；已有授权且可用的 UI/MCP 可直接执行，不重复索取许可。
4. 脚本检查当前命令为 SelectCommand，新建独立设计，创建20×10×3 mm拉伸，核验600 mm³并导出F3D/STEP。保留原有文档，不覆盖产品模型。
5. 结果位于 `%USERPROFILE%\codex-tools\fusion-test\result.json`，产物是同目录 `probe-<时间戳>.f3d/.step`。通过SSH读取最新报告，核对started/output与本次执行对应，不能把残留PASS当新结果。脚本会覆盖result.json并保留时间戳产物；同一Fusion实例串行运行，避免同秒重跑。
6. 若失败，读取error，检查活动命令/登录弹窗/脚本加载；不盲目重放修改。需要改脚本时给独立名称或版本。后续真实模型脚本应校验目标文档、输入哈希及零件选择，保存为新版本。

取回示例（确认结果路径后执行；含空格路径使用参数数组或正确引用）：

```sh
scp "${MSTACK_WIN_HOST:-thursday-win}:codex-tools/fusion-test/result.json" ./result.json
```

中文Windows的PowerShell输出可能包含非UTF8进度信息。调用使用 `-NoProfile -EncodedCommand`（命令UTF-16LE后base64）、关闭进度输出；子进程先接收bytes。解码失败不代表远端操作失败，先确认文件/结果再决定重试；机器JSON应显式UTF8编码，不能用替换解码后的文字做精确路径。

### 内存、交付与验收

- 用 `Get-Process Fusion360` 的 WorkingSet64 记录当前进程工作集；`Get-CimInstance Win32_OperatingSystem` 的 FreePhysicalMemory 记录系统可用内存。开始/结束采样不是峰值；实际大型模型需要运行期间采样后才能报告峰值。
- 2026-09-24 Fusion **2705.1.25**：小样1实体、600 mm³、建模与导出1.13秒；运行后工作集2.33 GB，系统可用37.6 GB。仅代表该小样，不能推导完整外壳性能或比Mac更快/省内存。
- 已验证F3D/STEP导出，下载后F3D ZIP完整性与STEP文件头检查通过；**尚未重开、独立几何重读、全壳试跑或远程自动启动验收**。后续按任务补原生重开、关键参数修改重建、几何比对及实物试装，不把ZIP完整当CAD正确。
- 原始证据：智能手表项目 `evidence/2026-09-24-fusion-windows.md` 与同名目录。通用步骤与测试脚本已在本Skill，其他项目无需依赖该项目临时目录。

官方脚本说明：https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/WritingDebugging_UM.htm
