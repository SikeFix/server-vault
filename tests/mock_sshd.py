"""测试用的模拟 SSH 服务器(paramiko 实现)。

- 用户密码保存在内存 USERS 中,模拟 chpasswd / sudo 修改密码
- 普通命令在本机 sh 中执行,PATH 前置 fake bin(nginx / ss / docker / sudo 假实现)
- 支持 PTY shell,用于测试 Web 终端
"""
import os
import pty
import select
import shlex
import socket
import subprocess
import threading
from pathlib import Path

import paramiko

HERE = Path(__file__).resolve().parent
FAKE_BIN = HERE / "fakebin"
USERS = {"root": "InitPass#123", "deploy": "DeployPass#1"}
LOCK = threading.Lock()


class Server(paramiko.ServerInterface):
    def __init__(self):
        self.username = None
        self.event = threading.Event()
        self.chan_cmd = {}

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_password(self, username, password):
        with LOCK:
            ok = USERS.get(username) == password
        if ok:
            self.username = username
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_exec_request(self, channel, command):
        threading.Thread(target=handle_exec, args=(channel, command.decode(), self.username), daemon=True).start()
        return True

    def check_channel_pty_request(self, channel, term, width, height, pw, ph, modes):
        return True

    def check_channel_shell_request(self, channel):
        threading.Thread(target=handle_shell, args=(channel,), daemon=True).start()
        return True

    def check_channel_window_change_request(self, channel, width, height, pw, ph):
        return True


def read_stdin(chan):
    buf = b""
    while True:
        d = chan.recv(65536)
        if not d:
            break
        buf += d
    return buf.decode()


def apply_chpasswd(lines):
    with LOCK:
        for line in lines:
            if ":" in line:
                u, p = line.split(":", 1)
                if u not in USERS:
                    return False
                USERS[u] = p
    return True


def env():
    e = dict(os.environ)
    e["PATH"] = f"{FAKE_BIN}:{e['PATH']}"
    return e


def handle_exec(chan, cmd, user):
    code = 0
    try:
        sudo_prefix = "sudo -S -p '' "
        if cmd == "chpasswd":
            data = read_stdin(chan)
            code = 0 if user == "root" and apply_chpasswd(data.splitlines()) else 1
        elif cmd.startswith(sudo_prefix):
            rest = cmd[len(sudo_prefix):]
            data = read_stdin(chan)
            first, _, remain = data.partition("\n")
            with LOCK:
                ok = USERS.get(user) == first
            if not ok:
                chan.sendall_stderr(b"sudo: incorrect password\n")
                code = 1
            elif rest == "chpasswd":
                code = 0 if apply_chpasswd(remain.splitlines()) else 1
            elif rest == "-k true":
                code = 0
            else:
                code = run_local(chan, rest, remain)
        elif cmd == "sh -s":
            code = run_local(chan, "sh -s", read_stdin(chan))
        else:
            code = run_local(chan, cmd, read_stdin(chan))
    except Exception as e:
        chan.sendall_stderr(f"mock error: {e}\n".encode())
        code = 255
    chan.send_exit_status(code)
    chan.close()


def run_local(chan, cmd, stdin):
    p = subprocess.run(["sh", "-c", cmd], input=stdin.encode(), capture_output=True, env=env(), timeout=60)
    if p.stdout:
        chan.sendall(p.stdout)
    if p.stderr:
        chan.sendall_stderr(p.stderr)
    return p.returncode


def handle_shell(chan):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update({"PS1": "mock$ ", "TERM": "xterm-256color"})
        os.execvp("sh", ["sh", "-i"])
    try:
        while True:
            r, _, _ = select.select([fd, chan], [], [], 0.5)
            if fd in r:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                chan.sendall(data)
            if chan in r:
                data = chan.recv(4096)
                if not data:
                    break
                os.write(fd, data)
    finally:
        try:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        except Exception:
            pass
        chan.send_exit_status(0)
        chan.close()


def serve(port, host_key, stop_event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(50)
    sock.settimeout(0.5)

    def conn_thread(client):
        t = paramiko.Transport(client)
        t.add_server_key(host_key)
        try:
            t.start_server(server=Server())
            while t.is_active():
                t.join(1)
        except Exception:
            pass

    while not (stop_event and stop_event.is_set()):
        try:
            client, _ = sock.accept()
        except socket.timeout:
            continue
        threading.Thread(target=conn_thread, args=(client,), daemon=True).start()
    sock.close()


def start(port=2222, host_key=None):
    stop = threading.Event()
    th = threading.Thread(target=serve, args=(port, host_key or paramiko.RSAKey.generate(2048), stop), daemon=True)
    th.start()
    return stop


if __name__ == "__main__":
    # 本地演示:python tests/mock_sshd.py → 127.0.0.1:2222,root / InitPass#123,deploy / DeployPass#1
    import time
    start(2222)
    print("模拟 SSH 服务器已启动: 127.0.0.1:2222  (root / InitPass#123, deploy / DeployPass#1)")
    while True:
        time.sleep(3600)
