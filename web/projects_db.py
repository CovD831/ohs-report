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
          owner_id  TEXT DEFAULT '',        -- 身份隔离: 游客guest-xxx / 登录用户名
          created   REAL,
          updated   REAL
        )
    """)
    # 迁移: 旧库可能没有 owner_id 列
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(project)").fetchall()]
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE project ADD COLUMN owner_id TEXT DEFAULT ''")
    conn.commit()
    return conn


def _owner_key(user: dict | None) -> str:
    """每个身份的唯一 owner 标识 (游客 guest-xxx / 登录用户名)"""
    if not user:
        return "anon"
    return user.get("username") or "anon"


def get_project(pid: str) -> dict | None:
    conn = _conn()
    r = conn.execute("SELECT id, name, data, status, owner_id FROM project WHERE id=?",
                     (pid,)).fetchone()
    conn.close()
    if not r:
        return None
    return {"id": r["id"], "name": r["name"],
            "data": json.loads(r["data"]), "status": r["status"],
            "owner_id": r["owner_id"]}


def get_or_create_report(user: dict | None, name: str = "", industry: str = "") -> dict:
    """按身份取回唯一的报告；不存在则创建。重传覆盖 (一个身份=一份报告)"""
    owner = _owner_key(user)
    conn = _conn()
    r = conn.execute("SELECT id, name, data, status, owner_id FROM project "
                     "WHERE owner_id=? ORDER BY created DESC LIMIT 1", (owner,)).fetchone()
    if r:
        conn.close()
        return {"id": r["id"], "name": r["name"],
                "data": json.loads(r["data"]), "status": r["status"],
                "owner_id": r["owner_id"], "is_new": False}
    pid = uuid.uuid4().hex[:10]
    project_data = {"industry": industry}
    conn.execute("INSERT INTO project (id, name, data, status, owner_id, created, updated) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (pid, name or "未命名报告", json.dumps(project_data, ensure_ascii=False),
                  "draft", owner, time.time(), time.time()))
    conn.commit()
    conn.close()
    return {"id": pid, "name": name or "未命名报告", "data": project_data,
            "status": "draft", "owner_id": owner, "is_new": True}


def list_reports(user: dict | None) -> list[dict]:
    """列出当前身份的报告 (只返回 owner 自己的)"""
    owner = _owner_key(user)
    conn = _conn()
    rows = conn.execute("SELECT id, name, data, status, owner_id, updated FROM project "
                        "WHERE owner_id=? ORDER BY updated DESC", (owner,)).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"],
             "data": json.loads(r["data"]), "status": r["status"],
             "owner_id": r["owner_id"], "updated": r["updated"]} for r in rows]


def list_all_reports() -> list[dict]:
    """管理后台: 全部报告"""
    conn = _conn()
    rows = conn.execute("SELECT id, name, data, status, owner_id, updated FROM project "
                        "ORDER BY updated DESC").fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"],
             "data": json.loads(r["data"]), "status": r["status"],
             "owner_id": r["owner_id"], "updated": r["updated"]} for r in rows]


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
    from web.report_struct import section_title, sub_title
    p = get_project(pid)
    if not p:
        return {"error": "not found"}
    data = dict(p["data"])
    sec_states = data.setdefault("section_states", {})
    # 标题来源: 若 text 首行有 '# 标题' 则用之, 否则用结构规范标题
    title = None
    if generated_text:
        for line in generated_text.splitlines():
            s = line.strip()
            if s.startswith('#') or s.startswith('##') or s.startswith('**'):
                title = s.lstrip('#* ').split('\n')[0].strip()
                break
    if not title:
        title = section_title(sec) if '.' not in sec or sub_title(sec) == sec else sub_title(sec)
    if '.' in sec:
        title = f"{sec} {sub_title(sec)}" if sub_title(sec) != sec else title
    sec_states[sec] = {"state": state, "text": generated_text, "title": title}
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
