"""宝塔面板(BT Panel)集成:读取版本/入口/用户名、修改面板账号密码、生成一次性临时登录链接。

所有修改都调用面板自带的 public 模块(与 `bt 5` / tools.py 相同的加盐哈希),兼容 9.x ~ 11.x。
面板文件属于 root,非 root 账号会自动通过 sudo 执行。
"""
import json
import re
import shlex

from . import services as S
from . import ssh
from .db import db
from .vault import vault

# 远端 python 片段:由面板自带的 python 执行,结果以 @@BT@@ 开头的 JSON 输出
_PY = r'''
import os, sys, json, time, re
P = os.environ.get("BT_PANEL", "/www/server/panel")
os.chdir(P); sys.path.insert(0, P + "/class"); sys.path.insert(0, P)
import public
req = json.loads(%s)
out = {}
users = lambda: public.M("users").where("id=?", (1,))
act = req["action"]
if act == "set":
    u = req.get("username")
    if u:
        if re.findall(r"[^\w,.]+", u) or u in ("admin", "root") or len(u) < 3:
            print("@@BT@@" + json.dumps({"error": "用户名至少 3 位,只能包含字母、数字、下划线、点,且不能是 admin / root"})); sys.exit(0)
        users().setField("username", u)
    pw = req.get("password")
    if pw:
        users().setField("password", public.password_salt(public.md5(pw), uid=1))
        out["verified"] = users().getField("password") == public.password_salt(public.md5(pw), uid=1)
elif act == "temp_login":
    now = int(time.time())
    public.M("temp_login").where("state=? and expire>?", (0, now)).delete()
    token, salt = public.GetRandomString(48), public.GetRandomString(12)
    row = {"token": public.md5(token + salt), "salt": salt, "state": 0, "login_time": 0,
           "login_addr": "", "expire": now + int(req.get("ttl", 900))}
    if not public.M("temp_login").count():
        row["id"] = 101
    public.M("temp_login").insert(row)
    out["token"] = token
out["username"] = users().getField("username")
def rd(name):
    try:
        return open(P + "/data/" + name).read().strip()
    except Exception:
        return ""
try:
    ver = re.search(r"g\.version\s*=\s*['\"]([0-9.]+)", open(P + "/class/common.py").read()).group(1)
except Exception:
    ver = ""
out["panel"] = {"bt_version": ver, "bt_port": rd("port.pl"), "bt_path": rd("admin_path.pl"),
                "bt_domain": rd("domain.conf"), "bt_ssl": "1" if os.path.exists(P + "/data/ssl.pl") else "0"}
print("@@BT@@" + json.dumps(out))
'''

_RUNNER = 'B="${BT_PANEL:-/www/server/panel}/pyenv/bin/python3"; [ -x "$B" ] || B=btpython; exec "$B" -'

INFO_KEYS = ("bt_version", "bt_port", "bt_path", "bt_ssl", "bt_domain", "bt_running")


def _root_exec(row, client, command, stdin=""):
    """以 root 身份执行(root 直接执行;否则自动使用免密 sudo 或带密码 sudo)。"""
    if row["username"] == "root":
        return ssh.run(client, command, 60, stdin)
    current = S.creds(row)["password"]
    mode = S._sudo_mode(client, current)
    wrapped = f"sh -c {shlex.quote(command)}"
    if mode == "nopasswd":
        return ssh.run(client, f"sudo -n {wrapped}", 60, stdin)
    if mode == "password":
        return ssh.run(client, f"sudo -S -p '' {wrapped}", 60, current + "\n" + stdin)
    raise S.ServiceError("操作宝塔需要 root 权限:该账号不是 root,也没有可用的 sudo(可在编辑中保存账号密码以使用 sudo)")


def _call(row, req: dict) -> dict:
    client = S.open_ssh(row)
    try:
        code, out, err, _ = _root_exec(row, client, _RUNNER, _PY % repr(json.dumps(req)))
    finally:
        client.close()
    m = re.search(r"@@BT@@(\{.*\})", out)
    if not m:
        raise S.ServiceError(f"宝塔操作失败: {(err or out).strip()[-300:] or f'exit {code}'}")
    data = json.loads(m.group(1))
    if data.get("error"):
        raise S.ServiceError(data["error"])
    return data


def panel_url(row, facts=None) -> str:
    f = facts if facts is not None else (json.loads(row["facts_json"] or "{}") or {})
    if not f.get("bt_port"):
        return ""
    proto = "https" if f.get("bt_ssl") == "1" else "http"
    host = f.get("bt_domain") or row["host"]
    return f"{proto}://{host}:{f['bt_port']}{f.get('bt_path') or ''}"


def info(row, include_secret=False) -> dict:
    f = json.loads(row["facts_json"] or "{}") or {}
    d = {
        "installed": bool(f.get("bt_installed")), "version": f.get("bt_version", ""),
        "running": f.get("bt_running") == "1", "port": f.get("bt_port", ""), "entrance": f.get("bt_path", ""),
        "ssl": f.get("bt_ssl") == "1", "url": panel_url(row, f), "username": row["bt_username"] or "",
        "has_password": bool(row["bt_password_enc"]), "updated_at": row["bt_updated_at"],
    }
    if include_secret:
        d["password"] = vault.decrypt(row["bt_password_enc"])
    return d


def refresh(row, actor="admin") -> dict:
    """重新读取宝塔信息(版本、端口、入口等随配置采集一起更新,用户名需 root 读取面板数据库)。"""
    S.collect_facts(row, actor=actor)
    row = S.get_row(row["id"])
    if not (json.loads(row["facts_json"] or "{}") or {}).get("bt_installed"):
        raise S.ServiceError("该服务器未安装宝塔面板")
    data = _call(row, {"action": "info"})
    # 非 root 账号采集配置时可能读不到面板文件,这里用 root 权限读到的值补全
    facts = json.loads(row["facts_json"] or "{}") or {}
    facts.update({k: v for k, v in (data.get("panel") or {}).items() if v})
    db.run("UPDATE servers SET bt_username=?, facts_json=? WHERE id=?",
           (data["username"], json.dumps(facts, ensure_ascii=False), row["id"]))
    return info(S.get_row(row["id"]))


def set_credentials(sid, username=None, password=None, actor="admin") -> dict:
    """修改宝塔面板账号密码;不指定密码时自动生成强密码。新密码加密保存,旧密码归档到历史。"""
    row = S.get_row(sid)
    username = (username or "").strip() or None
    if password is not None and password != "" and len(password) < 8:
        raise S.ServiceError("宝塔密码至少 8 位")
    password = password or S.generate_password(20)
    data = _call(row, {"action": "set", "username": username, "password": password})
    if not data.get("verified"):
        raise S.ServiceError("宝塔密码写入后校验失败,请在面板中确认")
    ts = S.now_iso()
    old = vault.decrypt(row["bt_password_enc"])
    with db.tx():
        if old:
            db.run("INSERT INTO password_history(server_id,password_enc,reason,created_at) VALUES(?,?,?,?)",
                   (sid, row["bt_password_enc"], f"宝塔旧密码 ({actor})", ts))
        db.run("UPDATE servers SET bt_username=?, bt_password_enc=?, bt_updated_at=? WHERE id=?",
               (data["username"], vault.encrypt(password), ts, sid))
    S.audit(actor, "bt.set_credentials", row, f"用户名 {data['username']}" + (" (已修改)" if username else ""))
    return {"server_id": sid, "server": row["name"], "username": data["username"], "password": password,
            "url": panel_url(row), "message": "宝塔账号密码已更新"}


def temp_login(sid, actor="admin", ttl=900) -> dict:
    """生成一次性临时登录链接(宝塔原生 tmp_token 机制,默认 15 分钟有效、用后即失效)。"""
    row = S.get_row(sid)
    f = json.loads(row["facts_json"] or "{}") or {}
    if not f.get("bt_port"):
        raise S.ServiceError("尚未采集到宝塔端口,请先刷新宝塔信息")
    data = _call(row, {"action": "temp_login", "ttl": ttl})
    proto = "https" if f.get("bt_ssl") == "1" else "http"
    url = f"{proto}://{f.get('bt_domain') or row['host']}:{f['bt_port']}/login?tmp_token={data['token']}"
    S.audit(actor, "bt.temp_login", row, f"有效期 {ttl // 60} 分钟")
    return {"server_id": sid, "server": row["name"], "url": url, "expires_in": ttl}
