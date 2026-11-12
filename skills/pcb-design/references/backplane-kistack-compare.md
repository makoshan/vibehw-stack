# Backplane / KiStack 对照（2026-11-10，只读代码，未安装未实跑）

来源：`BackplaneWorks/Backplane` 980bfad（Rust，MIT）、`BackplaneWorks/kistack` 641ade9（11 个 KiCad 技能，MIT 文本；GitHub 显示 NOASSERTION）。移植逻辑要保留出处。

## 它是什么

- Backplane：桌面 App + 常驻 agent，背后是它自己的 KiCad 分支（`10.99.0-bp.4`，macOS arm64 装 bp.6）。分支多出一条 `kicad-cli api-server`：不开界面，用 KiCad 官方接口（nng + protobuf，Python 用 `kicad-python==0.8.0`）改板。本机上游 KiCad 10.0.6 的 `kicad-cli` 没有这条子命令（2026-11-10 实查），所以上游只能对开着界面的 KiCad 用接口。
- KiStack：大多是几十行的提示词（原理图、PCB 审查、BOM、导出、Gerber、拼板、渲染、符号、封装）。只有 `layout-placement` 带脚本（10 个，约 1900 行）。它自己说是实验性的，只在几块 Nordic QFN 小板上测过，数字没重测。

## 值得借的（按对我们坑的命中排序）

1. **摆件审查 `placement_audit.py`**：以一颗 IC 为中心，逐脚量「脚 → 它的小件」距离。还检查：小件地焊盘是不是反朝芯片；地焊盘旁有没有合法过孔位；晶振、飞电容两边的距离差；脚前的扇出通道有没有被别网焊盘堵住；RF 链里的并联件在不在串联通路上。我们的 `layout_gate` 管走线、净空和地投影，**不管摆位**。这正是「滤波电容要贴芯片：DRC 0 查不出，要逐脚量」缺的量具。门限（它用「约两个器件长」）仍由 Mako 定。移植到 pcbnew 只读脚本即可，不用接口。**已移植（2026-11-10，提交 eaf3e56）**：`skills/pcb-design/scripts/placement/placement_audit.py`，五项照原版、pcbnew 只读、不依赖 shapely，自检 `test_placement_audit.py`；RF 链（`--chain`/`--tie`）未移植，以后加时 `--rf-track` 必填无默认。2026-11-10 Mako 定收窄「地焊盘朝向芯片」：原版对任何小件、任何距离都判，D20N 13 颗芯片报 25 条，几乎全是远处的电阻、ESD 管、晶振；改成只判电容、且离脚间隙不超过自身长度（焊盘外包络长边，按封装坐标量）的才判，同一脚几颗紧挨着的电容每颗都判；按此重跑 D20N 只剩 C902 一条，D17 归零，旧主控 J400 的 U13 报 C72、C56（晶振负载电容）两条。
2. **改板的回读和回滚（`move.py` + `sidecar.py`）**：一次挪件算一次提交，提交后回读器件数、固定铜的网名和各件位置，不对就整次撤回，并以非零码退出。保存前核对板文件在读入后有没有被别人改过，改过就拒存。对应我们踩过的坑：孔被挪来的焊盘盖住改网、预置孔被旧铺铜改成 GND、多会话撞名覆盖。`copper_hash.py` 只能事后比两块板，缺「写前防覆盖」和「挪后固定铜网名不变」这两道断言。
3. **检查参数默认取板子最严（`rules.py`）**：线宽、间距、孔径、离边都取全板最严值。遇到自定义 `.kicad_dru` 就拒跑，除非显式给数。给的数比板子松也拒跑，除非加 `--rules-override`，并且每行输出都注明「比板子松」。移植第 1 条时照这个做，合「安全门不许自己放宽」。

## 不借或已有

| 项 | 判断 |
|---|---|
| 换它的 KiCad 分支，走接口改板 | 能绕开 SWIG 句柄失效、文本改线这些坑。但要装第三方分支和整套 App（安装方式是 `curl \| sh`），我们的流水线、Spark 镜像都在 10.0.6 上。先不换；要试就用上游界面版开接口，拿副本板测，未实测 |
| 原理图「以标签为主连线」、按网上色 | 和 schematic-style「块内用线连、标签只做块间接口」相反，保留我们的 |
| 封装用 KiCad 官方生成器（YAML） | 标准封装能避开 easyeda2kicad 的镜像问题。遇到新封装再考虑 |
| 导出：`--schematic-parity --refill-zones` | `basic_gate.py` 已有 |
| Gerber 渲染目检、BOM 字段、KiKit 拼板、Blender 渲染 | 已有同等或更细的技能（量丝印、jlc-order、product-render） |
| 它的 layout 技能说「尽量别用自动布线，自己布」 | 和 2026-11-10 Mako「人画的板优先参考」同方向，只当佐证 |

## 主仓其余可借（2026-11-10，Backplane 980bfad，只读未安装；两个子代理分读，我抽查 7 处出处属实）

「已实现」= 代码里有；「文档」= 只写在文档里。现状对照的是 mstack 当时版本。

| # | 做法 | 出处 | mstack 现状 | 落地 |
|---|---|---|---|---|
| 1 | 整板 STEP 导出时，缺 3D 模型的件 kicad-cli 只打一行「Could not add 3D model for R1.」、退出码仍 0；数出来报 | `native/host/src/kicad/solidread.rs:70`，已实现带测试 | 没有。`pcb-enclosure-fit` 用整板 STEP 查碰壳，缺模型的件永远「不碰」 | 抓 stdout 计数，缺模型位号记未验证；配删模型的反例；**已移植 d8de2ed、958a39c**：`pcb-enclosure-fit/scripts/step_export_check.py` |
| 2 | 远端作业进程身份用 `pid:starttime`；超时只报一次「仍在跑、结果未知」，不杀；续跑前先核上一步副作用 | `agents/work.rs:105-118`，已实现 | 部分。`offload.sh:124`、`spark-dispatch.sh:141` 只看 `kill -0`，重启后 PID 被复用会误判在跑（推断未实测） | pid 文件写 `/proc/<pid>/stat` 第 22 字段；`--detach` 加 `--deadline`；**已移植 a7bc3f2**（做了进程号 + 启动时间、`--kill` 守卫 pid>1；`--deadline` 没做）；`--deadline` 已补 **a367e97** |
| 3 | 下单前从上传的 Gerber 量层数、外形、最小孔、孔数、钢网焊盘数，对 BOM/坐标位号；和声明的选项不符就警告、按量到的计价 | `order/fabsrv/analysis.py:195-239`，已实现 | 部分。`jlc-order` 只核包内自洽，订单详情没对文件实测值 | `fab_facts.py <zip> [bom] [cpl]` 出事实表，§4a 订单详情逐项对表；**已移植 30e833f**：`jlc-order/scripts/fab_facts.py` |
| 4 | 复核结论绑设计哈希：板、原理图、STEP、fab 文件的指纹和结论、≤8 条问题存 `review.json`，下单前只答 none/stale/current，门由工具判（「Agents skipped listing the issues when told to」） | `docs/order.md:53-61`，测试 `agents/tests/order.rs:16-28` 已实现 | 部分。项目里有 `host/fab_pin.py`，mstack 没收 | `review_gate.py record/check`，none/stale 不许上传；fab_pin.py 收进 mstack；**已移植 fab5e6a**：`jlc-order/scripts/review_gate.py` |
| 5 | 从已验证的提交出包：工作区脏就拒，在 archive 上导出，先写暂存目录、成功再改名，已有的包不重写 | `docs/design-review.md:69-101`（文档）；`kicad/release.rs:78-87` 已实现 | 部分。`jlc-order` 的 `export_fab.sh` 从工作区直出 | 出包统一 `git archive` → `.staging` → `fab-<日期>-<sha8>`，目录已存在就拒；**已移植 fab5e6a**：`jlc-order/scripts/fab_stage.sh` |
| 6 | HANDOFF 一类共享状态按 id 拆成独立记录、只追加，纠错另写「更正」 | `docs/bots.md:365-452`；`agents/rules.rs:121` 已实现 | 部分。HANDOFF 仍是单文件，整文件覆盖、带走别人条目、撞名都出在这 | 每会话一个 `handoff/<时间>-<会话>.md`，顶部视图脚本拼；**要 Mako 定，影响所有 agent** |
| 7 | 收据绑冻结的提交和树，写「不证明什么」，修好工具后旧 FAIL 不改判，缺环境记未验 | `docs/reporting/validation.md:24-27`（文档） | 部分。有证据落盘，缺「提交一变就失效」和「不证明」栏 | SKILL.md 证据格式加三栏；引用别人「QA 通过」必须带提交号，与 HEAD 不同按未验证；**已移植 dfb259d**（SKILL.md 证据格式、agent-comms.md） |
| 8 | 门禁只认本人最近一条原话解锁，转发来的消息（以 `[` 开头）不算；收据末行写「门禁未改」 | `agents/rules.rs:163` 已实现带测试 | 部分。「门不许自己放宽」只在原力手环 memory | SKILL.md 加门限只在 Mako 当轮原话下改；项目门限集中一文件，CI 查改动带 `Gate-Change:` 尾注 |
| 9 | 独占资源用命名锁，底层 OS flock，进程死了自动释放，排队先来后到 | `docs/locks.md`；`agents/lockrec.rs`、`locks.rs` 已实现 | 部分。设备占用靠 HANDOFF 写字声明 | `scripts/mlock.sh <名> -- <命令>`（Perl flock），串口、Altium、Spark GPU 走它；**已移植 0781530、9b86819**：`scripts/mlock.sh` |
| 10 | BOM 规则（重号、缺封装/值、同 MPN 不同封装）和版本间 BOM 差异 | `kicad/bom.rs:136-234` 已实现 | 部分。jlc-order 写了「脚本逐行核 C 号」但没脚本 | `bom_rules.py`、`bom_diff.py 旧 新`，差异表进新包 README；**已移植 946da5e**：`jlc-basic-parts/scripts/bom_rules.py`、`bom_diff.py` |
| 11 | 收尾递归停掉名下一切：唤醒订阅、作业、锁、委派任务、子线程 | `agents/settle.rs:12-22` 已实现 | 部分。收尾三步没清点名下远端作业 | 收工条目加「名下在跑」一栏，逐个停或写明交给谁；**已移植 f8c8378**（agent-comms.md「名下在跑」） |
| 12 | 每次工具调用后快照 KiCad 文件到旁路 git，可按步回看 | `agents/hist.rs`、`kicad/histsteps.rs` 已实现 | 部分。靠手动提交，D17 补丝印没提交就丢过 | 改板脚本入口先提交到 gitignore 的 `.history.git`；**已移植 c1af7e4**：`pcb-autoroute/scripts/snap.sh`（已接进 `tidy/` 三个整理脚本 da152f6；项目自己的 finish 脚本要各自加） |
| 13 | 验收技能只记录不修；开跑前按 `git log` 把新改动补进清单；结果固定 JSON | `skills/backplane-e2e/SKILL.md:22-28,142-160`（技能文字） | 部分。实现、验收分开已有 | bootstrap 的项目验证技能加这两句；**已移植 1227b75**（bootstrap-a-project.md） |
| 14 | 文字规则变成扫源码就红的测试（脚本里出现 `pkill`/`killall` 就失败） | `AGENTS.md:47`（文档，测试源码未检出） | 部分。铁律只有文字 | SKILL.md 自检加 grep，项目 CI 照抄；**已移植 fc81ace**：`scripts/check-no-pkill.sh`（已接进 daily-reflect finish，0.45.0） |

不借：unslop（与 pstack 版只差一对反引号，mstack 已接）；线程自己快进合并 main 不经评审（和 PR → CI → 独立复核相反）；多账号/多模型自动切换（产品功能，Spark 与 Mac 同一额度池）；Stripe 下单服务（对不上嘉立创网页下单）；机械 CAD 文档（ai-cad-pipeline 更全）；DRC 报告结构校验（basic_gate.py 已更严）；SendCutSend 技能（本机同名的那份来自 text-to-cad，不是同一个；Mako 不用 SendCutSend，只借「记被检文件哈希」并进第 4 条）。


移植时的事故（2026-11-10）：做第 2 条的 Spark 作业，自检把 pid「1」喂给老版 `offload.sh --kill`，执行了 `kill -- -1`，Spark 上 mako 名下所有进程被杀，用户服务靠 systemd linger 自动拉起。之后测杀进程的路径只许用假 kill；Spark 上 unshare 不可用。另外，Mac 自带的 bash 3.2 在 UTF-8 locale 下会把 `$x（` 里的全角字节读成变量名，`fab_stage.sh` 和 `daily-reflect.sh` 改成了 `${x}` 写法。
