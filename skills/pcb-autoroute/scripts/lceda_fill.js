// 嘉立创EDA 内置布线「逐级放宽间距补网」。用法（会话由 lceda_cli.sh 的 lc_open 打开，务必是副本）：
//   "$L" invoke --session $S --ext-uuid eda --timeout 1800000 \
//     --args '{"nets":["NET_A","NET_B"],"steps":[0.15,0.12,0.1]}' --code "$(cat lceda_fill.js)"
// 做法：先拆掉 nets 的线/孔（演示用；补真实断网时删掉 strip(N) 那行）→ 每级把 Safe Spacing 表前 8 行
// 小于 0.15 mm 的铜间距临时放到该级值，只补仍断开或有间距错的网（ignoreNets GND，keep 已有线，偏速度引擎）
// → 写回原规则 → 重铺铜 → DRC；有间距错的网删掉线孔进下一级；最后一级仍不行就停，不无限重试。
// 为什么放大铜间距：内置布线不读「孔到铜」那一行（改 0.25 布出几何不变），只能整体留余量。
// 返回：每级耗时/断开网/间距错网、各网线孔数、其它网逐段差异（应为空）、最终 DRC 明细、规则是否逐字恢复。
// 通用补线：拆掉 A.nets → 按 A.steps 逐级放宽铜间距，每级只补仍断开或有间距错的网 → 每级后恢复原规则、重铺铜、DRC
// 有间距错的网：删掉它的线/孔，下一级重补；最后一级后仍有错就停，不无限重试
const A = __CLI__.args, N = A.nets, sleep = ms => new Promise(r => setTimeout(r, ms));
const r = { steps: [] }, T0 = Date.now();
const snap = async () => { const by = {}; for (const l of await eda.pcb_PrimitiveLine.getAll()) if (!N.includes(l.net)) (by[l.net] = by[l.net] || []).push([l.layer, l.startX, l.startY, l.endX, l.endY, l.lineWidth].map(x => Math.round(x * 100) / 100).join(",")); for (const v of await eda.pcb_PrimitiveVia.getAll()) if (!N.includes(v.net)) (by[v.net] = by[v.net] || []).push("via," + Math.round(v.x * 100) / 100 + "," + Math.round(v.y * 100) / 100); return by; };
const drc = async () => { const out = []; const walk = o => { if (Array.isArray(o)) o.forEach(walk); else if (o && typeof o == "object") { if (o.errorObjType && o.obj1) out.push({ t: o.errorObjType, a: o.obj1.suffix || "", b: (o.obj2 && o.obj2.suffix) || "", d: o.explanation?.param?.minDistance }); else Object.values(o).forEach(walk); } }; walk(await eda.pcb_Drc.check(true, false, true)); return out; };
const netOf = s => (s.match(/^\(([^)]*)\)/) || [])[1];
const repour = async () => { for (const p of await eda.pcb_PrimitivePour.getAll()) if (p.rebuildCopperRegion) await p.rebuildCopperRegion(); await sleep(3000); };
const strip = async nets => { await eda.pcb_PrimitiveLine.delete((await eda.pcb_PrimitiveLine.getAll()).filter(l => nets.includes(l.net)).map(l => l.primitiveId)); await eda.pcb_PrimitiveVia.delete((await eda.pcb_PrimitiveVia.getAll()).filter(v => nets.includes(v.net)).map(v => v.primitiveId)); };
const before = await snap();
const boardNets = new Set(["GND", ...N, ...(await eda.pcb_PrimitiveLine.getAll()).map(l => l.net).filter(Boolean)]);
await strip(N);
const orig = await eda.pcb_Drc.getCurrentRuleConfiguration(), origS = JSON.stringify(orig.config);
let todo = [...N];
for (const cu of A.steps) {
  if (!todo.length) break;
  const c = JSON.parse(origS), t = c.Spacing["Safe Spacing"].copperThickness1oz.tables["1"].content;
  for (const row of t.slice(0, 8)) for (let i = 0; i < row.length; i++) if (row[i] < 0.15) row[i] = Math.max(row[i], cu);
  // 可选 A.viaCu：只把过孔那一行/列（第 5 行、第 5 列）再抬高。布线器不读孔距行，靠过孔铜间距间接让出孔距
  // 估算：需要的 viaCu ≈ 孔距规则 −（过孔外径−孔径）/2；C08A 0.45/0.30 孔、孔距 0.20 → 0.13（11-02 实测 V4_IN7P 由撞孔变 0 错）
  if (A.viaCu) for (let j = 5; j < 8; j++) for (let i = 0; i < t[j].length; i++) if (j == 5 || i == 5) t[j][i] = Math.max(t[j][i], A.viaCu);
  await eda.pcb_Drc.overwriteCurrentRuleConfiguration(c);
  const ignore = [...boardNets].filter(x => !todo.includes(x));
  const t1 = Date.now(), job = await eda.pcb_Document.startAutoRouting({ RoutingNets: todo, existingPrimitiveMode: "keep", optimization: 0, cornerStyle: 0, ignoreNets: ignore });
  let st; for (let i = 0; i < 300; i++) { st = await eda.pcb_Document.getAutoJobStatus(job.jobId); if (!st || ["completed", "canceled", "failed"].includes(st.state)) break; await sleep(3000); }
  if (st && st.state == "running") { await eda.pcb_Document.cancelAutoJob(job.jobId); await sleep(3000); }
  await eda.pcb_Drc.overwriteCurrentRuleConfiguration(JSON.parse(origS));
  await repour();
  const e = await drc();
  const open = new Set(e.filter(x => x.t == "SMD Pad" || x.t == "TH Pad").map(x => netOf(x.a)).filter(n => N.includes(n)));
  const bad = new Set(e.filter(x => !(x.t == "SMD Pad" || x.t == "TH Pad")).flatMap(x => [netOf(x.a), netOf(x.b)]).filter(n => N.includes(n)));
  r.steps.push({ cu, routed: todo.length, state: st && st.state, s: Math.round((Date.now() - t1) / 1000), open: [...open], clearanceBad: [...bad], drc: e.length, other: e.filter(x => !N.includes(netOf(x.a)) && !N.includes(netOf(x.b))).length });
  if (bad.size) await strip([...bad]);
  todo = [...new Set([...open, ...bad])];
}
r.rulesRestored = JSON.stringify((await eda.pcb_Drc.getCurrentRuleConfiguration()).config) === origS;
const ls = await eda.pcb_PrimitiveLine.getAll(), vs = await eda.pcb_PrimitiveVia.getAll();
r.nets = Object.fromEntries(N.map(n => [n, ls.filter(l => l.net == n).length + "/" + vs.filter(v => v.net == n).length]));
const after = await snap(); r.otherDiff = {};
for (const k of new Set([...Object.keys(before), ...Object.keys(after)])) { const a = new Set(before[k] || []), b = new Set(after[k] || []); const rm = [...a].filter(x => !b.has(x)).length, ad = [...b].filter(x => !a.has(x)).length; if (rm || ad) r.otherDiff[k] = { removed: rm, added: ad }; }
r.finalDrc = (await drc()).map(x => [x.t, x.a, x.b, x.d].join(" | "));
r.totalS = Math.round((Date.now() - T0) / 1000);
return r;
