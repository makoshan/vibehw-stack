#!/usr/bin/env python3
"""从 Mac 经 SSH 驱动 thursday-win 上的 Altium Designer MCP 完成查板、自动规划布线 (route_plan) 与落线 (pcb_place_tracks)。

前提：在 thursday-win 的 Altium Designer 中已执行一次：
  File » Run Script... » Altium_API.PrjScr » Dispatcher.pas » StartMCPServer

用法：
  python3 altium_mcp_route.py status --board "%USERPROFILE%\\...\\board.PcbDoc"
  python3 altium_mcp_route.py route  --board "%USERPROFILE%\\...\\board.PcbDoc" --net GND
"""
import argparse
import json
import os
import subprocess
import sys


class AltiumMCPClient:
    def __init__(self, host: str = os.environ.get("MSTACK_WIN_HOST", os.environ.get("WIN_HOST", "thursday-win"))):
        cmd = [
            "/usr/bin/ssh",
            "-T",
            "-o",
            "ConnectTimeout=5",
            host,
            r"set EDA_AGENT_TOOLSET=minimal&& %USERPROFILE%\codex-tools\eda-agent\.venv\Scripts\eda-agent.exe --headless",
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._id = 0
        self._call("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "altium-mcp-route", "version": "1.0"},
        })
        self._notify("notifications/initialized")

    def _notify(self, method: str, params: dict | None = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _call(self, method: str, params: dict) -> dict:
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("EOF from eda-agent stdio stream")
            try:
                resp = json.loads(line)
                if resp.get("id") == self._id:
                    return resp
            except json.JSONDecodeError:
                continue

    def invoke(self, tool_name: str, arguments: dict | None = None) -> dict:
        resp = self._call("tools/call", {
            "name": "tool_invoke",
            "arguments": {"name": tool_name, "arguments": arguments or {}},
        })
        content = resp.get("result", {}).get("content", [])
        if content and content[0].get("type") == "text":
            return json.loads(content[0]["text"])
        return resp

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Altium MCP board status & routing helper")
    ap.add_argument("action", choices=["ping", "status", "route"], help="Action to run")
    ap.add_argument("--board", help="Windows path to .PcbDoc")
    ap.add_argument("--net", default="", help="Optional net filter for route_plan")
    args = ap.parse_args()

    cli = AltiumMCPClient()
    try:
        ping = cli.invoke("app_ping")
        print("app_ping:", json.dumps(ping.get("result", ping), ensure_ascii=False))
        if not ping.get("result", {}).get("success"):
            print("提示: Altium 内 StartMCPServer 未激活，请先在 Altium 运行 Dispatcher.pas > StartMCPServer", file=sys.stderr)
            return 2

        if args.action == "ping":
            return 0

        if args.board:
            cli.invoke("proj_open", {"project_path": args.board})
            cli.invoke("pcb_focus_board", {"board_path": args.board})

        stats = cli.invoke("pcb_get_board_statistics")
        unrouted = cli.invoke("pcb_get_unrouted_nets")
        print("board_statistics:", json.dumps(stats.get("result", stats), ensure_ascii=False))
        print("unrouted_nets:", json.dumps(unrouted.get("result", unrouted), ensure_ascii=False))

        if args.action == "route":
            plan_args = {"net": args.net} if args.net else {}
            plan = cli.invoke("route_plan", plan_args).get("result", {})
            print("route_plan:", json.dumps(plan, ensure_ascii=False)[:500])
            tracks_spec = plan.get("tracks_arg", "")
            if tracks_spec:
                placed = cli.invoke("pcb_place_tracks", {"tracks": tracks_spec})
                print("pcb_place_tracks:", json.dumps(placed.get("result", placed), ensure_ascii=False))
                after = cli.invoke("pcb_get_unrouted_nets")
                print("after_unrouted:", json.dumps(after.get("result", after), ensure_ascii=False))
        return 0
    finally:
        cli.close()


if __name__ == "__main__":
    raise SystemExit(main())
