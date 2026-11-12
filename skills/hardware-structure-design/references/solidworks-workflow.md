# SOLIDWORKS 原生修改与远程执行

用于用户指定 SOLIDWORKS、原生零件/装配交付或既有原生模型修改。通用结构、实物和打印约束沿用主 Skill 与 mako-cad-fit，不在此重复。

## 先确定编辑真源

- 每个零件指定一个编辑真源：生成器或原生文件。跨工具前固定输入版本并记录哈希；不可在两边独立修改后静默覆盖。
- 声明交付档位：导入实体；导入基体加局部原生特征；原生参数化零件；原生约束装配。静态定位不等于配合关系，导入STEP不恢复原设计历史。
- 仅将本任务需要常改的尺寸、约束或方程原生化；旧导入体的孔距若不能联动，应回源修改或明确局部重建。
- “原生可编辑”须验证一个关键尺寸改值、重建、几何随动、恢复并保存。只有特征名称不能证明设计意图正确。

## 接入与预检

优先复用已授权、已配置的连接。当前验证过的路径是 SSH → Windows 已登录用户的 Interactive 任务 → MCP stdio → 桌面 SOLIDWORKS。SSH shell 可达不代表能访问桌面 COM。需要安装或修改远端环境时，遵守当前任务授权范围。

首次探测版本、活动文档、已有文档和当前编辑状态；核对模板与单位。先新建独立小测试件验证连接、保存和重开；同一环境已验证时无需每轮重复新建测试件。

机器配置默认读取本机 `~/.config/mako-cad/solidworks.json`，可用 `--config` 指定。键如下，具体值在本机配置，不写入通用 Skill：

- `ssh_host`：已有SSH别名；复用本机身份验证。
- `remote_dir`：远端用户主目录下的相对暂存目录，不含空格或 `..`。
- `python`：Windows已具备 `mcp`、`pywin32` 等服务依赖的Python绝对路径。
- `server`：已安装MCP服务入口的绝对路径。
- `timeout_seconds`：等待期限，默认600秒；仅控制主机等待，不代表远端已停止。

执行器是本 Skill 的 `scripts/solidworks_remote.py`，仅用Python标准库启动SSH；MCP依赖仅在Windows worker导入。调用文件为JSON数组：`[{"name":"tools/list","arguments":{}}]`。实际建模参数遵循当次 `tools/list` 返回的模式。

```sh
python3 <skill-dir>/scripts/solidworks_remote.py --check
python3 <skill-dir>/scripts/solidworks_remote.py calls.json
python3 <skill-dir>/scripts/solidworks_remote.py --status <run_id>
```

这不是自动注册到宿主工具列表的常驻MCP服务。不要宣称仅安装脚本即可从任意客户端直接调用命名工具。

## 写入与恢复

- 所有客户端通过此执行器写入，Windows用户目录的 `.solidworks-cad-writer.lock` 以排他创建保证同一用户串行写入。锁记录run ID、PID和请求路径。其他入口/其他Windows用户不受此锁保护，仍需设备占用约定。
- 在每个修改阶段开头断言活动文档路径、实体/特征及所需选择，输出到新版本；保留用户已有文档。引用路径、API单位和选面数量必须先核实。
- 把一个有边界的操作阶段放在一次调用中，避免大量跨进程小调用；复杂阶段中可自行写检查点。执行器记录每次MCP调用的 executing/returned/failed，不会看到 execute_python 内每一行的进度。
- 结果与进度用临时文件替换，位于远端暂存目录的 `<run_id>.progress.json` / `<run_id>.done.json`；错误结果立即停止剩余调用。该服务可能在文本 `[ERROR]` 中报告失败而 `isError=false`，两者都检查。
- 文件上传必须完成并核对哈希后再导入。不要在SFTP还写入时调用CAD读取同一文件；大STEP可压缩传输。
- `waiting_timeout` / `unknown`：先 `--status` 查执行到哪一步，不能自动重放。若断开时写入结果未知，锁保留；正常返回后释放。进程异常退出留下的锁也不自动删除。
- 清除遗留锁前，核对owner run/PID、任务与子进程、SOLIDWORKS当前文档和已生成特征，确认不存在继续执行的写入；记录如何恢复。只停止已核实属于本轮的进程，不结束SOLIDWORKS或其他会话。
- `blocked` 只代表另一调用或遗留锁占用，不能当作授权去抢锁。任务完成记录可查后再决定是否重提此前未执行的调用。

## COM 和几何选择

- `FeatureExtrusion2` 反向拉伸用第3参 `Dir`，第2参 `Flip` 只管切除侧；方向错不会报错，靠体积/包围盒自检抓（2026-09-24 实测差 260×16×1）。
- 不用通用 `callable()` 判断COM对象是不是需要调用的方法；返回的COM对象也可能可调用。核对对象/类型库，必要时对已确认的方法显式 `_FlagAsMethod`。适配差异留在适配层，不复制到每个模型。
- 中文模板与基准面按实际模板、特征类型或明确名称解析，不默认英文 `Part.prtdot` / `Front Plane`。
- 优先限定目标特征及面后选择边，验证类型、法向/位置、数量与是否重复。坐标选取返回True不能证明选对边；尽量不遍历整机所有边来完成一个局部修改。
- 变更后核对体积趋势、局部形状及受保护区域。选择错误可能仍得到有效实体，不能仅以“无API异常”判成功。

## 检查与交付

按改动范围选择尺寸、壁厚、实体数、间隙、干涉及安装路径检查，不重复无关整套检查。需要数学/几何参考时只建相关部分。

1. 保存原生文件，关闭本轮新文档后重开，验证实体与关键参数/特征。
2. STEP重读并独立核对；宣称保持不变的接口用差集或对应几何验证，不只比较总体积。
3. STL交付检查闭合、单位和打印姿态；查看变化处与必要装配视图。CAD检查和实物试装分别报告。
4. 原生装配交付须包含引用零件，验证在另一目录打开引用完整；不把一个失去引用的SLDASM当完整交付。

## 已实测范围与案例

2026-09-24，SOLIDWORKS 33.5.0 + alisamsam/Solidworks-MCP commit `ee8f42a1a919af5e0fa8d1dcd24270c9983ce027`：连接、草图矩形、拉伸、保存/重开、原生局部倒角和STEP交换已实测；文档/特征遍历需要[本地COM兼容补丁](solidworks-mcp-compat.patch)。补丁只对应所列commit；新安装先检查版本和差异，不对其他版本盲目应用。25工具可枚举不代表25工具全部验收。独立测试件的命名拉伸尺寸已验证：3→4→3 mm、体积600→800→600 mm³，恢复保存并重开通过；这不代表复杂参数联动已验收。装配配合、工程图、复杂参数联动按任务另验。

V04案例：坐标选择误选根部边，错误倒角仍生成有效实体，但体积增加；独立双向差检出3.78 mm³误差。改为从新增拉伸的六个顶面选24条边后，实际STEP与参考差为0。此事实支持“选取后验几何”的规则，不把案例尺寸作为通用公差。

资料：[设计意图](https://help.solidworks.com/2024/English/SolidWorks/acadhelp/c_design_intent.htm)、[装配间隙API](https://help.solidworks.com/2025/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IClearanceVerificationMgr.html)。
