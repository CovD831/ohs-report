"""后台任务管理 — 批量生成不因关页面而中断

task_job 表: 提交即返回 job_id, 线程池执行, 前端轮询 /api/tasks/<id>
状态: pending → running(progress/total) → done | failed(error)
"""
import json
import sqlite3
import threading
import time
import traceback
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

DB = Path(__file__).resolve().parent.parent / "data" / "ohs.db"
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ohsjob")
_jobs: dict[str, dict] = {}  # job_id -> 内存态 (progress 实时)


def _conn():
    conn = sqlite3.connect(str(DB), timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_tasks():
    conn = _conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS task_job (
      id TEXT PRIMARY KEY, user TEXT, pid TEXT, kind TEXT,
      status TEXT DEFAULT 'pending', progress INTEGER DEFAULT 0,
      total INTEGER DEFAULT 0, error TEXT,
      created TEXT DEFAULT (datetime('now','localtime')),
      finished TEXT);
    """)
    conn.commit()
    conn.close()


def create_job(user: str, pid: str, kind: str, total: int) -> str:
    import secrets
    jid = secrets.token_hex(5)
    conn = _conn()
    conn.execute("INSERT INTO task_job(id,user,pid,kind,total) VALUES(?,?,?,?,?)",
                 (jid, user or "-", pid, kind, total))
    conn.commit()
    conn.close()
    _jobs[jid] = {"progress": 0}
    return jid


def set_progress(jid: str, progress: int):
    """进度: 内存 + DB 双写 (多 worker 进程下轮询也能读到)"""
    _jobs.setdefault(jid, {})["progress"] = progress
    try:
        conn = _conn()
        conn.execute("UPDATE task_job SET progress=? WHERE id=?", (progress, jid))
        conn.commit()
        conn.close()
    except Exception:
        pass


def finish_job(jid: str, error: str | None = None):
    status = "failed" if error else "done"
    final = _jobs.get(jid, {}).get("progress", 0)
    conn = _conn()
    conn.execute("UPDATE task_job SET status=?, progress=?, error=?, finished=datetime('now','localtime') "
                 "WHERE id=?",
                 (status, final if error else final,
                  (error or "")[:800], jid))
    conn.commit()
    conn.close()


def get_job(jid: str) -> dict | None:
    conn = _conn()
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM task_job WHERE id=?", (jid,)).fetchone()
    conn.close()
    if not r:
        return None
    d = dict(r)
    d["live_progress"] = _jobs.get(jid, {}).get("progress", max(d["progress"], 0))
    return d


def list_jobs(limit: int = 50) -> list[dict]:
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM task_job ORDER BY created DESC LIMIT ?",
                        (min(limit, 200),)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["live_progress"] = _jobs.get(d["id"], {}).get("progress", d["progress"])
        out.append(d)
    return out


def submit(job_fn, *args, **kwargs):
    """提交到线程池 (应用进程内后台执行)"""
    return _executor.submit(job_fn, *args, **kwargs)


# 取消机制: job_id -> 停止标志
_cancel: dict[str, bool] = {}


def cancel_job(jid: str) -> bool:
    """请求取消任务: 标记停止 (worker 每单元检查 _should_stop)"""
    status = get_job(jid)
    if not status:
        return False
    if status["status"] in ("done", "failed", "cancelled"):
        return False
    _cancel[jid] = True
    conn = _conn()
    conn.execute("UPDATE task_job SET status='cancelled', error='用户取消', "
                 "finished=datetime('now','localtime') WHERE id=?", (jid,))
    conn.commit()
    conn.close()
    return True


def _should_stop(jid: str) -> bool:
    """worker 每单元检查是否被请求取消
    跨进程安全: uvicorn --workers 2 时 _cancel 是进程内 dict, 用户取消请求可能落在另一个
    worker 进程上 → 不能只看内存, 以 DB 状态为准 (DB 查询每单元一次, 开销可接受)"""
    conn = _conn()
    r = conn.execute("SELECT status FROM task_job WHERE id=?", (jid,)).fetchone()
    conn.close()
    return bool(r and r[0] == "cancelled")
