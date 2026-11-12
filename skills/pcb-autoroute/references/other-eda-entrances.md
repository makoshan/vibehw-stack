# 其他 EDA 的自动布线入口（2026-10-25 整理）

本 skill 的主线是 KiCad + Freerouting。别的 EDA 要自动布线时，先看下表选入口，再回到主线的门：**导入后原生 DRC 数断网，不信布线器自报**（`scripts/check_ses.py` 的做法）。

| 入口 | 引擎 | 本机/云 | 实测状态 |
|---|---|---|---|
| 嘉立创EDA专业版内置自动布线 | JRouter（默认，质量好）/ HRouter（快） | 官方页没写 | **v09 实测布通、0 断网、无新增间距错误**（见下）；只测一块板一次 |
| 嘉立创官方命令行 `LCEDA-Pro`（V4.1.60+） | 同内置布线，脚本经 `invoke` 调 | 本机客户端，需半离线/全离线 | **C08A 实测（2026-11-01）**：读数/DRC 与源板一致；补 3 网到 DRC 0（复现 2 次）；15 网一轮补净 11（见下「官方命令行」） |
| 嘉立创 eext-freerouting 扩展 | Freerouting ≥ 2.2.3 | 本机 API 服务，端口 37864 | **v09 实测：全板 0 断网但新增 200 条间距错误（根因已查清，见下）；锁线补网失败**，默认不用扩展直跑，离线跑须先用 `scripts/fix_and_route_easyeda_dsn.py` 修 DSN |
| Altium Designer 内置 Situs / MCP | Situs (`PCB:Autoroute`) / `eda-agent` `route_plan` | `thursday-win` 本机 | **v19D-compat8 实测接通**：界面调 `Mako_AutorouteAll` 或 SSH 调 `altium_mcp_route.py`（`route_plan` + `pcb_place_tracks`） |
| PcbAutoRouter（Altium） | 未写明，用 Vulkan 显卡加速 | 页面只写本地文件交换，没写是否联网 | 未实测，作者和价格不明 |
| PcbAutoRouter（Allegro） | 同上 | 同上 | 未实测 |

## 嘉立创官方命令行（2026-11-01 实测，优先于 easyeda-api 桥）

客户端 V4.1.60 起自带 `LCEDA-Pro`（Mac：`/Applications/嘉立创EDA(专业版).app/Contents/MacOS/LCEDA-Pro`，不在 PATH）：`open --path 副本 --headless true` → `invoke --session S --ext-uuid eda --code "<异步函数体>"`（`--args` 传 JSON，`--timeout` 最大 1800000）→ `session close --destroy`。不装扩展、不起桥、不碰用户窗口；`doc api --class-name X --method-name Y` 查签名和示例。`--mcp stdio` 只有 help/discover/call 三个通用工具，直接调命令行等价。工具：`scripts/lceda_cli.sh`。

| 能力 | C08A 实测（Mac 4.1.93，副本，不存盘） |
|---|---|
| 读数 / DRC | 259 件、898 焊盘（有网 892，与 KiCad 源板同口径）、13633 线、642 孔；DRC 8 s；注入异网短线能报出 |
| `pcb_Document.save()` | 落盘可靠（加 1 线 → 重开 13634） |
| 改板框 `pcb_PrimitivePolyline.modify` | 真改坐标后 `polyType` 仍从 BOARD_OUTLINE 变 NORMAL，继续改源码 |
| `importChanges(undefined, true)` | 不弹窗、2–3 s，但**全量同步**：删 PCB 独有件（H901–H904 安装孔），补回件不回原位，疑似导入前先存盘 |
| `importAutoRouteSesFile(new File([text],"x.ses"))` | 导入可用（网名正确、不动其它线，之后要重铺铜）；但 `getDsnFile` 把整板导成一个 u1 器件，Freerouting 不认已有线为已连（854 焊盘重扇出），**增量补线别走 DSN/SES** |
| `sys_FileSystem.*` | 半离线下全部报「非全离线客户端环境无法调用」，文件内容经 `--args` / 返回值传 |

**内置布线接口**：`startAutoRouting({RoutingNets:[网名…], existingPrimitiveMode:"keep"|"remove", optimization:0 偏速度|1 偏布通率, cornerStyle:0|1, layers, ignoreNets})` 返回 jobId（V4.1.79+），配 `getAutoJobStatus` / `getAutoJobLog` / `cancelAutoJob`；同步 `autoRouting()` 内部固定 15 min 上限、超时不留中间结果。没有草图半自动布线的 API。

**补网到 DRC 0 的做法**（`scripts/lceda_fill.js`）：内置布线**不读孔到铜那一行**（Safe Spacing 表 Hole 行改 0.25 布出几何不变，C08A 剩 3 项 7–7.7 mil < 7.9），所以布线前用 `pcb_Drc.overwriteCurrentRuleConfiguration` 把表前 8 行 0.1 mm 的铜间距临时放大，逐级 0.15 → 0.12 → 0.10，每级只补仍断开/有间距错的网，加 `ignoreNets:["GND"]`（不加会顺手补 2 段 GND），布完写回原配置并逐字核对，重铺铜，DRC 为准。
- 3 网（V4_IN7P、LED_A、CHG_PROG）：0.15 补 2 网、0.12 补 CHG_PROG → DRC 0，其它网逐段不变，复现 2 次，每步 30 s–2.5 min。
- 15 网一轮 18 min：11 网补净；相邻电极对 V4_IN6P/N、USB-C 区 CC2、nRF aQFN 内圈 PWR_HOLD 补不上——密集口仍要回 KiCad 流程或先放空间。
- 复测（11-01 夜，本机重载）：同参数仍 11 网，但失败的换了一组（V4_IN7P、V4_IN3P、CC2、PWR_HOLD）——引擎每轮随机种子，补不上的网不固定；只有 CC2、PWR_HOLD 两次都失败。单次 `invoke` 超 30 min 被放弃（编辑器内照跑、结果丢）→ **每级单独 invoke**。
- **第二阶段（剩 4 网逐条单补 + 限时偏布通率，11-02 凌晨实测）与 `startAutoRouting` 底层坑**：
  1. **`startAutoRouting` 静默忽略 `RoutingNets` 数组**：源码 `pcb.js` 中 `document-startAutoRouting` 调 `Qh.autoRouting(i, !1, !1, r)` 时第 3 个布尔参数传了 `false`，导致 `createPangolinJson(e, !0, i, o, t)` 的第 5 参 `i=false`，`if (i && Array.isArray(e.routingRange))` 恒不成立，回退为布全板所有未连网（仅排除 `ignoreNets`）。**真·单网或指定子集补线必须把非目标网塞进 `ignoreNets`**（`ignoreNets: [...allNets].filter(x => !targetNets.includes(x))`）。实测加了反选 `ignoreNets` 后，单网耗时从 274–335 s（实际 4 网同跑）降到 **34–124 s**，且其余 3 条断网保持 `0/0` 不被顺手改动。
  2. **逐条单补结果（0.12 → 0.10 mm）**：`V4_IN3P`（39 s）、`V4_IN7P`（37–40 s）在 0.12/0.10 下均能连上且铜间距 0 错，但因内置布线不读孔距行（`0.20 mm`），打下的过孔必撞邻网孔/线（`Hole to Via` / `Hole to Track` 2–16 项）；`CC2` 在 0.12 下未通、0.10 下（34 s）连上但同样撞 3 项 `Hole to Track`；`PWR_HOLD` 在 0.12（124 s）/ 0.10（110 s）下均未通（卡在 `U13_AD16` aQFN 内圈）。其它网逐段 0 变化，规则逐字还原。
  3. **偏布通率引擎（`optimization:1`，0.10 mm，限时 600 s）**：618 s 超时取消，4 网仍全断（0 进展）。
  4. **只抬过孔行（11-02 早，负载 25–65）**：副本只拆这 4 网，每次一网一级单独 invoke（ignoreNets 反选），走线铜间距 0.10、Safe Spacing 过孔那一行/列单独抬到 `viaCu`（`lceda_fill.js` 可选参数）。V4_IN3P 在 viaCu 0.10 下直接通且 0 错（47 s，上次撞孔是随机性）；**V4_IN7P viaCu 0.10 撞 1 处孔到线 → 0.13 连通 0 错**（50 s）；CC2 viaCu 0.13 一段都布不出、0.10 连上但撞同样 3 处孔到线（MCU_START 线、3V3 孔，与上次逐项相同，是空间不够不是随机）；PWR_HOLD 0.13/0.10 都布不出。viaCu 0.15 时 V4_IN3P 不打孔、布不通（抬多了没路）。结论：稀疏处撞孔用 viaCu≈孔距−孔环 解决；CC2（J400 区）、PWR_HOLD（U13_AD16 内圈）三轮都失败，内置布线到此为止，要先腾空间或回 KiCad 流程。
  5. **拆邻网重布（11-03 凌晨，负载 17–21，一轮）**：在 CC2、PWR_HOLD 焊盘 1 mm 内找挡路信号网，连同目标网一起拆，先布目标网再布邻网（viaCu 0.13→0.10，每次单独 invoke 限 7 min）。CC2 + MCU_START：CC2 0.10 下连上但仍撞 3V3 过孔 e1452（孔到线），MCU_START 布不回；PWR_HOLD + MCU_LED + XIAO_PWDN：4 网 8 次全部布不出，7 次跑满 7 min 超时。被拆的邻网原本都是 KiCad 布通的，内置布线一条都恢复不了——**nRF52840 aQFN 内圈和 USB-C 口这种密集区，嘉立创内置布线不能用，拆邻网只会更糟**；这类区域直接走 KiCad 全流程。
- `optimization:1` 在 C08A 上 14 min 卡在 1%（日志「Capacitor package should have only two pins」×42、splitLine 递归上限），cancel 后连已补的线一起撤回；运行中 `getAll` 会读到临时中间图元，作业结束前别计数。

## 嘉立创里两种布线怎么配合（2026-10-25 实测）

结论：**只用内置布线；FreeRouting 扩展既不适合布全板，也不适合补网。** 布完还有断网时，先用内置布线补；补不通就清空、整板重布；再不行手工补。每一步都以嘉立创 DRC 为准，不信任何布线器自报。

```
[0 准备]  导入 → 逐焊盘核网络 → 修过孔规则（外径/孔径对调）→ 布线前 DRC 基线（记 Connection Error 和间距错误各几条）
   ↓
[1 内置全板]  autoRouting() → 画布线数连续 1 分钟不变 = 布完 → DRC
             大板（几百个网）按 RoutingNets 分批，每批 25 网左右（见下方 C07 记录）
   ↓ Connection Error = 0 且没有新增间距错误 → [4 验收]
   ↓ 还有断网
[2 内置补网]  RoutingNets = DRC 里报 Connection Error 的网名，已有线保持默认 keep → DRC
             同样的网再补一轮没用（v09 第二轮 0/3）
   ↓ 还有断网
[3 清空重布]  删光线和过孔 → 回到 [1] 整板一次布（v09：整板一次 0 断网，先布一部分再补网反而剩 3 个）
             还不通就手工补，或回 KiCad 主线（2.2.3 jar）
   ↓
[4 验收]  Connection Error 0；间距错误不比基线多；画布线/过孔数有记录。全过才算布线通过，不等于可以下单
```

v09 实测（同一块板，同一份修好的过孔规则；每次都从空板开始）：
| 做法 | 结果 |
|---|---|
| 内置整板一次布 | 约 2 min，635 段/71 过孔，**0 断网**，无新增间距错误 |
| 扩展整板 | 22 s，524 段/80 过孔，0 断网，**新增 200 条间距错误** |
| 内置跳过 5 网 → 内置补 6 个断网 | 第 1 步 547 段/51 过孔、6 网断（含 1 个内置自己没布通的），两次复现结果相同；补网 29 s，**自报 5/6 成功，DRC 实际只通 3 个**；对剩下 3 个再补一轮：0/3 |
| 内置跳过 5 网 → 锁线 → 扩展补网 | **不能用**：锁线时 7 段线消失、GND 被弄断；Freerouting 把 559 段锁定线当已有走线带过去，起步 46 网未通、11 min 后仍 20 网未通，日志 36 次 `NullPointerException`（`from_corner is null`）；画回后新线叠在锁定线上，1186 段/109 过孔，7 个断网只补好 2 个，新增孔压孔 51、线压线 12、线压过孔 12 |

| 何时用哪个 | 内置布线 | FreeRouting 扩展 |
|---|---|---|
| 默认全板 | ✓ | ✗ 新增大量间距错误 |
| 补几个网 | ✓ 一轮，看 DRC | ✗ 锁线补网实测失败（见上） |
| 要和 KiCad 主线比结果 | ✗ | ✗ 直接用 2.2.3 jar 布 KiCad 导出的 DSN（SKILL.md 主线） |
| 能从桥触发 | ✓ `autoRouting()` | 只能界面点，或经桥加载安装包里的 `dist/index.js` 后调 `autoRoute()` |

坑：
- **`autoRouting()` 的返回值不可信**：`successNetsCount` 会把 DRC 仍报断网的网算成功（v09 补网自报 5/6，实际 3/6）。布完一律跑 DRC。超过 30 s 时接口报超时、拿不到返回值；30 s 内布完才会返回。
- **接口批量锁线会改动走线**（3.2.186）：`pcb_PrimitiveLine.modify(id,{primitiveLock:true})` 一批跑到一半报 `Cannot read properties of undefined (reading 'isAsync')`；改用 `get([id])→toAsync()→setState_PrimitiveLock(true)→done()` 能锁住，但有 7 段线 `get` 取不到、线总数从 547 变成 559，GND 断了一处。锁完要跑 DRC。
- **扩展每次运行都会删掉全部未锁定的线和过孔**，不只是它要布的网；锁定的线会作为已有走线带给 Freerouting，SES 回来时再画一遍，结果和原线重叠。
- **大板别整板一次布**：C07 设计评估会话 2026-10-25 报告，C07 r2（421 件、311 网、5 层走线、线宽/间距 0.1）整板 `autoRouting()` 会在约 20% 时静默退出；改成每批 25 网分 13 批，共约 15 min，布通 226 网、失败 47 网（集中在 nRF52840 aQFN 周围的 USB、晶振、去耦、SPI），第 4 批报超时，最后两批返回空，DRC 没跑。同起点 Freerouting 整板重布剩 13 未连。本会话没复核这些数。
- `autoRouting()` 的 `optimization` 枚举有 COMPLETION / FASTER 两项，实际字符串取值没核过；桥里枚举对象不可见，不确定就别传，默认就是布通率优先。
- `pcb_Document.clearRouting('all')` 在 v09 上报 500 且没生效；要清线就用 `pcb_PrimitiveLine/Arc/Via.delete(ids)`（锁定的也能删）。
- 同一台嘉立创一次只给一个会话用：开工前确认没有别的会话正在 openProject 或布线，用完告诉对方。

## 嘉立创EDA内置

来源：https://prodocs.lceda.cn/cn/pcb/route-auto-routing/

- 可设：引擎、迭代 1–100 次（默认 1）、每次 5–120 s（默认 30）、保留/删除已有线和过孔（默认保留）、速度优先/布通率优先、过孔多少、45°/90°、网络顺序（默认/随机/自定义/按焊盘数/按焊盘距离）、布线层、忽略网络。
- 官方指标原文："针对 1000 元件、2000 Pin 规模的 PCB，自动布线可在 20 分钟内完成，网络布通率不低于 90%，DRC 错误数不超过 10 个。" 这是厂商目标，不是我们测的。
- 中途停止会留下已完成的 90° 线和过孔，要手动清。
- 脚本触发：用 `easyeda-api` 的桥调 `eda.pcb_Document.autoRouting()`（不带参数 = 布所有未布网络；参数里没有过孔尺寸，过孔只从设计规则读）。接口 30 秒必超时，但客户端会继续布完，按走线数连续 1 分钟不变判断结束。

实测（2026-10-25，Mac 客户端 3.2.186，`carrier-v09-audio` 未布线板经 KiCad 导入，桥调 `autoRouting()` 默认参数）：
| 项 | 结果 |
|---|---|
| 导入核对 | 40 器件、37 网络；逐焊盘 (位号, 焊盘号, 网名) 对 KiCad：158 对 157，只差 U2 一个无编号无网络的机械焊盘 |
| 布线前 DRC 基线 | 37 个网络全部报 Connection Error，共 147 条（证明 DRC 能抓断网） |
| 布线 | 约 2 分钟；635 段线、71 个过孔（过程中线数先升后降，是它在优化） |
| 布线后 DRC | **Connection Error 0**；另有 6 条 Device to TH Pad，布线前就有（器件压通孔老问题） |

对照：同一 DSN 上 Freerouting 1.9.0 是 192 线/32 过孔、2.2.3 是 208 线/51 过孔，导入 KiCad 后都是 0 断网（嘉立创按段计数，线数不能直接比）。没有 SES，只能用画布计数 + 嘉立创自己的 DRC，没做 KiCad 侧复核。

**坑：KiCad 导入会把过孔外径和孔径弄反**。导入后规则「Via Size」成了外径 0.305 / 孔 0.61（孔比焊环大），界面布线报「过孔尺寸必须大于 0 mm」，接口调用则静默不布（10 分钟 0 条线、CPU 约 5%）。嘉立创两层板标准是外径 0.61 / 孔 0.305。修法：`getCurrentRuleConfiguration()` → 改 `config.Physics['Via Size'].viaSize.form` 的 `viaOuterdiameter*` 与 `viaInnerdiameter*`（Outer=外径、Inner=孔径）→ `overwriteCurrentRuleConfiguration(config)`，再读回核对。KiCad 10 板还要先在导入副本里补网络编号（见 SKILL.md「模拟小板与 KiCad 10 导入门」）。

**坑：KiCad 10 工程（原理图 + PCB 一起压 zip）导入后 PCB 网络基本全丢**（2026-10-29，C08A 259 件：898 焊盘里 883 个 `net:""`；7 块覆铜也全是空网）。不补就没法布，而且「导出 → Altium」也带不出网络。修法不用改 epro2（SKILL.md「模拟小板与 KiCad 10 导入门」是在 KiCad 导入副本里补 `(net ID "NAME")` 再导入，适合导入前能改文件的小板；已经导进嘉立创、不想重导时用下面的接口补法）：
- 焊盘：`pcb_PrimitiveComponent.getAll()` → `getAllPinsByPrimitiveId(id)` → 按 KiCad `(位号, 焊盘号) → 网名` 表对每个焊盘 `p.toAsync().setState_Net(net); await a.done()`，每次调用 30 个器件以内防超时；写完全量读回比对（C08A：878 个带编号焊盘 0 不一致，6 个无编号的安装孔/定位孔本来无网）。
- 覆铜：`pcb_PrimitivePour.get(ids)` 读 `complexPolygon`（mil，y 取反）和层，按包围盒对 KiCad `Zones()`，`pcb_PrimitivePour.modify(id,{net})`，再逐块 `rebuildCopperRegion()`。导入会多出一块比 GND 小岛大一圈的覆铜（3V3 挖空那圈），优先级低于 3V3，填 GND。
- **走线和过孔也会丢，要整板带走线进嘉立创时用 `scripts/kicad_to_easyeda.py`**（2026-10-30，C08A 14 未连板）：导入后线 0 条、过孔有位置没网络。脚本按 `.kicad_pcb` 依次写回规则（外径/孔径/线宽/间距矩阵，读回核对）、焊盘网络、删掉导入的空网孔并按 KiCad 重建（网络 + 尺寸）、铺铜配网、逐段建线（`pcb_PrimitiveLine.create`，mil、y 取反，分块送入、EDA 里后台跑）、重铺、读回。`--steps` 可只重跑某几步（建线建孔不能重跑，会重复）。实测：8771 段 / 600 孔 / 878 焊盘全部建成，覆盖采样 0 缺口；嘉立创 DRC 间距类 0，Connection Error 落在 13 网，与 KiCad 的 14 条未连同一批（$2N336 KiCad 2 条、嘉立创报 3 项）。
  - **坑 1，同号多块焊盘**：散热焊盘分块、固定脚 MP、按键两脚同号，`getAllPinsByPrimitiveId` 会返回多块同号焊盘；按号建字典只写到其中一块，另一块空网，地线压上去报「SMD Pad to Track 0 mil」。每块都要写（C08A：878 → 892 次写入，8 条焊盘到线 + 1 条同封装间距消失）。
  - **坑 2，孔到孔在另一张表**：`config.Spacing['Other Spacing'].otherClearance.form.hole2Hole` 默认 0.29972，不在「Safe Spacing」矩阵里；不改会报 9 条孔到孔（实测 0.206–0.295，按 KiCad 0.20 全合格）。
  - **坑 3，读回不能按段数比**：嘉立创会在 T 形接头/过孔处自动断线，也会合并重叠段并把端点吸附约 0.025 mm（8771 段建入，读回 8200 段，30 组总长短 0.01–2 mm）。按段数或总长比会报假差异；改成覆盖采样（KiCad 每段每 0.05 mm 一点，落在嘉立创同网同层线 0.03 mm 内）才对，结果 0 缺口。
  - 剩 1 条「PCB 与原理图网表不一致」是原理图导入的老问题，修法见下文，不影响布线。

**C08A 实测：密板上内置布线不如 KiCad 流程**（2026-10-29，Mac 3.2.186，4 层、0.30/0.45 普通孔、线宽/间距 0.10，nRF52840 aQFN73 + 两颗 ADS1298，全拆走线的摆位板起步）：

| 步骤 | 结果 |
|---|---|
| 信号 157 网分 7 批（25 网/批，`RoutingNets`） | 每批 15–84 s；自报失败 20 网；1411 段、166 孔 |
| 电源 16 网一批 | 656 s 一根没布（接口不返回） |
| 电源逐网单布（每网 ≤240 s，`Promise.race` 限时） | VBUS/VBAT/VBAT_SENSE/PWR_G/PWR_EN/+2.5V 自报成功；-2.5V 超时后线被撤回一部分 |
| 失败信号 66 网再补 | 0 网加线 |
| 最终嘉立创 DRC | Connection Error 388 / 72 网（GND 134、3V3 60 靠铺铜+过孔，布线器不处理；其余约 194 处在 70 网）；孔间距 17；VBUS 自报成功但 DRC 仍断 5 处 |

同板 Spark 上 KiCad 协商布线最好 58 未连。结论（单板实测、仅供参考）：嘉立创内置布线在 v09 这类稀疏小板上能到 0；C08A 这种 aQFN 扇出受限的密板上明显不如 KiCad 流程，GND/电源平面要先打孔连平面，布线器不会替你打。

## 嘉立创 eext-freerouting 扩展

来源：https://github.com/easyeda/eext-freerouting-intergration （Apache-2.0）

1. 装 Freerouting ≥ 2.2.3。
2. 嘉立创EDA「高级 → 扩展管理器」装扩展，扩展配置里开「外部交互」。
3. 跑仓库里对应系统的启动脚本：它找本机 Freerouting，以无界面、禁用认证的方式起 API（端口 37864）。
4. PCB 里点「直接自动布线」或「自定义自动布线」，几秒刷新一次中间结果。

源码怎么走（v1.7.1，commit dab0725，2026-07-30）：`eda.pcb_ManufactureData.getDsnFile()` 由嘉立创自己导出 DSN → `sys_ClientUrl.request` 提交给本机 Freerouting API → 轮询进度 → 删掉画布上所有未锁定的线/圆弧/过孔 → `SESImporter.ts` 调嘉立创自带的 `pcb_Document.importAutoRouteSesFile()` 画回画布 → `pcb_Drc.check`。导入用的是嘉立创原生 SES 导入，扩展本身只做转交。界面成功提示里的"线/过孔数"来自 Freerouting 的统计，不是画布上实际画出来的数。接口请求必须带 `Freerouting-Environment-Host: <工具>/<版本>` 头，否则返回错误。

实测（2026-10-25，Mac 3.2.186，官方 v1.7.1 zh-cn 包 sha256 fb5f4085…，Freerouting 2.2.3 jar 断网+关上报；与上面内置布线**同一块板、同一份已修过孔规则**，布线前清空画布）：
| 项 | 结果 |
|---|---|
| 触发 | 菜单在顶栏「导出 (R)」右边单独的 ⌄ 里（不是「导出 → 自动布线(DSN)」，那是导出 DSN 文件）。本次没点菜单：扩展函数关在它自己的沙箱里，桥调不到，所以把安装包里的 `dist/index.js` 原样经 run-api-gateway 桥加载后调 `autoRoute()`，代码路径与菜单「直接自动布线」相同 |
| Freerouting 任务 | 22 s、8 轮后停（任务里 `max_passes` 显示 9999，扩展传的 50 没生效）；**自报 29 个网未布通**；输出统计 232 条走线、80 个过孔 |
| SES → 画布 | SES 536 段 / 80 过孔 → 画布 524 段 / 80 过孔。**12 段去向不明**（SES 里没有零长度段、没有共线可合并段） |
| 嘉立创 DRC | **Connection Error 0**；但新增 200 条间距错误：Track to Via 114、Hole to Track 52、SMD Pad to Via 18、Via to Via 5、Hole to Via 4、Board Outline to Track 3、Hole to SMD Pad 3、TH Pad to Via 1（另 6 条 Device to TH Pad 是旧问题） |

结论：在这块板上，扩展直跑这条路**没有断网，但不能直接用**。同一块板上嘉立创内置布线 2 分钟布通且无新增间距错误，所以在嘉立创里优先用内置布线。

**200 条间距错误的根因与修法（2026-10-25 实查 `Autorouter_v09imp_2026-10-25.dsn` 坐实）**：
- **情形**：嘉立创 EDA 专业版（3.2.186）导出的 Specctra `.dsn` 喂给 Freerouting 后，导回画布爆出大量 Track to Via（114 条）和 Hole to Track（52 条）间距违规。
- **规则**：离线跑嘉立创导出的 `.dsn` 前，先运行 `python3 scripts/fix_and_route_easyeda_dsn.py <in.dsn>` 修补两处导出缺陷再调 Freerouting：(1) 将 `(padstack via0` 下的过孔圆直径从 `12`（`0.305 mm`，把钻孔当成了外径）改为 `24`（`0.61 mm`）；(2) 将全部网络 `(class ...)` 里写死的 `(clearance 4)` 改为与全局 `(clear 6.05)` 一致的 `(clearance 6.05)`。此外嘉立创导出 DSN 时会把全板焊盘压成单个虚拟器件 `(component u1)`、引脚名改成内部 ID（`i0ie33`），因此产出的 `.ses` 只能导回导出它的同一份嘉立创画布，不能跨导回 KiCad 或 Altium。
- **证据**：`Autorouter_v09imp_2026-10-25.dsn`（748 行）第 210 行写 `(circle TopLayer 12)`、第 402–743 行 39 个 `class` 写 `(clearance 4)`；修补 40 行后用 Freerouting 2.2.3（`-mp 100 -mt 1 --gui.enabled=false`）实跑，扇出 `138/141` 引脚（`0 insert errors`），主布线从 `122 unrouted` 收敛至 `0 unrouted`（得分 `977.04`），产出 `v09imp-fixed.ses`（`39,756 B`）。
- **置信**：高（源码级逐行比对 + 修补前后实跑对照）。

thursday-win 那次没测成：窗口空白、网关没连上，同一桌面还有别的会话的 Altium 停在断点报错框。

坑：
- **每次布线清掉所有未锁定的导线和过孔**。锁定后再跑会和锁定线重叠（见上「配合」实测），不要拿它补网。
- Freerouting 2.x 默认上报使用数据。启动脚本前设 `FREEROUTING__USAGE_AND_DIAGNOSTIC_DATA__DISABLE_ANALYTICS=true`；板子数据出机器要先问 Mako。
- 2.2.3 jar 要 Java 25+（class file 69）；Java 21 起不来。

## Altium Designer 自动布线（内置 Situs / MCP `route_plan` / DelphiScript）

在 `thursday-win` 的 Altium Designer 上有三条可用路径（详见 `altium-mcp` 与 `altium-delphi-scripts`）：

1. **内置 Situs 自动布线器（DelphiScript 一键入口）**：
   - 打开 `%USERPROFILE%\altium-work\Mako_AD_Tools\Mako_AD_Tools.PrjScr`：
     - `Mako_AutorouteAll`：`ResetParameters; AddStringParameter('Action', 'Start'); RunProcess('PCB:Autoroute');`
     - `Mako_AutorouteSelectedComponents`：`AddStringParameter('Action', 'Component'); RunProcess('PCB:Autoroute');`
     - `Mako_UnrouteAll`：`AddStringParameter('Scope', 'All'); RunProcess('PCB:DeRoute');`
2. **Mac 经 SSH + MCP 障碍感知规划与布线**：
   - 先调 `proj_open` + `pcb_focus_board`（必传 `{"board_path": "C:\\...\\xxx.PcbDoc"}`），再调 `route_plan` 生成避障轨迹串，最后调 `pcb_place_tracks` 落线并用 `pcb_get_unrouted_nets` 与 `pcb_get_clearance_violations` 读回验收（禁止在 SSH 下调 `pcb_run_drc` 模态框）。
   - 一键脚本：`python3 ~/.local/share/mstack/skills/altium-mcp/scripts/altium_mcp_route.py route --board "C:\...\board.PcbDoc"`。
3. **PcbAutoRouter（Altium / Allegro 外部 `.dsn` ↔ `.rte`）**

来源：https://www.pcbautorouter.top/zh/docs/altium-autorouting 、 https://www.pcbautorouter.top/zh/docs/allegro-autorouting

| 步 | Altium | Allegro |
|---|---|---|
| 导出 | File » Export » Specctra Design → `.dsn` | File > Export > Router → `.dsn` |
| 布线 | PcbAutoRouter 打开 DSN，F5 | 同左 |
| 导出结果 | 「导出RTE文件 (Altium)」→ `.rte`（**Altium 只认 RTE，不认 SES**） | 「导出SES文件」→ `.ses` |
| 导回 | File » Import » Specctra Design 导 `.rte`，跑 DRC | File > Import > Router 导 `.ses`，查 DRC |

- 导回同一份源板；导出 DSN 后别再改元件和网络。
- 软件打不开或报 Vulkan 初始化失败，查它的系统要求页。
- 作者、价格、是否联网都没写；用之前先断网试，确认能离线跑。
