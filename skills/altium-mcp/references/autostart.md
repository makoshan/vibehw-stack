# 不用人点：冷启动 Altium 并自动跑脚本（2026-11-01 thursday-win 实测）

目的：从 Mac 经 ssh 把 Altium 拉起来，并让它自动执行一个脚本（启动 MCP 桥，或无人值守生成 iBOM），全程不用人在 Windows 上点菜单。

## A 一次性安装

1. 把 `scripts/autostart/` 下的文件拷到 Windows 端 `%USERPROFILE%\codex-tools\altium-autostart\`，再在那个目录里跑 `powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1`。它会生成两个快捷方式（`Altium-Bridge.lnk`、`Altium-iBOM.lnk`），并注册计划任务 `AltiumStartBridge`（安装目录带空格的情况没测过）（仅交互式登录时运行，没有定时触发，只能手动 `schtasks /run`）。建计划任务算持久化配置，要先问 Mako。
2. 要用 iBOM 的话，另外把 `lianlian33/InteractiveHtmlBomForAD`（v0.3.17，2021 年后无更新）clone 到 `Documents\AltiumScripts\InteractiveHtmlBomForAD`，跑一次它自带的 `Initialize.bat`，生成 `dist\InteractiveHtmlBomForAD.js`（约 2.5 MB）。

## B 用法

| 要做的 | 命令 | 实测 |
|---|---|---|
| 启动桥 | `win_ps.sh <<< 'schtasks /run /tn AltiumStartBridge'`，然后 `app_ping` | 约 10 s ping 通 |
| 生成 iBOM | `scripts/autostart/ibom.sh 'C:\…\board.PcbDoc' <本机目录>` | v19B：16–20 s 冷启动 + 生成 + 拉回，html 148 KB |

两条都要求 **Altium 没在运行**。`ibom.sh` 检测到 X2 在跑会直接拒绝。`ibom-job.txt` 只要还在，任务就会去跑 iBOM 而不是起桥。所以 `auto.js` 在 finally 里删它，`ibom.sh` 不论成败也会删。如果起桥时 Altium 打开的是 iBOM，先看这个文件是不是残留了。板子路径可以带中文：任务文件按系统 ANSI 编码写，`auto.js` 用 FSO 按 ANSI 读。生成完 Altium 会留在桌面上，但不带桥；要用桥就先关掉它，再跑一次任务。

## C 坑（情形 → 规则 → 证据 → 置信）

1. **Altium 已开着，又跑 `X2.EXE -R…`** → 不要这样做。它不会把命令转给已开的窗口，而是另起一个只占 24–26 MB、没有窗口、一直卡着的 X2，`schtasks /end` 也杀不掉它，只能按 pid 结束。证据：pid 44492，11:12 起后一直不退出。置信：低（只观测到一个实例，只在 AD26 26.3 上试过）。
2. **计划任务直接执行 `X2.EXE`** → 会卡在启动前：不写 `DXP_Startup.log`，内存停在 20–95 MB，优先级 BelowNormal，窗口也不出来。这和带不带 `-R` 无关：不带参数的对照组同样卡了 8 分钟。改法：让任务执行 `explorer.exe xxx.lnk`，参数写在快捷方式里，Altium 就像双击打开一样，挂在桌面 explorer 下面，Normal 优先级，35 s 就到 980 MB。置信：中（有前后对照，也有不带参数的对照组，但只在同一台机、同一个任务上做过）。
3. **ssh 起的进程在 session 0**，看不到桌面 session 1 里的窗口：`MainWindowHandle=0`，`CloseMainWindow()` 返回 False，eda-agent 的 `app_list_open_dialogs` 回「没有弹窗」，这些读数都**不可信**。要让 Altium 正常退出，用不带 `/f` 的 `taskkill /pid`：它会报「只能强制结束」，但关闭消息其实已经送到了，Altium 十几秒后会自己退出（`DXP_Shutdown.log` 正常写完）。证据：pid 15244，`CloseMainWindow()` 返回 False，`taskkill` 不加 `/f` 报错，之后进程自行退出，`DXP_Shutdown.log` 记到 `Application Finalized`。置信：低（只观测过一次）。
4. **Altium 的 JScript 不执行脚本文件顶层的语句**：`var JOB_DIR = "…"` 运行时报「JOB_DIR 未定义」，函数则正常可用。常量要放进函数里返回。证据：Mako 在屏幕上看到报错，改成函数后立刻通过。置信：低（只观测过一次，没有单独写最小用例对照）。
5. **桥工程要指向安装版** `%USERPROFILE%\EDA Agent\scripts\Altium_API.PrjScr`。指向 `codex-tools\eda-agent\scripts\altium\` 下的源码时，Altium 起来了，但桥一直不响应。证据：pid 18120 用源码路径，`app_ping` 失败、没有 `_session_start`；换成安装版后 10 s 就 ping 通。置信：低（没去查源码版为什么不行）。
6. **`auto_shutdown_ms` 改了也不会生效**：eda-agent 的 Python 端每次连上都会按 `config.py` 的默认值 600000 把 `mcp_config.json` 写回去。`activity.log` 里 10-29、10-30 两次会话都是启动 10 分钟后结束。要长时间保活，只能靠有 MCP 客户端连着（每 30 s ping 一次），或者用本节的任务随时重新拉起。置信：高。
7. **eda-agent 写死禁止 `ScriptingSystem:*`**（见 `safety.py`）：不能通过桥去跑别的脚本。这是作者有意的安全设计，别去改它。要跑别的脚本，就走本节的冷启动 `-R`。证据：`safety.py` 里的 `FORBIDDEN_PROCESS_PREFIXES` 和它的注释。置信：高（直接读的源码）。
8. **截桌面**：从计划任务里跑 PowerShell 的 `System.Windows.Forms` / `System.Drawing` 截屏，4 次都报 TypeNotFound，原因没查清（置信：低）。要看桌面上的弹窗，最快是问 Mako。

## D iBOM 已知问题（未处理）

- 用从 KiCad 导入的 v19B 测试，Value 列全显示成 `Comment`，板框为空（`pcbdata.edges=0`）。原因可能在导入数据，也可能在 iBOM 的默认配置（`PcbOutlineMech1=false`），还没核对。
- iBOM 的 `main()` 最后会用 `explorer.exe` 打开输出目录，桌面上会多弹出一个文件夹窗口。
