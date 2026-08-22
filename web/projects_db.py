"""项目数据存取 — web 层 (SQLite project 表)

用途: 网页输入表单 ↔ 评估管线
  项目 = {id, name, industry, equipment[], detections[], processes[],
          process_text, created, updated}
存储: data/ohs.db → project (JSON 字段, 简单可靠)
"""
import json
import sqlite3
import time
import uuid
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "ohs.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project (
          id        TEXT PRIMARY KEY,
          name      TEXT NOT NULL,
          data      TEXT NOT NULL,      -- JSON: {industry, equipment, detections...}
          status    TEXT DEFAULT 'draft',   -- draft|generating|ready|reviewing
          created   REAL,
          updated   REAL
        )
    """)
    return conn


def list_projects() -> list[dict]:
    conn = _conn()
    rows = conn.execute("SELECT id, name, data, status, updated FROM project "
                        "ORDER BY updated DESC").fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"],
             "data": json.loads(r["data"]), "status": r["status"],
             "updated": r["updated"]} for r in rows]


def get_project(pid: str) -> dict | None:
    conn = _conn()
    r = conn.execute("SELECT id, name, data, status FROM project WHERE id=?",
                     (pid,)).fetchone()
    conn.close()
    if not r:
        return None
    return {"id": r["id"], "name": r["name"],
            "data": json.loads(r["data"]), "status": r["status"]}


def create_project(name: str, industry: str = "") -> dict | None:
    """创建空白项目；设备、检测和工艺数据由用户材料导入。"""
    pid = uuid.uuid4().hex[:10]
    project_data = {
        "industry": industry,
        "equipment": [],
        "detections": [],
        "processes": [], "process_text": "",
    }
    conn = _conn()
    now = time.time()
    conn.execute("INSERT OR REPLACE INTO project (id, name, data, status, created, updated) "
                 "VALUES (?, ?, ?, 'draft', ?, ?)",
                 (pid, name, json.dumps(project_data, ensure_ascii=False), now, now))
    conn.commit()
    conn.close()
    return get_project(pid)


def update_project(pid: str, name: str, data: dict) -> dict | None:
    conn = _conn()
    conn.execute("UPDATE project SET name=?, data=?, updated=?, status=? WHERE id=?",
                 (name, json.dumps(data, ensure_ascii=False), time.time(),
                  "draft" if data.get("_mark_draft") else "draft", pid))
    conn.commit()  # ← 必须提交, 否则回滚
    conn.close()
    return get_project(pid)


def update_section_state(pid: str, sec: str, state: str, generated_text: str = "") -> dict:
    """章节状态: mechanical(机械待确认) → generated(已生成) → confirmed(定稿)"""
    p = get_project(pid)
    if not p:
        return {"error": "not found"}
    data = dict(p["data"])
    sec_states = data.setdefault("section_states", {})
    sec_states[sec] = {"state": state, "text": generated_text}
    conn = _conn()
    conn.execute("UPDATE project SET data=?, updated=? WHERE id=?",
                 (json.dumps(data, ensure_ascii=False), time.time(), pid))
    conn.commit()
    conn.close()
    return {"ok": True}


def seed_demo() -> dict:
    """开发环境显式调用时植入演示项目；生产启动不会自动调用。"""
    conn = _conn()
    r = conn.execute("SELECT id FROM project WHERE id='demo-cx'").fetchone()
    conn.close()
    if r:
        return get_project("demo-cx")
    p = create_project("长兴特殊材料年产27080吨高性能光固化涂料材料项目", "261")
    # 固定 id 便于 demo
    conn = _conn()
    conn.execute("UPDATE project SET id='demo-cx' WHERE id=?", (p["id"],))
    conn.commit()
    conn.close()
    return get_project("demo-cx")
