---
name: jlc-order
version: 0.1.0
description: >-
  在嘉立创（jlc.com / jlc-smt.com）下 PCB 打样单 + 经济型 SMT 贴片单的实操流程：先算价、出制造包、
  网页下单、随单传 Gerber、进 SMT 传 BOM/坐标做匹配，停在付款前。来源：MAKO 手表载板 v0.16 第一次真实下单
  （2026-09-16），v0.19D 新版页面与两次提交后返工（2026-09-26），含每一步的页面顺序、必填项、隐藏坑和文件格式。Use when: 用户说"打板/下单/贴片/嘉立创/
  JLC/看价格/SMT 报价/出 Gerber 上传"，或问经济型贴片能不能做、换料费怎么算。前提是板子已过 pcb-autoroute
  的 ROUTING_ONLY_PASS；付款、发票、地址永远由用户自己操作。同平台的 3D 打印外发（外壳/树脂件）见 jlc-3dp。
---

# 嘉立创 PCB + SMT 下单

一句话：**价格先在计价页拿到，制造包脚本一键出，网页下单是"先提交订单再传文件"，SMT 是 PCB 单的子流程，付款前停。**

## 0. 下单前的门（不过不下）

- 板子过 `pcb-autoroute` 的 `ROUTING_ONLY_PASS`（KiCad DRC 0 违规 0 未连接）**和 `check_interfaces.py` 的 `INTERFACES_PASS`**（连接器嘴朝对手来的方向、焊线盘挨着对手）。2026-09-16 v0.19B：DRC 过、工程师 DFM 极性无警告、**已付 ¥182.18 并排产**，FPC 座嘴却朝板内——DRC/DFM 只看板子自己，看不见屏在哪。**一个座子放反，这单钱就没了：下单前的最后一道门只能是人对着渲染图逐个连接器问「线从哪来」。**
- **BOM 料号的焊盘要和你的封装对得上（不只是「封装名像」）**：拿立创旧接口的封装焊盘按编号拟合到自己封装（转角 + 平移），最优残差要远小于次优。2026-09-27 EMG4 v09 J5：1.27 1×2 贴片排针 C2881569（X1311WVS-02J **R1**）的交错脚是 KiCad `Pin1Left` 的镜像，任何角度都贴不上（残差 49.6 mil，次优 50.0）；同系列 **R2** C2881608 残差 2 mil。交错脚排针、侧插座子这类有左右手性的件必查。脚本：tiny-ai-watch 仓 `candidates/eda_check/add_3d.py`（同时给嘉立创 EDA 补 3D）。
- **贴片间距门：DRC 0 不等于贴得上。** 下单前跑 `pcb-design/scripts/dfm/` 下的两个脚本：先查 courtyard 有没有包住焊盘，再按 CPL 量同面 0201–0805 阻容的本体间隙（< 0.10 mm 就报；IC、连接器不查；阈值置信低）。外来工程拿不到 KiCad 板时，至少跑 CPL 那个。这次是工程师在 DFM 人工确认环节拦下的（SMT 单选了「不允许系统自动确认」）。2026-11-08 Y27：48 颗 0201 被判相互干涉，根因是导入封装的 courtyard 比焊盘还小，DRC 报不出来（见 `pcb-design` 阶段 3）。
- 有一份放行审核文件（例：`2.0/pcb/carrier/REVIEW_v16.md`）：封装与原厂图纸、供电预算、制造文件复核、用户要勾的实物项。
- **复核结论绑设计指纹，上传前由脚本判门**：制造复核做完用 `scripts/review_gate.py record <工程目录> --verdict ok|issues [--summary …] [--issue …]`（issues 必须逐条写，最多 8 条）记到 `<工程目录>/.review/review.json`；**上传 Gerber/BOM/CPL 前 `scripts/review_gate.py check <工程目录>` 必须退出 0（current 且 ok）**。退出 2（none，没记录）、3（stale，复核后设计文件变了，会列出改了哪些）就先做复核；退出 1（current 但有问题）就先改。唯一例外是 Mako 本人当轮原话说「不复核直接下」，记进 HANDOFF。指纹 = 板、原理图、工程、规则、STEP、DXF、Gerber、钻孔、BOM、CPL 文件的「相对路径 + 内容 sha256」，出包目录和 `.review/` 不算；`--exclude` 只能在 record 时加，check 不收。借自 Backplane `docs/order.md`（none/stale/current）；自检 `scripts/test_review_gate.py`。
- **付款、发票抬头、收货地址、账号登录：用户自己做。** 登录用用户的 Chrome（`claude-in-chrome`），不要用 Browser 面板——嘉立创要账号，站内计价页不登录也能看价。
- **claude-in-chrome 连不上时**（`list_connected_browsers` 为空）：`chrome-devtools` MCP 会自己拉起一个**独立配置**的 Chrome，没有用户的登录态——请用户在这个窗口里登录一次，登录后就能照常操作（2026-09-26 实测）。判断是否已登录：看 member.jlc.com 右上角「客编 XXXX」、余额不是「--」。它的 `take_snapshot filePath` 只能写到项目目录（scratchpad 会被拒），写完读完就删；用户可能同时在用这个窗口、会关掉标签页，页面报 No page found 时先 `list_pages`。

## 1. 算价（不登录也行）

| 什么 | 哪里 | 输入 |
|---|---|---|
| PCB | https://www.jlc.com → 顶部"PCB在线计价" → `https://www.jlc.com/newOrder/#/pcb/pcbPlaceOrder` | 板材 FR-4、层数、尺寸 **单位 CM**（27.5×37.5 mm 填 2.75 × 3.75）、数量下拉、成品板厚 |
| SMT | https://www.jlc-smt.com 首页"SMT 在线计价"卡片 → `https://www.jlc-smt.com/calc-online?patchLocation=T&patchQuantity=5&patchComponentType=18&patchComponentQuantity=112` | 单/双面、贴片数量、**种类/PCS**（不同料号数）、**焊点/PCS**（贴片焊盘数）；进页面后还要填板子尺寸才出价 |

焊点和种类从板文件数：遍历正面贴片封装，跳过 DNP、模块、焊线盘；焊点 = SMD 焊盘数；种类 = 不同料号数（同值阻容算一种）。

2026-09-16 实价（5 片 27.5×37.5 两层 0.8 mm，18 种 112 焊点）：PCB ¥40（下单助手 ¥30）；经济型 SMT 工程费 ¥50 + 焊点 ¥0.01/点 + 换料费（扩展库每种 ¥20，基础库免）+ 钢网免费 ≈ ¥146 + 元件费；标准型 ¥299。经济型限制：**只贴单面**，元件必须在嘉立创库有货。

## 2. 出制造包（KiCad 10）

**出包走 `scripts/fab_stage.sh`，不从工作区直出**：`fab_stage.sh [--review 工程目录] [--pin 板文件]... <提交> <输出根目录> -- <出包命令…>`；加 `--review` 时按该提交里的文件再跑一次 `review_gate.py check`，不是 current 且 ok 就拒。它先确认提交在 origin/main 上（不在就拒，先推送再 `git fetch`），`git archive` 到临时目录、在那里跑出包命令，命令把文件写进 `$FAB_OUT`（`<目标>.staging`）；命令失败或退出 0 却没出文件都算失败；成功后写 `SOURCE.json`（提交号、树号、每个输出文件 sha256；`--pin` 的源文件按 yuanli-band `host/fab_pin.py` 的 blob + sha256 格式记）再改名成 `fab-<YYYYMMDD>-<sha8>`，目标已存在就拒、不覆盖。下面的 `export_fab.sh` 这类脚本当作出包命令传进去，输出目录改读 `$FAB_OUT`。借自 Backplane `docs/design-review.md`「从提交出包」。

`2.0/pcb/carrier/export_fab.sh <board>`：`kicad-cli pcb export gerbers`（F/B Cu、F Paste、F/B Silk、F/B Mask、Edge，`--use-drill-file-origin`）+ `export drill`（Excellon mm，PTH/NPTH 分开，含 map）+ `export pos --side front --exclude-dnp` + 从 design.json 生成 BOM（MPN 表手写在脚本里）。然后：
- **上传 Gerber 用只含 Gerber/钻孔的平铺 zip**（11 个文件在根目录，不要子目录，不要把 pos/bom/json 塞进去）；带子目录的包 PCB 单能提交，但 SMT 2D 预览报"未检测到有效的 gerber / 未获取到PCB图形数据"。
- 机器自检：Gerber job 8 层；PTH 孔数 = 过孔数 + 通孔焊盘数；pos 行数 = SMT 实装数；BOM 里焊线盘/模块/马达孔标 `not-smt`，DNP 标 DNP。
- **BOM 的 Footprint 列写嘉立创的封装名**（0603 / 0805 / SOT-23-5 / TSSOP-20 / SC-70-6 / SMA(DO-214AC) / "SMD,P=0.5mm,卧贴"），不要写 KiCad 封装名，否则每行都弹"BOM填写的封装与选用不符"温馨提示（`export_fab.sh` 里有映射表）。
- 匹配页每行最左的勾是"核对"，全选框勾掉没有 ⚠ 的行；带 ⚠ 的行（例：CAT4002 被当成 LED 提示"需要确认颜色"）看过提示后直接点那一行的勾也能勾上。全带 C 号时 17/17 一次过，没有任何封装/参数提示。勾完点"存档当前操作"，停在"备料完成，下一步"前。
- SMT 文件另出两份：`jlc_smt_bom.csv`（列 `Comment,Designator,Footprint,Quantity`，同料号合并成一行、位号逗号连）和 `jlc_smt_cpl.csv`（列 `Designator,Mid X,Mid Y,Layer,Rotation`，坐标带 `mm` 后缀，**Y 直接用 KiCad pos 导出的值，不要取反**——它和 Gerber 同一坐标系）。

通用版（不依赖手表项目脚本）：vibe-hw `agents/jlc.mjs` 的 `runJlc(dir)` 用 `kicad-cli pcb export pos --smd-only` + `sch export bom --fields …,LCSC` 直接出 `jlc/jlc_smt_bom.csv` 和 `jlc_smt_cpl.csv`；任一贴片位号没 C 号就判 block 并列出位号（b-parasite 真板 20 个贴装缺 8 个，这是常态）。`npm run test:jlc`。

## 3. PCB 网页下单（顺序就是坑）

1. 登录后回到 pcbPlaceOrder 页，选 FR-4 → 填尺寸/数量/层数/板厚。**页面上没有上传入口**，Gerber 是提交订单之后才传的。
2. 左栏"SMT贴片/激光钢网"里"是否SMT贴片"选**需要**（右侧多出"提交pcb订单，去下smt"按钮），钢网不需要（经济型免费给）。
3. 点提交会弹**确认生产稿**对话框：需要 / 手动确认 / 确认 2 次（¥3）——第一块板选这个，它就是人眼核方向的那一步；三项都要点，否则对话框不关。
4. 点"检查订单"，右栏列出**未填项**，2026-09 是四项：板上加标志（选"加嘉立创客编(免费)"，再确认弹窗）、是否需要磨边（不需要）、确认订单方式（**手动确认订单**，避免自动扣款）、发货方式（不同交期不一起发货）。
5. "品质赔付服务"会被前面某个弹窗默认成"元器件移植全额赔付 ¥10"，改回"按标准合同常规处理"（免费），它还会再弹一个对比框，点左边。
6. 右栏"确认并提交" → 弹**上传文件**对话框（`input#uploadFile`，用 `find` 拿 ref 后 `file_upload`）→ 传 zip → 自动跳"下单成功，请等待审核"。此时**未付款**，审核 10–60 分钟，付款走下单助手/订单列表，**留给用户**。
7. 成功页有"不等审核结果，继续去下SMT订单"。**建议先别点**：等 PCB 审核通过（订单列表变成「待付款」，一般 1–5 分钟），再从该行「去下SMT订单」进 SMT，原因见 4a 第 2 条。

2026-09 新版下单页的差别（v0.19D 实测）：进页先弹「请选择板材类别」→ 点 FR-4；数量是按钮网格（5/10/…）；选「需要（确认2次，收费3元）」会弹「确认生产稿说明」，里面再点**手动确认 + 确认2次 + 确定**；品质赔付默认就是「元器件移植全额赔付 ¥10」，改「按标准合同」还会弹对比框，点左边；「加嘉立创客编」弹框点「确认」；选 SMT 需要后钢网自动取消；交期可选「24小时免费加急（SMT专享）」。右栏「检查订单」会弹整页参数清单，逐项核（板厚 0.8、品质赔付、手动确认订单），再点「确认并提交」弹上传框。

## 4. SMT 子流程

1. `https://www.jlc-smt.com/order?type=smtUploadBomFile&produceOrderAccessId=<PCB单ID>`：选中刚才的 PCB 单（列表按文件名/编号查）。
2. "BOM与坐标清单" → "上传BOM和坐标" → 导出文件选"AD,99SE,Allegro,PADS(宏)等其他"。
3. 坐标文件和 BOM 文件的 `input[type=file]` 用 `find "all input type=file elements"` 一般都能拿到 ref，直接 `file_upload`（第二次下单两个都成功了）。拿不到 BOM 的 ref 时别用 JS 改 DOM（会被自动模式判为真实交易拦下），让用户点"上传文件"。
   页面底部"基本信息"要先选：焊接层=只贴顶层、应用行业、产品类型=经济型、确认DFM（¥3；选"不需要"订单卡片就是"未做DFM 暂无图片"，选"需要"付款后有 PCBA 实物图可核方向，第一块板值这 3 块）、确认钢网=不需要，否则"下一步"只是滚回顶部不报错。
4. "下一步，BOM匹配选型"：看哪些料在基础库（免换料费）、哪些扩展库（¥20/种）、哪些没有（改型号或邮寄）。经济型不接受库外料。
5. 匹配完出精确报价 → "备料完成，下一步" → 结算页有**极性选项**：1 自己保证 / 2 传极性图 / 3 工程师按丝印核（¥10）——KiCad 封装嘉立创看不出极性，第一块板选 3。"提交订单"后页面回到空白 `order?spm=...Order_settlement.end`，不是失败；SMT 单在 `https://www.jlc.com/center/smt/home/order/smt_list/`（`jlc-smt.com/order/list` 是 404）看单号 `SMT26xxxxxxxx（PCB：Yn）`、金额和"等待您支付"。付款由用户，不点"全选支付"。

2026-09-27 补充（只报价、不下单时）：
- **还没有真 PCB 单也能拿 SMT 官方试算**：账号里自带「嘉立创SMT流程演示订单 Y1」（10×10、5 片），选它 → 上传 BOM/坐标 → 下一步到匹配页就出价；自动对齐弹窗点「否」。演示板按 15×15 算，和板尺寸相关的项会有出入。
- **匹配页总价不含扩展库元件**：「元件费」只算基础库；扩展库行的「使用 N / 私有库 N」是**本单要买进私有库的数量**（含损耗，如 8×5+4=44），不是已有库存，钱在「备料完成」之后才出现。报价时扩展料另估（jlcsearch `components/list.json?search=C号`，JLCPCB 国际站美元阶梯价，要带 UA）。「加工费」里含扩展库换料费 ¥20/种：EMG4 v09 扩展 17 种，加工费 ¥403.9，其中换料约 ¥340。
- **双面贴自动锁标准型**（经济型置灰）。
- **板厚照板文件填，别用默认 1.6**：`grep '(thickness' x.kicad_pcb` 第一处（`(general (thickness 0.8))`）。4 层 0.8 mm 不支持有铅喷锡，会弹「温馨提示」要勾「我确认改为推荐工艺」再确认，自动改成无铅喷锡（+¥30/m²）；2026-09-28 EMG4 v08 因此 ¥110 → ¥140。
- 计价页一开会弹「请选择板材类别」框，点歪选成铜基板等会顺带把阻焊改成白色（出「颜色费 ¥30」）；切回 FR-4 后看一眼阻焊是不是绿色、价格明细里还有没有颜色费。
- **PCB 计价页默认「确认订单方式：系统自动扣款并确认」**——只看价时绝不点「提交订单」。
- 这次在 SMT 选项页点单选框被 Claude 的自动模式分类器按「真实交易」拦下（前一块板同样的点击没拦）。被拦就停，把剩下几步写给 Mako 自己点，不绕。
- 「私有库」数量常大于 每板用量×5（损耗/最小包装，v09 的 J5、Q1 各 9），别当成算错。

2026-09-26 补充：
- 坐标文件的「上传文件」按钮第一次 `upload_file` 常报"did not trigger a file chooser"，**同一个 uid 再传一次就成功**。
- 「确认DFM → 需要」下面多了二选一：选「不允许系统自动确认」（要人看 DFM 结果才排产）。
- 经济型的「氮气焊接」被锁在「需要--经济订单」（¥5.5），改不了，别当异常。
- 扩展库每种 ¥20：v0.19D 的 U4 CAT4002 和 J1 FH12 是扩展库（¥40），其余基础库。
- 「备料完成，下一步」后结算页是右侧抽屉；**极性三选一必须用真实点击**（chrome-devtools `click` 按 uid / claude-in-chrome `computer` 点坐标），见下面「提交后必核」。

## 4a. 提交后必核（两个都踩过）

提交完别看结算抽屉，去订单里看服务端存下来的值：
1. **SMT 订单详情**（SMT 订单列表 → 该行「订单详情」）：费用明细里要有「极性费 ¥10」「确认DFM服务费 ¥3」，基本信息「订单备注」要是「人工确认极性」。如果是「嘉立创封装库」，说明极性存成了第 1 项（按嘉立创EDA库）。2026-09-26 根因：用 JS `label.click()` 选第 3 项，抽屉里单选框和总价（¥157.02）都变了，但提交上去的仍是默认值（订单 ¥147.02、备注"嘉立创封装库"）；换成 CDP 真实点击 radio 后才对。修法：该行「修改元件 → 开始修改」（原 SMT 单自动取消、库存保留 1 小时）→ 回匹配页「备料完成」→ 真实点击选极性 → 提交。**规则：影响钱和生产的选项一律真实点击，JS 点击只用来点不影响订单内容的按钮。**
2. **SMT 页面右侧预览要有板子**（绿色 PCB 上摆着器件），不能只有一堆浮空器件。只有器件时，接口 `POST /api/smtOrder/smtBomResultView/v1/getWebFileSuccess` 会返回「PCB源文件中未检测到有效的gerber文件（注：不影响SMT下单）」。这个结果**跟着 PCB 单走**（换别的 `advanceOrderAccessId` 结果不变），所以是嘉立创对那一单 PCB 的 Gerber 解析没出结果，不是 SMT 这边的问题。2026-09-26 Y13：zip 和上一单成功的 Y12 逐文件同名、Gerber 指令集一致，排除了文件本身。修法：PCB 订单 →「更多操作 → ①重新上传文件下单」，传同一个 zip → 生成新单号（Y14），旧单自动取消（未付款无损失），**等新单审核通过后**再从新单「去下SMT订单」，预览就有板子了。为什么 Y13 没解析出来仍不知道：Y13 是 18 秒「系统自动审核」、进 SMT 时审核还没过，Y14 是 3 分钟人工审核、审核后才进 SMT。所以稳妥的顺序是**PCB 审核通过后再下 SMT**，不走「不等审核结果」。订单详情里的「暂无实物仿真图」不能当判断依据：Y14 解析成功后也这么显示，要以 SMT 预览或上面那个接口为准。
3. **PCB 订单详情对文件实测值**：上传前对要传的 zip 跑 `scripts/fab_facts.py <zip> --bom jlc_smt_bom.csv --cpl jlc_smt_cpl.csv --out fab_facts.json`（从 Gerber/钻孔本身量铜层数、外形包络、最小镀铜孔、镀铜/非镀铜孔数、槽孔数、正反钢网焊盘数，并列出 BOM/CPL 对不上的位号；有问题退出 1）。提交后订单详情里的**层数、尺寸、最小孔、孔数逐项对这张表**，不对就停下查是填错还是传错文件；**板厚、颜色、表面处理、铜厚这类文件里没有的，照下单选项核**。尺寸是板框中心线包络，拼板单要对拼板 zip 量出来的数。嘉立创EDA 的 `PTH_Through.DRL` 已含 `PTH_Through_Via.DRL` 的过孔，脚本按坐标去重，表里有「跨文件重复孔」一行。做法借自 Backplane `order/fabsrv/analysis.py`；自检 `scripts/test_fab_facts.py`。2026-11-10 阳性对照：D20N 包与 pcbnew 实量逐项相同（4 层、43.10×36.50、0.300、镀铜孔 266、钢网 305/222）；嘉立创EDA 主控 R4U3 包 4 层、43.10×36.50、0.300、BOM 46 行 110 位号 = CPL 110，与 README 相同。

## 4b. 取消重下（改版后换文件）

只想换同工艺的文件：订单详情「更多操作 → ①重新上传文件下单」最快，参数照搬、系统出新单号、旧单自动取消（2026-09-26 Y13→Y14 实测）。工艺也要改时才走取消重下：`https://member.jlc.com/` → 左栏"PCB / FPC 订单" → "PCB / FPC订单"（跳到 `jlc.com/newOrder/#/pcb/pcbOrderList`）→ 该行"取消" → 弹"请确认是否取消订单？"点确定（有时要点两次）。然后从第 3 步重来；新单会拿到新的 `produceOrderAccessId`，SMT 那边重新选单、重传 BOM/坐标（历史匹配不会自动带过去）。2026-09-16 v16→v18 实测：全程 6 分钟。

## 5. 记账

订单参数、报价、`customerOrderAssessId`、上传了什么、停在哪一步，写进项目 HANDOFF；未付款的单要写明"待审核、未付款"。

## 已知坑

- SMT 预览只有器件、没有板子 = 这张 PCB 单的 Gerber 解析缺失，文件没问题；用「重新上传文件下单」重来（4a-2）。
- 结算抽屉显示的价格 ≠ 服务端存下的选项；提交后去订单详情核「极性费 / 订单备注」（4a-1）。

- 尺寸单位是 CM，数量是下拉不是输入。
- 板厚默认 1.6，要手点 0.8，**提交前在右栏"检查订单"里再看一眼板厚和 SMT**——2026-09-16 一次点了没生效，下成 1.6 mm 只能取消重下（Y11）。
- `browser_batch` 里 screenshot 偶尔"Image omitted due to error"，单独再截一次即可。
- 页面 renderer 偶尔卡死导致 click 超时 30 s，等两秒重试。
- 嘉立创 EDA 的 KiCad 10 导入丢网络和走线，不能替代 KiCad DRC；只用它查"元件外形到通孔焊盘"这类几何项（见 pcb-autoroute）。

## 6. SMT 帮助中心要点（2026-10-24 读 17 篇，网页文字，未下单实测）

见 [6. SMT 帮助中心要点（2026-10-24 读 17 篇，网页文字，未下单实测）](references/smt-help-center.md)。

## 7. HDI 盲埋孔（2026-11-02 计价页实测 + 帮助页原文，未登录、未下单）

见 [7. HDI 盲埋孔（2026-11-02 计价页实测 + 帮助页原文，未登录、未下单）](references/hdi-blind-buried.md)。

## 8. 外来 .epro 的双面贴全流程（2026-11-05/06 实测，采集板 Y27 + SMT2611060002）

**取制造文件**
- `.epro` 用 `importProjectByProjectFile` 导入 `.eprj3`，`JLCEDA Pro`、`EasyEDA Pro` 都返回 None、不出 PCB；`Offline Client Local Path` 报未启用外部交互权限。能用的路：让客户端打开它，再 `session list` 找标题为工程名的窗口，只读调 `pcb_ManufactureData.getGerberFile / getBomFile('x','csv') / getPickAndPlaceFile('x','csv')`。BOM、坐标是 UTF-16 制表符。
- 微信容器文件读不了；浏览器 `file_upload` 只收会话目录内的文件，下载文件夹的先复制到 scratchpad。

**BOM 先人工核，别只看行数**：这份 25 行里 21 颗没料号、20 颗「值和型号不符」（100nF 写 100pF 的型号）。值以位号网络和原理图为准；缺货阻值（104k）要问人再换值。

**匹配页**
- PCB 审核前可以匹配并保存，但「备料完成」只弹「PCB 审核中」；预览也没有板子。审核通过（SMT 下单页状态「我方已确认」）后刷新才有板。
- 全选只勾无提示的行，「同料号两行」这类红叹号行要逐行勾（JS 点未勾 checkbox 可行），核对到 25/25 才能走。
- 进结算前选 SMT 生产时间。极性选项每次返回匹配都重置，要重选；¥10 算进总价要等几秒，别把旧总价当含极性价。

**提交失败**
- `可用预订库存不足 C…`：库存数字再大也可能订不了，标「共享」的料重试无效，用「替换」换料号；从共享换成普通扩展库多一笔换料费和最小起订，这次 +¥12。
- 提交卡在「提交订单中」1 分钟以上：别连点。先看 SMT 订单列表（`member.jlc.com/center/smt/home/order/smt_list/`，`jlc-smt.com/order/list` 是 404），没有再刷新重走，这次重走成功且无重复单。

**价格与工艺边**
- 3.82×2.51 cm 小板双面贴，工厂自动加四周工艺边、Mark、定位孔；PCB 从试算 ¥213.67 到待付款 ¥266.49，明细未核。
- 2 片双面 4 层实价：嘉立创 SMT ¥1183.23（元件 ¥581.37 + 加工 ¥601.86）；同配置华秋 SMT 页 ¥1481.73 且元件费另算。小批量先比固定费。
- FPC 座开口朝工艺边时，分板前插不了排线。

## 9. 库存、寄料与提交后的选项回读

合作库存的交期/最小包装、原始BOM与选中料号双显、核对勾选保存、沉板治具、烘烤费用与对象、标准型费用逐项对账，见 [采购与最终订单复核](references/procurement-order-readback.md)。这里的BOM勾选只确认已检查范围，不能代替方向、治具、拼板稿或到板验收；订单已提交后先只读核对，不自动取消重下。
