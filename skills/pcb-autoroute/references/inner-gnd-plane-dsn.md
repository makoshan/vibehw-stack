# 4 层板内层地平面进 Freerouting 1.9.0（2026-10-26，C08A/B/C 沙盒对比）

情形：4 层（F / In1 GND 平面 / In2 / B），In1 在 KiCad 里是 power 类型、整层 GND 铺铜，用 1.9.0 自动布线。

1. **KiCad 导出 DSN 会把 power 类型的 In1 整层丢掉。** 引用 In1 的禁布区留在 DSN 里，1.9.0 会 0% CPU 卡死。做法：临时把 In1 改成 signal 再导出，导出后把 DSN 里 In1 的 `(type signal)` 改回 `(type power)`，这样会得到 `(plane GND (polygon In1.Cu ...))`。
2. **临时板要带同名 `.kicad_pro/.kicad_dru`**，否则 DSN 的间距退回 0.2 mm 默认值。
3. **DSN 间距要盖住孔到铜。** 0.30/0.45 过孔的环宽是 0.075，项目要求孔到铜 0.20 时，DSN clearance 设成 125 µm，不能用线到线的 100。
4. **1.9.0 不会从贴片焊盘自己打孔下到 DSN 平面**，GND 会大面积未连。布线前先做 GND 扇出：盘外 0.48–0.96 mm 打 0.30/0.45 孔，加 0.15 短线并锁定。开窗板的内边也要按板边距离检查。
5. **1.9.0 只在最后写 SES。** 时间到了会被杀，什么都不留。用 `-mp 50 -mt 0`（关优化）再加外层 `perl -e 'alarm N; exec @ARGV'`。
6. **KiCad 10 的 `ImportSpecctraSES` 遇到 SES 里有 `(placement` 段会静默返回 False。** 删掉 placement 到 routes 之间的内容再导入。导入后必须跑 `ZONE_FILLER(b).Fill(b.Zones())`，否则旧铺铜和新过孔互相报 clearance / hole_clearance。

证据：沙盒 /tmp/c08-abc-autoroute-test（未入库）。

| 板 | 未连（旧 DSN） | 未连（v2，In1 平面） | 未连（v2 + 扇出） | 布线后 error |
|---|---|---|---|---|
| B | 59 | 56 | 42 | 233 → 11（全是板边） |
| C | — | 103 | 92 | 2（全是板边） |

C08A（292 件）90 分钟没布完。

置信度：低，单次。
