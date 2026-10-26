"""从本机 OpenSSH 配置(~/.ssh/config)导入服务器。

用 `ssh -G <别名>` 让 OpenSSH 自己解析最终生效的配置(含 Match / Include / ProxyCommand),
私钥在服务端读取后直接加密入库,主机指纹取自 ~/.ssh/known_hosts。
"""
import base64
import glob
import ipaddress
import os
import re
import subprocess
from pathlib import Path

import paramiko

from . import services as S
from . import ssh
from .db import db

SSH_DIR = Path(os.path.expanduser("~/.ssh"))
KEY_PREF = ["ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "rsa-sha2-512", "ssh-rsa"]


def _config_files(path, seen=None):
    seen = seen if seen is not None else set()
    path = Path(path)
    if path in seen or not path.is_file():
        return []
    seen.add(path)
    files = [path]
    for line in path.read_text(errors="ignore").splitlines():
        m = re.match(r"\s*Include\s+(.+)", line, re.I)
        if m:
            for pat in m.group(1).split():
                pat = os.path.expanduser(pat)
                if not os.path.isabs(pat):
                    pat = str(SSH_DIR / pat)
                for f in sorted(glob.glob(pat)):
                    files += _config_files(f, seen)
    return files


def aliases():
    """配置中所有不含通配符的 Host 别名(保持出现顺序)。"""
    out = []
    for f in _config_files(SSH_DIR / "config"):
        for line in f.read_text(errors="ignore").splitlines():
            m = re.match(r"\s*Host\s+(.+)", line, re.I)
            if not m:
                continue
            for name in m.group(1).split():
                if not any(c in name for c in "*?!") and name not in out:
                    out.append(name)
    return out


def resolve(alias) -> dict:
    p = subprocess.run(["ssh", "-G", alias], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    cfg = {}
    for line in p.stdout.splitlines():
        k, _, v = line.partition(" ")
        if k == "identityfile":
            cfg.setdefault(k, []).append(v)
        elif k not in cfg:
            cfg[k] = v
    return cfg


def _load_key(path):
    """返回 (私钥文本, 说明);带口令或无法解析时文本为 None。"""
    try:
        text = Path(path).read_text()
    except OSError:
        return None, "无法读取"
    try:
        ssh.load_pkey(text)
        return text, ""
    except ssh.SSHError as e:
        return None, str(e)


def _known_host(host, port):
    name = host if int(port) == 22 else f"[{host}]:{port}"
    p = subprocess.run(["ssh-keygen", "-F", name, "-f", str(SSH_DIR / "known_hosts")], capture_output=True, text=True, timeout=10)
    keys = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and not line.startswith("#"):
            keys[parts[1]] = parts[2]
    for t in KEY_PREF:
        if t in keys:
            try:
                key = paramiko.PKey.from_type_string(t, base64.b64decode(keys[t]))
                return f"{t} {keys[t]}", ssh.fingerprint(key)
            except Exception:
                continue
    return None, None


def _is_private(host):
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def scan() -> list:
    existing = {r["name"] for r in db.all("SELECT name FROM servers")}
    items = []
    for alias in aliases():
        cfg = resolve(alias)
        host, port, user = cfg.get("hostname", alias), int(cfg.get("port", 22)), cfg.get("user", "root")
        proxy = cfg.get("proxycommand", "")
        proxy = "" if proxy.lower() == "none" else proxy
        key_path, key_note = None, "未找到可用私钥"
        for f in cfg.get("identityfile", []):
            path = os.path.expanduser(f)
            if os.path.isfile(path):
                text, note = _load_key(path)
                if text:
                    key_path, key_note = path, ""
                    break
                key_note = f"{f}: {note}"
        host_key, fp = _known_host(host, port)
        items.append({
            "alias": alias, "host": host, "port": port, "username": user, "proxy_command": proxy,
            "key_path": key_path.replace(os.path.expanduser("~"), "~") if key_path else None, "key_note": key_note,
            "host_key_fp": fp, "group": "内网" if proxy or _is_private(host) else "公网",
            "exists": alias in existing,
        })
    return items


def import_aliases(selected, actor="admin") -> dict:
    by_alias = {i["alias"]: i for i in scan()}
    created, skipped, errors = [], [], []
    for alias in selected:
        item = by_alias.get(alias)
        if not item:
            errors.append(f"{alias}: 配置中不存在")
            continue
        if item["exists"]:
            skipped.append(alias)
            continue
        key_text = _load_key(os.path.expanduser(item["key_path"]))[0] if item["key_path"] else None
        try:
            s = S.save_server({
                "name": alias, "host": item["host"], "port": item["port"], "username": item["username"],
                "auth_type": "key" if key_text else "password", "private_key": key_text,
                "proxy_command": item["proxy_command"], "group_name": item["group"],
                "notes": f"从本机 ~/.ssh/config 导入" + (f"(私钥 {item['key_path']})" if key_text else f"({item['key_note']},请补充密码)"),
            }, actor=actor)
            host_key, fp = _known_host(item["host"], item["port"])
            db.run("UPDATE servers SET host_key=?, host_key_fp=?, source='ssh_config' WHERE id=?", (host_key, fp, s["id"]))
            created.append(alias)
        except Exception as e:
            errors.append(f"{alias}: {e}")
    S.audit(actor, "server.import_local", detail=f"导入 {len(created)} 台,跳过 {len(skipped)} 台")
    return {"created": created, "skipped": skipped, "errors": errors}
