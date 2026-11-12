# TraceMaker：KiCad 原生增量布线（v0.12.1）

（2026-11-10 从 SKILL.md 原文挪来，只改了相对链接）

适合尝试现有 KiCad 板的未连网；默认保留已有铜，`--reroute` 会移除未锁定走线和过孔，只有明确授权独立重布时才用。先复制 `.kicad_pcb`、同名 `.kicad_pro` 和存在时的 `.kicad_dru`；输入、输出分别配同名工程与规则，禁止覆盖源板。

```bash
tracemaker route input.kicad_pcb -o routed.kicad_pcb --time 120 --threads 4 --variants 4 --no-gpu --no-kb --json result.json --report report.md
# 给 routed.kicad_pcb 配套复制 routed.kicad_pro 和存在时的 routed.kicad_dru 后：
kicad-cli pcb drc --refill-zones --format json -o input-drc.json input.kicad_pcb
kicad-cli pcb drc --refill-zones --format json -o routed-drc.json routed.kicad_pcb
```

- 核 `report.md` 的 Rules / `result.json` 的 `rules.not_read`：未读规则不能视为布线器已遵守。C08A 实测 `Parent` 条件未被读取；保留原规则，以 KiCad 重铺铜 DRC 为准；针对漏读规则在独立副本做必触发的反例，确认 KiCad 报出对应违规及规则数值，否则该规则标未验证。独立审查旧网络连通、关键网及工艺。
- 比较输入与输出的 KiCad 错误、警告、未连接数量及具体连接；路由器自报 `routed` 包含与 KiCad 口径不同的候选连接，不能代替实际断点减少。限时结果依机器而变；无改善须做已知可布通的阳性对照。
- 实测 C08A 22→22 未连；另一个 C08D 软板草稿 20→12，但前后均有 40 条铜到板边错误。两次都不能放行制造。证据：原力手环仓 `evidence/2026-10-30-tracemaker-trial/`。
- 官方 v0.12.1 预编译包仅 Linux x86_64；可在 Ubuntu 24.04 WSL 跑 CPU。用指定版本并记录运行参数，不把本次结果推广为其他板的效果。
