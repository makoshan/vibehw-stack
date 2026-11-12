---
name: pcb-autoroute
metadata:
  version: "0.10.3"
description: >-
  KiCad 板子"不手工布线"的可复现流水线：布局脚本 → Specctra DSN → Freerouting 家族自动布线器 → SES 回导 →
  清残线/加宽电源/铺地 → 原生 DRC → 嘉立创工艺规则复核，每一步留脚本和 JSON 证据。来源：MAKO 手表载板
  v0.4→v0.11 的十轮实打（2026-09），加上拆 iterxAI.app（0.1.99）成板流水线得到的做法。
  Use when: 用户要自动布线、重新布线、"别手工拉线"、把 KiCad 板子跑到 DRC 0、判断能不能交给嘉立创，
  或问 iterxAI/Freerouting 怎么用、SES 导入后断网怎么办，或自写网格寻路太慢要换 Rust / 问 GPU 布线能不能加速，或要把布线/改板(finish)/DRC 放到 Windows 上跑，或布完 DRC 0 但走线乱（细碎台阶、少数网绕远路）要不挪件变整齐，或要在嘉立创EDA/Altium/Allegro 里自动布线（含嘉立创内置布线与 FreeRouting 扩展怎么配合）、核对 Freerouting 2.x 的 SES 丢不丢线，或剩几根未连要判「到底有没有路」、想比 Zap!/TraceMaker/Freerouting 几家挑最好一版，或想在卡点周围小窗口里拆线重布 / 证明窗口内无解。原理图绘制与电路审查转 pcb-design，产品创意与方案匹配转 vibe-hardware-idea，
  不管外壳（pcb-enclosure-fit）。
---

# PCB 自动布线流水线

一句话：**布局是代码，布线是外部求解器，验收是原生 DRC + 工艺规则；人不拉线，只改脚本重跑。**

## 接收原理图与现有板
先按 [原理图到PCB交接](../pcb-design/references/schematic-pcb-review.md) 确认是新设计、保电路重排还是批准的电气变更。已有板默认复用本轮冻结候选，不因示例流水线写了重建就清掉旧布局/已连铜；可在独立候选中局部拆线、挪件重布，保全指最终恢复既有电气连接，不要求每段未锁定铜几何不变；用户锁定的链路不可擅动。整板重布仅用于获授权的独立候选，不覆盖冻结/最好版本。不凭网络名字相同认定物理连通。外部分析器只作补充，用户锁定的规则、原生 DRC 和旧连接保全继续执行。

## 流水线（每步一个脚本、一个产物）

| 步 | 做什么 | 产物 | 门 |
|---|---|---|---|
| 0 | `build_design.py` 生成 design.json + 原理图 + BOM，内含 assert（引脚唯一、极性、关键网） | design.json, .kicad_sch | `kicad-cli sch erc` 0 违规；`check_netlist.py` 导出网表 == design.json |
| 1 | `add_vNN.py`：从上一版**未布线**板出发，删/加封装，**所有焊盘按 design.json 重新赋网**，放置时 assert 无 courtyard 重叠、不出板框 | `carrier-vNN-store.kicad_pcb`, `carrier-vNN.dsn` | 脚本自身 assert |
| 2 | 自动布线 | `.ses` | 日志 "completed"；SES 非空 |
| 3 | `finish_vNN.py`：SES 回导 → 删一轮 dangling 残线/过孔（之后核关键网仍连通，别循环删，见 [C07 挪件与 Python 坑](references/kicad10-placement-pitfalls.md) 第 4 条）→ 电源网加宽到 0.4，撞规则的段退回 0.2 → 铺局部地 → DRC | `carrier-vNN-routed.kicad_pcb`, `.drc.json` | DRC 0 违规 0 未连接 |
| 4 | `verify_board.py`：逐焊盘核对网名、列未放置器件、原生 DRC | `board-check.json` | `ROUTING_ONLY_PASS` |
| 5 | 工艺复核（见下） | README/verification.json 里的未决项 | 人判断，不自动通过 |

**改板留快照**：改板脚本开始时和每步之后都调 `scripts/snap.sh <板目录> "<这步做了什么>"`，把 .kicad_pcb/sch/pro/dru 提交到板目录下的旁路仓库 `.history.git`（项目主仓库 git status 看不到，`git clean -x` 会删掉它；拷来的、不是本机建的 `.history.git` 会被挪开另建，不在上面跑 git）；`snap.sh --log <板目录>` 看历史，`--show <版本> <文件> <板目录>` 取回旧版。D17 补的丝印没提交就丢了，这一步就是防这个。借自 Backplane `agents/hist.rs`；自检 `bash scripts/test_snap.sh`。`scripts/tidy/` 的 run.sh、fixloop.sh、rr_loop.sh 已在开头和结尾自动调（快照失败只提示不挡）；项目自己的 finish 脚本要自己加。

**ROUTING_ONLY_PASS ≠ 可打样。** 天线禁布、机械堆叠、额定值、声路、光窗这些 DRC 看不见的东西，永远列在"未决项"里，谁也不能拿 DRC 0 冒充制造就绪。iterxAI 的措辞是 "result preserved for editing; manufacturing issues remain in Problems"——同一态度。

**基础错误不能只靠口头提醒。** AI生成、自动布线或SES回导后，执行[基础错误门禁](references/basic-errors.md)：`python3 scripts/basic_gate.py <PCB> --out <新的证据目录>`。它运行本次原生DRC，包含警告/排除项，关键规则被关闭也拦；缺工具/报告记未验证。每个新增检查先用故意错误的独立副本验证，再跑正常板。布局质量、电气额定值、RF和实物必须另审；不能拿角度或过孔数量直接代替这些判断。

有带来源的网络/布局声明时，再执行[专项布局门禁](references/layout-gate.md)：使用KiCad Python运行`layout_gate.py <PCB> <规则JSON> --out <新的证据目录>`。按实际铜检查敏感网避让、线长/焊盘路径、参考层/地覆盖、侵孔与铜净空；缺RF安装位或去耦回流证据列未验证。只读源板，正常和故意错误板分别实跑。

## 密板尾部搜索：先限时，再扩大

协商拆线、清残线或一批网持续无改善时，执行[有时限的增量布线](references/bounded-routing.md)：固定代码版本和独立状态，先小样测 CPU/墙钟；每个候选经全网旧连接保全与原生门后才晋级。单网及整批设总预算，无收益就换策略；不无限等待、不让失败板成为后续输入。具体起始预算及回归样本见该文，预算不是实测性能承诺。

**锁定已有铜后 Freerouting 不收敛、自写网格 A* 又太慢**：见[自写网格寻路换 Rust 核心](references/grid-route-rust.md)（Rust 核心 `scripts/grid_astar/`，含等价门与 GPU/其它布线器选型）。顺序寻路先布堵后布、拆线重布不收敛时，改协商拥堵（`astar_pf`，同文第 6 节）。

**只剩几根未连、工艺定死全通孔且不许盘中孔**：见[收尾残余未连](references/residual-nets-through-hole.md)（`scripts/residual/`）。任何补线器报连通后先跑 `via_in_pad.py`（KiCad DRC 不报同网盘中孔，补线器常借它「连通」，有压盘退出 1）；GND 扇出孔围死相邻信号脚就改连 EP 拆孔；开窗只剪窗内未锁定线再接回；A* 结果只当走廊，交付线按 0/45/90° 手工重画；盲埋孔结果先对嘉立创 HDI 工艺。

**DRC 能过但走线乱到没法用、根因是摆位**（派生板件挤一边、长网绕芯片、敏感链大量打孔）：见[按信号流重摆重布](references/signal-flow-relayout.md)。先撤线量飞线判根因；按信号流手排、两面镜像；芯片转向后整段换脚（通道映射谁吸收问用户）；骨架脚本直连锁定 + 逐网 A*（敏感链限本面 0 孔）+ 电源先窄后逐段加宽 + 地缝合孔 ≥ 70%；直跑与 `--refill-zones` 两口径 DRC 都 0/0。C08B 采集板 5965 段 / 470 孔 → 763 / 222，电极链最长 142 → 9 mm。

## 布完以后变整齐（不挪件、不退步）

先量清是"碎"（短于 0.1 mm 段占比）还是"绕"（每网线长 ÷ 焊盘八方向 MST），再分别拉直（`scripts/tidy/run.sh`）或逐网拆布（`scripts/tidy/rr_loop.sh`，每网写回即 DRC + 无新锐角 + 模拟耦合关）。必带约束：不碰同网别的线、不新增锐角、不重合、焊盘只当叶子、过孔不压焊盘、可选件焊盘不中转。验收要查每一层（含内层）铺铜孤岛面积（同网同层先并集）；拉直切出新孤岛用 `fixloop.sh` 整层退回。C08A 13633→6411 段、过孔 642→618，三轮独立复核。细节与坑见 [布完以后变整齐](references/post-route-tidy.md)。

## 布线器：用哪个、怎么调

下表都是 Freerouting 血统，CLI 一样：`java -jar X.jar -de in.dsn -do out.ses -mp <passes> [-mt <threads>] [-da -dct 1 -l en]`

| 器 | 结论 |
|---|---|
| Freerouting **1.9.0** | 布线质量一般，但 **SES 写出完整**。默认用它，`-mp 500`。 |
| Freerouting **2.1.0** | **SES 导出静默丢线**：自报 276 线/48 过孔、0 未完成，文件里只有 225/36，导入后 8~11 处断网。别用它出 SES。2026-10-25 同一份 `carrier-v09.dsn` 复现：最后一轮自报 0 未布通，导入后 14 处断网。 |
| Freerouting **2.2.3** | **这块板上没丢线**（2026-10-25，`carrier-v09.dsn`，`-mp 500 -mt 1 --gui.enabled=false`，断网 + 关上报）：13.6 min、自报 0 未布通，SES 208 线/51 过孔，导入后 **0 断网**；1 个 `copper_edge_clearance`（贴边老问题，见下）。只测了一块板一次，换板仍要过 `check_ses.py`。要 Java 25+（`/opt/homebrew/opt/openjdk@26/bin/java`），无界面跑要加 `--gui.enabled=false`。嘉立创 eext-freerouting 扩展要求的就是这一版以上。 |
| iterxAI 内置 `iterx-java-autorouter.jar`（包名 `app.iterxairouting`，Freerouting **2.x** 核心改名版，带私有 JRE 17） | 路径 `/Applications/iterxAI.app/Contents/Resources/java-autorouter/`；`jre/bin/java -jar iterx-java-autorouter.jar -de -do -mp -mt`，可脱离 App 单独跑。实测（同一份 53 封装/149 网 DSN，`-mp 500 -mt 4`）：12 min，**12 网未通、18 违规**，日志按网列出未通连接（比 2.1.0 诚实），但没有比 1.9.0 强。App 里那些 repair pass 是在它之外做的，jar 本身不含。 |

时间：27.5×37.5 mm 两层板、42 封装 → 1.9.0 约 4 分钟；53 封装 → 10 分钟以上。跑后台，别在前台等。

**SES 完整性门（任何 2.x 布线器都要过）**：别信布线器自报的"0 unrouted"。用 KiCad 自带 python 跑 `scripts/check_ses.py <未布线源板.kicad_pcb> <a.ses> [b.ses ...]`，它数 SES 里的 wire/via，导入源板副本后跑 `kicad-cli pcb drc` 数 unconnected。同时放一份 1.9.0 的 SES 当阴性对照（应为 0），未布线源板直接跑 DRC 当基线（v09 是 108）。

**其他 EDA**（嘉立创内置 JRouter/HRouter、eext-freerouting 扩展、Altium 内置 Situs / MCP `route_plan`、PcbAutoRouter）：见 [其他 EDA 入口](references/other-eda-entrances.md)。**嘉立创 V4.1.60+ 先用官方命令行 `LCEDA-Pro`**（`scripts/lceda_cli.sh`，补网用 `scripts/lceda_fill.js` 逐级放宽铜间距，见入口文档「官方命令行」节）。**在嘉立创里优先用内置布线**：整板一次布 → 有断网先用内置补一轮 → 补不通再评估获授权的独立全重布候选（保留冻结/最好版本，不清原板）；FreeRouting 扩展直跑全板会爆 200 条间距错误（2026-10-25 拆 `Autorouter_v09imp_2026-10-25.dsn` 坐实根因：嘉立创导出 DSN 把 `via0` 外径写为 `12 mil`、39 个网络 `class` 间距写死 `4 mil` 覆盖全局 `6.05 mil`；若离线跑须先用 `scripts/fix_and_route_easyeda_dsn.py` 修补 40 行再喂给 Freerouting 2.2.3，实测 122 网收敛至 `0 unrouted`），锁线补网实测也失败（v09，2026-10-25）。`autoRouting()` 自报的成功网数不可信，一律以 DRC 为准。Altium 在 `thursday-win` 上用 `Mako_AutorouteAll`（`PCB:Autoroute`）或 `altium-mcp` 的 `route_plan` + `pcb_place_tracks`。

**DSN 板框修正**：pcbnew 导出的 DSN 边界有时带圆角/偏移，`add_vNN.py` 末尾用正则把 `(boundary (path pcb 0 ...))` 重写成板框四角（单位 µm，y 取负）。

## TraceMaker：KiCad 原生增量布线（v0.12.1）

见 [TraceMaker：KiCad 原生增量布线（v0.12.1）](references/tracemaker.md)。

## 卡在少数几根未连：先预检，再多布线器挑优（2026-11-03）

剩几根布不通时，先问「只看已固定的铜，这根到底有没有路」，再决定换布线器、挪件还是改 GPIO。两个脚本都在 `scripts/`，只读源板、在副本上干活。

1. **出口预检** `escape.py 板.kicad_pcb --prove 目录 --json 出.json`（`uv run --no-project --with numpy --with scipy --with pillow python -I`）：逐个未连缺口判 **有路 / 临界 / 无路**。别网铜按间距 + 半线宽膨胀，别网铺铜不算障碍，禁布区、NPTH、孔距都算，孔环不碰贴片焊盘；内层铺铜盖过半块板的当平面层，只许孔穿。`--prove` 会把找到的路写进副本跑 DRC，缺口消失、别网没多未连、无新违规才算「已证实」。**无路 = 换哪个布线器都没用，只能挪件或改孔位**；临界的按实际间距求路，几乎都过不了 DRC。没建模：DRU 局部规则、板级铜图形。BGA 0.5 间距用 `--pitch 0.02`，6 层 46×40 板 3 个缺口约 35 s、峰值 1.8 GB。
2. **多布线器挑优** `route.py 板.kicad_pcb -o 目录 [--through-only] [--candidate 名=路径]`：TraceMaker 几组开关推到 thursday-win 并行跑，可把 Freerouting / 旧结果用 `--candidate` 一起排名；本机 kicad-cli 统一打分（未连 → 新增违规 → 过孔×10 mm + 线长），删了已有铜、新铺铜（默认）、违反 `--through-only` 的直接出局。报告附出口预检结果。
3. **窗口重布** `window.py 板.kicad_pcb --net 网 -o 目录 [--keep 正则] [--half 1.5]`（上面的 uv 命令再加 `--with ortools`）：escape 判无路 / 临界的缺口，拆掉卡点 ±half mm 里未锁定的最小线宽线和过孔（更宽的线、有铺铜或宽线的网的过孔、`--keep` 的射频/CC/晶振/去耦网都不拆），被拆的网和目标网在「两格 = 线宽 + 间距」的格点上一起重布：先协商式布线找解（窗口级，numpy，< 1 s），找不到就只拿还冲突的那几个网交给 CP-SAT 证无解（去掉别的网只会更宽松，这都无解 = 原问题无解）。接回老线的小段从老线精确线心出发，和别网格点/孔位/小段按真实距离互斥。远端不在窗口里就从窗口边出去（出口只当叶子），窗外用 escape 的最短路补。写板后清掉新线搭老线中段留下的悬空残桩/过孔，DRC 按「类型 + 各条目 uuid」对比原板：目标网未连变少、别网不变差、无新违规 = 已证实。**「窗口内无解」只对这个窗口和 detour 成立**；窗外补线可能绕远（实测 30–39 mm），交付前要人看、跑盘中孔门。多个缺口串着解时顺序有影响，先解窗外补线短的。
4. **Zap!（云端，zap.mutual.solutions）**：只有 `--allow-cloud` 才跑，**每块板上传前先问 Mako**。登录用 `zap.py login` 设备码，不粘 API key。它总会给电源网（VBAT/3V3/VBUS）加铺铜，`--ground-pour off` 只关地、关不掉电源铺铜，所以 route.py 默认判出局。
5. **口径坑**：TraceMaker 从焊盘出发，不算已有短线，会把有短线伸出的缺口误判死（thru3 的 H2）；以 escape.py + DRC 为准。

C08B 实测（6b547f2、c04311d）：thru3 IMU_SCL 已证实（39 mm、3 孔）、USB_DM 无路、MCU_DP 临界，和 Zap、TraceMaker、人工一致；s6e 10 缺口里 Zap 与 TraceMaker 各布通一根不同的网。窗口重布（11-03）：**IMU_SCL、MCU_DP 都已证实**，先 MCU_DP 后 IMU_SCL 串起来对原板 3 → 1 未连、0 错误、0 盘中孔（反过来 IMU_SCL 的窗外补线会占掉 MCU_DP 的通道）；整网保护电源/去耦/CC/晶振后 MCU_DP 自己的图就不连通；**USB_DM 窗口内无解**（只含 USB_DM + USB_DP 就被 CP-SAT 证无解：J400 单列 B6/A7/A6/B7 = DP/DM/DP/DM 交错、东头贴板边，只能从西头出，必须有一网换层，而西头孔位被底面 U17 占了 → 要挪 U17）。

## iterxAI 学来的三件事

见 [iterxAI 学来的三件事](references/iterxai-lessons.md)。

## 嘉立创两层板工艺复核（步 5）

见 [嘉立创两层板工艺复核（步 5）](references/jlc-2layer-process-review.md)。

## 嘉立创 EDA 对照检查（可选，步 5 的补充）

见 [嘉立创 EDA 对照检查（可选，步 5 的补充）](references/easyeda-crosscheck.md)。

## 在 Windows 上跑布线和 DRC（省 Mac 内存，2026-09-24 实测）

见 [在 Windows 上跑布线和 DRC（省 Mac 内存，2026-09-24 实测）](references/windows-route-drc.md)。

## 已知坑

- pcbnew 的 `SaveBoard` 会让进程里所有对象句柄失效 → `finish_vNN.py` 每个改板阶段用子进程跑，跨阶段用 uuid 找对象。
- KiCad 自带 Python 在 `/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3`，系统 python 没有 `pcbnew`。
- 丝印需满足所选工厂文字规则且不压焊盘；密集区可移动到背面或装配图，但电源/电极/外部接口的针序标识不能只靠 README，也不能为过 DRC 直接删光。
- 同一板上 THT 焊盘两面都占地，背面放器件时要躲正面 THT（XIAO 的两排孔）。
- 布线只证明连通，走线长度自己量（`GetLength()` 按网汇总）；SPI 30 mm 级在 8 MHz 下可接受，更快要重摆。
- pcbnew 里 `board.Remove(footprint)` / `footprint.Remove(graphic)` 之后，同进程再 `FootprintLoad` 或遍历会拿到悬空的 swig 对象（`'SwigPyObject' has no attribute ...`）。删封装在**文本层**做（解析 `(footprint ...)` 块按 Reference 丢弃，和删 segment/via/zone 同一个解析器）；不想要库丝印就 `SetLayer(F_Fab)`，别 Remove。
- **规则区（rule area）在 DSN 里一律变成全禁布**：KiCad 里只勾"禁止铺铜"的区域，Freerouting 照样不给走线、不给过孔——载板 v0.19 一块"天线区不铺铜"的正面规则区让 20 个网全部不通。要禁铺铜就别画规则区，直接把铺铜多边形画小；规则区只用在真要禁线/禁孔的地方。
- 小板上 Freerouting 会把线贴着板边走，KiCad 再报 `copper_edge_clearance`。把 DSN 的 `(boundary (path pcb 0 ...))` 向内缩 0.6 mm 就没了——路由器只认边界，不认板边规则。
- 板子没有穿层的 GND（没有 THT、没有 GND 过孔）时别铺背面地：全是孤岛，`isolated_copper` 一堆。只铺有 GND 焊盘的那一面。
- 1.9.0 的耗时随密度非线性：42 封装 4 分钟，53 封装 40 分钟还在优化；密板先减件再布，或者把 `-mp` 调小。
- Freerouting 1.9.0 遇到**翻到背面的 roundrect 焊盘**会在优化阶段反复抛 `FloatPoint.rotate ... p_point is null`，不写 SES 也不退出（载板 v0.19 的 XIAO 底面镜像盘）。把那几个焊盘改成 `rect` 就好；同一板上 U1 的 roundrect 半孔盘没触发，所以是特定组合，别指望它自己恢复，看到 NullPointerException 就 `pkill`。
- 停止布线器前按命令行和 cwd 核对任务归属，优先用本任务进程句柄或明确 PID；禁止宽泛 pkill。
- **从现成板派生、换件后重布**（局部补不完就全流程、全撤保留扇出、拆线变差就撤回、工程文件随板加载、参考地核实际填铜和接地）：见 [派生板重布](references/derived-board-reroute.md)。
- **无路径不先归因于实物布局。** 情形：寻路目标从未用端点中选择，试算时临时改网、改角色或改通行权。规则：先核端点在障碍模型中的真实归属及邻接格；用已知可达样本做阳性对照。临时归入目标网须在构建障碍之前完成，选完恢复所有未选端点并查无额外接触；最终保存件仍须逐端点身份/网名、旧连接保全与原生间距检查。未完成这些核验只报“本求解器未找到路径”，不报“必须挪件/加层”。证据：原力手环 PR #133，BENCH回读候选曾因NC端点被当别网膨胀而报告不可达；修正建网格顺序后，同摆位63秒接通W24，原生检查仍73未连、图板差异0、旧连接损失0（提交37281b71）。置信：低（单次已复现；不推断其他无路径都属此原因）。
- **自定义规则文件可能整份静默失效，反例要按规则自己的数字判。** 情形：给 DRC/ERC/lint 加了自定义规则文件（KiCad `.kicad_dru` 等），检查报 0。规则：规则文件里不写解析器不认的注释和条件（KiCad DRU 不写 `;;`）；每份规则配一个反例，判据是违规描述里出现**规则自己的数字**（如 `0.3000`），只被板级下限报出不算；新规则写完先跑反例看红。证据：原力手环 10-23 表带软板，DRU 里一行 `;;`（另一版是不认的条件 `A.Parent.Value`）让 kicad-cli 整份规则失效、不报错，DRC 0 照样出，焊线盘离边 0.188 被放过；逐行二分查出（提交 448ff7c3）。置信：低（同一会话二分出来，提交只把失效归到 `;;`，条件写错那种未单独复现）。
- **全流程重建要先吞下手工 ECO 的结果，重建后和现板逐件比。** 情形：现板由换件/局部改板脚本改出，又要用全流程（摆件→布线→收尾）重建。规则：重建前把 ECO 的位置、库封装原样、扇出并进全流程；重建后对现板逐件比位置与封装（KiCad 的 `lib_footprint_mismatch` 是信号）。证据：C06 全流程把 J501 摆在旧位置，摆件脚本又给它补外框、改丝印层，DRC 报 lib_footprint_mismatch；并入位置、收尾时换回库封装后 0/0（提交 efe1ac3f）。置信：低（单次）。
- **KiCad 10 Python 删走线后对象失效、`is` 比较 swig 代理、DRC 拷贝读不到项目规则、循环删悬空线头会剥掉真线、找位脚本的四种冲突、去耦电容不自动挪、Freerouting 2.5.0 在 6 层密板不如 1.9、换工艺前单芯片局部扇出试验（BGA 先地孔、过孔按孔到铜放大）、DSN 非 ASCII 让 1.9 静默卡死、DSN 写 autoroute_settings 让 1.9 一根不布**：见 [C07 挪件与 Python 坑](references/kicad10-placement-pitfalls.md)（2026-10-25）。
- **4 层内层 GND 平面进 1.9.0（power 层导出丢失卡死、DSN 间距盖孔到铜、先打 GND 扇出孔、SES 只在最后写、placement 段让 KiCad 10 导入静默失败、导入后重铺）**：见 [内层地平面进 DSN](references/inner-gnd-plane-dsn.md)（2026-10-26）。
- **串接局部补线与回导的四个坑（原力手环 C08B thru3 / BA-n54，2026-11-03）**：
  - `window.py` 结论是「DRC 不过」时照样写出 `window.kicad_pcb`；串下一步前先判结论是「已证实」，否则停（反例：VBUS 第 1 步弄断 CC2，下一步在这块板上接着补，整条链作废）。
  - 挪件前剥铜，只挂被剥短线的孔要一起删：留着的孔被挪来的焊盘盖住，KiCad 读板时按焊盘把孔改网（GND 孔变 USB_DM），DRC 报 clearance。
  - SES 回导去重按容差比，不按 0.01 mm 取整当键：差 1 nm 的两颗孔落在取整分界两侧漏成叠孔；KiCad DRC 不报同网叠孔，Freerouting 1.9 读到 `Multiple vias skipped` 后停在加载阶段 4 h 不开布。
  - 板文件里存的铺铜可能是旧的：DRC 只用 `--refill-zones` 跑会看着 0，直接出 Gerber 却有过孔泡在地铜里（thru3 窗口版起就带着：不重铺 32 错）。交付前 `--refill-zones --save-board` 存回，再不重铺跑一遍。

## 证据

- MAKO 载板 v0.9→v0.15：`~/go/202608/智能手表/2.0/pcb/carrier/`（README 每版一节，`verification.json`，`board-check.json`）；后盖传感器板：`~/go/202608/智能手表/2.0/pcb/back-sensor/`（从零建板的最小脚本组：build_design / build_board / finish / verify_board）。
- iterxAI 0.1.99 拆包：`/Applications/iterxAI.app/Contents/Resources/{java-autorouter,codex-guidance/skills/iterxai-board-delivery}`；字符串证据来自 `app.asar`（`bundled-java-autorouter`、repair pass 名、JLC 诊断文案）。
- iterx jar 实测（2026-09-16）：`~/go/202608/智能手表/2.0/pcb/carrier/logs/iterx-autorouter-v11.log`——"started with 149 unrouted nets, completed in 12 minutes 18.29 seconds, final score: 919.28 (12 unrouted and 18 violations)"；SES 291 wire / 73 via。

## 接口路径门（2026-09-16，载板 v0.19B 放反 FPC 座的根因）

见 [接口路径门（2026-09-16，载板 v0.19B 放反 FPC 座的根因）](references/interface-path-gate.md)。

## 模拟小板与 KiCad 10 导入门（2026-09-20 EMG 复核）

见 [模拟小板与 KiCad 10 导入门（2026-09-20 EMG 复核）](references/analog-small-board-kicad10-gate.md)。

## 对外接口脚序与下单门（2026-09-26）

涉及 FPC、板对板、背面模块、电池或跨板排线时，布线前及出制造包前读 [接口配对检查](references/interface-mating-gate.md) 并用 KiCad Python 跑 `scripts/interface_gate.py <board.kicad_pcb> --spec <interfaces.json>`（自检 `--selftest`）。朝向、接触面、编号、信号功能、背靠背单调性分别核对；DRC 0 与同源网表一致不能证明接对外部实物。未确认的电源/地/插接映射列为下单阻塞，不能用“到货后再试”替代。
