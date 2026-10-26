"""业务逻辑:Web 管理端、Agent REST、MCP 共用同一套实现。"""
import csv
import hashlib
import io
import ipaddress
import json
import re
import secrets
import shlex
import socket
import string
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import collectors, ssh
from .db import db
from .vault import vault


class ServiceError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def now_dt():
    return datetime.now(timezone.utc).replace(microsecond=0)


def now_iso():
    return now_dt().isoformat()


def parse_iso(s):
    return datetime.fromisoformat(s) if s else None


# ---------------- 设置 ----------------

DEFAULT_SETTINGS = {
    "status_interval_min": 10,       # 自动批量检测状态间隔(分钟,0=关闭)
    "facts_interval_hours": 24,      # 自动采集配置间隔(小时,0=关闭)
    "password_length": 24,
    "default_rotation_days": 30,
    "geo_lookup": True,              # 通过 ip-api.com 查询公网 IP 归属地
    "exec_timeout": 60,
    "session_hours": 12,
    "agent_deny_patterns": [
        r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(--no-preserve-root\s+)?/(\s|$|\*)",
        r"\bmkfs(\.\w+)?\b",
        r"\bdd\s+.*of=/dev/(sd|nvme|vd|xvd|hd)",
        r"\b(shutdown|poweroff|halt|init\s+0)\b",
        r":\(\)\s*\{\s*:\|:&\s*\};:",
        r"\b(chpasswd|passwd)\b",
        r">\s*/dev/(sd|nvme|vd)",
    ],
}


def get_settings() -> dict:
    stored = json.loads(db.get_meta("settings") or "{}")
    return {**DEFAULT_SETTINGS, **stored}


def update_settings(patch: dict, actor="admin") -> dict:
    cur = get_settings()
    for k, v in patch.items():
        if k not in DEFAULT_SETTINGS:
            continue
        default = DEFAULT_SETTINGS[k]
        if isinstance(default, bool):
            v = bool(v)
        elif isinstance(default, int):
            v = int(v)
            if v < 0:
                raise ServiceError(f"{k} 不能为负数")
        elif isinstance(default, list):
            if isinstance(v, str):
                v = [x for x in v.splitlines() if x.strip()]
            for p in v:
                try:
                    re.compile(p)
                except re.error as e:
                    raise ServiceError(f"正则无效: {p} ({e})")
        cur[k] = v
    if not 12 <= cur["password_length"] <= 64:
        raise ServiceError("密码长度需在 12-64 之间")
    db.set_meta("settings", json.dumps(cur, ensure_ascii=False))
    audit(actor, "settings.update", detail=",".join(patch.keys()))
    return cur


# ---------------- 审计 ----------------

def audit(actor, action, row=None, detail="", ok=True):
    db.run("INSERT INTO audit_logs(ts,actor,action,server_id,server_name,detail,ok) VALUES(?,?,?,?,?,?,?)",
           (now_iso(), actor, action, row["id"] if row else None, row["name"] if row else None,
            (detail or "")[:2000], 1 if ok else 0))


def list_audit(limit=100, offset=0, server_id=None, actor=None, action=None):
    where, params = [], []
    if server_id:
        where.append("server_id=?")
        params.append(server_id)
    if actor:
        where.append("actor LIKE ?")
        params.append(f"%{actor}%")
    if action:
        where.append("action LIKE ?")
        params.append(f"%{action}%")
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.one(f"SELECT COUNT(*) c FROM audit_logs {w}", params)["c"]
    rows = db.all(f"SELECT * FROM audit_logs {w} ORDER BY id DESC LIMIT ? OFFSET ?", (*params, limit, offset))
    return {"total": total, "items": rows}


# ---------------- 服务器 ----------------

def public(row) -> dict:
    """对外展示的服务器信息(不含任何密文/明文凭据)。"""
    return {
        "id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"],
        "username": row["username"], "auth_type": row["auth_type"], "use_sudo": bool(row["use_sudo"]),
        "group_name": row["group_name"], "tags": [t for t in row["tags"].split(",") if t],
        "location": row["location"], "provider": row["provider"], "notes": row["notes"],
        "rotation_enabled": bool(row["rotation_enabled"]), "rotation_days": row["rotation_days"],
        "last_rotated_at": row["last_rotated_at"], "next_rotation_at": row["next_rotation_at"],
        "rotation_status": row["rotation_status"], "rotation_error": row["rotation_error"],
        "status": row["status"], "status_detail": json.loads(row["status_detail"] or "null"),
        "latency_ms": row["latency_ms"], "last_checked_at": row["last_checked_at"],
        "facts": json.loads(row["facts_json"] or "null"), "facts_at": row["facts_at"],
        "sites_at": row["sites_at"], "host_key_fp": row["host_key_fp"],
        "proxy_command": row["proxy_command"], "source": row["source"],
        "has_password": bool(row["password_enc"]), "has_key": bool(row["private_key_enc"]),
        "has_pending_password": bool(row["pending_password_enc"]),
        "ssh_command": f"ssh {'-p ' + str(row['port']) + ' ' if row['port'] != 22 else ''}{row['username']}@{row['host']}",
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def get_row(sid) -> dict:
    row = db.one("SELECT * FROM servers WHERE id=?", (sid,))
    if not row:
        raise ServiceError("服务器不存在", 404)
    return row


def resolve(ref, groups=None) -> dict:
    """按 id / 名称 / 主机地址 查找服务器,并校验分组权限。"""
    ref = str(ref).strip()
    row = None
    if ref.isdigit():
        row = db.one("SELECT * FROM servers WHERE id=?", (int(ref),))
    if not row:
        row = db.one("SELECT * FROM servers WHERE name=?", (ref,))
    if not row:
        rows = db.all("SELECT * FROM servers WHERE host=?", (ref,))
        if len(rows) > 1:
            raise ServiceError(f"主机 {ref} 匹配到多台服务器,请使用名称或 id", 409)
        row = rows[0] if rows else None
    if not row or (groups and row["group_name"] not in groups):
        raise ServiceError(f"找不到服务器: {ref}", 404)
    return row


def list_rows(groups=None, group=None, status=None, keyword=None):
    rows = db.all("SELECT * FROM servers ORDER BY group_name, name")
    out = []
    kw = (keyword or "").lower()
    for r in rows:
        if groups and r["group_name"] not in groups:
            continue
        if group and r["group_name"] != group:
            continue
        if status and r["status"] != status:
            continue
        if kw and kw not in " ".join(str(r[k]) for k in ("name", "host", "group_name", "tags", "location", "provider", "notes")).lower():
            continue
        out.append(r)
    return out


def creds(row) -> dict:
    return {
        "password": vault.decrypt(row["password_enc"]),
        "private_key": vault.decrypt(row["private_key_enc"]),
        "key_passphrase": vault.decrypt(row["key_passphrase_enc"]),
        "pending_password": vault.decrypt(row["pending_password_enc"]),
    }


def _next_rotation(base_iso, days):
    base = parse_iso(base_iso) or now_dt()
    nxt = base + timedelta(days=days)
    return max(nxt, now_dt()).isoformat()


def save_server(data: dict, sid=None, actor="admin") -> dict:
    old = get_row(sid) if sid else None
    pick = lambda k, d: data[k] if k in data and data[k] is not None else (old[k] if old else d)
    name = str(pick("name", "")).strip()
    host = str(pick("host", "")).strip()
    if not name or not host:
        raise ServiceError("名称和主机地址必填")
    try:
        port = int(pick("port", 22))
        assert 1 <= port <= 65535
    except (ValueError, AssertionError):
        raise ServiceError("端口无效")
    auth_type = pick("auth_type", "password")
    if auth_type not in ("password", "key"):
        raise ServiceError("认证方式只能是 password 或 key")
    tags = data.get("tags", old["tags"] if old else "")
    if isinstance(tags, list):
        tags = ",".join(t.strip() for t in tags if t.strip())
    rotation_days = int(pick("rotation_days", get_settings()["default_rotation_days"]))
    if rotation_days < 1:
        raise ServiceError("轮换周期至少 1 天")
    fields = {
        "name": name, "host": host, "port": port,
        "username": str(pick("username", "root")).strip() or "root",
        "auth_type": auth_type,
        "use_sudo": 1 if pick("use_sudo", 0) else 0,
        "group_name": str(pick("group_name", "默认")).strip() or "默认",
        "tags": ",".join(t.strip() for t in str(tags).replace("，", ",").split(",") if t.strip()),
        "location": str(pick("location", "")).strip(),
        "provider": str(pick("provider", "")).strip(),
        "notes": str(pick("notes", "")),
        "proxy_command": str(pick("proxy_command", "")).strip(),
        "rotation_enabled": 1 if pick("rotation_enabled", 0) else 0,
        "rotation_days": rotation_days,
        "updated_at": now_iso(),
    }
    # 凭据:空字符串表示保持不变,clear_* 表示清除
    for key, col in (("password", "password_enc"), ("private_key", "private_key_enc"), ("key_passphrase", "key_passphrase_enc")):
        if data.get(key):
            val = data[key].strip() + "\n" if key == "private_key" else data[key]
            fields[col] = vault.encrypt(val)
        elif data.get(f"clear_{key}"):
            fields[col] = None
    if fields["rotation_enabled"]:
        if not old or not old["rotation_enabled"] or old["rotation_days"] != rotation_days or not old["next_rotation_at"]:
            fields["next_rotation_at"] = _next_rotation(old["last_rotated_at"] if old else None, rotation_days)
    else:
        fields["next_rotation_at"] = None
    if old and (old["host"] != host or old["port"] != port):
        fields["host_key"] = None
        fields["host_key_fp"] = None
    dup = db.one("SELECT id FROM servers WHERE name=?", (name,))
    if dup and dup["id"] != sid:
        raise ServiceError(f"名称已存在: {name}")
    if old:
        sets = ",".join(f"{k}=?" for k in fields)
        db.run(f"UPDATE servers SET {sets} WHERE id=?", (*fields.values(), sid))
    else:
        fields["created_at"] = fields["updated_at"]
        cols = ",".join(fields)
        sid = db.run(f"INSERT INTO servers({cols}) VALUES({','.join('?' * len(fields))})", tuple(fields.values()))
    row = get_row(sid)
    audit(actor, "server.update" if old else "server.create", row)
    return public(row)


def delete_server(sid, actor="admin"):
    row = get_row(sid)
    db.run("DELETE FROM servers WHERE id=?", (sid,))
    audit(actor, "server.delete", row, f"{row['username']}@{row['host']}:{row['port']}")


def import_servers(text: str, actor="admin") -> dict:
    """CSV 批量导入,表头:name,host,port,username,password,group,location,provider,tags"""
    reader = csv.DictReader(io.StringIO(text.strip()))
    created, errors = [], []
    for i, rec in enumerate(reader, start=2):
        rec = {(k or "").strip().lower(): (v or "").strip() for k, v in rec.items()}
        try:
            s = save_server({
                "name": rec.get("name") or rec.get("host"), "host": rec.get("host"),
                "port": rec.get("port") or 22, "username": rec.get("username") or rec.get("user") or "root",
                "password": rec.get("password"), "group_name": rec.get("group") or "默认",
                "location": rec.get("location", ""), "provider": rec.get("provider", ""),
                "tags": rec.get("tags", "").replace(";", ","),
            }, actor=actor)
            created.append(s["name"])
        except Exception as e:
            errors.append(f"第 {i} 行: {e}")
    return {"created": created, "errors": errors}


def groups():
    return [r["group_name"] for r in db.all("SELECT DISTINCT group_name FROM servers ORDER BY group_name")]


def reset_host_key(sid, actor="admin"):
    row = get_row(sid)
    db.run("UPDATE servers SET host_key=NULL, host_key_fp=NULL, status='unknown' WHERE id=?", (sid,))
    audit(actor, "server.reset_hostkey", row, row["host_key_fp"] or "")


def password_history(sid):
    rows = db.all("SELECT * FROM password_history WHERE server_id=? ORDER BY id DESC", (sid,))
    return [{"id": r["id"], "password": vault.decrypt(r["password_enc"]), "reason": r["reason"],
             "created_at": r["created_at"]} for r in rows]


def reveal(sid, actor="admin") -> dict:
    row = get_row(sid)
    c = creds(row)
    audit(actor, "secret.reveal", row)
    return {"id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"],
            "username": row["username"], "password": c["password"], "private_key": c["private_key"],
            "key_passphrase": c["key_passphrase"], "pending_password": c["pending_password"],
            "ssh_command": public(row)["ssh_command"]}


# ---------------- SSH 操作 ----------------

def open_ssh(row, password=None, use_key=True, timeout=10):
    c = creds(row)
    kwargs = dict(password=password or c["password"],
                  pkey_text=c["private_key"] if use_key and row["auth_type"] == "key" else None,
                  passphrase=c["key_passphrase"], known_host_key=row["host_key"], timeout=timeout,
                  proxy_command=row["proxy_command"] or None)
    try:
        for attempt in range(3):
            try:
                client = ssh.connect(row["host"], row["port"], row["username"], **kwargs)
                break
            except (ssh.AuthFailed, ssh.HostKeyMismatch):
                raise
            except ssh.SSHError:
                # 代理(如 VPN 的 SOCKS5)在并发高时偶尔会断开,稍等重试
                if not row["proxy_command"] or attempt == 2:
                    raise
                time.sleep(0.6 * (attempt + 1))
    except ssh.HostKeyMismatch as e:
        db.run("UPDATE servers SET status='hostkey_mismatch', status_detail=?, last_checked_at=? WHERE id=?",
               (json.dumps({"error": str(e)}, ensure_ascii=False), now_iso(), row["id"]))
        raise ServiceError(str(e), 409)
    if not row["host_key"]:
        db.run("UPDATE servers SET host_key=?, host_key_fp=? WHERE id=?",
               (client.sv_host_key, client.sv_host_key_fp, row["id"]))
        row["host_key"] = client.sv_host_key
    return client


def _wrap_sudo(row, command, sudo):
    if sudo and row["username"] != "root":
        pw = creds(row)["password"]
        if not pw:
            raise ServiceError("sudo 需要账号密码,但该服务器未保存密码")
        return f"sudo -S -p '' sh -c {shlex.quote(command)}", pw + "\n"
    return command, None


def check_deny(command):
    for p in get_settings()["agent_deny_patterns"]:
        if re.search(p, command):
            return p
    return None


def exec_command(row, command, timeout=None, sudo=False, actor="admin", enforce_policy=False) -> dict:
    command = (command or "").strip()
    if not command:
        raise ServiceError("命令不能为空")
    if enforce_policy:
        hit = check_deny(command)
        if hit:
            audit(actor, "exec.denied", row, f"{command[:500]}  [规则 {hit}]", ok=False)
            raise ServiceError(f"命令被安全策略拦截(规则: {hit})", 403)
    timeout = int(timeout or get_settings()["exec_timeout"])
    cmd, stdin = _wrap_sudo(row, command, sudo)
    t0 = time.time()
    client = open_ssh(row)
    try:
        code, out, err, timed_out = ssh.run(client, cmd, timeout, stdin)
    finally:
        client.close()
    audit(actor, "exec", row, ("[sudo] " if sudo else "") + command[:1000], ok=(code == 0))
    return {"server_id": row["id"], "server": row["name"], "exit_code": code, "stdout": out, "stderr": err,
            "timed_out": timed_out, "duration_ms": int((time.time() - t0) * 1000)}


def run_many(rows, fn, workers=24):
    """并发对多台服务器执行 fn(row),单台异常不影响其他。"""
    def wrap(row):
        try:
            res = fn(row)
            return {"ok": True, **res} if isinstance(res, dict) else {"ok": True, "result": res}
        except Exception as e:
            return {"ok": False, "server_id": row["id"], "server": row["name"], "error": str(e)}
    if not rows:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(rows))) as pool:
        return list(pool.map(wrap, rows))


def _explain(err, code):
    """把常见的远程执行失败翻译成可操作的提示。"""
    if "password has expired" in err.lower() or "password change required" in err.lower():
        return "账号密码已过期,服务器拒绝执行命令。请用 Web 终端登录并按提示修改密码"
    return (err.strip().splitlines() or [f"命令执行失败 (exit {code})"])[0][:300]


def check_status(row) -> dict:
    detail, status, latency = {}, "offline", None
    t0 = time.time()
    reachable = True
    if not row["proxy_command"]:
        try:
            socket.create_connection((row["host"], row["port"]), timeout=5).close()
            latency = int((time.time() - t0) * 1000)
        except OSError as e:
            reachable, detail["error"] = False, f"端口不可达: {e}"
    if reachable:
        try:
            client = open_ssh(row, timeout=10)
            if latency is None:
                latency = int((time.time() - t0) * 1000)
            try:
                code, out, err, _ = ssh.run(client, "sh -s", 15, collectors.STATUS_SCRIPT)
            finally:
                client.close()
            if code != 0 and not out.strip():
                status, detail["error"] = "error", _explain(err, code)
            else:
                detail = collectors.parse_status(out)
                status = "online"
        except ServiceError as e:
            status, detail["error"] = ("hostkey_mismatch" if e.status == 409 else "error"), str(e)
        except ssh.AuthFailed as e:
            status, detail["error"] = "auth_failed", str(e)
        except Exception as e:
            proxy_down = row["proxy_command"] and ("ProxyCommand" in str(e) or "closed" in str(e).lower())
            status, detail["error"] = ("offline", f"经代理连接失败(VPN/代理是否已连接?): {e}") if proxy_down else ("error", str(e))
    ts = now_iso()
    db.run("UPDATE servers SET status=?, status_detail=?, latency_ms=?, last_checked_at=? WHERE id=?",
           (status, json.dumps(detail, ensure_ascii=False), latency, ts, row["id"]))
    return {"server_id": row["id"], "server": row["name"], "status": status, "latency_ms": latency,
            "detail": detail, "checked_at": ts}


def check_many(rows, actor="admin"):
    res = run_many(rows, check_status, workers=48)
    online = sum(1 for r in res if r.get("status") == "online")
    audit(actor, "status.check", detail=f"{len(rows)} 台,在线 {online}")
    return res


def geo_lookup(host) -> dict:
    try:
        ip = socket.gethostbyname(host)
        addr = ipaddress.ip_address(ip)
    except (OSError, ValueError):
        return {}
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return {"ip": ip, "region": "内网地址"}
    try:
        url = f"http://ip-api.com/json/{ip}?lang=zh-CN&fields=status,country,regionName,city,isp,org,as"
        with urllib.request.urlopen(url, timeout=5) as r:
            d = json.loads(r.read().decode())
        if d.get("status") != "success":
            return {"ip": ip}
        return {"ip": ip, "country": d.get("country", ""), "region": d.get("regionName", ""),
                "city": d.get("city", ""), "isp": d.get("isp", ""), "org": d.get("org", ""), "as": d.get("as", "")}
    except Exception:
        return {"ip": ip}


CLOUDS = [
    (("alibaba", "aliyun", "alicloud"), "阿里云"), (("tencent",), "腾讯云"), (("huawei",), "华为云"),
    (("baidu",), "百度智能云"), (("volcengine", "bytedance"), "火山引擎"), (("jdcloud", "jingdong"), "京东云"),
    (("ucloud",), "UCloud"), (("ksyun", "kingsoft"), "金山云"), (("amazon", "aws", "ec2"), "AWS"),
    (("google",), "Google Cloud"), (("microsoft", "azure"), "Azure"), (("oracle",), "Oracle Cloud"),
    (("digitalocean",), "DigitalOcean"), (("vultr", "choopa", "constant"), "Vultr"), (("linode", "akamai"), "Linode"),
    (("hetzner",), "Hetzner"), (("ovh",), "OVH"), (("china telecom", "chinanet"), "中国电信"),
    (("china unicom", "unicom"), "中国联通"), (("china mobile",), "中国移动"),
]


def detect_cloud(facts) -> str:
    """根据 DMI 厂商与 IP 归属组织推断云厂商 / 运营商。"""
    g = facts.get("geo") or {}
    for text in (facts.get("vendor", ""), facts.get("product", ""), g.get("org", ""), g.get("isp", ""), g.get("as", "")):
        low = (text or "").lower()
        for keys, name in CLOUDS:
            if any(k in low for k in keys):
                return name
    return ""


def collect_facts(row, actor="admin") -> dict:
    client = open_ssh(row)
    try:
        code, out, err, _ = ssh.run(client, "sh -s", 40, collectors.FACTS_SCRIPT)
    finally:
        client.close()
    if code != 0 and not out.strip():
        raise ServiceError(f"采集失败: {_explain(err, code)}")
    facts = collectors.parse_facts(out)
    if get_settings()["geo_lookup"]:
        facts["geo"] = geo_lookup(row["host"])
    facts["cloud"] = detect_cloud(facts)
    ts = now_iso()
    db.run("UPDATE servers SET facts_json=?, facts_at=?, status='online', last_checked_at=? WHERE id=?",
           (json.dumps(facts, ensure_ascii=False), ts, ts, row["id"]))
    audit(actor, "facts.collect", row)
    return {"server_id": row["id"], "server": row["name"], "facts": facts, "facts_at": ts}


def collect_sites(row, actor="admin") -> dict:
    client = open_ssh(row)
    try:
        code, out, err, _ = ssh.run(client, "sh -s", 60, collectors.SITES_SCRIPT)
        if "@@END@@" not in out:
            raise ServiceError(f"扫描失败: {_explain(err, code)}")
        sites = collectors.parse_sites(out)
        certs = sorted({s["cert"] for s in sites["nginx"] if s.get("cert")})[:60]
        if certs:
            _, cout, _, _ = ssh.run(client, "sh -s", 30, collectors.cert_script(certs))
            dates = collectors.parse_cert_dates(cout)
            for s in sites["nginx"]:
                if s.get("cert"):
                    s["cert_expires"] = dates.get(s["cert"])
    finally:
        client.close()
    ts = now_iso()
    sites["collected_at"] = ts
    db.run("UPDATE servers SET sites_json=?, sites_at=? WHERE id=?", (json.dumps(sites, ensure_ascii=False), ts, row["id"]))
    audit(actor, "sites.collect", row, f"nginx {len(sites['nginx'])} / apache {len(sites['apache'])} / 端口 {len(sites['ports'])}")
    return {"server_id": row["id"], "server": row["name"], **sites}


def get_sites(row):
    return json.loads(row["sites_json"]) if row["sites_json"] else None


# ---------------- 密码轮换 ----------------

_locks, _locks_guard = {}, threading.Lock()


def _server_lock(sid):
    with _locks_guard:
        return _locks.setdefault(sid, threading.Lock())


def generate_password(length=None) -> str:
    length = max(12, min(64, int(length or get_settings()["password_length"])))
    # 不含 : 与引号等字符,避免 chpasswd 与 shell 转义问题
    sets = [string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#%^*-_=+."]
    alphabet = "".join(sets)
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if all(any(c in s for c in pw) for s in sets):
            return pw


def _rotation_failed(row, actor, msg):
    retry = (now_dt() + timedelta(hours=1)).isoformat() if row["rotation_enabled"] else None
    db.run("UPDATE servers SET rotation_status='failed', rotation_error=?, next_rotation_at=COALESCE(?, next_rotation_at) WHERE id=?",
           (msg[:500], retry, row["id"]))
    audit(actor, "rotate", row, msg, ok=False)
    raise ServiceError(msg)


def rotate_password(sid, actor="admin", new_password=None) -> dict:
    lock = _server_lock(sid)
    if not lock.acquire(blocking=False):
        raise ServiceError("该服务器正在轮换中", 409)
    try:
        row = get_row(sid)
        c = creds(row)
        user = row["username"]
        if new_password:
            if len(new_password) < 8 or ":" in new_password or "\n" in new_password:
                raise ServiceError("自定义密码至少 8 位,且不能包含冒号或换行")
        if user != "root" and not row["use_sudo"]:
            _rotation_failed(row, actor, "非 root 账号需要开启 sudo 才能轮换密码")
        if user != "root" and not c["password"]:
            _rotation_failed(row, actor, "sudo 需要当前密码,但未保存密码")
        new = new_password or generate_password()
        # 预写:先把新密码加密落库,任何中断都不会丢失新密码
        db.run("UPDATE servers SET pending_password_enc=? WHERE id=?", (vault.encrypt(new), sid))
        try:
            client = open_ssh(row)
        except Exception as e:
            db.run("UPDATE servers SET pending_password_enc=NULL WHERE id=?", (sid,))
            _rotation_failed(row, actor, f"连接失败: {e}")
        try:
            if user == "root":
                code, out, err, _ = ssh.run(client, "chpasswd", 30, f"{user}:{new}\n")
            else:
                code, out, err, _ = ssh.run(client, "sudo -S -p '' chpasswd", 30, f"{c['password']}\n{user}:{new}\n")
        finally:
            client.close()
        if code != 0:
            db.run("UPDATE servers SET pending_password_enc=NULL WHERE id=?", (sid,))
            _rotation_failed(row, actor, f"chpasswd 执行失败 (exit {code}): {(err or out).strip()[:300]}")
        # chpasswd 成功即视为服务器密码已变更,立即提交
        ts = now_iso()
        nxt = _next_rotation(ts, row["rotation_days"]) if row["rotation_enabled"] else None
        with db.tx():
            if c["password"]:
                db.run("INSERT INTO password_history(server_id,password_enc,reason,created_at) VALUES(?,?,?,?)",
                       (sid, row["password_enc"], f"轮换前旧密码 ({actor})", ts))
            db.run("""UPDATE servers SET password_enc=pending_password_enc, pending_password_enc=NULL,
                      last_rotated_at=?, next_rotation_at=?, rotation_status='ok', rotation_error=NULL, updated_at=?
                      WHERE id=?""", (ts, nxt, ts, sid))
            db.run("""DELETE FROM password_history WHERE server_id=? AND id NOT IN
                      (SELECT id FROM password_history WHERE server_id=? ORDER BY id DESC LIMIT 20)""", (sid, sid))
        # 验证新密码
        verified, note = True, ""
        try:
            fresh = get_row(sid)
            if row["auth_type"] == "password":
                open_ssh(fresh, password=new, use_key=False).close()
            elif user != "root":
                cl = open_ssh(fresh)
                try:
                    vcode, _, verr, _ = ssh.run(cl, "sudo -S -p '' -k true", 15, new + "\n")
                finally:
                    cl.close()
                verified, note = vcode == 0, verr.strip()
        except Exception as e:
            verified, note = False, str(e)
        if not verified:
            db.run("UPDATE servers SET rotation_status='warn', rotation_error=? WHERE id=?",
                   (f"密码已修改,但新密码验证失败: {note}"[:500], sid))
        audit(actor, "rotate", row, "新密码验证通过" if verified else f"已修改但验证失败: {note}", ok=verified)
        return {"server_id": sid, "server": row["name"], "rotated_at": ts, "verified": verified,
                "next_rotation_at": nxt, "message": "轮换成功" if verified else f"密码已修改,但验证失败: {note}"}
    finally:
        lock.release()


def rotate_many(rows, actor="admin"):
    return run_many(rows, lambda r: rotate_password(r["id"], actor), workers=8)


# ---------------- 自动化任务 ----------------

def _automation_public(a):
    return {**a, "use_sudo": bool(a["use_sudo"]), "enabled": bool(a["enabled"]),
            "last_result": json.loads(a["last_result"] or "null")}


def list_automations():
    return [_automation_public(a) for a in db.all("SELECT * FROM automations ORDER BY id")]


def save_automation(data, aid=None, actor="admin"):
    name = (data.get("name") or "").strip()
    script = (data.get("script") or "").strip()
    if not name or not script:
        raise ServiceError("名称和脚本必填")
    target_type = data.get("target_type", "all")
    if target_type not in ("all", "group", "ids"):
        raise ServiceError("目标类型无效")
    target_value = data.get("target_value", "")
    if isinstance(target_value, list):
        target_value = ",".join(str(x) for x in target_value)
    fields = {"name": name, "script": script, "target_type": target_type, "target_value": str(target_value),
              "interval_minutes": max(0, int(data.get("interval_minutes") or 0)),
              "use_sudo": 1 if data.get("use_sudo") else 0, "enabled": 1 if data.get("enabled", True) else 0,
              "updated_at": now_iso()}
    if aid:
        if not db.one("SELECT id FROM automations WHERE id=?", (aid,)):
            raise ServiceError("任务不存在", 404)
        db.run(f"UPDATE automations SET {','.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), aid))
    else:
        fields["created_at"] = fields["updated_at"]
        aid = db.run(f"INSERT INTO automations({','.join(fields)}) VALUES({','.join('?' * len(fields))})", tuple(fields.values()))
    audit(actor, "automation.save", detail=name)
    return _automation_public(db.one("SELECT * FROM automations WHERE id=?", (aid,)))


def delete_automation(aid, actor="admin"):
    a = db.one("SELECT * FROM automations WHERE id=?", (aid,))
    if not a:
        raise ServiceError("任务不存在", 404)
    db.run("DELETE FROM automations WHERE id=?", (aid,))
    audit(actor, "automation.delete", detail=a["name"])


def automation_targets(a):
    vals = [v.strip() for v in a["target_value"].split(",") if v.strip()]
    if a["target_type"] == "group":
        return [r for r in list_rows() if r["group_name"] in vals]
    if a["target_type"] == "ids":
        return [r for r in list_rows() if str(r["id"]) in vals]
    return list_rows()


def run_automation(aid, actor="admin"):
    a = db.one("SELECT * FROM automations WHERE id=?", (aid,))
    if not a:
        raise ServiceError("任务不存在", 404)
    rows = automation_targets(a)
    results = run_many(rows, lambda r: exec_command(r, a["script"], sudo=bool(a["use_sudo"]), actor=actor))
    ok = sum(1 for r in results if r["ok"] and r.get("exit_code") == 0)
    status = "ok" if ok == len(results) else ("failed" if ok == 0 else "partial")
    trimmed = [{**r, "stdout": (r.get("stdout") or "")[-4000:], "stderr": (r.get("stderr") or "")[-2000:]} for r in results]
    db.run("UPDATE automations SET last_run_at=?, last_status=?, last_result=? WHERE id=?",
           (now_iso(), status, json.dumps(trimmed, ensure_ascii=False), aid))
    audit(actor, "automation.run", detail=f"{a['name']}: {ok}/{len(results)} 成功", ok=(status == "ok"))
    return {"automation": a["name"], "status": status, "results": trimmed}


# ---------------- Agent Token ----------------

SCOPES = {
    "read": "查看服务器列表、配置、状态、网站",
    "exec": "在服务器上执行命令",
    "secret": "读取明文密码/私钥",
    "rotate": "触发密码轮换",
}


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def token_public(t):
    return {"id": t["id"], "name": t["name"], "prefix": t["prefix"], "scopes": t["scopes"].split(","),
            "groups": [g for g in t["group_scope"].split(",") if g], "created_at": t["created_at"],
            "expires_at": t["expires_at"], "last_used_at": t["last_used_at"], "revoked": bool(t["revoked"])}


def list_tokens():
    return [token_public(t) for t in db.all("SELECT * FROM agent_tokens ORDER BY id DESC")]


def create_token(name, scopes, group_scope=None, expires_days=None, actor="admin"):
    name = (name or "").strip()
    if not name:
        raise ServiceError("Token 名称必填")
    scopes = [s for s in scopes or [] if s in SCOPES]
    if not scopes:
        raise ServiceError("至少选择一个权限")
    raw = "sv_" + secrets.token_urlsafe(32)
    expires = (now_dt() + timedelta(days=int(expires_days))).isoformat() if expires_days else None
    tid = db.run("INSERT INTO agent_tokens(name,token_hash,prefix,scopes,group_scope,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                 (name, _hash(raw), raw[:10], ",".join(scopes), ",".join(group_scope or []), now_iso(), expires))
    audit(actor, "token.create", detail=f"{name} [{','.join(scopes)}]")
    return {"token": raw, **token_public(db.one("SELECT * FROM agent_tokens WHERE id=?", (tid,)))}


def revoke_token(tid, actor="admin"):
    t = db.one("SELECT * FROM agent_tokens WHERE id=?", (tid,))
    if not t:
        raise ServiceError("Token 不存在", 404)
    db.run("UPDATE agent_tokens SET revoked=1 WHERE id=?", (tid,))
    audit(actor, "token.revoke", detail=t["name"])


def verify_token(raw):
    if not raw:
        return None
    t = db.one("SELECT * FROM agent_tokens WHERE token_hash=?", (_hash(raw),))
    if not t or t["revoked"]:
        return None
    if t["expires_at"] and parse_iso(t["expires_at"]) < now_dt():
        return None
    db.run("UPDATE agent_tokens SET last_used_at=? WHERE id=?", (now_iso(), t["id"]))
    return t
