"""SSH 基础能力:连接(带主机指纹校验)、执行命令。"""
import base64
import hashlib
import io
import socket
import time

import paramiko


class SSHError(Exception):
    pass


class AuthFailed(SSHError):
    pass


class HostKeyMismatch(SSHError):
    pass


def load_pkey(text: str, passphrase=None):
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(text), password=passphrase or None)
        except paramiko.PasswordRequiredException:
            raise SSHError("私钥已加密,需要填写私钥口令")
        except Exception:
            continue
    raise SSHError("无法解析私钥(支持 Ed25519 / ECDSA / RSA)")


def fingerprint(key) -> str:
    digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    return f"{key.get_name()} SHA256:{digest}"


class _TrustOnFirstUse(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        pass


def expand_proxy(cmd, host, port, user):
    """展开 OpenSSH ProxyCommand 中的 %h %p %r %% 占位符。"""
    out, i = [], 0
    while i < len(cmd):
        if cmd[i] == "%" and i + 1 < len(cmd):
            out.append({"h": str(host), "p": str(port), "r": str(user), "%": "%"}.get(cmd[i + 1], cmd[i:i + 2]))
            i += 2
        else:
            out.append(cmd[i])
            i += 1
    return "".join(out)


def connect(host, port, username, password=None, pkey_text=None, passphrase=None,
            known_host_key=None, timeout=10, proxy_command=None) -> paramiko.SSHClient:
    """连接服务器。known_host_key 为 "类型 base64",已记录时在认证前校验,防止中间人窃取密码。
    proxy_command 与 OpenSSH 的 ProxyCommand 相同(如 nc -X 5 -x 127.0.0.1:1080 %h %p)。"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_TrustOnFirstUse())
    if known_host_key:
        ktype, b64 = known_host_key.split(" ", 1)
        name = host if int(port) == 22 else f"[{host}]:{port}"
        client.get_host_keys().add(name, ktype, paramiko.PKey.from_type_string(ktype, base64.b64decode(b64)))
    kwargs = dict(hostname=host, port=int(port), username=username, timeout=timeout,
                  banner_timeout=timeout, auth_timeout=timeout, allow_agent=False, look_for_keys=False)
    if pkey_text:
        kwargs["pkey"] = load_pkey(pkey_text, passphrase)
    if password:
        kwargs["password"] = password
    try:
        if proxy_command:
            kwargs["sock"] = paramiko.ProxyCommand(expand_proxy(proxy_command, host, port, username))
        client.connect(**kwargs)
    except paramiko.BadHostKeyException:
        client.close()
        raise HostKeyMismatch("主机指纹与记录不一致,可能存在中间人攻击或服务器已重装")
    except paramiko.AuthenticationException:
        client.close()
        raise AuthFailed("认证失败(用户名/密码/私钥错误)")
    except (socket.timeout, TimeoutError):
        client.close()
        raise SSHError("连接超时")
    except (OSError, paramiko.SSHException, EOFError) as e:
        client.close()
        raise SSHError(f"连接失败: {e}")
    key = client.get_transport().get_remote_server_key()
    client.sv_host_key = f"{key.get_name()} {key.get_base64()}"
    client.sv_host_key_fp = fingerprint(key)
    return client


def generate_ed25519(comment):
    """生成新的 Ed25519 密钥对,返回 (OpenSSH 私钥文本, authorized_keys 公钥行)。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    key = ed25519.Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                             serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    return priv, f"{pub} {comment}"


def run_passwd(client, current, new, timeout=30):
    """在 PTY 中驱动交互式 passwd(无 root / sudo 权限的账号用),返回 (exit_code, 输出)。"""
    chan = client.get_transport().open_session(timeout=10)
    chan.get_pty(term="vt100", width=200, height=24)
    chan.settimeout(timeout)
    chan.exec_command("LC_ALL=C LANG=C passwd")
    buf, handled = "", 0
    deadline = time.time() + timeout
    while True:
        if chan.recv_ready():
            buf += chan.recv(4096).decode("utf-8", "replace")
            pending = buf[handled:].rstrip().lower()
            last = pending.splitlines()[-1].strip() if pending else ""
            if last.endswith(":"):
                if "current" in last or "old" in last or "(current)" in last:
                    chan.sendall((current + "\n").encode())
                elif "retype" in last or "again" in last or "re-enter" in last or "new" in last:
                    chan.sendall((new + "\n").encode())
                else:
                    continue
                handled = len(buf)
            continue
        if chan.exit_status_ready():
            break
        if time.time() > deadline:
            chan.close()
            return -1, buf + "\n[超时]"
        time.sleep(0.05)
    code = chan.recv_exit_status()
    chan.close()
    return code, buf


def run(client, command, timeout=60, stdin_data=None, max_bytes=1_000_000):
    """执行命令,返回 (exit_code, stdout, stderr, timed_out)。"""
    chan = client.get_transport().open_session(timeout=10)
    chan.settimeout(timeout)
    chan.exec_command(command)
    if stdin_data is not None:
        chan.sendall(stdin_data.encode())
    chan.shutdown_write()
    out, err = bytearray(), bytearray()
    deadline = time.time() + timeout
    timed_out = False
    while True:
        got = False
        if chan.recv_ready():
            data = chan.recv(65536)
            if len(out) < max_bytes:
                out += data
            got = True
        if chan.recv_stderr_ready():
            data = chan.recv_stderr(65536)
            if len(err) < max_bytes:
                err += data
            got = True
        if not got:
            if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                break
            if time.time() > deadline:
                timed_out = True
                break
            time.sleep(0.02)
    code = -1 if timed_out else chan.recv_exit_status()
    chan.close()
    return code, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), timed_out
