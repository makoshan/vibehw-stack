---
name: pcb-reference
version: 0.3.0
description: >
  查「真实开源硬件在怎么做」的参考库：某颗芯片配什么外围、某个值用什么封装、
  某颗器件在真板上怎么接线、哪些开源仓可以当参考、作者给某个值选的是哪个立创料号。
  数据来自 GitHub：star 头部 KiCad 仓的 5408 块板 + 提到嘉立创的仓里 789 块嘉立创 EDA 专业版板；
  34.5 万条器件（5.6 万带立创料号）、121 万条引脚连接、每块板一个用途标签。准确数字以 `query.py stats` 为准。
  Use when: 选型时想知道「别人都配什么」「这颗 MCU 的最小系统要哪些件」
  「这个封装常见吗」「有没有可以抄的开源板」；或要给某颗芯片找真实接法参考。
  不管落料号（那是 jlc-basic-parts），不管布线与 DRC（那是 pcb-autoroute），
  不管外壳（pcb-enclosure-fit）。
---

# pcb-reference

一个 SQLite 库 + 一个只读查询脚本。回答**「参考什么」**，不回答「买什么」和「怎么布线」。

## 怎么用

```bash
python3 ~/.claude/skills/pcb-reference/scripts/query.py stats              # 规模与口径，先看这个
python3 ~/.claude/skills/pcb-reference/scripts/query.py like esp32         # 型号写法五花八门，先模糊找
python3 ~/.claude/skills/pcb-reference/scripts/query.py part RP2040        # 谁在用 · 常用封装 · 立创料号 · 常配什么 · 参考板
python3 ~/.claude/skills/pcb-reference/scripts/query.py pins STM32F103C8Tx # 真实板上的引脚接法
python3 ~/.claude/skills/pcb-reference/scripts/query.py boards keyboard    # 按关键词找参考板（带用途标签）
python3 ~/.claude/skills/pcb-reference/scripts/query.py kinds             # 各用途类别有多少块板
python3 ~/.claude/skills/pcb-reference/scripts/query.py kind 电源          # 列出某一类的参考板
```

库的位置默认在 `~/go/202609/vibe-hw/work/pcbdb/pcb.db`，用 `PCBDB=<路径>` 覆盖。

**`part` 查不到就先 `like`。** 型号没有归一：`ESP32-S3` / `ESP32-S3-WROOM-1` / `ESP32-S3-WROOM-1-N16R8` 在库里是不同的值
（嘉立创那边的值多是完整订货型号，KiCad 这边常是系列名）。阻容感的值归一过（`100n` `100nF` `0.1uF` 都归到 `100nF`），型号没有。

**立创料号是作者设计时的选择，不是推荐。** `part AMS1117-3.3` 会印「69 仓用 C6186」，意思是这么多人这么选过；
现在有没有货、多少钱、是不是基础库，去 `jlc-basic-parts` 查。料号几乎都来自嘉立创专业版的板（71% 器件带料号，KiCad 只有 8%）。

## 读结果时必须守的三条

1. **`lift` 才是信号，次数不是。** 按次数排，任何芯片的邻居都是 `100nF` 和 `10kΩ` —— 它们跟什么都共现。`lift > 1` 才表示「真的经常一起用」。
2. **样本少于 5 个仓的结论不要当结论。** 输出里会标「样本太少，当线索不当结论」。共现按**不同仓**去重（同一项目的多块改版板只算一次），器件集合完全相同的仓也只算一次（实见一个学生项目被 7 个账号各传一份），但小样本的 lift 仍然会虚高，改了一两个件的近似拷贝也还在。
3. **许可必须跟着走。** 每个参考板都印许可；印「许可未声明」的**不要默认可以抄**。拿开源设计做参考和拿它的文件做衍生是两件事。

## 用途标签

每块板有一个用途标签，由 **Spark 上的本地模型**（vLLM · Qwen3.8-Flash-Next）从固定 13 类里选：键盘/输入设备、开发板/核心板、电源/充电、传感器/采集、无线/通信、音频、电机/驱动、显示/灯光、仪器/调试工具、机器人/无人机、转接/扩展板、其他、不确定。

**标签是模型的判断，不是作者声明。** 信息不够时模型会选「不确定」，这是设计好的，不是故障。

**全部板按「分类的是这一块板」的提示词重标过**（2026-09-25，KiCad 5408 块；嘉立创专业版 789 块直接用这版标）。
验收（`scripts/pcbdb/check_labels.py`，只读），前一列是最初那版提示词：

| 检查 | 最初 | 现在 |
|---|---|---|
| KiCad 板覆盖 | 5408/5408 | 5408/5408，模型回了表外或调用失败 0 |
| 仓名/描述含键盘关键词的 458 块 | 94.5% | 92.8% |
| 含音频关键词的 471 块 | 77.3% | 73.7%（其中 18 块是音频仓里的电源板，被挪去电源，算对） |
| 含灯光/显示关键词的 159 块 | 76.1% | 74.2% |
| 含电源关键词的 55 块 | 69.1% | 69.1%（剩下的多是「带充电的开发板」，本来就该标开发板） |
| **板文件名像电源的 163 块** | 62.6% | **79.1%** |
| 多板仓里全部板同一标签 | 70.9% | 60.7%（按板分类后同仓不同板本来就会分开，这是噪声旁证不是准确率） |
| 全量重标变了的 | — | 881 / 5408 |
| 抽 30 条非电源的变化人工看 | — | 约 12 条变好（背板/IO 卡→转接、编码器板→传感器）、13 条持平、**4–5 条变差** |

变差的都是同一个模式：板上信息少时模型不再借仓库主题——Eurorack 模块标成「其他」、stompbox 标成「不确定」、
FPGA 合成器标成开发板。**所以音频类要连仓一起看**：`boards synth` / `boards eurorack` 比 `kind 音频` 全。

为什么改提示词：最初的错是板级的——仓库主题压过了板本身，键盘仓里的 BMS 板、合成器仓里的电源模块都跟着仓库走
（见 `design/pcbdb/EXEC_PLAN_power_relabel.md`）。最初那版标签存在 `label_prev`，可以一条 SQL 回退。
（更早写的「电源关键词 59.1%」是关键词没加词边界算出来的，`keezyboost40`、「15 bucks」都被当成了电源板。）

**用法上的后果**：`kind 键盘` 可以直接信；`kind 电源` 基本可用，但只看板不看仓——同一个仓的其他板可能是别的用途。按用途找板时，`boards <关键词>` 往往比 `kind` 准——它查的是作者自己写的仓名和描述。

默认排除 `KiCad/` 官方组织的仓——那是 EDA 软件自己的测试夹具（bug 复现板），不是设计。

## 这个库答不了什么

- **不是全体开源硬件。** GitHub 搜索每条查询上限 1000，只拿得到 star 头部；KiCad 候选仓的规模在扩，数字看 `stats`。
- **很多仓根本没有板。** 最初盘的 3315 个 KiCad 候选里只有 1774 个真有 `.kicad_pcb`。
- **嘉立创只有 GitHub 上的那部分。** 立创开源广场本身没进库；GitHub 上 572 个仓有嘉立创源文件，并进来的是其中 348 个仓里能解析的
  789 块**专业版** PCB。标准版 json、2025 年后的 V3 格式、作者加了密码的 5 个工程都没进。
- **封装大多抄不过来。** 19633 种封装里只有 8% 被 ≥3 个仓用过，其余是各项目自带库，库名对你没用。
- **接法有板就有，但不是每颗器件都连上了。** 焊盘没接网络、或网络名是 `unconnected-*` 的不显示。
- **没有原理图语义。** 数据来自 PCB 文件（`.kicad_pcb` / 嘉立创 `.epcb`），有网络名和焊盘号，没有符号引脚功能名。`pad 12 → /I2C_SDA` 能看出来，「这个引脚在芯片手册里叫什么」看不出来。

## 数据怎么来的

`~/go/202609/vibe-hw/work/pcbdb/scripts/`：`collect.py`（搜索）→ `inventory.py`（树 API 盘点，不克隆）→
`mine.py`（定向取 `.kicad_pcb` + 解析器件）→ `mine_nets.py`（抽引脚网络）→
嘉立创专业版另一条线：`easyeda_inventory.py`（盘 GitHub 上的嘉立创文件）→ `mine_epro.py` + `parse_epro.py`（取回、内存里解析，不落盘）→
`merge_easyeda.py`（并进 pcb.db，板带 `kicad_version='easyeda-pro'`）。
两条线汇合后：`normalize.py`（阻容感值归一；专业版里没写值、只写了型号的阻容，按同料号的多数值补）→
`cooccur.py`（共现 + lift）→ `label.py`（Spark 本地模型打用途标签，`llm.py` 运行时读 openclaw 配置，不落凭据）。
解析器自检：`check_parse.py` / `check_nets.py` / `check_normalize.py` / `check_label.py` / `check_epro.py`，每个都带 `--fail-demo`；标签的外部验收是 `check_labels.py`。

`.eprj` 是嘉立创客户端的本地库，里面有 `users` 表（含 password 字段）。`parse_epro.py` 用 SQLite authorizer 在库层面只放行四张设计表。

KiCad 每个大版本都换格式，解析器认三种写法（KiCad 5 的 `module`、6/7 的 `fp_text`、8+ 的 `property`、9/10 的无编号 `net`）。
**踩过三次同样的坑**：按单一写法写的解析器会静默丢数据，所以跳过必须记原因——三次都是靠跳过原因的分布反常发现的。
