"""MCP (Model Context Protocol) 服务端:Streamable HTTP 的无状态 JSON 模式,挂在 POST /mcp。"""
import json

from . import agent
from .config import VERSION
from .services import ServiceError
from .vault import vault

PROTOCOL_VERSIONS = ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"]

INSTRUCTIONS = (
    "Server Vault:本地服务器凭据与运维中心。先用 list_servers 查看可用服务器(可按分组/状态/关键字过滤),"
    "server 参数可传 id、名称或主机地址。exec_command 在服务器上执行 shell 命令(危险命令会被策略拦截,"
    "需要 root 权限时设 sudo=true)。所有操作都会记录审计日志。"
)

_SERVER = {"type": "string", "description": "服务器 id、名称或主机地址"}
_SERVERS = {"type": "array", "items": {"type": "string"}, "description": "服务器列表(id/名称/主机),留空表示全部"}

TOOLS = [
    {"name": "whoami", "scope": None, "description": "查看当前 Token 的权限范围与可访问服务器数量",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_servers", "scope": "read", "description": "列出服务器(名称、地址、分组、位置、状态、系统、型号、配置概要)",
     "inputSchema": {"type": "object", "properties": {
         "group": {"type": "string", "description": "按分组过滤"},
         "status": {"type": "string", "enum": ["online", "offline", "auth_failed", "error", "unknown", "hostkey_mismatch"]},
         "keyword": {"type": "string", "description": "按名称/主机/标签/位置模糊搜索"},
         "kind": {"type": "string", "enum": ["normal", "compute"], "description": "normal 普通服务器 / compute 算力(GPU/NPU)服务器"}}}},
    {"name": "get_gpu_status", "scope": "read", "description": "实时查询算力服务器的 GPU/NPU:型号、利用率、显存、温度、功耗、占用进程(用户/PID/命令)",
     "inputSchema": {"type": "object", "properties": {"server": _SERVER}, "required": ["server"]}},
    {"name": "get_server", "scope": "read", "description": "获取单台服务器详细信息(含硬件配置、状态、轮换计划,不含密码)",
     "inputSchema": {"type": "object", "properties": {"server": _SERVER}, "required": ["server"]}},
    {"name": "check_status", "scope": "read", "description": "批量实时检测服务器状态(端口连通、SSH 登录、负载、内存、磁盘)",
     "inputSchema": {"type": "object", "properties": {"servers": _SERVERS}}},
    {"name": "get_server_facts", "scope": "read", "description": "获取服务器硬件与系统配置(CPU、内存、磁盘、厂商型号、虚拟化、IP 归属地)",
     "inputSchema": {"type": "object", "properties": {"server": _SERVER,
                     "refresh": {"type": "boolean", "description": "true 则重新 SSH 采集"}}, "required": ["server"]}},
    {"name": "get_websites", "scope": "read", "description": "获取服务器上运行的网站(Nginx/Apache 站点、域名、反代、SSL 到期)、监听端口、Docker 容器",
     "inputSchema": {"type": "object", "properties": {"server": _SERVER,
                     "refresh": {"type": "boolean", "description": "true 则重新扫描"}}, "required": ["server"]}},
    {"name": "exec_command", "scope": "exec", "description": "在指定服务器上执行 shell 命令,返回退出码、stdout、stderr",
     "inputSchema": {"type": "object", "properties": {
         "server": _SERVER, "command": {"type": "string"},
         "timeout": {"type": "integer", "description": "超时秒数,默认 60"},
         "sudo": {"type": "boolean", "description": "非 root 账号以 sudo 执行"}}, "required": ["server", "command"]}},
    {"name": "batch_exec", "scope": "exec", "description": "在多台服务器上并发执行同一条命令",
     "inputSchema": {"type": "object", "properties": {
         "servers": _SERVERS, "command": {"type": "string"},
         "timeout": {"type": "integer"}, "sudo": {"type": "boolean"}}, "required": ["command"]}},
    {"name": "bt_panel_info", "scope": "read", "description": "宝塔面板信息:版本、运行状态、面板地址(含安全入口)、用户名;有 secret 权限时含密码",
     "inputSchema": {"type": "object", "properties": {"server": _SERVER}, "required": ["server"]}},
    {"name": "bt_set_password", "scope": "rotate", "description": "修改宝塔面板登录密码(不传 password 则自动生成强密码),可同时修改用户名;新密码加密保存",
     "inputSchema": {"type": "object", "properties": {"server": _SERVER, "username": {"type": "string"},
                     "password": {"type": "string"}}, "required": ["server"]}},
    {"name": "bt_temp_login", "scope": "secret", "description": "生成宝塔面板一次性临时登录链接(15 分钟有效,用后失效)",
     "inputSchema": {"type": "object", "properties": {"server": _SERVER}, "required": ["server"]}},
    {"name": "get_credentials", "scope": "secret", "description": "读取服务器明文登录凭据(密码/私钥),仅在必须时使用",
     "inputSchema": {"type": "object", "properties": {"server": _SERVER}, "required": ["server"]}},
    {"name": "rotate_password", "scope": "rotate", "description": "立即轮换服务器凭据:target=password 轮换登录密码(自动选择 chpasswd/sudo/passwd),target=key 轮换 SSH 密钥",
     "inputSchema": {"type": "object", "properties": {"server": _SERVER,
                     "target": {"type": "string", "enum": ["password", "key"], "description": "默认 password"}}, "required": ["server"]}},
]


def _call(ctx, name, a):
    if name == "whoami":
        return agent.whoami(ctx)
    if name == "list_servers":
        return agent.list_servers(ctx, a.get("group"), a.get("status"), a.get("keyword"), a.get("kind"))
    if name == "get_gpu_status":
        return agent.gpu_status(ctx, a["server"])
    if name == "get_server":
        return agent.get_server(ctx, a["server"])
    if name == "check_status":
        return agent.check_status(ctx, a.get("servers"))
    if name == "get_server_facts":
        return agent.facts(ctx, a["server"], a.get("refresh", False))
    if name == "get_websites":
        return agent.sites(ctx, a["server"], a.get("refresh", False))
    if name == "exec_command":
        return agent.exec_command(ctx, a["server"], a["command"], a.get("timeout"), a.get("sudo", False))
    if name == "batch_exec":
        return agent.batch_exec(ctx, a.get("servers"), a["command"], a.get("timeout"), a.get("sudo", False))
    if name == "bt_panel_info":
        return agent.bt_info(ctx, a["server"])
    if name == "bt_set_password":
        return agent.bt_set_password(ctx, a["server"], a.get("username"), a.get("password"))
    if name == "bt_temp_login":
        return agent.bt_temp_login(ctx, a["server"])
    if name == "get_credentials":
        return agent.credentials(ctx, a["server"])
    if name == "rotate_password":
        return agent.rotate(ctx, a["server"], a.get("target", "password"))
    raise ServiceError(f"未知工具: {name}", 404)


def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_message(ctx, msg):
    """处理单条 JSON-RPC 消息;通知返回 None。"""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if rid is None:
        return None
    if method == "initialize":
        want = params.get("protocolVersion")
        return _result(rid, {
            "protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "server-vault", "version": VERSION},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        tools = [{k: v for k, v in t.items() if k != "scope"} for t in TOOLS if not t["scope"] or t["scope"] in ctx.scopes]
        return _result(rid, {"tools": tools})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not vault.unlocked:
            return _result(rid, {"content": [{"type": "text", "text": "错误: 保险库已锁定,请管理员先在 Web 界面解锁"}], "isError": True})
        try:
            data = _call(ctx, name, args)
            text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
            return _result(rid, {"content": [{"type": "text", "text": text}], "isError": False})
        except ServiceError as e:
            return _result(rid, {"content": [{"type": "text", "text": f"错误: {e}"}], "isError": True})
        except KeyError as e:
            return _result(rid, {"content": [{"type": "text", "text": f"缺少参数: {e}"}], "isError": True})
        except Exception as e:
            return _result(rid, {"content": [{"type": "text", "text": f"执行失败: {e}"}], "isError": True})
    if method in ("resources/list", "prompts/list"):
        return _result(rid, {method.split("/")[0]: []})
    return _error(rid, -32601, f"Method not found: {method}")
