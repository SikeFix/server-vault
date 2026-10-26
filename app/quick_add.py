"""快速添加:只填 IP + 账号密码,自动完成命名、安装本机 SSH 公钥、写入 ~/.ssh/config 与 known_hosts、检测与采集。"""
import re
import shlex
from datetime import timedelta

from . import local_import as L
from . import services as S
from . import ssh
from .db import db


def _unique_name(base, host):
    base = (base or host).strip()
    for cand in (base, f"{base}-{host}", *(f"{base}-{i}" for i in range(2, 100))):
        if not db.one("SELECT id FROM servers WHERE name=?", (cand,)):
            return cand
    raise S.ServiceError("无法生成唯一名称")


def _install_key(client, pub_line):
    b64 = pub_line.split()[1]
    script = f"""set -e
umask 077
mkdir -p ~/.ssh
f=~/.ssh/authorized_keys
touch "$f"
if grep -q -F {shlex.quote(b64)} "$f"; then echo exists; exit 0; fi
if [ -n "$(tail -c1 "$f")" ]; then echo >> "$f"; fi
echo {shlex.quote(pub_line)} >> "$f"
echo added
"""
    code, out, err, _ = ssh.run(client, "sh -s", 30, script)
    if code != 0:
        raise S.ServiceError(f"安装公钥失败: {(err or out).strip()[:200]}")
    return out.strip()


def quick_add(host, password, port=22, username="root", name=None, group=None, server_type="auto",
              temp_days=None, install_key=True, write_config=True, actor="admin") -> dict:
    host, username = (host or "").strip(), (username or "root").strip()
    port = int(port or 22)
    if not host or not password:
        raise S.ServiceError("IP 和密码必填")
    dup = db.one("SELECT name FROM servers WHERE host=? AND port=? AND username=?", (host, port, username))
    if dup:
        raise S.ServiceError(f"已存在同一服务器: {dup['name']}")
    steps = []
    known_key, _ = L._known_host(host, port)
    try:
        client = ssh.connect(host, port, username, password=password, known_host_key=known_key, timeout=12)
    except ssh.SSHError as e:
        raise S.ServiceError(f"连接失败: {e}")
    host_key, fp = client.sv_host_key, client.sv_host_key_fp
    steps.append(f"密码登录成功,主机指纹 {fp}" + (" (与 known_hosts 一致)" if known_key else ""))
    keypair = L.local_keypair() if install_key else None
    try:
        hostname = ssh.run(client, "hostname", 10)[1].strip().split(".")[0]
        if keypair:
            res = _install_key(client, keypair[1])
            steps.append(f"本机公钥 {keypair[2].rsplit('/', 1)[-1]}.pub " + ("已存在" if res == "exists" else "已安装到 authorized_keys"))
        elif install_key:
            steps.append("未找到可用的本机密钥(~/.ssh/id_ed25519 或 id_rsa),仅使用密码登录")
    finally:
        client.close()
    if keypair:
        try:
            ssh.connect(host, port, username, pkey_text=keypair[0], known_host_key=host_key, timeout=12).close()
            steps.append("密钥登录验证通过")
        except ssh.SSHError as e:
            steps.append(f"密钥登录验证失败({e}),将使用密码登录")
            keypair = None
    final_name = _unique_name(name or (hostname if re.fullmatch(r"[\w.-]+", hostname or "") and hostname not in ("localhost",) else ""), host)
    expires = (S.now_dt() + timedelta(days=int(temp_days))).isoformat() if temp_days else None
    s = S.save_server({
        "name": final_name, "host": host, "port": port, "username": username, "password": password,
        "auth_type": "key" if keypair else "password", "private_key": keypair[0] if keypair else None,
        "group_name": group or ("临时" if temp_days else "默认"), "server_type": server_type,
        "temp_expires_at": expires,
        "notes": "快速添加" + (f",临时服务器({temp_days} 天后自动移除)" if temp_days else ""),
    }, actor=actor)
    db.run("UPDATE servers SET host_key=?, host_key_fp=?, source='quick_add' WHERE id=?", (host_key, fp, s["id"]))
    if write_config:
        taken = L.config_aliases()
        alias = final_name if re.fullmatch(r"[A-Za-z0-9._-]+", final_name) and final_name not in taken else host
        L.add_config_block(final_name, alias, host, port, username, keypair[2] if keypair else None)
        L.add_known_host(host, port, host_key)
        steps.append(f"已写入 ~/.ssh/config,终端可直接 ssh {alias}")
    row = S.get_row(s["id"])
    st = S.check_status(row)
    steps.append(f"状态检测:{st['status']}" + (f" · {st['latency_ms']} ms" if st["latency_ms"] is not None else ""))
    if st["status"] == "online":
        try:
            facts = S.collect_facts(S.get_row(s["id"]), actor=actor)["facts"]
            steps.append(f"配置采集完成:{facts.get('os') or ''} {facts.get('cpu_cores') or '?'} 核"
                         + (f" · {facts['gpu_summary']}" if facts.get("gpu_summary") else "")
                         + (f" · 宝塔 {facts.get('bt_version')}" if facts.get("bt_installed") else ""))
            if facts.get("bt_installed"):
                from . import btpanel
                btpanel.refresh(S.get_row(s["id"]), actor=actor)
        except Exception as e:
            steps.append(f"配置采集失败:{e}")
    S.audit(actor, "server.quick_add", S.get_row(s["id"]), "; ".join(steps)[:1500])
    return {"server": S.public(S.get_row(s["id"])), "steps": steps}


def parse_batch(text, defaults) -> list:
    """批量格式:每行 `IP 密码 [端口] [用户名]`(空格或逗号分隔)。"""
    items = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = re.split(r"[\s,]+", line)
        if len(p) < 2:
            raise S.ServiceError(f"格式错误: {line}")
        items.append({**defaults, "host": p[0], "password": p[1],
                      "port": int(p[2]) if len(p) > 2 and p[2].isdigit() else defaults.get("port", 22),
                      "username": p[3] if len(p) > 3 else defaults.get("username", "root")})
    return items
