# iterxAI 学来的三件事

（2026-11-10 从 SKILL.md 原文挪来，只改了相对链接）

1. **布线前做"可行性探针"，布线后做"工艺诊断探针"**，两者都是独立产物、不混进布线结果。我们的对应：布局脚本里的 courtyard/板框 assert = 前探针；`verify_board.py` + 下面的工艺表 = 后探针。
2. **每条走线带 `source` 标签**（`autorouter` / `autorouter-pth-endpoint-fanout-repair` / `autorouter-conflict-layer-jumper-repair` / `autorouter-conflict-tail-layer-jumper-repair` / `autorouter-layer-transition`）：修补是可审计的独立 pass，不是手改。我们的对应：`finish_vNN.py` 里删残线、加宽、退回各是一段，并把退回的段 uuid 写进 `review-narrow-escapes-vNN.json`。
3. **求解器失败就停，不降级**（"routing stopped without TS fallback"；SES 空/缺/解析失败各有 reason 码）。别在布线失败时偷偷放宽 clearance 让它过——我们 v0.5 的教训一致："没有降低规则"。

它的板模型字段可直接借来当规则清单：`designRules{minTraceWidth, minClearance, minHoleClearance, boardEdgeClearance, minDrillDiameter, minAnnularRing, componentClearance, routeAngle}`；`componentSideMode: preserve | double-auto | single-top`（single-top 时 SES 出现底层线或过孔直接报错）。
