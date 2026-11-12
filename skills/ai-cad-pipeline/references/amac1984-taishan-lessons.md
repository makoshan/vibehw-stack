# aMac1984 × 立创泰山派 纯打印改造 —— 切片与交接踩坑（2026-09-05）

项目：`~/go/202608/赛博 AI/amac1984-taishan/`（README 有完整链路）。几何/配合的教训进了 `pcb-enclosure-fit`，
这里只记 **切片 → 打印机** 这一段，因为它和几何一样会静默出错。

## OrcaSlicer CLI（2.4.2，`$gcode` skill 驱动）

1. **机型预设默认冷板，PETG 上冷板被拒后静默回退成 PLA 默认值**：返回码 0、有 gcode，头里 `filament_type = PLA`、
   `nozzle_temperature = 200`、`textured_plate_temp = 45`。必须在工艺预设里写 `curr_bed_type = "Textured PEI Plate"`，
   切完看头里 `curr_bed_type / filament_type` 和正文的 `M190 S…` / `M109 S…`，不看返回码。
2. `--load-settings` / `--load-filaments` 吃的 JSON 必须是正规预设（`type / name / from / instantiation`），
   `from: User` 的文件**不会替你解析系统 `inherits` 链**——名字对了数值全是默认。
   用 `print/flatten_preset.py <machine|process|filament> <系统预设名> <out.json> [覆盖.json]` 沿 BBL 链压平再喂。
   机型用系统 JSON 即可（它在 vendor 目录里能自解析）；工艺和耗材压平。
3. `$gcode slice --execute` 有时只留 `<outputdir>/plate_1.gcode` 不落 `--output` 名——`validate` 前先确认文件存在、
   删旧文件再切（zsh `rm a/*` 无匹配会中止整条命令，旧文件就被当新结果）。
4. wrapper 的 `machine.motion_bounds_mm` 给开机/换料/结束 G-code 的行程：A1 mini 左擦嘴 X −13.5、右料屑口 X 187、
   后清料线 Y 185、Z −1.01 → `x [-14,190] y [-5,186] z [-2,180]`。`validate` 报这些不是模型出界。
5. 模型在 STL 里的位置就是床上的位置（CLI 不自动居中）：切前把 STL 挪到 (90, 90)。
6. `--export-3mf` 出的切片项目 `printer_model_id` 为空；A1 mini 是 **N2S**（从 Mako 自己的 Bambu Studio 3mf 里对出来的），
   打补丁写进 `Metadata/slice_info.config` 再给机器，否则可能判不兼容。

## Bambu LAN（`$bambu-labs` skill）

- 本机 Bambu Studio 常驻会占 UDP 2021，SSDP 监听 `Address already in use`，主动 M-SEARCH 也没回——
  改探 TCP **8883（MQTT）+ 990（FTPS）** 同时开的主机，一台就是它。
- 序列号从 8883 的 TLS 证书 CN 取（`bambu_lan_print.py serial --host …`），不用问人。
- LAN access code 只能从触摸屏读（Settings → 网络 → LAN Only / Developer Mode），不从 Bambu Studio 配置里抠。
- 用户只说"可以"不算"开始打印"的授权：把 dry-run 计划、gcode、3mf 都备好，两条路（Bambu Studio 点打印 / LAN 直发）让他选。

## 环境

- `~/.claude/skills/cad/.venv` 是 uv 管的，**没有 pip**：`uv pip install --python <venv>/bin/python …`。
- Homebrew 装大件（OrcaSlicer 246 MB、KiCad 2.4 GB）走 Surge `http://127.0.0.1:6152` 快 40 倍。
