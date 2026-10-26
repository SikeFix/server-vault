"""macOS 钥匙串:可选地记住主密码,服务启动时自动解锁(保证无人值守时定时轮换不中断)。

通过系统自带的 `security` 命令交互模式写入,避免主密码出现在进程参数里。
"""
import os
import subprocess
import sys

SERVICE = "Server Vault"
ACCOUNT = "master-password"


def available() -> bool:
    return sys.platform == "darwin" and os.environ.get("SV_KEYCHAIN", "1") != "0"


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def get():
    if not available():
        return None
    p = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"],
                       capture_output=True, text=True, timeout=10)
    return p.stdout.rstrip("\n") if p.returncode == 0 else None


def save(password: str) -> bool:
    if not available():
        return False
    cmd = f"add-generic-password -U -s {_q(SERVICE)} -a {_q(ACCOUNT)} -l {_q('Server Vault 主密码')} -w {_q(password)}\n"
    p = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=10)
    return p.returncode == 0 and get() == password


def delete():
    if available():
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", ACCOUNT], capture_output=True, timeout=10)
