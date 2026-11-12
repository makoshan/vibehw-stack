// 把 KiCad 源板的设计规则写进嘉立创当前工程，丝印文字设 Kanit Medium，重铺全部铺铜，跑 DRC，存盘。
// 用法：LCEDA-Pro open --path <工程.eprj3> --headless true 取会话号 → LCEDA-Pro invoke --session <号> --ext-uuid eda --code "$(cat apply_rules.js)" → 等 5 s → session close。
// 规则值照 C08B 采集板 D5（.kicad_dru：铜间距 0.1、孔 0.3、外径 0.4、孔到孔 0.2、板边 0.3），别的板先改数。
const sleep=ms=>new Promise(r=>setTimeout(r,ms)); for(let i=0;i<60&&!((await eda.dmt_Pcb.getAllPcbsInfo())||[]).length;i++) await sleep(1000); const pcb=(await eda.dmt_Pcb.getAllPcbsInfo())[0]; await eda.dmt_EditorControl.openDocument(pcb.uuid); for(let i=0;i<30&&!(await eda.pcb_PrimitiveComponent.getAll()).length;i++) await sleep(1000); await sleep(2000);
const __run = async () => {
// 把 KiCad D5 的规则写进嘉立创当前规则（isSetDefault 保持原值，改成 false 会被拒），丝印文字设 Kanit，重铺全部铺铜，跑 DRC
const sleep = ms => new Promise(r => setTimeout(r, ms)), o = {};
const c = JSON.parse(JSON.stringify((await eda.pcb_Drc.getCurrentRuleConfiguration()).config));
const ss = c.Spacing['Safe Spacing'].copperThickness1oz, rows = ss.row, cols = ss.column;
ss.tables['1'].content.forEach((r, i) => r.forEach((_, j) => {
  if (/Text|Line|Slot/.test(rows[i] + cols[j])) {}                                   // 槽区/文字/线先跳过，保持默认（KiCad 没有对应规则）
  else if (rows[i] == 'Board Outline' || cols[j] == 'Board Outline') r[j] = 0.3;     // KiCad 铜到板边 0.3
  else if (rows[i] == 'Hole' || cols[j] == 'Hole') r[j] = 0.2;                       // 孔到铜 0.2
  else r[j] = 0.1; }));                                                              // 铜到铜 0.1（含铺铜）
const vf = c.Physics['Via Size'].viaSize.form;                                       // 字段名里 Inner = 孔、Outer = 外径（R3 的 DRC 文案「过孔内径 11.8mil」对应孔）
Object.assign(vf, {viaInnerdiameterMin: 0.3, viaOuterdiameterMin: 0.4, viaInnerdiameterDefault: 0.3, viaOuterdiameterDefault: 0.4});   // 照 D5：孔 ≥ 0.3、外径 ≥ 0.4
c.Spacing['Other Spacing'].otherClearance.form.hole2Hole = 0.2;
c.Physics.Track.copperThickness1oz.form.data['1'].minValue = 0.1;
o.rules = await eda.pcb_Drc.overwriteCurrentRuleConfiguration(c);
const rb = (await eda.pcb_Drc.getCurrentRuleConfiguration()).config; o.readback = {zone: rb.Spacing['Safe Spacing'].copperThickness1oz.tables['1'].content[7], via: rb.Physics['Via Size'].viaSize.form};
const strs = await eda.pcb_PrimitiveString.getAll(); for (const s of strs) { s.setState_FontFamily('Kanit Medium'); await s.done(); }
let nAttr = 0; for (const a of await eda.pcb_PrimitiveAttribute.getAll()) if (a.getState_ValueVisible && a.getState_ValueVisible() && [3, 4].includes(a.getState_Layer())) { a.setState_FontFamily('Kanit Medium'); await a.done(); nAttr++; }
o.font = [strs.length, nAttr];
for (const p of await eda.pcb_PrimitivePour.getAll()) await p.rebuildCopperRegion();
await sleep(3000);
o.ratlines = (await eda.pcb_Document.getRatlines() || {}).total;
const cnt = {}, ex = {}; const walk = x => { if (Array.isArray(x)) x.forEach(walk); else if (x && typeof x == 'object') { if (x.errorObjType && x.obj1) { const k = x.errorType + '|' + x.errorObjType; cnt[k] = (cnt[k] || 0) + 1; (ex[k] = ex[k] || []).length < 3 && ex[k].push([x.obj1.suffix, x.obj2 && x.obj2.suffix, x.explanation && x.explanation.param && x.explanation.param.minDistance, x.explanation && x.explanation.param && x.explanation.param.shouldBe]); } else Object.values(x).forEach(walk); } };
walk(await eda.pcb_Drc.check(true, false, true)); o.drc = cnt; o.ex = ex;
return o;

};
const R = await __run(); R.saved = await eda.pcb_Document.save(); return R;
