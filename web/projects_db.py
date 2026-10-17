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

from knowledge.oel import connect_ro
from knowledge.paths import DATA_DIR
from knowledge.paths import DB_PATH as DB
from knowledge.paths import MATERIALS_DIR, same_origin


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """project 表的结构准备 (DDL + owner_id 迁移)。拆出来只为让"读"能不碰它。"""
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
        try:
            conn.execute("ALTER TABLE project ADD COLUMN owner_id TEXT DEFAULT ''")
        except sqlite3.OperationalError as e:
            # 同样是 check-then-act 形状 (查列 → 加列), 两个进程会抢。
            # 这里**不**直接吞: 重新读一次列名, 只有"列确实已在"才当它是并发赢家的成果,
            # 否则原样抛出 —— 吞掉 OperationalError 会把"库根本没法用"伪装成"迁移完成"。
            now = [r["name"] for r in conn.execute("PRAGMA table_info(project)").fetchall()]
            if "owner_id" not in now:
                raise
            print(f"[schema] project.owner_id 已由并发进程添加, 本次跳过 ({e})")
    conn.commit()


def ensure_runtime_schema() -> None:
    """运行时表结构就绪 (S1c′ D1): 启动期显式调一次, 读路径从此不再碰 DDL。

    原状是 `_conn()` 每次调用都 `CREATE TABLE IF NOT EXISTS` + `PRAGMA journal_mode=WAL`
    (+ 可能的 ALTER)。看着无害, 但它让**只读取数也在写库**: 主验收门
    (tools/export_fingerprint.py → get_project_data → _conn) 每次跑都要开一次可写连接,
    于是"验收链是只读的"这句写在注释里的话没有任何东西保证 —— 见 tools/test_import_purity.py
    与 tools/test_acceptance_readonly.py 的行为级判据。
    """
    conn = sqlite3.connect(str(DB), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        _ensure_schema(conn)
    finally:
        conn.close()


def _conn() -> sqlite3.Connection:
    """可写连接 (写路径专用)。自带 schema 准备, 所以单跑脚本/CLI 不必先调启动步骤。"""
    conn = sqlite3.connect(str(DB), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    return conn


def _ro() -> sqlite3.Connection:
    """只读连接 (取数专用): 不建表、不设 WAL、不 mkdir。

    缺 `project` 表时直接抛 `no such table: project` —— **不做静默建表兜底**:
    读路径顺手建表正是"验收链在写库"的源头, 而空 volume 首启的正常修法是在
    启动钩子里跑 ensure_runtime_schema() (web/app.py:_bootstrap)。
    """
    conn = connect_ro()
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _owner_key(user: dict | None) -> str:
    """每个身份的唯一 owner 标识 (游客 guest-xxx / 登录用户名)"""
    if not user:
        return "anon"
    return user.get("username") or "anon"


# ============ 生命周期 (C档: 自动过期规则, 通用不针对个案) ============
# MATERIALS_DIR 的产地是 knowledge/paths.py (与 DB_PATH 同源), 不在这里派生 ——
# 见 cleanup_expired() 的说明: "库指到别处、材料目录没跟着走" 会删掉真实企业材料。
EMPTY_PROJECT_TTL = 30 * 86400        # 空项目(未命名+无材料+无生成) 30天回收


class LifecycleRefused(RuntimeError):
    """生命周期回收拒绝执行。具名错误, 因为它的含义是"这里的数据不属于本进程",
    静默跳过会让每次启动都少做一次清理而没人知道。"""


def _project_used(pid: str, data: dict, name: str) -> bool:
    """项目是否被真实使用过: 生成过 / 用户命名 / 有提取数据 / 上传过材料"""
    if name and name != "未命名报告":
        return True
    if any(bool(data.get(k)) for k in ("materials", "hazard_grid", "staffing",
                                       "equipment", "detections", "section_states")):
        return True
    return (MATERIALS_DIR / pid).exists()


def cleanup_expired(apply_rows: bool = False, apply_dirs: bool = False,
                    verbose: bool = True) -> dict:
    """生命周期自动回收 (启动期调用, 幂等)。两个开关**分开**, 因为两件事的风险差一个量级:

    1. `apply_rows` —— 删"空项目行": 未命名 + 无提取数据 + 无生成 + 无材料目录, 超 TTL。
       游客每次访问首页自动建一个项目, 不清会堆几千行。删的都是"库里一行且没有任何文件"。
    2. `apply_dirs` —— 删"孤儿材料目录" (`shutil.rmtree`), **默认关, 生产建议长期关着**。

    为什么目录这条默认关 (判据是从产生路径推出来的, 不是保守起见):
      · `_project_used()` 里"**有材料目录的项目**"永远判为已使用 → TTL 从不删它的行;
      · 用户主动删项目的 `/api/projects/<pid>` (api_delete_project) 是**行 + 目录一起删**;
      · 所以"目录在、库里没行"只剩三种来源: 删到一半崩了、换/挂错库、手改 DB。
        也就是说孤儿目录是**异常信号**而不是日常垃圾。把它做成每次启动自动 rmtree,
        等于让一次错配就变成不可逆的数据丢失 (本地 data/materials 实测 249MB 真实企业材料)。
      · 攒着的代价是有界的、可观测的: 每次启动把清单打在日志里, 要删就人判后用
        `--apply` 的批处理工具删 (与数据清洗 A 组同一套做法), 不在启动路径里自动动手。

    原状 (S1c′ D0 之前): `web/app.py` 在 **import 期**无条件跑本函数且只有一个 apply,
    于是任何 `import web.app` (取证工具、守卫套、挂错 volume 的容器首启) 都带 rmtree 能力。
    现在: 只在服务启动跑, 且两个开关都要显式传。

    两道硬闸 (即使 apply_dirs=True 也不越):
      闸1 `same_origin(MATERIALS_DIR)`: 目录必须在本进程 DATA_DIR 子树内。
      闸2 **库里一个项目 id 都读不到时不扫目录**: "目录在、库里无此行" 在
         空库/读挂了的库上恒成立, 那正是把全部材料目录判成孤儿的情形。
    """
    now = time.time()
    conn = _conn() if apply_rows else _ro()   # 不动行就只开只读连接 (启动不写库)
    dropped_rows = would_drop_rows = 0
    ids = set()
    for r in conn.execute("SELECT id, name, data, updated FROM project").fetchall():
        ids.add(r["id"])
        try:
            data = json.loads(r["data"]) if r["data"] else {}
        except Exception:
            data = {}
        if not _project_used(r["id"], data, r["name"]) and (now - (r["updated"] or now)) > EMPTY_PROJECT_TTL:
            # 计数口径与目录那侧**必须同构**: dropped_* = 真删了几个, would_drop_* = 检出几个。
            # 第一版这里只在 apply 时加计数, 于是 dry-run 报"检出 1 行"被读成"删了 1 行"。
            if apply_rows:
                conn.execute("DELETE FROM project WHERE id=?", (r["id"],))
                conn.execute("DELETE FROM task_job WHERE pid=?", (r["id"],))
                dropped_rows += 1
            else:
                would_drop_rows += 1
    if apply_rows:
        conn.commit()
    conn.close()

    # 孤儿材料目录 (db 行已不存在)
    orphans: list[str] = []
    refused = ""
    if not MATERIALS_DIR.exists():
        pass
    elif not ids:
        refused = "project 表读到 0 个 id"
        if apply_dirs:
            raise LifecycleRefused(
                f"{refused}, 但 {MATERIALS_DIR} 存在 → 拒绝按孤儿删目录 "
                f"(库与材料错配是删数据的唯一路径; 确认无误后用逐批 --apply 的工具清理)")
    elif not same_origin(MATERIALS_DIR):
        refused = f"{MATERIALS_DIR} 不在 DATA_DIR({DATA_DIR}) 子树内"
        if apply_dirs:
            raise LifecycleRefused(f"{refused} → 拒绝 rmtree")
    else:
        for d in MATERIALS_DIR.iterdir():
            if d.is_dir() and d.name not in ids:
                orphans.append(str(d))
                if apply_dirs:
                    import shutil
                    shutil.rmtree(d, ignore_errors=True)
    out = {"dropped_rows": dropped_rows, "would_drop_rows": would_drop_rows,
           "dropped_dirs": len(orphans) if apply_dirs else 0,
           "would_drop_dirs": len(orphans), "apply_rows": apply_rows,
           "apply_dirs": apply_dirs, "refused": refused}
    if verbose and (dropped_rows or would_drop_rows or orphans or refused):
        print(f"[lifecycle] 空项目行 真删 {dropped_rows} / 检出 {would_drop_rows}"
              f" ({'apply_rows=1' if apply_rows else '只报不删'})"
              + f", 孤儿材料目录 真删 {len(orphans) if apply_dirs else 0} / 检出 {len(orphans)}"
              f" ({'apply_dirs=1' if apply_dirs else '只报不删'})"
              + (f": {[Path(p).name for p in orphans[:5]]}"
                 f"{'…' if len(orphans) > 5 else ''}" if orphans else "")
              + (f" [跳过目录扫描: {refused}]" if refused else ""))
    return out



def get_project(pid: str) -> dict | None:
    conn = _ro()
    try:
        r = conn.execute("SELECT id, name, data, status, owner_id FROM project WHERE id=?",
                         (pid,)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    return {"id": r["id"], "name": r["name"],
            "data": json.loads(r["data"]), "status": r["status"],
            "owner_id": r["owner_id"]}


def get_project_data(pid: str) -> dict:
    """项目输入 (评估管线取数格式): dict(project.data) + name。

    原 `web/app.py:_get_project_data` 迁入 (S1a, 2026-10-15) —— 不是为整洁, 是因为当时
    `web/app.py` 在 **import 期**就跑 `_auth.init_users() / _tasks.init_tasks() /
    cleanup_expired()` (**写库**, 后者还能 rmtree 材料目录)。实测每次导出验收都会改动
    users/task_job 行, 一个"只读取证工具"不该有写副作用。
    S1c′ D0 已把那批调用移进启动钩子 (import web.app 现在零副作用), 但取数仍留在本模块:
    取证/导出工具不该为了拿一个 dict 而 import 整个 web 层。
    """
    p = get_project(pid)
    if not p:
        return {}
    data = dict(p["data"])
    data["name"] = p["name"]
    return data


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
    conn = _ro()
    try:
        rows = conn.execute("SELECT id, name, data, status, owner_id, updated FROM project "
                            "WHERE owner_id=? ORDER BY updated DESC", (owner,)).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "name": r["name"],
             "data": json.loads(r["data"]), "status": r["status"],
             "owner_id": r["owner_id"], "updated": r["updated"]} for r in rows]


def list_all_reports() -> list[dict]:
    """管理后台: 全部报告"""
    conn = _ro()
    try:
        rows = conn.execute("SELECT id, name, data, status, owner_id, updated FROM project "
                            "ORDER BY updated DESC").fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "name": r["name"],
             "data": json.loads(r["data"]), "status": r["status"],
             "owner_id": r["owner_id"], "updated": r["updated"]} for r in rows]


def create_project(name: str, industry: str = "", owner_id: str = "") -> dict | None:
    """创建空白项目；设备、检测和工艺数据由用户材料导入。

    ⚠ owner_id 必须落库: 早期 INSERT 未写此列 → 永远是 '' →
      _assert_owner 里非 admin 用户 (含创建者本人/游客) **导出与下载一律 403**
      (实测: 游客建项目后 GET /export → 403 "无权访问")。
    """
    pid = uuid.uuid4().hex[:10]
    project_data = {
        "industry": industry,
        "equipment": [],
        "detections": [],
        "processes": [], "process_text": "",
    }
    conn = _conn()
    now = time.time()
    conn.execute("INSERT OR REPLACE INTO project (id, name, data, status, created, updated, owner_id) "
                 "VALUES (?, ?, ?, 'draft', ?, ?, ?)",
                 (pid, name, json.dumps(project_data, ensure_ascii=False), now, now, owner_id or ""))
    conn.commit()
    conn.close()
    return get_project(pid)


def update_project(pid: str, name: str, data: dict) -> dict | None:
    """整体覆盖项目数据 (name + data + status)

    ⚠ 与 `_save_project_data` 同样是**整体覆盖**语义。本项目多次因"传旧快照"清空数据
      (实测 c64c1704bf / 06ea028767)。故此处也加同样的防呆:
      新数据若比库中现存**少 >30% 且 >=3 个字段** → 拒绝写入并记 warning。
      (调用方若确需删字段, 应先显式从库里读出完整数据再删)
    """
    _p = _conn()
    _p.row_factory = __import__("sqlite3").Row
    try:
        _row = _p.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
        if _row:
            _old = json.loads(_row["data"] or "{}")
            if isinstance(_old, dict) and _old and isinstance(data, dict):
                _missing = [k for k in _old if k not in data and k != "section_states"]
                if len(_missing) >= 3 and len(_missing) > len(_old) * 0.3:
                    import logging
                    logging.getLogger("ohs").warning(
                        f"[update_project] 疑似误清空 pid={pid} — 拒绝保存。"
                        f"将丢失 {len(_missing)}/{len(_old)} 字段: {_missing[:8]}")
                    _p.close()
                    return get_project(pid)
    except Exception:
        pass
    finally:
        try:
            _p.close()
        except Exception:
            pass
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
    # 统一出口去污 (单一合并点): 内部术语不得进正文 (实测 LLM 照抄 prompt 里的"信息块")
    # 与 web.llm_draft._deskew_jargon 同一定义 (惰性导入, 避免循环依赖)
    try:
        from web.llm_draft import _deskew_jargon as _dj
        st["text"] = _dj(st["text"])
    except Exception:
        pass
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
