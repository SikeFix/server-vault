"""端到端测试:启动模拟 SSH 服务器 + Server Vault 后端,覆盖管理端、轮换、采集、Agent REST、MCP、Web 终端。

用法: .venv/bin/python tests/e2e_test.py
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import paramiko
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mock_sshd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
MASTER = "Master#Pass2026"
passed = 0


def ok(cond, msg):
    global passed
    if not cond:
        raise AssertionError(msg)
    passed += 1
    print(f"  ✓ {msg}")


def main():
    data_dir = tempfile.mkdtemp(prefix="sv-test-")
    ssh_dir = Path(tempfile.mkdtemp(prefix="sv-ssh-"))
    sys.path.insert(0, str(ROOT))
    from app.ssh import generate_ed25519
    priv, pub = generate_ed25519("me@mac")
    (ssh_dir / "id_ed25519").write_text(priv)
    (ssh_dir / "id_ed25519").chmod(0o600)
    (ssh_dir / "id_ed25519.pub").write_text(pub + "\n")
    (ssh_dir / "config").write_text("Host existing\n  HostName 10.0.0.1\n")
    global SSH_DIR
    SSH_DIR = ssh_dir
    mock_stop = mock_sshd.start(2222)
    mock2_stop = mock_sshd.start(2223, paramiko.RSAKey.generate(2048))
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
                           cwd=ROOT, env={**os.environ, "SV_DATA_DIR": data_dir, "SV_SSH_DIR": str(ssh_dir), "SV_KEYCHAIN": "0"},
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                httpx.get(BASE + "/api/state", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        run_tests(mock2_stop, data_dir)
        print(f"\n全部通过:{passed} 项断言")
    finally:
        srv.terminate()
        out = srv.communicate(timeout=5)[0].decode()
        if "Traceback" in out:
            print("---- 服务端日志中的异常 ----")
            print(out[-4000:])
        mock_stop.set()


def run_tests(mock2_stop, data_dir):
    c = httpx.Client(base_url=BASE, timeout=60)

    print("[1] 保险库初始化与登录")
    st = c.get("/api/state").json()
    ok(st["initialized"] is False and st["authed"] is False, "初始状态未初始化")
    ok(c.get("/api/servers").status_code == 401, "未登录访问被拒绝")
    ok(c.post("/api/init", json={"password": ""}).status_code == 400, "空主密码被拒绝")
    ok(c.post("/api/init", json={"password": MASTER}).status_code == 200, "初始化保险库")
    ok(c.get("/api/state").json()["authed"], "初始化后已登录")

    print("[2] 服务器管理")
    r = c.post("/api/servers", json={"name": "web-01", "host": "127.0.0.1", "port": 2222, "username": "root",
                                     "password": "InitPass#123", "group_name": "生产", "location": "上海机房",
                                     "provider": "自建", "tags": "web, nginx", "rotation_enabled": True, "rotation_days": 30})
    ok(r.status_code == 200, f"新增服务器 {r.text[:200]}")
    web = r.json()
    ok(web["tags"] == ["web", "nginx"] and web["next_rotation_at"], "标签解析、轮换计划生成")
    ok("InitPass#123" not in json.dumps(web) and "_enc" not in json.dumps(web), "列表不泄露密码")
    dep = c.post("/api/servers", json={"name": "app-02", "host": "127.0.0.1", "port": 2222, "username": "deploy",
                                       "password": "DeployPass#1", "use_sudo": True, "group_name": "测试"}).json()
    bad = c.post("/api/servers", json={"name": "bad-03", "host": "127.0.0.1", "port": 2222, "username": "root",
                                       "password": "wrong", "group_name": "测试"}).json()
    off = c.post("/api/servers", json={"name": "off-04", "host": "127.0.0.1", "port": 2299, "password": "x"}).json()
    ok(c.post("/api/servers", json={"name": "web-01", "host": "1.1.1.1"}).status_code == 400, "重名被拒绝")
    imp = c.post("/api/servers/import", json={"csv": "name,host,port,username,password,group\nimp-05,127.0.0.1,2223,root,InitPass#123,导入"}).json()
    ok(imp["created"] == ["imp-05"] and not imp["errors"], "CSV 批量导入")

    print("[3] 批量状态检测")
    res = {x["server"]: x for x in c.post("/api/check", json={}).json()}
    ok(res["web-01"]["status"] == "online" and res["web-01"]["latency_ms"] is not None, "web-01 在线")
    ok(res["app-02"]["status"] == "online", "app-02 (deploy) 在线")
    ok(res["bad-03"]["status"] == "auth_failed", "错误密码 → 认证失败")
    ok(res["off-04"]["status"] == "offline", "端口不通 → 离线")
    ok(res["web-01"]["detail"].get("load"), f"采集到负载 {res['web-01']['detail']}")
    s = c.get(f"/api/servers/{web['id']}").json()
    ok(s["host_key_fp"] and s["host_key_fp"].startswith("ssh-"), "首次连接记录主机指纹")

    print("[4] 配置采集 & 网站扫描")
    f = c.post(f"/api/servers/{web['id']}/facts").json()["facts"]
    ok(f["cpu_cores"] and f["mem_total"] and f["os"], f"硬件配置: {f['os']} / {f['cpu_cores']} 核 / {f['mem_total']}")
    ok(f["geo"].get("region") == "内网地址", "内网 IP 识别")
    sites = c.post(f"/api/servers/{web['id']}/sites").json()
    doms = {d for x in sites["nginx"] for d in x["domains"]}
    ok({"shop.example.com", "www.shop.example.com", "blog.example.com", "_"} <= doms, f"解析 nginx 域名 {doms}")
    shop = next(x for x in sites["nginx"] if "www.shop.example.com" in x["domains"])
    ok(shop["ssl"] and shop["root"] == "/var/www/shop" and shop["proxy"] == ["http://api_backend"], "SSL/根目录/反代解析")
    ok(shop["upstreams"] == {"api_backend": ["127.0.0.1:8080", "127.0.0.1:8081"]}, "upstream 解析")
    ok(shop.get("cert_expires", "").startswith("2027-01-10"), "证书到期时间")
    ok(any(x["type"] == "stream" for x in sites["nginx"]), "stream 块识别")
    ok({p["port"] for p in sites["ports"]} >= {22, 80, 443, 2368}, "监听端口解析")
    ok([d["name"] for d in sites["docker"]] == ["ghost", "redis"], "Docker 容器解析")
    ok(c.get(f"/api/servers/{web['id']}/sites").json()["nginx"], "网站结果已缓存")

    print("[5] 执行命令")
    r = c.post(f"/api/servers/{web['id']}/exec", json={"command": "echo hello && echo oops >&2; exit 3"}).json()
    ok(r["stdout"].strip() == "hello" and "oops" in r["stderr"] and r["exit_code"] == 3, "stdout/stderr/退出码")
    r = c.post(f"/api/servers/{dep['id']}/exec", json={"command": "echo via-sudo", "sudo": True}).json()
    ok(r["stdout"].strip() == "via-sudo" and r["exit_code"] == 0, "sudo 执行")
    r = c.post("/api/exec", json={"ids": [web["id"], dep["id"], off["id"]], "command": "echo hi"}).json()
    ok([x["ok"] for x in r] == [True, True, False], "批量执行(离线机器单独失败)")
    r = c.post(f"/api/servers/{web['id']}/exec", json={"command": "sleep 5", "timeout": 1}).json()
    ok(r["timed_out"] and r["exit_code"] == -1, "命令超时")

    print("[6] 密码轮换")
    r = c.post(f"/api/servers/{web['id']}/rotate").json()
    ok(r["verified"], f"root 轮换并验证 {r}")
    new_root = mock_sshd.USERS["root"]
    ok(new_root != "InitPass#123" and len(new_root) == 24, "服务器密码已变更为 24 位新密码")
    sec = c.get(f"/api/servers/{web['id']}/secret").json()
    ok(sec["password"] == new_root and sec["pending_password"] is None, "保险库保存了新密码")
    hist = c.get(f"/api/servers/{web['id']}/history").json()
    ok(hist[0]["password"] == "InitPass#123", "旧密码进入历史")
    s = c.get(f"/api/servers/{web['id']}").json()
    ok(s["rotation_status"] == "ok" and s["last_rotated_at"] and s["next_rotation_at"] > s["last_rotated_at"], "轮换时间更新")
    r = c.post(f"/api/servers/{dep['id']}/rotate", json={"password": "Custom#Pass99"}).json()
    ok(r["verified"] and mock_sshd.USERS["deploy"] == "Custom#Pass99", "非 root + sudo 自定义密码轮换")
    r = c.post(f"/api/servers/{bad['id']}/rotate")
    ok(r.status_code == 400 and "连接失败" in r.json()["detail"], "连接失败的轮换报错")
    s = c.get(f"/api/servers/{bad['id']}").json()
    ok(s["rotation_status"] == "failed" and not s["has_pending_password"], "失败状态记录且不残留待生效密码")

    print("[6b] 非 root 账号 / SSH 密钥轮换")
    sys.path.insert(0, str(ROOT))
    from app.ssh import generate_ed25519
    mk = lambda name, user, **kw: c.post("/api/servers", json={"name": name, "host": "127.0.0.1", "port": 2222,
                                                                 "username": user, "group_name": "其他", **kw}).json()
    ops = mk("ops-06", "ops", password="OpsPass#1")
    r = c.post(f"/api/servers/{ops['id']}/rotate").json()
    ok(r["method"] == "sudo -n chpasswd" and r["verified"] and mock_sshd.USERS["ops"] != "OpsPass#1", "免密 sudo → sudo -n chpasswd")
    dev = mk("dev-07", "dev", password="DevPass#1")
    r = c.post(f"/api/servers/{dev['id']}/rotate").json()
    ok(r["method"] == "passwd" and r["verified"], f"无 sudo → 交互式 passwd ({r['message']})")
    ok(c.get(f"/api/servers/{dev['id']}/secret").json()["password"] == mock_sshd.USERS["dev"] != "DevPass#1", "passwd 修改后保险库一致")
    priv, pub = generate_ed25519("my-laptop")
    ak = mock_sshd.home("keyuser") / ".ssh" / "authorized_keys"
    ak.write_text(pub + "\n")
    ku = mk("key-08", "keyuser", auth_type="key", private_key=priv)
    ok(c.post("/api/check", json={"ids": [ku["id"]]}).json()[0]["status"] == "online", "仅密钥账号在线")
    r = c.post(f"/api/servers/{ku['id']}/rotate")
    ok(r.status_code == 428, "未保存密码且无 sudo → 428 请求当前密码")
    r = c.post(f"/api/servers/{ku['id']}/rotate", json={"current_password": "wrong"})
    ok(r.status_code == 400 and mock_sshd.USERS["keyuser"] == "KeyUser#1", "当前密码错误 → 失败且密码未变")
    r = c.post(f"/api/servers/{ku['id']}/rotate", json={"current_password": "KeyUser#1"}).json()
    sec = c.get(f"/api/servers/{ku['id']}/secret").json()
    ok(r["method"] == "passwd" and sec["password"] == mock_sshd.USERS["keyuser"] != "KeyUser#1", "提供一次当前密码后轮换并保存")
    ok(c.post(f"/api/servers/{ku['id']}/rotate").json()["verified"], "之后无需再输入当前密码")
    c.post(f"/api/servers/{ku['id']}/rotate-key").raise_for_status()
    t1 = ak.read_text()
    k1 = c.get(f"/api/servers/{ku['id']}/secret").json()["private_key"]
    ok("my-laptop" in t1 and t1.count("server-vault#") == 1 and k1 != priv, "密钥轮换:新增本系统公钥,保留用户自己的公钥")
    c.post(f"/api/servers/{ku['id']}/rotate-key").raise_for_status()
    t2 = ak.read_text()
    old_b64 = next(l.split()[1] for l in t1.splitlines() if "server-vault#" in l)
    ok("my-laptop" in t2 and t2.count("server-vault#") == 1 and old_b64 not in t2, "再次轮换:旧的本系统公钥被移除")
    ok(c.post("/api/check", json={"ids": [ku["id"]]}).json()[0]["status"] == "online", "新密钥可登录")
    hist = c.get(f"/api/servers/{ku['id']}/history").json()
    ok(sum("PRIVATE KEY" in h["password"] for h in hist) == 2, "旧私钥归档到历史")

    print("[6c] 宝塔面板")
    import hashlib
    bt_db = lambda: json.load(open(mock_sshd.BT_DIR / "data" / "fake_db.json"))
    salt = lambda pw: hashlib.md5((hashlib.md5(pw.encode()).hexdigest() + "|salt|1").encode()).hexdigest()
    f = c.post(f"/api/servers/{web['id']}/facts").json()["facts"]
    ok(f.get("bt_installed") == "1" and f.get("bt_version") == "11.8.1" and f.get("bt_port") == "36090"
       and f.get("bt_path") == "/abc123" and f.get("bt_ssl") == "1", "采集宝塔版本 / 端口 / 安全入口 / SSL")
    bt = c.post(f"/api/servers/{web['id']}/bt/refresh").json()
    ok(bt["username"] == "bt_admin" and bt["url"] == "https://127.0.0.1:36090/abc123", "读取宝塔用户名与面板地址")
    r = c.post(f"/api/servers/{web['id']}/bt/credentials", json={}).json()
    ok(len(r["password"]) == 20 and r["username"] == "bt_admin", "自动生成 20 位宝塔密码")
    ok(bt_db()["users"][0]["password"] == salt(r["password"]), "按宝塔算法写入面板数据库")
    ok(c.get(f"/api/servers/{web['id']}/bt/secret").json()["password"] == r["password"], "宝塔密码加密保存可查看")
    r2 = c.post(f"/api/servers/{web['id']}/bt/credentials", json={"username": "ops_admin", "password": "MyBtPass#2026"}).json()
    ok(bt_db()["users"][0]["username"] == "ops_admin" and bt_db()["users"][0]["password"] == salt("MyBtPass#2026"), "同时修改用户名和密码")
    ok(any(h["reason"].startswith("宝塔旧密码") and h["password"] == r["password"] for h in c.get(f"/api/servers/{web['id']}/history").json()), "宝塔旧密码进入历史")
    ok(c.post(f"/api/servers/{web['id']}/bt/credentials", json={"username": "admin"}).status_code == 400, "拒绝 admin 等弱用户名")
    t = c.post(f"/api/servers/{web['id']}/bt/temp-login").json()
    tok = t["url"].split("tmp_token=")[1]
    ok(t["url"].startswith("https://127.0.0.1:36090/login?tmp_token=") and any(
        x["token"] == hashlib.md5((tok + x["salt"]).encode()).hexdigest() for x in bt_db()["temp_login"]), "生成宝塔一次性临时登录令牌")
    c.post(f"/api/servers/{dep['id']}/facts")
    ok(c.post(f"/api/servers/{dep['id']}/bt/refresh").json()["username"] == "ops_admin", "非 root 账号通过 sudo 操作宝塔")
    c.post(f"/api/servers/{dev['id']}/facts")
    r = c.post(f"/api/servers/{dev['id']}/bt/refresh")
    ok(r.status_code == 400 and "root" in r.json()["detail"], "无 sudo 账号给出权限提示")
    res = c.post("/api/bt/reset", json={"ids": [web["id"], dep["id"]]}).json()
    ok(all(x["ok"] for x in res) and bt_db()["users"][0]["password"] == salt(c.get(f"/api/servers/{dep['id']}/bt/secret").json()["password"]), "批量修改宝塔密码")

    print("[6d] 快速添加 / 临时服务器 / 算力服务器")
    ok(c.post("/api/servers/quick", json={"host": "127.0.0.1", "port": 2222, "password": "x"}).json()["detail"].startswith("已存在"), "重复服务器被拒绝")
    r = c.post("/api/servers/quick", json={"host": "127.0.0.1", "port": 2222, "username": "gpu", "password": "GpuPass#1", "temp_days": 7}).json()
    q = r["server"]
    ok(q["status"] == "online" and q["auth_type"] == "key" and q["group_name"] == "临时", f"只填 IP+密码即添加成功并自动检测 ({q['name']})")
    ok(any("公钥" in x for x in r["steps"]) and any("config" in x for x in r["steps"]), "步骤:安装公钥、写入 ssh config")
    ak = (mock_sshd.home("gpu") / ".ssh" / "authorized_keys").read_text()
    ok("me@mac" in ak, "本机公钥已写入服务器 authorized_keys")
    cfg = (SSH_DIR / "config").read_text()
    ok(f"# >>> server-vault: {q['name']} >>>" in cfg and "HostName 127.0.0.1" in cfg and "Port 2222" in cfg and "Host existing" in cfg, "写入 ~/.ssh/config 且保留原有内容")
    ok("[127.0.0.1]:2222" in (SSH_DIR / "known_hosts").read_text(), "主机指纹写入 known_hosts")
    ok(q["temp_expires_at"] and q["temp_expires_at"] > "2026", "临时服务器带到期时间")
    ok(q["kind"] == "compute" and q["facts"]["gpu_summary"] == "2× NVIDIA A100-SXM4-80GB" and q["facts"]["gpu_cuda"] == "12.4", "自动识别为算力服务器(2× A100 / CUDA 12.4)")
    ok(len(q["status_detail"]["gpus"]) == 2 and q["status_detail"]["gpus"][0]["util"] == 87, "状态检测含每张卡利用率")
    g = c.get(f"/api/servers/{q['id']}/gpu").json()
    ok(g["gpus"][0]["power"] == 310.5 and g["processes"][0]["pid"] == 1 and g["processes"][0]["gpu"] == 0 and g["processes"][0]["user"], "实时 GPU:功耗、占用进程及用户")
    ok(c.get(f"/api/servers/{web['id']}/gpu").status_code == 400, "普通服务器查询 GPU 给出提示")
    kinds = {x["name"]: x["kind"] for x in c.get("/api/servers?kind=compute").json()}
    ok(list(kinds.values()) == ["compute"], "按类型筛选算力服务器")
    c.put(f"/api/servers/{q['id']}", json={"server_type": "normal"})
    ok(c.get(f"/api/servers/{q['id']}").json()["kind"] == "normal", "可手动指定类型")
    c.put(f"/api/servers/{q['id']}", json={"server_type": "auto"})
    res = c.post("/api/servers/quick", json={"batch": "127.0.0.1 OpsPass#bad 2222 ops\n127.0.0.1 DevPass#1 2222 nosuch", "install_key": False}).json()
    ok(len(res) == 2 and not any(x["ok"] for x in res), "批量添加:错误密码逐条报告")
    import sqlite3
    con = sqlite3.connect(Path(data_dir) / "vault.db")
    con.execute("UPDATE servers SET temp_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (q["id"],))
    con.commit()
    con.close()
    ok(all(x["id"] != q["id"] for x in c.get("/api/servers").json()), "临时服务器到期自动移除")
    ok(f"server-vault: {q['name']}" not in (SSH_DIR / "config").read_text() and "Host existing" in (SSH_DIR / "config").read_text(), "移除时同步清理 ssh config")

    print("[6e] 服务器地图")
    mp = c.get("/api/map").json()
    ok(mp["hub"]["name"] == "湖南 · 湘潭" and mp["hub"]["coord"] == [112.9442, 27.8297], "内网枢纽默认湖南湘潭")
    ok(all(p["intranet"] and p["coord"] == mp["hub"]["coord"] for p in mp["points"] if not p["manual"]), "内网 / 本地地址服务器定位到湘潭")
    ok(next(p for p in mp["points"] if p["name"] == "web-01")["coord"] == [121.4737, 31.2304], "手填「上海机房」定位到上海")
    c.put("/api/settings", json={"intranet_location": "测试机房", "intranet_coords": "120.1,30.2"})
    ok(c.get("/api/map").json()["hub"] == {"name": "测试机房", "coord": [120.1, 30.2]}, "可在设置中修改内网位置")
    c.put("/api/settings", json={"intranet_location": "湖南 · 湘潭", "intranet_coords": "112.9442,27.8297"})
    c.put(f"/api/servers/{web['id']}", json={"location": "重庆"})
    pt = next(p for p in c.get("/api/map").json()["points"] if p["id"] == web["id"])
    ok(pt["coord"] == [106.5516, 29.563] and pt["place"] == "重庆" and pt["manual"], "手填「重庆」的内网服务器定位到重庆")
    ok(c.get(f"/api/servers/{web['id']}").json()["place"] == "重庆", "卡片显示手填位置")
    c.put(f"/api/servers/{web['id']}", json={"location": "实验室@120.15,30.27"})
    pt = next(p for p in c.get("/api/map").json()["points"] if p["id"] == web["id"])
    ok(pt["coord"] == [120.15, 30.27] and pt["place"] == "实验室", "支持「名称@经度,纬度」自定义坐标")
    c.put(f"/api/servers/{web['id']}", json={"location": ""})
    ok(c.get(f"/api/servers/{web['id']}").json()["place"] == "湖南 · 湘潭 · 内网", "未填位置的内网服务器显示内网枢纽")

    print("[7] 自动化任务")
    a = c.post("/api/automations", json={"name": "巡检", "script": "echo check-$(whoami)", "target_type": "group",
                                         "target_value": ["生产", "测试"], "interval_minutes": 0}).json()
    r = c.post(f"/api/automations/{a['id']}/run").json()
    ok(len(r["results"]) == 3 and r["status"] == "partial", f"按分组运行(3 台,其中 bad-03 失败) {r['status']}")
    ok(c.get("/api/automations").json()[0]["last_status"] == "partial", "运行结果已记录")

    print("[8] Agent Token / REST")
    t_read = c.post("/api/tokens", json={"name": "reader", "scopes": ["read"], "groups": ["生产"]}).json()["token"]
    t_ops = c.post("/api/tokens", json={"name": "ops", "scopes": ["read", "exec", "secret", "rotate"]}).json()["token"]
    H = lambda t: {"Authorization": f"Bearer {t}"}
    ok(httpx.get(BASE + "/api/agent/servers", headers=H("sv_bad")).status_code == 401, "无效 Token 被拒绝")
    lst = httpx.get(BASE + "/api/agent/servers", headers=H(t_read)).json()
    ok([x["name"] for x in lst] == ["web-01"], "分组范围限制(只看到生产组)")
    ok(httpx.get(BASE + "/api/agent/servers/app-02", headers=H(t_read)).status_code == 404, "越权访问其他分组 → 404")
    r = httpx.post(BASE + "/api/agent/servers/web-01/exec", headers=H(t_read), json={"command": "id"})
    ok(r.status_code == 403, "无 exec 权限被拒绝")
    r = httpx.post(BASE + "/api/agent/servers/web-01/exec", headers=H(t_ops), json={"command": "echo agent"}, timeout=30).json()
    ok(r["stdout"].strip() == "agent", "Agent 执行命令(按名称引用服务器)")
    r = httpx.post(BASE + "/api/agent/servers/web-01/exec", headers=H(t_ops), json={"command": "rm -rf / "})
    ok(r.status_code == 403 and "拦截" in r.json()["detail"], "危险命令被策略拦截")
    r = httpx.get(BASE + "/api/agent/servers/web-01/sites", headers=H(t_ops)).json()
    ok(len(r["nginx"]) >= 4, "Agent 读取网站")

    print("[9] MCP")
    def rpc(token, method, params=None, rid=1):
        return httpx.post(BASE + "/mcp", headers={**H(token), "Accept": "application/json, text/event-stream"},
                          json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, timeout=60)
    init = rpc(t_ops, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}).json()
    ok(init["result"]["protocolVersion"] == "2025-06-18" and init["result"]["serverInfo"]["name"] == "server-vault", "initialize")
    n = httpx.post(BASE + "/mcp", headers=H(t_ops), json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    ok(n.status_code == 202, "通知返回 202")
    tools_ops = {t["name"] for t in rpc(t_ops, "tools/list").json()["result"]["tools"]}
    tools_read = {t["name"] for t in rpc(t_read, "tools/list").json()["result"]["tools"]}
    ok({"exec_command", "get_credentials", "rotate_password"} <= tools_ops, "ops 可见全部工具")
    ok("exec_command" not in tools_read and "list_servers" in tools_read, "reader 只看到只读工具")
    call = lambda tok, name, args: rpc(tok, "tools/call", {"name": name, "arguments": args}).json()["result"]
    r = call(t_ops, "list_servers", {"group": "测试"})
    ok(not r["isError"] and {x["name"] for x in json.loads(r["content"][0]["text"])} == {"app-02", "bad-03"}, "tools/call list_servers")
    r = call(t_ops, "exec_command", {"server": "app-02", "command": "echo mcp-ok", "sudo": True})
    ok(json.loads(r["content"][0]["text"])["stdout"].strip() == "mcp-ok", "tools/call exec_command (sudo)")
    r = call(t_ops, "check_status", {"servers": ["web-01", "off-04"]})
    st = {x["server"]: x["status"] for x in json.loads(r["content"][0]["text"])}
    ok(st == {"web-01": "online", "off-04": "offline"}, "tools/call check_status")
    r = call(t_ops, "get_server_facts", {"server": "web-01"})
    ok(json.loads(r["content"][0]["text"])["facts"]["cpu_cores"], "tools/call get_server_facts")
    r = call(t_read, "exec_command", {"server": "web-01", "command": "id"})
    ok(r["isError"] and "exec" in r["content"][0]["text"], "无权限调用返回 isError")
    r = call(t_ops, "exec_command", {"server": "web-01", "command": "shutdown -h now"})
    ok(r["isError"] and "拦截" in r["content"][0]["text"], "MCP 危险命令拦截")
    r = call(t_ops, "rotate_password", {"server": "web-01"})
    ok(json.loads(r["content"][0]["text"])["verified"], "tools/call rotate_password")
    r = call(t_ops, "get_credentials", {"server": "web-01"})
    ok(json.loads(r["content"][0]["text"])["password"] == mock_sshd.USERS["root"], "tools/call get_credentials 与服务器一致")
    r = json.loads(call(t_ops, "bt_panel_info", {"server": "web-01"})["content"][0]["text"])
    ok(r["version"] == "11.8.1" and r["password"], "tools/call bt_panel_info(含 secret 权限时返回密码)")
    r = json.loads(call(t_ops, "bt_set_password", {"server": "web-01"})["content"][0]["text"])
    ok(bt_db()["users"][0]["password"] == salt(r["password"]), "tools/call bt_set_password")
    ok("bt_temp_login" not in tools_read, "只读 Token 看不到宝塔临时登录工具")

    print("[10] stdio 桥接")
    msgs = "\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}}]) + "\n"
    p = subprocess.run([sys.executable, str(ROOT / "mcp_stdio.py")], input=msgs.encode(), capture_output=True,
                       env={**os.environ, "SV_URL": BASE, "SV_TOKEN": t_read}, timeout=30)
    lines = [json.loads(x) for x in p.stdout.decode().splitlines()]
    ok(len(lines) == 2 and lines[0]["result"]["protocolVersion"] == "2024-11-05", "stdio initialize")
    ok(json.loads(lines[1]["result"]["content"][0]["text"])["scopes"] == ["read"], "stdio tools/call")

    print("[11] Web 终端")
    cookie = c.cookies.get("sv_session")

    async def term():
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/terminal/{web['id']}?cols=80&rows=24",
                                      additional_headers={"Cookie": f"sv_session={cookie}"}) as ws:
            await ws.send(json.dumps({"type": "resize", "cols": 100, "rows": 30}))
            await ws.send(json.dumps({"type": "data", "data": "echo TERM_$((40+2))\n"}))
            buf = ""
            end = time.time() + 10
            while "TERM_42" not in buf and time.time() < end:
                m = json.loads(await asyncio.wait_for(ws.recv(), 10))
                buf += m.get("data", "")
            return buf
    ok("TERM_42" in asyncio.run(term()), "WebSocket 终端交互")

    async def term_noauth():
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/terminal/{web['id']}") as ws:
            await ws.recv()
    try:
        asyncio.run(term_noauth())
        ok(False, "未登录终端应被拒绝")
    except websockets.exceptions.ConnectionClosed as e:
        ok(e.rcvd.code == 4401, "未登录终端被拒绝 (4401)")

    print("[12] 主机指纹校验")
    imp_srv = next(x for x in c.get("/api/servers").json() if x["name"] == "imp-05")
    c.put(f"/api/servers/{imp_srv['id']}", json={"password": mock_sshd.USERS["root"]})
    ok(c.post("/api/check", json={"ids": [imp_srv["id"]]}).json()[0]["status"] == "online", "imp-05 首次连接")
    mock2_stop.set()
    time.sleep(1.2)
    mock_sshd.start(2223, paramiko.RSAKey.generate(2048))
    time.sleep(0.3)
    r = c.post("/api/check", json={"ids": [imp_srv["id"]]}).json()[0]
    ok(r["status"] == "hostkey_mismatch", f"主机密钥变化 → 拒绝连接 ({r['detail'].get('error')})")
    ok(mock_sshd.USERS["root"] == c.get(f"/api/servers/{web['id']}/secret").json()["password"], "指纹不符时未发送凭据(密码未被改动)")
    c.post(f"/api/servers/{imp_srv['id']}/reset-hostkey")
    ok(c.post("/api/check", json={"ids": [imp_srv["id"]]}).json()[0]["status"] == "online", "重置指纹后恢复")

    print("[13] 审计、设置、主密码")
    logs = c.get("/api/audit?limit=500").json()
    actions = {x["action"] for x in logs["items"]}
    ok({"exec", "rotate", "exec.denied", "secret.reveal", "terminal.open", "token.create"} <= actions, "审计日志覆盖关键操作")
    ok(any(x["actor"] == "agent:ops" for x in logs["items"]), "Agent 操作带身份")
    ok(c.put("/api/settings", json={"password_length": 8}).status_code == 400, "设置校验")
    ok(c.put("/api/settings", json={"status_interval_min": 5}).json()["status_interval_min"] == 5, "保存设置")
    ok(c.post("/api/change-master", json={"old": "wrong", "new": "NewMaster#2026"}).status_code == 400, "原主密码错误被拒绝")
    ok(c.post("/api/change-master", json={"old": MASTER, "new": "NewMaster#2026"}).status_code == 200, "修改主密码(全量重加密)")
    ok(c.get(f"/api/servers/{web['id']}/secret").json()["password"] == mock_sshd.USERS["root"], "重加密后凭据可正常解密")

    print("[14] 锁定")
    ok(c.post("/api/lock").status_code == 200, "锁定保险库")
    r = httpx.get(BASE + "/api/agent/servers", headers=H(t_ops))
    ok(r.status_code == 423, "锁定后 Agent REST 返回 423")
    r = call(t_ops, "list_servers", {})
    ok(r["isError"] and "锁定" in r["content"][0]["text"], "锁定后 MCP 工具返回锁定提示")
    c2 = httpx.Client(base_url=BASE)
    ok(c2.post("/api/login", json={"password": MASTER}).status_code == 401, "旧主密码无法解锁")
    ok(c2.post("/api/login", json={"password": "NewMaster#2026"}).status_code == 200, "新主密码解锁")
    ok(httpx.get(BASE + "/api/agent/servers", headers=H(t_ops)).status_code == 200, "解锁后 Agent 恢复")

    print("[15] 调度器:到期自动轮换 + 定时任务")
    import sqlite3
    before = mock_sshd.USERS["root"]
    con = sqlite3.connect(Path(data_dir) / "vault.db")
    con.execute("UPDATE servers SET next_rotation_at='2000-01-01T00:00:00+00:00' WHERE name='web-01'")
    con.commit()
    con.close()
    c2.post("/api/automations", json={"name": "定时", "script": "echo tick", "target_type": "ids",
                                      "target_value": [web["id"]], "interval_minutes": 1})
    end = time.time() + 45
    while time.time() < end and mock_sshd.USERS["root"] == before:
        time.sleep(1)
    ok(mock_sshd.USERS["root"] != before, "到期服务器被自动轮换")
    time.sleep(1)
    s = c2.get(f"/api/servers/{web['id']}").json()
    ok(s["next_rotation_at"] > "2026", "下次轮换时间已顺延")
    ok(c2.get(f"/api/servers/{web['id']}/secret").json()["password"] == mock_sshd.USERS["root"], "自动轮换后保险库与服务器一致")
    autos = {a["name"]: a for a in c2.get("/api/automations").json()}
    ok(autos["定时"]["last_status"] == "ok", "定时任务已自动执行")
    ok(any(x["actor"] == "system" and x["action"] == "rotate" for x in c2.get("/api/audit?actor=system").json()["items"]), "审计记录 system 轮换")


if __name__ == "__main__":
    main()
