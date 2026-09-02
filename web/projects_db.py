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


# ============ 生命周期 (C档: 自动过期规则, 通用不针对个案) ============
MATERIALS_DIR = DB.parent / "materials"
EMPTY_PROJECT_TTL = 30 * 86400        # 空项目(未命名+无材料+无生成) 30天回收


def _project_used(pid: str, data: dict, name: str) -> bool:
    """项目是否被真实使用过: 生成过 / 用户命名 / 有提取数据 / 上传过材料"""
    if name and name != "未命名报告":
        return True
    if any(bool(data.get(k)) for k in ("materials", "hazard_grid", "staffing",
                                       "equipment", "detections", "section_states")):
        return True
    return (MATERIALS_DIR / pid).exists()


def cleanup_expired() -> dict:
    """生命周期自动回收 (启动时调用, 幂等):
    1. 空项目: 未命名+无提取数据+无生成+无材料目录, 超TTL → 删 db 行
       (游客访问首页自动建项目, 大量从未使用的行会堆积)
    2. 孤儿材料目录: 目录在但 db 无此项目 → 删目录
    有生成内容/用户命名的项目一律不动。"""
    now = time.time()
    conn = _conn()
    dropped_rows = 0
    ids = set()
    for r in conn.execute("SELECT id, name, data, updated FROM project").fetchall():
        ids.add(r["id"])
        try:
            data = json.loads(r["data"]) if r["data"] else {}
        except Exception:
            data = {}
        if not _project_used(r["id"], data, r["name"]) and (now - (r["updated"] or now)) > EMPTY_PROJECT_TTL:
            conn.execute("DELETE FROM project WHERE id=?", (r["id"],))
            conn.execute("DELETE FROM task_job WHERE pid=?", (r["id"],))
            dropped_rows += 1
    conn.commit()
    conn.close()
    # 孤儿材料目录 (db 行已不存在)
    dropped_dirs = 0
    if MATERIALS_DIR.exists():
        for d in MATERIALS_DIR.iterdir():
            if d.is_dir() and d.name not in ids:
                import shutil
                shutil.rmtree(d, ignore_errors=True)
                dropped_dirs += 1
    return {"dropped_rows": dropped_rows, "dropped_dirs": dropped_dirs}


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
        return {"id": r[0], "name": r[1],
                "data": json.loads(r[2]), "status": r[3],
                "owner_id": r[4], "is_new": False}
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


def create_new_project(user: dict | None, name: str = "", industry: str = "") -> dict:
    """每次上传自动新建项目 (不再复用旧项目; 不同报告=不同项目, 互不污染).
    应用场景: 用户每次上传材料 = 新建一个报告项目, 避免长兴材料混进浦发项目. """
    pid = uuid.uuid4().hex[:10]
    owner = _owner_key(user)
    project_data = {"industry": industry}
    conn = _conn()
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


def update_section_state(pid: str, sec: str, state: str, generated_text: str = "",
                         validation: dict | None = None) -> dict:
    """章节状态: mechanical(机械待确认) → generated(已生成) → confirmed(定稿)
    validation: 逐节校验器结果 (生成时检测数据/要素/幻觉), 存 section_states[sec]['validation']"""
    from web.report_struct import section_title, sub_title, sub3_title, sub4_title
    p = get_project(pid)
    if not p:
        return {"error": "not found"}
    data = dict(p["data"])
    sec_states = data.setdefault("section_states", {})
    # 标题统一用 report_struct 正规标题 (避免LLM输出带序号/标题串, 造成'4 4.1'重复)
    ndots = sec.count(".")
    if ndots >= 3:                          # 四级: "5.8.2.1"
        st = sub4_title(sec)
        title = f"{sec} {st}" if st != sec else sec
    elif ndots == 2:                        # 三级: "2.1.1"
        st = sub3_title(sec)
        title = f"{sec} {st}" if st != sec else sec
    elif ndots == 1:                        # 二级: "4.1"
        st = sub_title(sec)
        title = f"{sec} {st}" if st != sec else sec
    else:                                   # 一级: "1"
        title = section_title(sec)
    st = {"state": state, "text": generated_text, "title": title}
    if validation:
        st["validation"] = validation
    sec_states[sec] = st
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
