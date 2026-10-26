#!/usr/bin/env python3
"""stdio ↔ HTTP 的 MCP 桥接,供只支持 stdio 的 Agent 客户端(Claude Desktop、部分 IDE)使用。

仅依赖标准库。环境变量:
  SV_URL    Server Vault 地址,默认 http://127.0.0.1:8765
  SV_TOKEN  Agent Token(在 Web 界面「Agent 接入」创建)
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("SV_URL", "http://127.0.0.1:8765").rstrip("/") + "/mcp"
TOKEN = os.environ.get("SV_TOKEN", "")


def forward(line: str):
    req = urllib.request.Request(URL, data=line.encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            body = r.read().decode()
            return body or None
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            msg = json.loads(body)["error"]["message"]
        except Exception:
            msg = body or str(e)
        return error_for(line, f"Server Vault 返回 {e.code}: {msg}")
    except Exception as e:
        return error_for(line, f"无法连接 Server Vault ({URL}): {e}")


def error_for(line, message):
    try:
        rid = json.loads(line).get("id")
    except Exception:
        rid = None
    if rid is None:
        return None
    return json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": message}}, ensure_ascii=False)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        out = forward(line)
        if out:
            sys.stdout.write(out.strip() + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
