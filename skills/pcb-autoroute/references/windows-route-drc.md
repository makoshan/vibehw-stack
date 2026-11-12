# 在 Windows 上跑布线和 DRC（省 Mac 内存，2026-09-24 实测）

（2026-11-10 从 SKILL.md 原文挪来，只改了相对链接）

Freerouting 的界面、嘉立创EDA 和 pcbnew 改板都很吃内存，所以全放到 Windows 上跑（SSH 别名 `thursday-win`，可以用 `WIN_HOST` 改）。Mac 只负责发指令、收产物。

| 脚本 | 做什么 |
|---|---|
| `scripts/win_route.sh in.dsn out.ses [passes=500] [timeout_s=3600]` | 在 Windows 上跑 Freerouting **1.9.0** → 取回 SES 和 `*.freerouting.log` |
| `scripts/win_finish.sh path/to/finish_vNN.py [args]` | 在 Windows 上跑改板脚本（导入 SES、删残线、加宽、铺铜、DRC），**脚本本身不用改**；产物写回脚本所在目录。超时用 `WIN_TIMEOUT` 设，默认 3600 s |
| `scripts/win_drc.sh board.kicad_pcb [out.json]` | 把板子连同 `.kicad_pro`、`fp-lib-table`、`*.pretty` 一起传过去 → Windows 上的 `kicad-cli pcb drc` → 取回 JSON，打印 unconnected/violations |
| `scripts/win_job.sh dir timeout 命令行` | 前两个脚本共用的底层：把目录打包上传（排除 references/export/logs/图片/3D/zip/jar）→ 用当前用户的 **Interactive** 临时计划任务执行 → Mac 每 10 s 用一条新的短连接轮询 → 只取回运行中新建或改过的文件 |
| `scripts/win_shim.py` | 把脚本里写死的两个 Mac 路径（KiCad 的 Python、`/opt/homebrew/bin/kicad-cli`）在 `subprocess` 调用时换成 Windows KiCad 的路径；遇到没映射的 Mac 路径直接报错，不会半翻译着跑下去 |

- Windows 环境：Temurin 25 JRE（`C:\Program Files\Eclipse Adoptium\jre-25.0.4.101-hotspot`）；jar 在 `%USERPROFILE%\codex-tools\freerouting\freerouting-1.9.0.jar`（sha256 `9084a488…3102`，跟 `2.0/pcb/carrier/references/` 里那份一样）；KiCad **10.0.6**（跟 Mac 同版本），自带 Python 3.11.5 + pcbnew。
- 1.9.0 **没有 headless 模式**：直接在 SSH 里跑会报 `HeadlessException`，所以要走 Interactive 计划任务，窗口会开在 Windows 桌面上。**Windows 必须保持登录，而且会话要挂在物理控制台上**：远程桌面「断开」后会话状态变成「断开」、没有显示器，任务照样报 HeadlessException（Codex 2026-09-25 遇到过）。`query session` 里 当前用户那行应该是 `console … 运行中`。如果不是，在远程桌面里以管理员身份运行 `tscon <会话ID> /dest:console`，它会结束远程连接、把会话交回物理屏幕（副作用：物理屏幕停在解锁状态）。**更省事的路：thursday-win 的 WSL 自带 WSLg 显示，`DISPLAY=:0 java -jar freerouting-1.9.0.jar …` 用 `offload.sh --host win` 派即可，不依赖桌面会话**；容器里无头或 `xvfb-run` 跑 1.9.0 会静默卡死不出 SES（见[收尾残余未连](residual-nets-through-hole.md) 第 2 节）。
- 为什么轮询用短连接：Tailscale 链路会断（2026-09-24 断过一次，`Broken pipe`），一条长 SSH 会话一断就丢结果；现在任务在 Windows 上照跑，轮询断了会自动重试。
- **`codex-tools\` 是多个会话共用的，只删自己的 job id。** 2026-09-24 我整目录 `rmdir` 了 `freerouting\runs`，结果把另一个会话正在跑的任务的 `run.cmd` 也删了：cmd 执行批处理时每一行都要按文件名重新打开文件，删掉后布线跑完也写不出 `done.txt`。后来按原内容逐字节重建才救回来。删之前先查 `schtasks /Query` 和 java 进程的命令行。
- 成功后远端 `codex-tools\jobs\<id>` 会删掉；失败或超时就保留，去那里看 `log.txt`。超时不删任务、也不重跑，退出码 124。碰到下面「已知坑」里那种 NullPointerException 死循环时，只 kill 这个 job 目录下的那个 java 进程，别用宽泛的 taskkill。
- 验收（back-sensor，2026-09-24）：
  - 布线：Windows 上 44–54 s，SES 79 wires / 13 vias；导入后 Windows DRC 0 unconnected / 0 violations，Mac DRC 结果相同。
  - DRC 阴性对照：未布线的板，Win 和 Mac 都报 42 个 unconnected。
  - `finish.py`：Windows 26 s，Mac 49 s。两边摘要行完全一样（widened 45 / kept_narrow 22 / 0 violations / 0 unconnected）；用 pcbnew 逐段比对，171 条走线（净名、层、线宽、端点）和 13 个过孔一一相同，铺铜面积都是 69.1957 mm²。
- 搭环境的坑：SSH 配置开了连接复用，`-N` 转发要加 `-o ControlMaster=no -o ControlPath=none`；Windows 上 winget 的 CDN 可能不通，这时从 github.com 的 `KiCad/kicad-source-mirror` release 下载，再对 digest；KiCad 静默安装必须写 `/S /allusers`（只写 `/S` 会返回 666660，这是 NsisMultiUser 的「参数无效」错误码）。
