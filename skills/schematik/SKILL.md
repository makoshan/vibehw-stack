---
name: schematik
version: 0.1.0
description: >-
  用 schematik.io（"Cursor for Hardware"）的公开面当资料源：6021 个创客元件的接口/电源摘要、
  采购来源与价格、少量 STEP/STL，以及约 450 个带完整接线图+固件的参考整机。含 scripts/schematik.py
  （search / part / cad / project，无需登录、无 API key）和实测的数据可信度边界。
  Use when: 选模块或找替代料、要某个模块的电源/接口摘要、想要它的 3D 模型、
  找"别人这块板子怎么接的"参考接线与可编译固件、或有人让你"学/用 schematik"。
  不要拿它当完整引脚表来源（实测 Arduino Uno 页只列 5V/3.3V/GND）。
---

# schematik.io 公开面怎么用

一句话：**它的公开面能给你「参考整机 + 选型/采购元数据 + 少量 CAD」，给不了完整引脚表。**
完整 pin assignment / 生成方案 / 浏览器烧录都在登录后的 `/app` 里（robots 禁抓，本技能不碰）。

## 1. 三个入口（都走 `scripts/schematik.py`，Python 3 标准库，无密钥）

```sh
S=~/.claude/skills/schematik/scripts/schematik.py
python3 $S search bmi270                      # 按 slug 匹配（本地缓存 sitemap，不打服务器）
python3 $S search esp32 oled --kind projects  # 找参考整机
python3 $S part studio-xiao-nrf52840-sense    # 摘要 + 引脚（可能残缺）+ CAD 链接
python3 $S cad --step-only -o ./cad slug1 slug2 …  # 批量下 STEP/STL
python3 $S project esp32-mp3-walkman-player-c5d5   # 元件 + 接线边 + 完整固件
python3 $S selfcheck                          # 解析器还对不对（页面改版会红）
```

原理：页面是 Next.js RSC，数据就摊在 `self.__next_f.push` 的 flight chunk 里，拼起来直接 `json.loads`。
站点改版 → `selfcheck` 先红，别等结论错了才发现。

## 2. 数据可信度（2026-09-16 随机抽 20 个 part 页实测）

| 字段 | 实测覆盖 | 怎么用 |
|---|---|---|
| 引脚 | 11/20 有，且**只有电源/接口那几根**（最多 8 根） | 只当「这模块走 I2C 还是 SPI、吃 3.3 还是 5V」的速查；**引脚号去原厂 datasheet** |
| CAD | **10/120 有**（另抽 120 个实测） | 有就是白捡，没有别指望；它不自己建模，见下 |
| 价格/库存 | 少数有（Adafruit/Seeed/Arduino 类目多，DFRobot 类目常缺） | 当量级参考，下单价以 `sourceUrl` 原厂页为准 |

必须核的坑（都是实际撞到的）：

- **公开页的引脚是摘要不是引脚表。** `arduino-uno` 只列 `5V / 3.3V / GND`，D0–D13 一根没有；
  `studio-xiao-nrf52840-sense` 只列 Grove 的 `GND/VCC/SDA/SCL` 四根，14 脚的板子当 4 脚模块收录。
- 页面自己标了 **"Logical pinout · physical placement not verified"**，也就是**顺序和物理位置不保证**。
- **分类会错**：XIAO nRF52840 Sense 被归到 `wireless-comms / bluetooth-modules`，不是 boards ——
  按 `search` 的 slug 找，别信 group。
- **project 页的规格行会错**：同一块 2000 mAh 电池在 BOM 里印成 "3.7 V, 85,000 mAh"。数字都回原厂页核。
- **CAD 不是他们建的，是镜像三个开源源**：`adafruit/Adafruit_CAD_Parts`（472 个目录，Adafruit 自家模块，
  板级真模型）、KiCad `packages3D`（裸芯片封装）、`earthtojake/step.parts`。所以覆盖率 = 上游覆盖率：
  **Adafruit 生态基本都有，Seeed/DFRobot/杂牌基本没有**（实测 XIAO ESP32S3、SSD1306 OLED 都没有）。
  要 Adafruit 全量就直接 clone 上游仓库，别一个个页面翻。
- **元件卡上那些好看的线稿图是 AI 生成的 PNG**（`parts/catalogue-visuals/…/generated-visual-pump/…png`），
  **不是模型**。看到图 ≠ 有 STEP。
- **CAD 按 URL 判质量**：`parts-cad/kicad/packages3d/…` 是 KiCad 封装库里的**裸芯片本体**（BMI270 就是个
  LGA-14 小方块，不是那块 breakout 板）；`step.parts/catalog/…`、厂商 ecad 的才是**整个模块/产品外形**。
  拿去做外壳配合前先看它到底是哪种，并且按 `pcb-enclosure-fit` 的规矩验路径而不只验体积。

## 3. 值得抄的是它的输出契约

一个 project 页固定六段：概述 → 接线图 → BOM（带电气规格和"要不要库"）→ 装配步骤 → 连接复核表 → 固件。
其中三条对我们自己的硬件文档直接有用：

1. **连接复核表按功能分组**（POWER / GROUND / I2C / GPIO），左右两列是**实例名**（`solar_panel_1.V+ → solar_charger_1.VIN`），
   不是"把传感器接到板子上"。我们的 `check_interfaces.py` 已经在做同一件事，口径可以对齐。
2. **每个装配步骤 = 动作 + 一句为什么 + 若干条"不要…"**（"不要把 5V 接到 3V3 脚"）。安全项写成禁止句，不写在注释里。
3. **固件里引脚一律具名 `constexpr`**（`I2S_BCLK_PIN`），页面的 pin 表和代码是同一份来源。

## 4. 和已有技能的分工

| 要做 | 用 |
|---|---|
| 离线选型 / 生成方案 | `vibe-hardware-idea`（自带 824 条离线库，先用它） |
| 这个模块还有什么替代、别人怎么接的、有没有现成固件 | 本技能（联网、6021 件 / ~450 整机） |
| 贴片选料、C 号、换料费 | `jlc-basic-parts` |
| 外壳/载具配合、接口路径 | `pcb-enclosure-fit` + `cad`（本技能只负责把 STEP 拿下来） |
| 原理图/PCB 实际落地 | `easyeda-api` |

## 5. 不做的事

- 不碰 `/app` 和 `/api`（robots.txt `Disallow`），不注册账号，不登录。
- 不批量爬 6021 个 part 页：要什么取什么；`search` 只读本地缓存的 sitemap。
- 不把它的 BOM 数字直接抄进设计文件——过一遍 `sourceUrl` 原厂页再落盘。

## 6. 授权（2026-09-16 核过，非法律意见）

站点**没有 ToS、没有 license 页**（`/terms` `/legal` `/license` 全 404，只有 `/privacy`），约束只有 robots.txt。
所以按数据来源分别判：

| 数据 | 许可 | 结论 |
|---|---|---|
| `Adafruit_CAD_Parts` 的 STEP/STL | **MIT** | 随便用，含商用；再分发时带上版权声明 |
| `step.parts` 的 STEP | **MIT** | 同上 |
| KiCad `packages3D` 的 STEP | CC-BY-SA 4.0 **+ 例外** | 「用在电子设计里」明确放弃 article 3：**设计文件不必开源、不必署名**；但**整包/多个模型当集合再分发**就要 CC-BY-SA + 署名 |
| 引脚、电压、尺寸、料号、价格 | 事实，不受版权 | 随便抄，但照样回原厂页核 |
| 元件描述文字 / 分类 / 项目说明 / 固件 | 无声明 → 默认版权保留 | 自己参考、照着重写可以；**别原样 commit 进公开仓库，别整库爬下来做成自己的数据集** |

KiCad 的 STEP 文件头自己写着：*"DO NOT RELY UPON ANY INFORMATION FOUND HERE WITHOUT INDEPENDENT VERIFICATION."*
