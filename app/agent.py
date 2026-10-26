"""Agent 能力层:按 Token 权限(scope)与分组范围约束,供 REST 与 MCP 共用。"""
from dataclasses import dataclass, field

from . import btpanel
from . import services as S
from .vault import vault


@dataclass
class AgentCtx:
    token_id: int
    name: str
    scopes: set
    groups: list = field(default_factory=list)

    @property
    def actor(self):
        return f"agent:{self.name}"

    def need(self, scope):
        if scope not in self.scopes:
            raise S.ServiceError(f"当前 Token 没有 {scope} 权限({S.SCOPES[scope]})", 403)


def authenticate(authorization: str, require_unlocked=True) -> AgentCtx:
    raw = (authorization or "").removeprefix("Bearer ").strip()
    t = S.verify_token(raw)
    if not t:
        raise S.ServiceError("Agent Token 无效、已吊销或已过期", 401)
    if require_unlocked and not vault.unlocked:
        raise S.ServiceError("保险库已锁定,请管理员先在 Web 界面解锁", 423)
    return AgentCtx(t["id"], t["name"], set(t["scopes"].split(",")), [g for g in t["group_scope"].split(",") if g])


def _brief(row):
    p = S.public(row)
    f = p["facts"] or {}
    d = p["status_detail"] or {}
    return {
        "id": p["id"], "name": p["name"], "host": p["host"], "port": p["port"], "username": p["username"],
        "group": p["group_name"], "tags": p["tags"], "location": p["location"], "provider": p["provider"],
        "status": p["status"], "latency_ms": p["latency_ms"], "load": d.get("load"),
        "os": f.get("os"), "model": f.get("model"), "cpu_cores": f.get("cpu_cores"),
        "mem_total_gb": round(f["mem_total"] / 1024**3, 1) if f.get("mem_total") else None,
        "last_checked_at": p["last_checked_at"], "kind": p["kind"],
        "gpu": f.get("gpu_summary"), "temporary_until": p["temp_expires_at"],
    }


def list_servers(ctx, group=None, status=None, keyword=None, kind=None):
    ctx.need("read")
    return [_brief(r) for r in S.list_rows(ctx.groups, group, status, keyword, kind)]


def gpu_status(ctx, ref):
    ctx.need("read")
    return S.gpu_live(S.resolve(ref, ctx.groups), actor=ctx.actor)


def get_server(ctx, ref):
    ctx.need("read")
    return S.public(S.resolve(ref, ctx.groups))


def _rows(ctx, refs):
    if not refs:
        return S.list_rows(ctx.groups)
    return [S.resolve(r, ctx.groups) for r in refs]


def check_status(ctx, refs=None):
    ctx.need("read")
    return S.check_many(_rows(ctx, refs), actor=ctx.actor)


def facts(ctx, ref, refresh=False):
    ctx.need("read")
    row = S.resolve(ref, ctx.groups)
    if refresh or not row["facts_json"]:
        return S.collect_facts(row, actor=ctx.actor)
    p = S.public(row)
    return {"server_id": p["id"], "server": p["name"], "facts": p["facts"], "facts_at": p["facts_at"]}


def sites(ctx, ref, refresh=False):
    ctx.need("read")
    row = S.resolve(ref, ctx.groups)
    if refresh or not row["sites_json"]:
        return S.collect_sites(row, actor=ctx.actor)
    return {"server_id": row["id"], "server": row["name"], **S.get_sites(row)}


def exec_command(ctx, ref, command, timeout=None, sudo=False):
    ctx.need("exec")
    return S.exec_command(S.resolve(ref, ctx.groups), command, timeout, sudo, actor=ctx.actor, enforce_policy=True)


def batch_exec(ctx, refs, command, timeout=None, sudo=False):
    ctx.need("exec")
    rows = _rows(ctx, refs)
    hit = S.check_deny(command or "")
    if hit:
        raise S.ServiceError(f"命令被安全策略拦截(规则: {hit})", 403)
    return S.run_many(rows, lambda r: S.exec_command(r, command, timeout, sudo, actor=ctx.actor, enforce_policy=True))


def credentials(ctx, ref):
    ctx.need("secret")
    return S.reveal(S.resolve(ref, ctx.groups)["id"], actor=ctx.actor)


def rotate(ctx, ref, target="password"):
    ctx.need("rotate")
    sid = S.resolve(ref, ctx.groups)["id"]
    return S.rotate_key(sid, actor=ctx.actor) if target == "key" else S.rotate_password(sid, actor=ctx.actor)


def bt_info(ctx, ref):
    ctx.need("read")
    return btpanel.info(S.resolve(ref, ctx.groups), include_secret="secret" in ctx.scopes)


def bt_set_password(ctx, ref, username=None, password=None):
    ctx.need("rotate")
    res = btpanel.set_credentials(S.resolve(ref, ctx.groups)["id"], username, password, actor=ctx.actor)
    if "secret" not in ctx.scopes:
        res["password"] = "(已加密保存,当前 Token 无 secret 权限,不返回明文)"
    return res


def bt_temp_login(ctx, ref):
    ctx.need("secret")
    return btpanel.temp_login(S.resolve(ref, ctx.groups)["id"], actor=ctx.actor)


def whoami(ctx):
    return {"token": ctx.name, "scopes": sorted(ctx.scopes), "groups": ctx.groups or "全部",
            "server_count": len(S.list_rows(ctx.groups))}
