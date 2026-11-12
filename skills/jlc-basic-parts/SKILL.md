---
name: jlc-basic-parts
description: >-
  设计阶段就按嘉立创 SMT 库选料：每个贴片件在原理图/design.json 里带 LCSC C 号，优先基础库（免换料费）、
  扩展库每种 ¥20、库外不选。含查库方法（jlc-smt.com/lcsc/basic、匹配页替换搜索、立创商城）、
  2026-09-16 实测过的常用件 C 号表、以及把 C 号写进 build_design.py 的约定。Use when: 选元件、定阻容值、
  换料号、算换料费、BOM 出现"封装不符/未匹配/库存不足"，或用户说"考虑嘉立创有什么料"。
  下单流程本身在 jlc-order。
---

# 按嘉立创库选料（设计时就做）

一句话：**料号在设计时定死（C 号），BOM 按 C 号匹配，就没有"封装不符""参数识别""库存 8 < 10"这些下单时才冒出来的事。**

## 规则

1. 每个实装贴片件在 design.json 里带 `lcsc`（C 号）和 `jlc_lib`（basic/ext）；`build_design.py` 里维护 `LCSC={ref: 'Cxxxx'}`、`EXT={...}`、`NOT_SMT={...}`，缺号直接 assert 失败。DNP 件可以没有。
2. 优先级：**基础库 > 扩展库 > 邮寄/库外**。经济型贴片只贴单面、只用库内料；一个扩展库料号 = ¥20 换料费/单，所以"为一颗 7.68k 多付 ¥20"这种就改成基础库有的值（7.5k）。
3. 阻容：只用基础库有的值和封装（0402/0603/0805 的 E24 常用值都有），电阻 1% 厚膜，电容 X5R/X7R；BOM 的 Footprint 写嘉立创封装名（0603 / 0805），不写 KiCad 名。
4. IC/连接器：定型号前先在库里搜，看**库存 ≥ 需求×1.5**（它按 5 片 + 损耗算"使用量"，14 颗的需求 8 颗库存就红）。
5. BOM 文件列：`Comment, Designator, Footprint, LCSC Part #, Quantity`；有 C 号的行嘉立创直接锁定，不再做文本解析。

## 查库的三个入口

| 入口 | 用法 | 得到 |
|---|---|---|
| `https://www.jlc-smt.com/lcsc/basic` | 基础库全表，按封装/类目/关键词筛 | 免换料费的料号池（要登录才看得全） |
| SMT 匹配页 → 某行"替换" → 搜索框 | 输 C 号或型号；左侧筛"元器件库类型=基础库" | 库存、单价、损耗数、最小贴装数、基础/扩展 |
| `https://www.lcsc.com/product-detail/Cxxxx.html` | 直接看某个 C 号 | 数据手册、封装名、参数 |

没有对外 API。要批量核对就把 BOM 传到 SMT 匹配页看一遍（PCB 单可先不审核）。

## 已核过的料号（2026-09-16，嘉立创匹配页实测）

| 用途 | 型号 / 值 | 封装 | C 号 | 库 |
|---|---|---|---|---|
| 电阻 1k / 10k / 22k / 100k ±1% | 0603 厚膜 | 0603 | C21190 / C25804 / C31850 / C25803 | 基础 |
| 电阻 7.5k ±1%（背光 RSET，替代 7.68k） | 0603 | 0603 | C23234（7.68k 是 C2930128 扩展） | 基础 |
| 电容 10nF / 100nF 50V | 0603 | 0603 | C57112 / C14663 | 基础 |
| 电容 1µF 50V | 0603 | 0603 | C15849 | 基础 |
| 电容 4.7µF / 10µF 25V | 0805（1.25 mm 高，屏下别用） | 0805 | C1779 / C15850 | 基础 |
| 电容 4.7µF 25V / 10µF 10V X5R（屏下用，0.9 mm 高） | 0603 | 0603 | C19666 / C19702 | 基础（载板 v0.18 选用，**匹配页未核**） |
| N-MOS AO3400A / P-MOS AO3401A | SOT-23 | SOT-23 | C20917 / C15127 | 基础 |
| 肖特基 SS14 | SMA | SMA(DO-214AC) | C2480 | 基础 |
| LDO 3.0V 500mA ME6211C30M5G-N（替代 TLV75530，库存只有 8） | SOT-23-5，脚序 IN/GND/EN/NC/OUT | SOT-23-5 | C403651 | 扩展 |
| 背光恒流 CAT4002ASD-GT3 | SC-70-6 | SC-70-6 | C890382 | 扩展 |
| 电平缓冲 SN74LVC245APWR | TSSOP-20 | TSSOP-20 | C7848 | 扩展 |
| FPC 座 FH12-12S-0.5SH(55)（0.3 mm 排线） | 0.5 mm 12P 下接翻盖 | SMD,P=0.5mm,卧贴 | C88360（库存 450） | 扩展 |
| 电池座 JST SM02B-GHS-TB | GH 1.25 mm 2P 卧贴 | SMD,P=1.25mm,卧贴 | C189893（库存 273） | 扩展 |
| SPI NOR P25Q32H-UXH-IR | USON-8 2×3 | USON-8 | C414068 | 扩展 |

库存和基础/扩展归属会变，下单当天在匹配页再看一眼。

## 选料时顺手看高度

屏/盖板底下的贴片按**封装最大高度**过一遍（0603 ≤0.9、0805 ≤1.25、SOT-23 1.2、SOD-323 1.0、SMA 2.3、JST GH 4.25、FH12 2.0 mm）：载板 v0.16 的 JST 电池座 4.25 mm 和两颗 SMA 都在屏底下，是渲染图才看出来的。`2.0/pcb/carrier/check_heights.py <board> <limit>` 按位置分"屏下/屏外"报超高件。

## 在 build_design.py 里的写法

```python
LCSC={'R1':'C21190', ...}            # 每个实装贴片件
EXT={'C88360','C189893',...}         # 当天是扩展库的 C 号
NOT_SMT={'U1','JX1','J3','SW1',...}  # 模块、通孔、焊线盘：手焊，不进 SMT BOM
for part in parts:
    if part.get('annotation') or part['ref'] in NOT_SMT or part['dnp']: continue
    assert part['ref'] in LCSC, ('every fitted SMT part needs an LCSC number', part['ref'])
    part['lcsc']=LCSC[part['ref']]; part['jlc_lib']='ext' if LCSC[part['ref']] in EXT else 'basic'
```
`export_fab.sh` 把 `lcsc` 带进 bom.csv；`export_jlc_smt.py` 出带 `LCSC Part #` 的 SMT BOM 并 assert 每行有号。

## BOM 规则与版本差异（脚本）

- `scripts/bom_rules.py <bom.csv>`：重复位号、未标注（`R?`）、缺封装/缺值、别的件有 LCSC/MPN 这件没有（DNP 除外）、同一 LCSC/MPN 配了不同封装（判错）、Quantity 与位号数不符。有错退出 1。
- `scripts/bom_diff.py <旧.csv> <新.csv>`：按位号列增、删、改了哪个字段，再列数量变了的料；Markdown 表贴进新包 README。无差异退出 0、有差异 1。**两版用同一种格式比**（KiCad 封装名和嘉立创封装名不同，混比会满表报改，脚本会警告）。
- 两个都认 KiCad 导出的 BOM 和嘉立创 SMT BOM（位号逗号连写、区间 `C3-5`；UTF-8/GBK/UTF-16）。借自 Backplane `kicad/bom.rs`；自检 `scripts/test_bom_tools.py`。2026-11-10 阳性对照：yuanli-band D20n `jlc_smt_bom.csv` 127 位号 0 错 0 警；D17→D20n 删 32（C384950）、改 16（C76922→C76924）。

## 执行器（2026-09-22 起，不再靠人工填）

C 号 → KiCad 符号 + 封装 + STEP 一条命令：`easyeda2kicad --full --lcsc_id=C14663 --output <项目>/lib/jlc`（pip install easyeda2kicad；
实测 C14663 出 jlc.kicad_sym / jlc.pretty/C0603.kicad_mod / jlc.3dshapes/*.step）。GitHub topic:kicad 头部的 JLC2KiCadLib 也能用，同样效果，只留一个。
vibe-hw 里封装成 `agents/jlc.mjs` 的 `lcscLib(dir, ids)`：结果写 `lib/jlc.json`，没装工具判 unverified 不判 pass。脚序仍要对原厂图纸核一遍。

## 来源

MAKO 载板 v0.16 第一次下单：18 行 BOM 全部匹配，但 TLV75530 库存不足、7.68k 是扩展库、每行弹"封装不符"——三件事都是设计时没带 C 号造成的。`2.0/pcb/carrier/build_design.py`（design.json 0.14）是落地样例。
