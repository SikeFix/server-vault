import asyncio
import codecs
import hashlib
import json
import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, Body, Depends, FastAPI, Header, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import agent, btpanel, keychain, local_import, mcp, quick_add, scheduler
from .db import db
from . import services as S
from .config import AUTO_UNLOCK_PASSWORD, BASE_DIR, STATIC_DIR, VERSION
from .vault import VaultError, VaultLocked, vault

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("server-vault")


@asynccontextmanager
async def lifespan(app):
    if AUTO_UNLOCK_PASSWORD and vault.initialized:
        log.info("使用 SV_MASTER_PASSWORD 自动解锁: %s", "成功" if vault.unlock(AUTO_UNLOCK_PASSWORD) else "失败")
    unlock_task = None
    if not vault.unlocked and vault.initialized and db.get_meta("keychain_unlock") == "1":
        # 放到后台:钥匙串可能弹出授权对话框,不能阻塞服务启动
        async def keychain_unlock():
            pw = await asyncio.to_thread(keychain.get)
            log.info("从 macOS 钥匙串自动解锁: %s", "成功" if pw and vault.unlock(pw) else "失败")
        unlock_task = asyncio.create_task(keychain_unlock())
    task = asyncio.create_task(scheduler.loop())
    yield
    task.cancel()


app = FastAPI(title="Server Vault", version=VERSION, lifespan=lifespan)


@app.exception_handler(S.ServiceError)
async def _service_error(request, exc: S.ServiceError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.exception_handler(VaultLocked)
async def _locked(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=423)


@app.exception_handler(VaultError)
async def _vault_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ---------------- 管理端会话 ----------------

_fails: list = []


def _hash(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def _session_ok(tok) -> bool:
    """会话持久化在数据库中(只存哈希),服务重启后浏览器/菜单栏应用无需重新登录。"""
    if not tok:
        return False
    r = db.one("SELECT expires FROM sessions WHERE token_hash=?", (_hash(tok),))
    return bool(r) and r["expires"] > time.time()


def _start_session(response: Response):
    tok = secrets.token_urlsafe(32)
    hours = S.get_settings()["session_hours"]
    db.run("DELETE FROM sessions WHERE expires<?", (time.time(),))
    db.run("INSERT INTO sessions(token_hash, expires) VALUES(?,?)", (_hash(tok), time.time() + hours * 3600))
    response.set_cookie("sv_session", tok, httponly=True, samesite="strict", max_age=hours * 3600)


def _throttle():
    now = time.time()
    _fails[:] = [t for t in _fails if now - t < 300]
    if len(_fails) >= 5:
        raise S.ServiceError("失败次数过多,请 5 分钟后再试", 429)


def require_admin(request: Request):
    if not _session_ok(request.cookies.get("sv_session")):
        raise S.ServiceError("未登录", 401)
    if not vault.unlocked:
        raise S.ServiceError("保险库已锁定", 423)


@app.get("/api/state")
def state(request: Request):
    return {"initialized": vault.initialized, "unlocked": vault.unlocked,
            "authed": _session_ok(request.cookies.get("sv_session")) and vault.unlocked, "version": VERSION,
            "keychain": {"available": keychain.available(), "enabled": db.get_meta("keychain_unlock") == "1"}}


@app.post("/api/init")
def init(response: Response, payload: dict = Body(...)):
    vault.setup(payload.get("password", ""))
    S.audit("admin", "vault.init")
    _start_session(response)
    return {"ok": True}


@app.post("/api/login")
def login(response: Response, payload: dict = Body(...)):
    _throttle()
    pw = payload.get("password", "")
    ok = vault.verify(pw) if vault.unlocked else vault.unlock(pw)
    if not ok:
        _fails.append(time.time())
        time.sleep(0.5)
        S.audit("admin", "login", detail="主密码错误", ok=False)
        raise S.ServiceError("主密码错误", 401)
    S.audit("admin", "login")
    _start_session(response)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    db.run("DELETE FROM sessions WHERE token_hash=?", (_hash(request.cookies.get("sv_session", "")),))
    response.delete_cookie("sv_session")
    return {"ok": True}


api = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


@api.post("/lock")
def lock(response: Response):
    S.audit("admin", "vault.lock")
    vault.lock()
    db.run("DELETE FROM sessions")
    response.delete_cookie("sv_session")
    return {"ok": True}


@api.post("/keychain")
def set_keychain(payload: dict = Body(...)):
    if not keychain.available():
        raise S.ServiceError("仅 macOS 支持钥匙串自动解锁")
    if payload.get("enable"):
        if not vault.verify(payload.get("password", "")):
            raise S.ServiceError("主密码错误", 400)
        if not keychain.save(payload["password"]):
            raise S.ServiceError("写入钥匙串失败")
        db.set_meta("keychain_unlock", "1")
    else:
        keychain.delete()
        db.set_meta("keychain_unlock", "0")
    S.audit("admin", "vault.keychain", detail="开启" if payload.get("enable") else "关闭")
    return {"enabled": bool(payload.get("enable"))}


@api.post("/change-master")
def change_master(payload: dict = Body(...)):
    vault.change_password(payload.get("old", ""), payload.get("new", ""))
    if db.get_meta("keychain_unlock") == "1":
        keychain.save(payload.get("new", ""))
    S.audit("admin", "vault.change_master")
    return {"ok": True}


@api.get("/servers")
def servers(group: str = None, status: str = None, q: str = None, kind: str = None):
    S.cleanup_expired()
    return [S.public(r) for r in S.list_rows(group=group, status=status, keyword=q, kind=kind)]


@api.post("/servers/quick")
def quick(payload: dict = Body(...)):
    opts = {k: payload.get(k) for k in ("port", "username", "group", "server_type", "temp_days", "install_key", "write_config",
                                       "proxy_mode", "socks", "proxy_command")
            if payload.get(k) not in (None, "")}
    if payload.get("batch"):
        items = quick_add.parse_batch(payload["batch"], opts)

        def one(item):
            try:
                return {"ok": True, "host": item["host"], **quick_add.quick_add(**item)}
            except Exception as e:
                return {"ok": False, "host": item["host"], "error": str(e)}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(items) or 1)) as pool:
            return list(pool.map(one, items))
    return quick_add.quick_add(payload.get("host"), payload.get("password"), name=payload.get("name"), **opts)


@api.get("/map")
def map_data():
    return S.map_data()


@api.get("/servers/{sid}/gpu")
def gpu(sid: int):
    return S.gpu_live(S.get_row(sid))


@api.post("/servers")
def create_server(payload: dict = Body(...)):
    return S.save_server(payload)


@api.get("/servers/{sid}")
def get_server(sid: int):
    return S.public(S.get_row(sid))


@api.put("/servers/{sid}")
def update_server(sid: int, payload: dict = Body(...)):
    return S.save_server(payload, sid)


@api.delete("/servers/{sid}")
def delete_server(sid: int):
    S.delete_server(sid)
    return {"ok": True}


@api.post("/servers/import")
def import_servers(payload: dict = Body(...)):
    return S.import_servers(payload.get("csv", ""))


@api.get("/import/local")
def scan_local():
    return local_import.scan()


@api.post("/import/local")
def import_local(payload: dict = Body(...)):
    return local_import.import_aliases(payload.get("aliases") or [])


@api.get("/groups")
def groups():
    return S.groups()


@api.get("/servers/{sid}/secret")
def secret(sid: int):
    return S.reveal(sid)


@api.get("/servers/{sid}/history")
def history(sid: int):
    S.audit("admin", "secret.history", S.get_row(sid))
    return S.password_history(sid)


@api.post("/servers/{sid}/reset-hostkey")
def reset_hostkey(sid: int):
    S.reset_host_key(sid)
    return {"ok": True}


def _rows(ids):
    return [S.get_row(i) for i in ids] if ids else S.list_rows()


@api.post("/check")
def check(payload: dict = Body(default={})):
    return S.check_many(_rows(payload.get("ids")))


@api.post("/facts")
def facts_many(payload: dict = Body(default={})):
    return S.run_many(_rows(payload.get("ids")), S.collect_facts, workers=12)


@api.post("/servers/{sid}/facts")
def facts(sid: int):
    return S.collect_facts(S.get_row(sid))


@api.get("/servers/{sid}/sites")
def sites(sid: int):
    return S.get_sites(S.get_row(sid))


@api.post("/servers/{sid}/sites")
def scan_sites(sid: int):
    return S.collect_sites(S.get_row(sid))


@api.post("/servers/{sid}/rotate")
def rotate(sid: int, payload: dict = Body(default={})):
    return S.rotate_password(sid, new_password=payload.get("password") or None,
                             current_password=payload.get("current_password") or None)


@api.post("/servers/{sid}/rotate-key")
def rotate_key(sid: int):
    return S.rotate_key(sid)


@api.post("/rotate")
def rotate_many(payload: dict = Body(...)):
    return S.rotate_many(_rows(payload.get("ids")))


@api.post("/servers/{sid}/exec")
def exec_one(sid: int, payload: dict = Body(...)):
    return S.exec_command(S.get_row(sid), payload.get("command"), payload.get("timeout"), payload.get("sudo", False))


@api.post("/exec")
def exec_many(payload: dict = Body(...)):
    cmd, timeout, sudo = payload.get("command"), payload.get("timeout"), payload.get("sudo", False)
    return S.run_many(_rows(payload.get("ids")), lambda r: S.exec_command(r, cmd, timeout, sudo))


@api.get("/servers/{sid}/bt")
def bt_info(sid: int):
    return btpanel.info(S.get_row(sid))


@api.post("/servers/{sid}/bt/refresh")
def bt_refresh(sid: int):
    return btpanel.refresh(S.get_row(sid))


@api.get("/servers/{sid}/bt/secret")
def bt_secret(sid: int):
    row = S.get_row(sid)
    S.audit("admin", "secret.reveal", row, "宝塔面板密码")
    return btpanel.info(row, include_secret=True)


@api.post("/servers/{sid}/bt/credentials")
def bt_credentials(sid: int, payload: dict = Body(default={})):
    return btpanel.set_credentials(sid, payload.get("username"), payload.get("password"))


@api.post("/servers/{sid}/bt/temp-login")
def bt_temp_login(sid: int):
    return btpanel.temp_login(sid)


@api.post("/bt/reset")
def bt_reset_many(payload: dict = Body(...)):
    return S.run_many(_rows(payload.get("ids")), lambda r: btpanel.set_credentials(r["id"]), workers=8)


@api.get("/generate-password")
def gen_password(length: int = None):
    return {"password": S.generate_password(length)}


@api.get("/automations")
def automations():
    return S.list_automations()


@api.post("/automations")
def create_automation(payload: dict = Body(...)):
    return S.save_automation(payload)


@api.put("/automations/{aid}")
def update_automation(aid: int, payload: dict = Body(...)):
    return S.save_automation(payload, aid)


@api.delete("/automations/{aid}")
def delete_automation(aid: int):
    S.delete_automation(aid)
    return {"ok": True}


@api.post("/automations/{aid}/run")
def run_automation(aid: int):
    return S.run_automation(aid)


@api.get("/tokens")
def tokens():
    return {"tokens": S.list_tokens(), "scopes": S.SCOPES}


@api.post("/tokens")
def create_token(payload: dict = Body(...)):
    return S.create_token(payload.get("name"), payload.get("scopes"), payload.get("groups"), payload.get("expires_days"))


@api.delete("/tokens/{tid}")
def revoke_token(tid: int):
    S.revoke_token(tid)
    return {"ok": True}


@api.get("/audit")
def audit_logs(limit: int = 100, offset: int = 0, server_id: int = None, actor: str = None, action: str = None):
    return S.list_audit(min(limit, 500), offset, server_id, actor, action)


@api.get("/settings")
def settings():
    return S.get_settings()


@api.put("/settings")
def put_settings(payload: dict = Body(...)):
    return S.update_settings(payload)


@api.get("/agent-info")
def agent_info():
    return {"tools": [{"name": t["name"], "scope": t["scope"], "description": t["description"]} for t in mcp.TOOLS],
            "bridge_path": str(BASE_DIR / "mcp_stdio.py"), "scopes": S.SCOPES}


app.include_router(api)


# ---------------- Agent REST API(Bearer Token) ----------------

def agent_ctx(authorization: str = Header(default="")):
    return agent.authenticate(authorization)


ag = APIRouter(prefix="/api/agent", tags=["agent"])


@ag.get("/whoami")
def ag_whoami(ctx=Depends(agent_ctx)):
    return agent.whoami(ctx)


@ag.get("/servers")
def ag_servers(group: str = None, status: str = None, q: str = None, kind: str = None, ctx=Depends(agent_ctx)):
    return agent.list_servers(ctx, group, status, q, kind)


@ag.get("/servers/{ref}/gpu")
def ag_gpu(ref: str, ctx=Depends(agent_ctx)):
    return agent.gpu_status(ctx, ref)


@ag.get("/servers/{ref}")
def ag_server(ref: str, ctx=Depends(agent_ctx)):
    return agent.get_server(ctx, ref)


@ag.post("/check")
def ag_check(payload: dict = Body(default={}), ctx=Depends(agent_ctx)):
    return agent.check_status(ctx, payload.get("servers"))


@ag.get("/servers/{ref}/facts")
def ag_facts(ref: str, refresh: bool = False, ctx=Depends(agent_ctx)):
    return agent.facts(ctx, ref, refresh)


@ag.get("/servers/{ref}/sites")
def ag_sites(ref: str, refresh: bool = False, ctx=Depends(agent_ctx)):
    return agent.sites(ctx, ref, refresh)


@ag.post("/servers/{ref}/exec")
def ag_exec(ref: str, payload: dict = Body(...), ctx=Depends(agent_ctx)):
    return agent.exec_command(ctx, ref, payload.get("command"), payload.get("timeout"), payload.get("sudo", False))


@ag.post("/exec")
def ag_batch_exec(payload: dict = Body(...), ctx=Depends(agent_ctx)):
    return agent.batch_exec(ctx, payload.get("servers"), payload.get("command"), payload.get("timeout"), payload.get("sudo", False))


@ag.get("/servers/{ref}/credentials")
def ag_credentials(ref: str, ctx=Depends(agent_ctx)):
    return agent.credentials(ctx, ref)


@ag.get("/servers/{ref}/bt")
def ag_bt(ref: str, ctx=Depends(agent_ctx)):
    return agent.bt_info(ctx, ref)


@ag.post("/servers/{ref}/bt/password")
def ag_bt_password(ref: str, payload: dict = Body(default={}), ctx=Depends(agent_ctx)):
    return agent.bt_set_password(ctx, ref, payload.get("username"), payload.get("password"))


@ag.post("/servers/{ref}/bt/temp-login")
def ag_bt_temp_login(ref: str, ctx=Depends(agent_ctx)):
    return agent.bt_temp_login(ctx, ref)


@ag.post("/servers/{ref}/rotate")
def ag_rotate(ref: str, target: str = "password", ctx=Depends(agent_ctx)):
    return agent.rotate(ctx, ref, target)


app.include_router(ag)


# ---------------- MCP ----------------

@app.post("/mcp")
async def mcp_endpoint(request: Request, authorization: str = Header(default="")):
    try:
        ctx = agent.authenticate(authorization, require_unlocked=False)
    except S.ServiceError as e:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": str(e)}}, status_code=e.status)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
    if isinstance(body, list):
        out = [r for r in [await asyncio.to_thread(mcp.handle_message, ctx, m) for m in body] if r]
        return JSONResponse(out) if out else Response(status_code=202)
    res = await asyncio.to_thread(mcp.handle_message, ctx, body)
    return JSONResponse(res) if res else Response(status_code=202)


@app.get("/mcp")
@app.delete("/mcp")
def mcp_no_stream():
    return Response(status_code=405, headers={"Allow": "POST"})


# ---------------- Web 终端 ----------------

@app.websocket("/ws/terminal/{sid}")
async def terminal(ws: WebSocket, sid: int, cols: int = 120, rows: int = 32):
    await ws.accept()
    if not _session_ok(ws.cookies.get("sv_session")) or not vault.unlocked:
        await ws.close(code=4401)
        return
    try:
        row = S.get_row(sid)
        client = await asyncio.to_thread(S.open_ssh, row)
        client.get_transport().set_keepalive(30)
        chan = client.invoke_shell(term="xterm-256color", width=cols, height=rows)
    except Exception as e:
        await ws.send_text(json.dumps({"type": "error", "data": str(e)}, ensure_ascii=False))
        await ws.close()
        return
    S.audit("admin", "terminal.open", row)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def reader():
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                data = chan.recv(32768)
                if not data:
                    break
                text = dec.decode(data)
                if text:
                    loop.call_soon_threadsafe(queue.put_nowait, text)
        except Exception:
            pass
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=reader, daemon=True).start()

    async def sender():
        while True:
            text = await queue.get()
            if text is None:
                return
            while not queue.empty():
                nxt = queue.get_nowait()
                if nxt is None:
                    await ws.send_text(json.dumps({"type": "data", "data": text}))
                    return
                text += nxt
            await ws.send_text(json.dumps({"type": "data", "data": text}))

    async def receiver():
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "data":
                chan.send(msg["data"])
            elif msg.get("type") == "resize":
                chan.resize_pty(width=int(msg["cols"]), height=int(msg["rows"]))

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        chan.close()
        client.close()
        try:
            await ws.send_text(json.dumps({"type": "exit"}))
            await ws.close()
        except Exception:
            pass
        S.audit("admin", "terminal.close", row)


# ---------------- 静态页面 ----------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/terminal")
def terminal_page():
    return FileResponse(STATIC_DIR / "terminal.html")
