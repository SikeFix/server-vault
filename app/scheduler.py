"""后台自动化:到期密码轮换、定期状态检测、定期配置采集、定时任务。"""
import asyncio
import logging
import time

from . import services as S
from .db import db
from .vault import vault

log = logging.getLogger("server-vault")
_running: set = set()
_tasks: set = set()


def _spawn(key, fn, *args):
    if key in _running:
        return

    async def runner():
        _running.add(key)
        try:
            await asyncio.to_thread(fn, *args)
        except Exception as e:
            log.warning("后台任务 %s 失败: %s", key, e)
        finally:
            _running.discard(key)

    task = asyncio.create_task(runner())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _due(meta_key, interval_seconds) -> bool:
    if interval_seconds <= 0:
        return False
    last = db.get_meta(meta_key)
    if last and time.time() - float(last) < interval_seconds:
        return False
    db.set_meta(meta_key, str(time.time()))
    return True


def _rotate(sid):
    try:
        S.rotate(sid, actor="system")
    except S.ServiceError as e:
        log.warning("自动轮换失败 #%s: %s", sid, e)


def _refresh_stale_facts(hours):
    cutoff = S.now_dt().timestamp() - hours * 3600
    rows = [r for r in S.list_rows() if r["status"] == "online"
            and (not r["facts_at"] or S.parse_iso(r["facts_at"]).timestamp() < cutoff)]
    S.run_many(rows, lambda r: S.collect_facts(r, actor="system"), workers=8)


def tick():
    if not vault.unlocked:
        return
    st = S.get_settings()
    if _due("sched_cleanup_at", 300):
        _spawn("cleanup", S.cleanup_expired)
    for r in db.all("SELECT id FROM servers WHERE rotation_enabled=1 AND next_rotation_at IS NOT NULL AND next_rotation_at<=?",
                    (S.now_iso(),)):
        _spawn(f"rotate:{r['id']}", _rotate, r["id"])
    if _due("sched_status_at", st["status_interval_min"] * 60):
        _spawn("status", lambda: S.check_many(S.list_rows(), actor="system"))
    if st["facts_interval_hours"] > 0 and _due("sched_facts_at", 1800):
        _spawn("facts", _refresh_stale_facts, st["facts_interval_hours"])
    for a in db.all("SELECT * FROM automations WHERE enabled=1 AND interval_minutes>0"):
        last = S.parse_iso(a["last_run_at"])
        if not last or (S.now_dt() - last).total_seconds() >= a["interval_minutes"] * 60:
            _spawn(f"automation:{a['id']}", S.run_automation, a["id"], "system")


async def loop():
    while True:
        try:
            tick()
        except Exception as e:
            log.exception("调度异常: %s", e)
        await asyncio.sleep(30)
