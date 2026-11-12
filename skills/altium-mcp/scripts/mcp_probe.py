"""MCP stdio 探针：initialize → tools/list → tool_invoke(app_ping)。
用法: python3 mcp_probe.py <注册里的完整命令...>   例: python3 mcp_probe.py /usr/bin/ssh -T thursday-win "set EDA_AGENT_TOOLSET=minimal&& ...eda-agent.exe --headless"
进程一启动就退出会打印 EOF（--toolset 在 Windows 上就是这样）。minimal 以外的工具集，tool_invoke 也在。"""
import json, subprocess, sys, time
cmd = sys.argv[1:]
p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
def send(m): p.stdin.write(json.dumps(m)+"\n"); p.stdin.flush()
def recv():
    while True:
        line = p.stdout.readline()
        if not line: raise SystemExit("EOF")
        try: return json.loads(line)
        except json.JSONDecodeError: print("NON-JSON:", line[:80].rstrip())
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}})
print("init:", recv().get("result",{}).get("serverInfo"))
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/list"})
tools = recv()["result"]["tools"]; print("tools:", len(tools), [t["name"] for t in tools][:5])
send({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"tool_invoke","arguments":{"name":"app_ping","arguments":{}}}})
r = recv(); print("tool_invoke app_ping:", json.dumps(r.get("result",r))[:300])
p.stdin.close(); p.terminate()
