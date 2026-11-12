# 嘉立创EDA 专业版官方命令行（V4.1.60+，要半离线/全离线）的小工具。用法：. lceda_cli.sh
#   S=$(lc_open 副本.eprj2)   # 隐藏窗口打开工程，等到 PCB 真正就绪，打印会话号
#   lc $S 'return (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length'
#   lc_close $S               # 只关自己开的会话
# 只在副本上跑；不传 --path 的 open 会开可见窗口。
L="${LCEDA_PRO:-/Applications/嘉立创EDA(专业版).app/Contents/MacOS/LCEDA-Pro}"
lc_open() {
  local S; S=$("$L" open --path "$1" --headless true | /usr/bin/python3 -c 'import sys,json;print(json.load(sys.stdin)["value"]["sessionId"])') || return 1
  # 刚 open 时工程信息先出、PCB 列表后出；要等到拿到 PCB uuid 且图元列表非空
  "$L" invoke --session $S --ext-uuid eda --timeout 120000 --code 'for(let i=0;i<30;i++){const p=await eda.dmt_Project.getCurrentProjectInfo(); const pcb=p&&p.data.flatMap(d=>d.itemType=="PCB"?[d]:(d.pcb&&d.pcb.uuid?[d.pcb]:[]))[0]; if(pcb){ await eda.dmt_EditorControl.openDocument(pcb.uuid); await new Promise(r=>setTimeout(r,3000)); for(let j=0;j<20;j++){if(await eda.pcb_PrimitiveLine.getAllPrimitiveId()) return pcb.uuid; await new Promise(r=>setTimeout(r,1000));} throw new Error("pcb not open");} await new Promise(r=>setTimeout(r,1000));} throw new Error("not ready")' >/dev/null || return 1
  echo $S
}
lc() { "$L" invoke --session "$1" --ext-uuid eda --timeout 600000 --code "$2"; echo; }
lc_close() { "$L" session close --session "$1" --destroy >/dev/null; }
