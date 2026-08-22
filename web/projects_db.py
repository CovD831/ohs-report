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
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
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
    """创建项目 (用长兴模板初始化设备/检测)"""
    pid = uuid.uuid4().hex[:10]
    demo_data = {
        "industry": industry or "261",
        "equipment": ["酯化釜", "纯化槽", "洗涤塔", "溶剂回收槽", "中和真空槽",
                      "酯化第一冷凝器", "真空除沫器", "洗釜泵", "油相溶剂泵"],
        "detections": [{"factor": "甲苯", "ctwa": 30, "cste": 95},
                       {"factor": "环己烷", "ctwa": 0.3, "peak": 4.0}],
        "processes": [], "process_text": "",
    }
    conn = _conn()
    now = time.time()
    conn.execute("INSERT OR REPLACE INTO project (id, name, data, status, created, updated) "
                 "VALUES (?, ?, ?, 'draft', ?, ?)",
                 (pid, name, json.dumps(demo_data, ensure_ascii=False), now, now))
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
    """首次启动: 植入演示项目 (长兴)"""
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
