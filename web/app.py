"""职业病危害预评价报告生成工作台 — FastAPI 入口

页面:
  /                 项目列表
  /projects/<id>    项目详情 (三栏: 章节树/内容/依据侧栏)

依据: DESIGN.md (UI规范)
  配色: 青蓝#2563EB | 字体: 系统无衬线+等宽 | 8px网格 | 圆角4-12px
  徽标: 🟦标准依据(蓝) ⬛设计建议(灰)
运行: .venv/bin/uvicorn web.app:app --reload --port 8000
"""
import json
import os
import re
import secrets
import sqlite3
import time
import traceback
import asyncio
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from jinja2 import Environment, FileSystemLoader

from knowledge.evidence_engine import evidence_for_section  # noqa: E402
from web.report_struct import CHAPTERS, SUBS, SUBS3, SUBS4  # noqa: E402
from knowledge.project_assess import assess_project  # noqa: E402
from knowledge.oel import connect  # noqa: E402
from web.projects_db import get_project, create_project, update_project, seed_demo, get_or_create_report, update_section_state

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ohs.db"
TEMPLATES = Path(__file__).resolve().parent.parent / "web" / "templates"

app = FastAPI(title="职业病危害预评价报告工作台")
env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
# 演示数据只能通过显式环境变量开启，生产环境默认关闭。
if os.getenv("SEED_DEMO", "").lower() in {"1", "true", "yes"}:
    seed_demo()

# 用户认证 + 审计 (多人使用基础设施)
from web import auth as _auth  # noqa: E402

_auth.init_users()
_sessions: dict[str, dict] = {}  # token -> user info
GUEST_RE = re.compile(r"guest-[0-9a-f]{6}")


def _current_user(request: Request) -> dict | None:
    tok = request.cookies.get("ohs_session")
    if not tok:
        return None
    if tok in _sessions:
        return _sessions[tok]
    u = _auth.parse_token(tok)
    if u:
        _sessions[tok] = u
    return u


def _resolve_user(request: Request) -> tuple[dict, str | None]:
    """登录用户 或 自动游客身份. 返回 (user, 待下发的游客cookie或None)

    游客: 首次访问生成 guest-xxxxxx (cookie 持久1年), 审计可按游客区分
    """
    u = _current_user(request)
    if u:
        return u, None
    gid = request.cookies.get("ohs_uid") or ""
    if GUEST_RE.fullmatch(gid):
        return {"username": gid, "is_admin": False}, None
    gid = f"guest-{secrets.token_hex(3)}"
    return {"username": gid, "is_admin": False}, gid


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    """审计 + 游客身份: 每个请求归属到 登录用户 或 guest-xxx"""
    start = time.time()
    user, new_gid = _resolve_user(request)
    request.state.user = user
    response = await call_next(request)
    if new_gid:
        response.set_cookie("ohs_uid", new_gid, max_age=365 * 86400,
                            httponly=True, samesite="lax")
    path = request.url.path
    if path.startswith("/api/") or path.startswith("/projects/") or path == "/":
        _auth.audit(user["username"],
                    request.method, path, response.status_code,
                    request.client.host if request.client else "-",
                    int((time.time() - start) * 1000))
    return response

_cache = {}


def _get_project_data(pid: str) -> dict:
    """从数据库读项目输入 (评估管线输入格式) — 返回全部提取字段, 供 coverage/assess/生成取数"""
    p = get_project(pid)
    if not p:
        return {}
    d = p["data"]
    # 返回项目 data 的全部字段 (materials/staffing/protection/ppe/emergency/management/public_works/buildings/products/health_check/detections/equipment/process_text...)
    # 关键: coverage_report(project=...) 依赖这些字段匹配类别(_ext_cat), 只返回部分字段会导致类别缺失误判
    data = dict(d)
    data["name"] = p["name"]
    return data


def _save_project_data(pid: str, data: dict) -> bool:
    """保存项目数据回数据库 (import/一键生成后更新)"""
    p = get_project(pid)
    if not p:
        return False
    conn = connect()
    conn.execute("UPDATE project SET data=?, updated=? WHERE id=?",
                 (json.dumps(data, ensure_ascii=False), time.time(), pid))
    conn.commit()
    conn.close()
    return True


def _get_assess(pid: str) -> dict:
    """评估结果缓存 (项目数据变化时失效)

    顺带: 用 assess 结果把"评估链路驱动"的骨架表补填并**落盘**。
    (2026-09: 这些表原先只有导入时建的空壳 — 那时 assess 还没跑, 永远 0 行;
     导出只能靠 fill_section 现算, 面板也显示"待评估"。这里跑完评估就回填。)
    """
    key = f"assess:{pid}"
    if key not in _cache:
        conn = connect()
        proj = _get_project_data(pid)
        assess = assess_project(conn, proj)
        # 把完整项目数据(含hazard_grid)塞进 _project_data, 供 fill_section/build_grid 内嵌表格取数
        assess["_project_data"] = dict(proj)
        _cache[key] = assess
        conn.close()
        # 回填骨架表并落盘 (只在确有新增填充时写, 避免无谓 IO)
        try:
            from web.table_skeleton import build_skeletons
            _old = proj.get("built_tables") or {}
            _new = build_skeletons(proj, assess)
            _old_rows = sum(len((v or {}).get("rows") or []) for v in _old.values()
                            if isinstance(v, dict))
            _new_rows = sum(len((v or {}).get("rows") or []) for v in _new.values()
                            if isinstance(v, dict))
            if _new_rows > _old_rows:
                # ⚠ _save_project_data 是**整体覆盖** data — 必须传完整数据,
                #   只传 {"built_tables":...} 会把其它字段全清空。
                _merged = dict(proj)
                _merged["built_tables"] = _new
                _save_project_data(pid, _merged)
                _cache[key] = assess
        except Exception:
            pass
    return _cache[key]


# ============ 报告工作台 (上传→一键生成→顺读报告) ============
_upload_cache: dict[str, dict] = {}  # pid -> 上传列表
_import_cache: dict[str, dict] = {}  # pid -> import提取结果 (上传中缓存, 避免每文件重复提取)


def _resolve_pid(request: Request) -> str:
    """按当前身份取回唯一报告 pid (一个身份=一份报告, 无则创建)"""
    user = request.state.user if hasattr(request.state, "user") else None
    return get_or_create_report(user)["id"]


def _assert_owner(request: Request, pid: str) -> bool:
    """身份归属校验: pid 必须属于当前用户, 或当前是 admin"""
    user = request.state.user if hasattr(request.state, "user") else None
    if not user:
        return False
    if user.get("is_admin"):
        return True
    p = get_project(pid)
    return bool(p and p.get("owner_id") == user.get("username"))


@app.post("/api/report/upload", response_class=JSONResponse)
async def api_report_upload(request: Request):
    """一次上传多个文件 → 自动归类 → 存材料目录 → 返回分类结果
    默认自动新建项目(每次上传=新报告, 不污染旧项目); 可用 ?pid= 指定上传到已有项目"""
    from web.uploads import classify_file, save_upload, project_dir
    from web.projects_db import create_new_project
    global _import_cache
    # 初始身份 (无则创建身份)
    user = request.state.user if hasattr(request.state, "user") else None
    # 指定 pid (编辑已有报告) → 用该 pid; 否则自动新建项目
    pid = request.query_params.get("pid")
    if not pid:
        p = create_new_project(user, "未命名报告", "")
        pid = p["id"]
    form = await request.form()
    files = form.getlist("files")
    if not files:
        # 兼容单文件
        files = [f for f in form.getlist("file") if hasattr(f, "read") and hasattr(f, "filename")]
    result: dict[str, list[dict]] = {}
    cats = "C1,C2,C3,C4,C5,C6,C7,C8,C9,C10,C11,C12,C13,C14,C15,C16,C17".split(",")
    for c in cats:
        result[c] = []
    result["uncat"] = []
    for f in files:
        if not hasattr(f, "read") or not hasattr(f, "filename"):
            continue
        content = await f.read(200 * 1024 * 1024 + 1)   # 200MB(覆盖大PDF图纸/检测报告) — 原20MB太小致静默丢弃
        if len(content) > 200 * 1024 * 1024:
            continue
        name = f.filename or "unnamed"
        cat = classify_file(name, content)
        try:
            # unknown 进待定区(uncat), 不兜底到 C2 (避免SDS/无法识别文件挤占项目概况)
            save_upload(pid, cat if cat in cats else "uncat", name, content)
            result.setdefault(cat if cat in cats else "uncat", []).append({
                "name": name, "cat": cat if cat in cats else "uncat", "ok": True})
        except Exception as e:
            result.setdefault("uncat", []).append({"name": name, "cat": cat, "ok": False, "error": str(e)})
    _upload_cache[pid] = result
    counts = {k: len(v) for k, v in result.items() if v}
    # 资料覆盖度(轻量): 只按已收集类别判断, 不触发 LLM抽取 (性能: 上传时别反复调LLM)
    from web.uploads import coverage_report
    try:
        coverage = coverage_report(pid)  # 只按类别判断缺失
    except Exception:
        coverage = {"covered": [], "missing": [], "completeness": 0}
    # 资料提取: 仅 finalize(最后一文件传完) 时完整提取一次, 且放线程执行。
    # 修复"上传卡1/229": 之前每个文件上传都同步执行 import_materials_from_dir(解析已传全部
    # 材料, 47s+) 阻塞事件循环 → 第2个文件请求排队等待 → 前端永远卡在 1/229。
    extracted = None
    finalize = request.query_params.get("finalize")  # 最后一文件传完 → 强刷完整提取
    try:
        if finalize:  # finalize=1: 清缓存强制完整提取(此时所有文件已传完)
            _import_cache.pop(pid, None)
            imported = await asyncio.to_thread(import_materials_from_dir, pid)
            _import_cache[pid] = imported
            extracted = imported
        elif pid in _import_cache:
            extracted = _import_cache[pid]
    except Exception:
        pass
    # 完整度用提取字段计算(类别覆盖缺≠数据缺): extracted有字段→对应类别视为已覆盖
    if extracted:
        try:
            coverage = coverage_report(pid, project=extracted)
        except Exception:
            pass
    return {"ok": True, "pid": pid, "counts": counts, "detail": result,
            "coverage": coverage, "extracted": extracted}


@app.post("/api/report/generate", response_class=JSONResponse)
def api_report_generate(request: Request):
    """一键生成: 导入已传材料 → 识别/判定/分级 → 后台生成全部章节

    修复"点生成卡住不跳转": 材料解析(复合报告PDF 46s/个, 大文件内存700MB+)原来同步执行,
    POST 60-90s 才返回 → 前端 fetch 等不到响应 → 不跳转; 且小内存服务器(1.8G)并发解析会
    OOM(exit 137) → 生成不完全的报告。现在解析整体挪进后台线程, 接口秒回 → 前端立刻
    跳转报告页, 进度由 /api/tasks 轮询(含"解析材料中"阶段)。
    """
    from web.report_struct import count_units
    # 优先用前端上传记录的 pid (修复"上传长兴却生成浦发"); 否则用身份最新
    pid = request.query_params.get("pid") or _resolve_pid(request)
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    # 项目名/行业先取旧值 (后台解析后覆盖)
    proj_name = p["name"]
    # 生命周期: 重新生成必须先清空旧的 section_states,
    # 否则打勾的是上次材料的旧内容, 与当前数据不一致 (用户指出的 bug)
    data = dict(p["data"])
    data["section_states"] = {}
    data["generating"] = True
    _save_project_data(pid, data)
    _cache.pop(f"assess:{pid}", None)
    total = count_units(data)
    user = request.state.user if hasattr(request.state, "user") else None
    jid = _tasks.create_job((user or {}).get("username", "-"), pid, "generate_all", total + 1)  # +1 = 材料解析阶段

    def _job():
        try:
            # ---- 阶段0: 材料解析 (后台, 不阻塞 HTTP 响应) ----
            # 内存保护(硬性): 复合报告 PDF 解析峰值 ~800MB, 1.87G 小内存服务器上若在
            # worker 进程内解析, 全局 OOM-kill 会杀死整个 worker → 任务永远 0%。
            # 对策: 解析挪到独立子进程 reparse_worker.py (setrlimit 限地址空间,
            # 超限抛 MemoryError → 返回空 dict; 子进程被杀也不影响 worker),
            # 主进程继续用项目已有 data 生成, 任务永不因解析 OOM 卡死。
            import subprocess as _sp
            import json as _json
            import sys as _sys
            import gc as _gc
            _gc.collect()
            _tasks.set_progress(jid, 0)
            imported = {}
            try:
                _worker = str(Path(__file__).resolve().parent / "reparse_worker.py")
                _r = _sp.run([_sys.executable, "-u", _worker, pid],
                             capture_output=True, timeout=900, cwd=str(Path(__file__).resolve().parent.parent))
                if _r.returncode == 0 and _r.stdout.strip():
                    imported = _json.loads(_r.stdout)
            except MemoryError:
                imported = {}          # OOM: 跳过解析, 沿用项目已有 data
            except Exception:
                imported = {}
            _gc.collect()
            p2 = get_project(pid)
            data = dict(p2["data"]) if p2 else {}
            # imported 为空(OOM/解析失败) → 沿用项目已有字段, 不覆盖
            data["equipment"] = imported.get("equipment") or data.get("equipment") or []
            data["detections"] = imported.get("detections") or data.get("detections") or []
            data["process_text"] = imported.get("process_text") or data.get("process_text") or ""
            # 结构化字段: 物料/定员/防护/PPE/应急/建构筑物/设施配置/产品/公辅/概况细节
            for k in ("materials", "staffing", "protection", "ppe", "emergency",
                      "buildings", "facilities", "products", "public_works",
                      "investment", "ohy_investment", "area", "capacity", "nature", "location",
                      "equipment_detail", "shifts", "health_check", "management",
                      "hazard_grid", "emergency_supplies",
                      "company", "founded", "registered_capital", "registered_capital_all",
                      "legal_rep", "investor"):
                if imported.get(k):
                    data[k] = imported[k]
            # 项目名/行业从 C1/C2 概况解析 (避免"未命名报告/待补充")
            if imported.get("industry"):
                data["industry"] = imported["industry"]
            nonlocal_name = imported.get("name") or p2["name"] if p2 else None
            if nonlocal_name and nonlocal_name != "未命名报告":
                data["name"] = nonlocal_name
                update_project(pid, nonlocal_name, data)
            else:
                update_project(pid, p2["name"] if p2 else pid, data)
            _cache.pop(f"assess:{pid}", None)
            _run_generate_all(pid, jid)
            # 生成结束清 generating 标记
            p3 = get_project(pid)
            if p3:
                d3 = dict(p3["data"])
                d3["generating"] = False
                _save_project_data(pid, d3)
            # 生成后自动评测 (benchmark 钩子: 失败不影响生成结果, 结果存 data.bench)
            try:
                _run_bench_after_generate(pid)
            except Exception as _be:
                import logging
                logging.getLogger("ohs").warning(f"自动评测失败: {_be}")
            _tasks.finish_job(jid, None)
        except Exception as e:
            p3 = get_project(pid)
            if p3:
                d3 = dict(p3["data"])
                d3["generating"] = False
                _save_project_data(pid, d3)
            _tasks.finish_job(jid, traceback.format_exc())

    import threading
    threading.Thread(target=_job, daemon=True).start()
    return {"ok": True, "pid": pid, "job_id": jid, "total": total,
            "parsing": True,
            "imported": {"equipment": len(p["data"].get("equipment") or []),
                         "detections": len(p["data"].get("detections") or [])}}


@app.get("/api/report", response_class=JSONResponse)
def api_report_get(request: Request):
    """当前身份的报告 + 材料清单 + 章节状态"""
    pid = _resolve_pid(request)
    p = get_project(pid)
    if not p:
        return {"pid": pid, "report": None}
    from web.uploads import list_materials
    mats = list_materials(pid)
    d = p["data"]
    ss = d.get("section_states", {})
    gen = len([k for k, v in ss.items() if v.get("state") == "generated"])
    # status 由实际生成内容推导: 有生成→ready, 否则 draft
    if gen > 0:
        status = "ready"
    else:
        status = p["status"] if p["status"] == "generating" else "draft"
    return {"pid": pid, "report": {"name": p["name"], "status": status,
            "data": d}, "materials": mats}


@app.get("/api/reports", response_class=JSONResponse)
def api_reports_list(request: Request):
    """当前身份的所有报告列表 (供'已生成报告'查看/切换, 每次上传=新项目)"""
    from web.projects_db import list_reports
    user = request.state.user if hasattr(request.state, "user") else None
    reps = list_reports(user) or []
    out = []
    for r in reps:
        d = r.get("data") or {}
        ss = d.get("section_states", {})
        gen = len([k for k, v in ss.items() if v.get("state") == "generated"])
        out.append({
            "id": r["id"], "name": r.get("name", "未命名报告"),
            "status": "ready" if gen > 0 else (r.get("status") or "draft"),
            "industry": str(d.get("industry", "")), "gen_count": gen,
            "updated": r.get("updated"), "materials": len((r.get("_mats") or [])),
            "bench": (d.get("bench") or {}).get("total_deterministic")})
    return {"ok": True, "reports": out}


@app.delete("/api/projects/{pid}", response_class=JSONResponse)
def api_delete_project(pid: str, request: Request):
    """删除项目 (级联: db行 + 材料目录 + assess缓存 + 关联任务)
    生命周期B档: 用户自主管理生成历史, 删除后材料目录同步回收。
    归属校验: 只能删自己的项目 (admin 可删任意)。"""
    if not _assert_owner(request, pid):
        return JSONResponse({"error": "无权访问"}, status_code=403)
    import shutil
    from web.uploads import project_dir
    conn = connect()
    conn.execute("DELETE FROM project WHERE id=?", (pid,))
    conn.execute("DELETE FROM task_job WHERE pid=?", (pid,))
    conn.commit()
    conn.close()
    pd = project_dir(pid)
    if pd.exists():
        shutil.rmtree(pd, ignore_errors=True)
    _cache.pop(f"assess:{pid}", None)
    return {"ok": True, "deleted": pid}


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return env.get_template("login.html").render()


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    u = _auth.verify_user(str(form.get("username", "")), str(form.get("password", "")))
    if not u:
        return env.get_template("login.html").render(error="用户名或密码错误")
    tok = _auth.make_token(u["username"])
    _sessions[tok] = u
    resp = HTMLResponse('<script>location.href="/"</script>')
    resp.set_cookie("ohs_session", tok, max_age=7 * 86400, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    return HTMLResponse('<script>location.href="/login"</script>').set_cookie(
        "ohs_session", "", max_age=0)


def _require_login(request: Request) -> dict | None:
    """页面保护: 未登录返回 None (路由里跳转登录)"""
    return _current_user(request)


@app.get("/", response_class=HTMLResponse)
def index_page(request: Request):
    user = request.state.user if hasattr(request.state, "user") else _current_user(request)
    # 报告工作台: 预创建当前身份的报告 (一个身份=一份)
    pid = get_or_create_report(user)["id"]
    return env.get_template("index.html").render(user=user, pid=pid)


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    """管理后台: 审计日志 + 用户管理 (仅admin)"""
    user = _require_login(request)
    if not user or not user.get("is_admin"):
        return HTMLResponse("需要管理员权限", status_code=403)
    rows = _auth.query_audit(300)
    users = _auth.list_users()
    trs = "".join(
        f"<tr><td>{r['ts']}</td><td>{r['user']}</td><td>{r['method']}</td>"
        f"<td class='mono'>{r['path'][:60]}</td><td>{r['status']}</td>"
        f"<td>{r['ip']}</td><td>{r['duration_ms']}ms</td></tr>"
        for r in rows)
    urows = "".join(
        f"<tr><td>{u['username']}</td><td>{'管理员' if u['is_admin'] else '用户'}</td>"
        f"<td>{u['created']}</td>"
        f"<td><button onclick=\"delUser('{u['username']}')\">删除</button></td></tr>"
        for u in users)
    return HTMLResponse(f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>管理后台</title><style>
body {{ font-family: -apple-system,'PingFang SC',sans-serif; margin: 24px; background:#F9FAFB; }}
h1 {{ font-size:20px; }} h2 {{ font-size:15px; margin:28px 0 10px; }}
table {{ border-collapse: collapse; width:100%; background:#fff; font-size:12.5px; }}
th,td {{ border:1px solid #E5E7EB; padding:6px 10px; text-align:left; }}
th {{ background:#F3F4F6; }}
.mono {{ font-family: ui-monospace,monospace; }}
button {{ border:1px solid #d1d5db; background:#fff; border-radius:6px; padding:2px 10px; cursor:pointer; }}
.add {{ margin:10px 0; }} .add input {{ padding:6px 10px; border:1px solid #d1d5db; border-radius:6px; }}
.add button {{ padding:7px 16px; background:#2563EB; color:#fff; border:none; }}
.out {{ float:right; }}
</style></head><body>
<h1>管理后台 <a class="out" href="/">← 返回工作台</a></h1>
<h2>用户</h2>
<div class="add">
  <input id="nu" placeholder="用户名"><input id="np" placeholder="密码" type="password">
  <label><input type="checkbox" id="na"> 管理员</label>
  <button onclick="addUser()">添加用户</button>
</div>
<table><tr><th>用户名</th><th>角色</th><th>创建时间</th><th></th></tr>{urows}</table>
<h2>使用记录 (最近300条)</h2>
<table><tr><th>时间</th><th>用户</th><th>方法</th><th>路径</th><th>状态</th><th>IP</th><th>耗时</th></tr>
{trs}</table>
<script>
async function addUser() {{
  const r = await fetch('/api/admin/users', {{method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{username: nu.value, password: np.value, is_admin: na.checked}})}});
  if ((await r.json()).ok) location.reload(); else alert('添加失败(可能重名)');
}}
async function delUser(u) {{
  if (!confirm('删除 ' + u + '?')) return;
  await fetch('/api/admin/users/' + u, {{method:'DELETE'}});
  location.reload();
}}
</script></body></html>""")


@app.post("/api/admin/users", response_class=JSONResponse)
async def api_add_user(request: Request):
    user = _current_user(request)
    if not user or not user.get("is_admin"):
        return JSONResponse({"ok": False}, status_code=403)
    try:
        body = json.loads(await request.body())
        ok = _auth.create_user(str(body.get("username", "")), str(body.get("password", "")),
                               bool(body.get("is_admin", False)))
        return {"ok": ok}
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)


@app.delete("/api/admin/users/{username}", response_class=JSONResponse)
def api_del_user(username: str, request: Request):
    user = _current_user(request)
    if not user or not user.get("is_admin"):
        return JSONResponse({"ok": False}, status_code=403)
    return {"ok": _auth.delete_user(username)}


@app.get("/api/admin/audit", response_class=JSONResponse)
def api_audit(request: Request, limit: int = 200, user: str | None = None):
    me = _current_user(request)
    if not me or not me.get("is_admin"):
        return JSONResponse([], status_code=403)
    return _auth.query_audit(limit, user)


@app.get("/projects/{pid}", response_class=HTMLResponse)
def project_page(request: Request, pid: str):
    p = get_project(pid)
    if not p:
        return HTMLResponse("项目不存在", status_code=404)
    sections = []
    from web.report_struct import CHAPTERS, SUBS, SUBS3
    for sec in CHAPTERS:
        subs = [{"id": sn, "title": st} for sn, st in SUBS.get(sec, [])]
        for sn, st in SUBS.get(sec, []):
            for sub3, (parent, t3) in SUBS3.items():
                if parent == sn:
                    subs.append({"id": sub3, "title": t3})
        sections.append({"id": sec, "title": CHAPTERS[sec],
                         "tables": 0, "paragraphs": 0, "subs": subs})
    return env.get_template("project.html").render(
        project=p, sections=sections, pid=pid)


@app.get("/report/{pid}", response_class=HTMLResponse)
def report_page(request: Request, pid: str):
    """报告阅读页 (顺读式) — 一个身份一份报告, 校验归属"""
    if not _assert_owner(request, pid):
        return HTMLResponse("无权访问该报告", status_code=403)
    p = get_project(pid)
    if not p:
        return HTMLResponse("报告不存在", status_code=404)
    # 注入报告整体结构 (11章 + 二级 + 三级 + 固定四级 + 数据三级/四级), 供前端目录动态渲染
    from web.report_struct import CHAPTERS, SUBS, SUBS3, SUBS4, DATA3, _extract_product_units
    prod = _extract_product_units(p["data"] or {})
    tree = []
    for ch in CHAPTERS:
        node = {"key": ch, "title": CHAPTERS[ch], "level": 1, "children": []}
        for sn, t in SUBS.get(ch, []):
            snode = {"key": sn, "title": f"{sn} {t}", "level": 2, "children": []}
            for sub3, (parent, t3) in SUBS3.items():
                if parent == sn:
                    s3node = {"key": sub3, "title": f"{sub3} {t3}", "level": 3, "children": []}
                    for num, t4 in SUBS4.get(sub3, []):
                        s3node["children"].append({"key": num, "title": f"{num} {t4}", "level": 4, "children": []})
                    snode["children"].append(s3node)
            # 数据三级 (产品/工段级, 如 3.5.x 产品工艺): 挂在对应二级下
            for sub3_key, cfg in DATA3.items():
                if sub3_key.split(".")[0] == ch and sub3_key == sn:
                    for num, t4 in prod:
                        if num.startswith(sub3_key + "."):
                            snode["children"].append({"key": num, "title": f"{num} {t4}", "level": 3, "children": []})
            node["children"].append(snode)
        tree.append(node)
    return env.get_template("report.html").render(pid=pid, struct=tree, prod=prod)


@app.post("/api/projects/{pid}/upload/{cat}", response_class=JSONResponse)
async def api_upload(pid: str, cat: str, file: UploadFile):
    """上传材料文件 → 项目材料目录 (按附录A类别)"""
    from web.uploads import save_upload
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    from web.uploads import MAX_UPLOAD_BYTES
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse({"error": "文件不能超过 20 MB"}, status_code=413)
    try:
        r = save_upload(pid, cat, file.filename or "unnamed", content)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, "name": r["name"], "size": r["size"], "category": r["category"]}


@app.get("/api/projects/{pid}/materials", response_class=JSONResponse)
def api_project_materials(pid: str):
    """项目已上传材料 (按类别)"""
    from web.uploads import list_materials, CATEGORIES
    if not get_project(pid):
        return JSONResponse({"error": "not found"}, status_code=404)
    files = list_materials(pid)
    # 按类别组织
    by_cat = {c["key"]: [] for c in CATEGORIES}
    for f in files:
        by_cat.setdefault(f["category"], []).append(f)
    return {"categories": [
        {"key": c["key"], "name": c["name"], "desc": c["desc"],
         "files": by_cat.get(c["key"], [])} for c in CATEGORIES]}


@app.post("/api/projects", response_class=JSONResponse)
def api_create_project(payload: dict):
    """新建空白项目；材料由用户上传。"""
    name = payload.get("name", "未命名项目")
    industry = payload.get("industry", "")
    p = create_project(name, industry)
    if not p:
        return JSONResponse({"error": "create failed"}, status_code=500)
    return {"id": p["id"], "name": p["name"], "seed_materials": 0}


@app.put("/api/projects/{pid}", response_class=JSONResponse)
def api_update_project(pid: str, payload: dict):
    """更新项目输入数据 (设备/检测/行业码) → 缓存失效"""
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = p["data"]
    rebuild = False
    for k in ("industry", "equipment", "detections", "processes", "process_text"):
        if k in payload:
            data[k] = payload[k]
    # 影响骨架表的字段变了 → 重建 (location/company/investment/staffing/shifts)
    for k in ("location", "company", "investment", "capacity", "staffing", "shifts",
              "materials", "products", "equipment_detail", "buildings", "hazard_grid"):
        if k in payload:
            data[k] = payload[k]
            rebuild = True
    if rebuild:
        try:
            from web.company_profile import build_profile
            data["profile"] = build_profile(data)
            try:
                from web.derived_fields import derive_fields
                for _k, _v in derive_fields(data).items():
                    if _v:
                        data[_k] = _v
            except Exception:
                pass
            from web.table_skeleton import build_skeletons
            data["built_tables"] = build_skeletons(data)
        except Exception:
            pass
    update_project(pid, payload.get("name", p["name"]), data)
    _cache.pop(f"assess:{pid}", None)  # 失效缓存
    return {"ok": True}


@app.get("/api/projects/{pid}/quality", response_class=JSONResponse)
async def api_quality(pid: str):
    """数据质检结果 (导入时沉淀; 无则现算一次)"""
    p = get_project(pid)
    data = p["data"]
    qr = data.get("quality_report")
    if not qr:
        try:
            from web.data_quality import quality_check
            qr = quality_check(pid, data)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)[:100]})
    return JSONResponse({"ok": True, "quality": qr})


@app.get("/api/projects/{pid}/coverage", response_class=JSONResponse)
async def api_coverage(pid: str):
    """数据完整度: 40张取证表骨架×数据沉淀状态 (覆盖度面板)"""
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    from web.coverage import coverage_report
    cov = coverage_report(p["data"])
    return JSONResponse({"ok": True, "coverage": cov})


@app.get("/api/projects/{pid}/provenance", response_class=JSONResponse)
async def api_provenance(pid: str):
    """数字溯源审计: 每个数字的来源 (rule/std/llm) + 无据清单

    用户红线: 不信任 LLM 提取/合成的数字。本端点把"这个数字哪来的"变成可审计事实。
    built_tables 缺失时现场用规则建一次 (只读, 不落盘), 保证面板始终有数据。
    """
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = p["data"]
    bt = data.get("built_tables") or {}
    live = False
    if not bt:
        from web.table_builder import build_data_tables
        bt = build_data_tables(data)
        live = True
    from web.number_provenance import audit_tables
    rep = audit_tables(bt)
    rep["live"] = live          # True=表为现场规则生成 (项目还没导入沉淀)
    return JSONResponse({"ok": True, "provenance": rep})


@app.get("/api/materials", response_class=JSONResponse)
def api_materials():
    """已上传的材料文件清单 (附录A分类)"""
    from web.materials_extract import OUT_DIR
    files = []
    for f in sorted(OUT_DIR.glob("*.json")) + sorted(OUT_DIR.glob("*.csv")) + sorted(OUT_DIR.glob("*.txt")):
        size = f.stat().st_size
        lines = sum(1 for _ in open(f, encoding="utf-8", errors="ignore")) if size else 0
        files.append({"name": f.name, "size": size, "lines": lines,
                      "kind": f.suffix.lstrip(".").upper()})
    return {"files": files, "count": len(files)}


@app.post("/api/projects/{pid}/import-materials", response_class=JSONResponse)
def api_import_materials(pid: str):
    """从材料文件导入项目数据 (设备/检测/工艺) → 保存+缓存失效
    材料目录: 优先 data/materials/<pid>/, 否则 seed(data/materials归长兴)"""
    from web.uploads import project_dir, list_materials
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    # 项目专属材料目录存在 → 用项目材料
    pm_files = list_materials(pid)
    if not pm_files:
        return JSONResponse({"ok": False, "error": "请先上传项目材料"}, status_code=400)
    # 只从当前项目材料解析，避免误读全局演示数据。
    data = import_materials_from_dir(pid)
    dets = [d for d in data["detections"] if d.get("ctwa") is not None]
    merged = dict(p["data"])
    merged["equipment"] = data["equipment"]
    merged["detections"] = dets
    merged["process_text"] = data["process_text"]
    # 重新导入 = 材料重新解析: 解析出的全部字段覆盖旧值 (否则提取修复永远落不到库)
    for _k in ("materials", "staffing", "shifts", "ppe", "emergency", "buildings",
               "facilities", "products", "public_works", "health_check", "management",
               "hazard_grid", "emergency_supplies", "equipment_detail",
               "investment", "ohy_investment", "area", "capacity", "nature", "location",
               "company", "founded", "registered_capital", "registered_capital_all",
               "legal_rep", "investor", "protection"):
        _v = data.get(_k)
        if _v not in (None, "", [], {}):  # 材料没给的键不覆盖 (保留旧值/手填值)
            merged[_k] = _v
    # 企业画像 + 表骨架先行: 数据处理阶段沉淀 (画像=结构化聚合; 骨架表=规则+LLM增强), 导出纯读取
    try:
        from web.company_profile import build_profile
        merged["profile"] = build_profile(merged)
        # 派生字段 (纯规则): 辐射源项 / 工作制度 — 真实报告1.1基本情况必备字段
        try:
            from web.derived_fields import derive_fields
            for _k, _v in derive_fields(merged).items():
                if _v:                       # 材料没给的派生项不覆盖旧值
                    merged[_k] = _v
        except Exception:
            pass
        # 表骨架先行: 36 张表全部建出来 (能填的填, 缺的标待补充/待评估)
        from web.table_skeleton import build_skeletons
        merged["built_tables"] = build_skeletons(merged)
        _ext_pid = pid
        _ext_data = dict(merged)
    except Exception:
        _ext_pid, _ext_data = None, None
    # 外部取数表 (气象等需联网/LLM): 后台异步补齐, 不阻塞导入返回
    # (用户设计: "异步联网请求, 同时也可以开始建表了")
    if _ext_pid and _ext_data is not None:
        try:
            from web.external_tables import fill_external_async
            fill_external_async(_ext_pid, _ext_data)
        except Exception:
            pass
    # 数据质检: 导入后自动跑, 结果沉淀 (前端/导出前可查; error 不阻断但醒目提示)
    qr = None
    try:
        from web.data_quality import quality_check
        qr = quality_check(pid, merged)
        merged["quality_report"] = {"summary": qr["summary"], "items": qr["items"],
                                    "checked_at": int(time.time())}
    except Exception:
        pass
    update_project(pid, p["name"], merged)
    _cache.pop(f"assess:{pid}", None)
    resp = {"ok": True, "equipment": len(merged["equipment"]),
            "detections": len(dets), "materials": data.get("material_count", 0)}
    if qr:
        resp["quality"] = {"summary": qr["summary"],
                           "errors": [i["message"] for i in qr["items"] if i["level"] == "error"],
                           "warns": [i["message"] for i in qr["items"] if i["level"] == "warn"]}
    return resp


def import_materials_from_dir(pid: str) -> dict:
    """从项目材料目录导入 (上传的文件 → 设备/检测/工艺文本)
    列名容错: 检测 CTWA(mg/m3)|CTWA 均可; <1 → 0.5
    """
    import csv, json, io
    from web.uploads import project_dir
    pd = project_dir(pid)
    eq, dets = [], []
    proc = ""
    mats, staffs, ppe = [], [], []
    health_checks = []  # 职业健康检查(现状/体检)
    eq_detail, shifts = [], []
    seen_mat, seen_st, seen_eq, seen_det = set(), set(), set(), set()
    prot, emergency = "", ""
    management = ""  # 职业卫生管理措施
    proj_name, industry = "", ""
    buildings, facilities, products, public_works = [], [], [], []
    hazard_grids = []  # 危害识别网格 (评价单元|岗位|产品|工段|危害因素)
    emergency_supplies = []  # 应急物资 (类别|名称|数量|放置点位)
    investment, ohy_investment, area, capacity, nature, location = "", "", "", "", "", ""
    company, founded, reg_cap, reg_cap_all, legal_rep, investor = "", "", "", "", "", ""

    def _read_rows(f):
        """读取表格文件: 兼容 csv/txt/xlsx, 多种分隔符(\x07/\t/,/;), 容错编码"""
        # xlsx
        if f.suffix.lower() in (".xlsx", ".xls"):
            try:
                from openpyxl import load_workbook
                wb = load_workbook(f, read_only=True, data_only=True)
                ws = wb.active
                rows = [[c.value if c.value is not None else "" for c in r] for r in ws.iter_rows()]
                return rows
            except Exception:
                return []
        # csv/txt: 试多种编码 + 分隔符
        for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                raw = f.read_bytes().decode(enc)
                break
            except Exception:
                continue
        else:
            return []
        # 探测分隔符: \x07 > \t > , > ;
        sep = max(["\x07", "\t", ",", ";", "|"], key=lambda s: raw.count(s))
        if sep in (",", ";"):
            reader = csv.reader(io.StringIO(raw))
            return [r for r in reader if any(str(c).strip() for c in r)]
        # \x07/\t/|: 手工劈分
        return [[c.strip() for c in ln.split(sep)] for ln in raw.splitlines() if sep in ln]

    def _dict_rows(rows):
        """首行做表头 → 每行dict"""
        if not rows:
            return []
        header = [str(h).strip() for h in rows[0]]
        out = []
        for r in rows[1:]:
            d = {}
            for i, h in enumerate(header):
                if i < len(r):
                    d[h] = str(r[i]).strip()
                else:
                    d[h] = ""
            if any(v for v in d.values()):
                out.append(d)
        return out

    def _get(row, *keys):
        """列名容错取值: 任一 key 命中(前缀优先, 含关键词兜底)非空"""
        for k in keys:
            if not k:
                continue
            # 1) 精确匹配
            for rk, rv in row.items():
                if rk.strip() == k and rv:
                    return rv
            # 2) 前缀匹配: 列名带单位如'占地面积(㎡)' 命中'占地面积'
            for rk, rv in row.items():
                if k and rv and rk.strip().startswith(k):
                    return rv
            # 3) 包含匹配兜底: '建筑名称'/'原辅料名称' 命中关键词('名称'不在前缀)
            #    但排除单位后缀误配(如'层数'不该匹配'计算容积率面积')
            for rk, rv in row.items():
                rks = rk.strip()
                if not rv:
                    continue
                if k in rks:
                    # 排除: 关键词是'层数'但列名其实是'计算容积率面积'(含面积单位)
                    if k in ("层数", "高度") and ("容积率" in rks or "面积" in rks and k == "层数"):
                        continue
                    return rv
        return ""

    for f in pd.rglob("*"):
        if not f.is_file():
            continue
        name = f.name
        # ---- 复合报告解析 (申请报告/可研/现状评价/初步设计 → PDF/docx) ----
        # 从成套报告文档里抽取 概况/工艺/设备/物料/产品 (真实场景企业给的是报告不是拆好的表格)
        if any(k in name for k in ("申请报告", "可研", "现状评价", "现状", "初步设计", "项目申请", "报告书")) \
                and f.suffix.lower() in (".pdf", ".docx", ".doc"):
            try:
                from web.report_parser import parse_report_file
                rp = parse_report_file(f)
                proj_name = proj_name or rp.get("name", "")
                # 项目基本信息(industry/capacity)以"可研/申请报告"为准(本项目), 现状报告是现有企业(存量)不覆盖
                _is_proj_report = any(k in name for k in ("可研", "申请报告", "项目申请", "初步设计"))
                if _is_proj_report:
                    # 申请报告优先: 覆盖现状报告的行业/产能(本项目新增)
                    if rp.get("industry"):
                        industry = rp["industry"]
                    if rp.get("capacity"):
                        capacity = rp["capacity"]
                else:
                    industry = industry or rp.get("industry", "")
                    capacity = capacity or rp.get("capacity", "")
                nature = nature or rp.get("nature", "")
                location = location or rp.get("location", "")
                investment = investment or rp.get("investment", "")
                ohy_investment = ohy_investment or rp.get("ohy_investment", "")
                area = area or rp.get("area", "")
                # 企业基本信息只从"本项目报告"(可研/申请)取; 现状评价报告是存量企业描述
                # (修复: 04现状报告写'2300万美元', 03可研写'4100万美元' — 现状报告不得覆盖本项目企业信息)
                if _is_proj_report:
                    company = company or rp.get("company", "")
                    founded = founded or rp.get("founded", "")
                    reg_cap = reg_cap or rp.get("registered_capital", "")
                    reg_cap_all = reg_cap_all or rp.get("registered_capital_all", "")
                    legal_rep = legal_rep or rp.get("legal_rep", "")
                    investor = investor or rp.get("investor", "")
                else:
                    company = company or rp.get("company", "")
                    founded = founded or rp.get("founded", "")
                if rp.get("process_text") and not proc:
                    proc = rp["process_text"] if len(rp["process_text"]) > len(proc) else proc
                if rp.get("equipment"):
                    for e in rp["equipment"]:
                        if e not in seen_eq:
                            eq.append(e); seen_eq.add(e)
                if rp.get("materials"):
                    # materials 现为 dict 列表(带完整列: name/规格/年用量/最大储量/物态/储存地点), 取name去重, 保留完整列
                    for m in rp["materials"]:
                        nm = m.get("name") if isinstance(m, dict) else m
                        if isinstance(m, dict):
                            nm = (m.get("name") or "").strip()
                            if nm and nm not in seen_mat:
                                mats.append(m); seen_mat.add(nm)
                        else:
                            if m and m not in seen_mat:
                                mats.append({"name": m, "msds": "报告内"}); seen_mat.add(m)
                if rp.get("products"):
                    # products 现为 dict 列表({name/output/变化量}), 按name去重
                    for p in rp["products"]:
                        if isinstance(p, dict):
                            pn = (p.get("name") or "").strip()
                            if pn and pn not in [x.get("name") for x in products]:
                                products.append(p)
                            elif pn:
                                # 同name已存在: 后处理文件带 delta(可研: 本项目产品判定) → 覆盖旧条目
                                for x in products:
                                    if x.get("name") == pn and p.get("delta_num") is not None:
                                        x.update(p)
                                        break
                        else:
                            if p and p not in products:
                                products.append({"name": p})
                # 额外字段合并: 定员/班制/检测/建构筑物/防护设施/PPE/体检 (从复合报告提取)
                if rp.get("staffing"):
                    for s in rp["staffing"]:
                        if s.get("post") not in [x.get("post") for x in staffs]:
                            staffs.append(s)
                if rp.get("shifts"):
                    for s in rp["shifts"]:
                        if s not in shifts:
                            shifts.append(s)
                if rp.get("detections"):
                    # 判重需**归一化**: 全角/半角括号 '（树脂）'/' (树脂)' 是同一因素。
                    # 原实现用精确字符串比对 → 同一因素存两条 (实测 其他粉尘（环氧树脂）)。
                    _nm = lambda s: (str(s or "").replace("（", "(").replace("）", ")")
                                     .replace(" ", "").strip())  # noqa: E731
                    _have = {_nm(x.get("factor")) for x in dets}
                    for dt in rp["detections"]:
                        _f = _nm(dt.get("factor"))
                        if _f and _f not in _have:
                            _have.add(_f)
                            seen_det.add(str(dt.get("factor")).strip())
                            # dt 键: factor/ctwa/cste/cme (parse_report_file 检测表提取)
                            # ⚠ 必须带上 _file: 否则表格 provenance 无从标注来源 →
                            #   prov_rule(traceable=False) 降级 untraced → 审计判 unverified
                            dets.append({"factor": dt.get("factor"), "ctwa": dt.get("ctwa", ""),
                                         "factory": dt.get("factory", ""),
                                         "_file": f.name, "source": "text"})
                if rp.get("buildings"):
                    _BADB = ("功能区", "建构筑物名称", "名称", "None", "序号")
                    for b in rp["buildings"]:
                        nm = (b.get("name") or "").strip() if isinstance(b, dict) else ""
                        if nm and nm not in _BADB and nm not in [x.get("name") for x in buildings]:
                            buildings.append(b)
                if rp.get("facilities"):
                    for fa in rp["facilities"]:
                        if fa.get("facility") not in [x.get("facility") for x in facilities]:
                            facilities.append(fa)
                if rp.get("ppe"):
                    for p in rp["ppe"]:
                        if p.get("item") not in [x.get("item") for x in ppe]:
                            ppe.append(p)
                if rp.get("health_check") and not health_checks:
                    health_checks = rp["health_check"]
                if rp.get("hazard_grid"):
                    for g in rp["hazard_grid"]:
                        if g not in hazard_grids:
                            hazard_grids.append(g)
                if rp.get("public_works"):
                    for pw in rp["public_works"]:
                        if pw not in public_works:
                            public_works.append(pw)
                if rp.get("emergency") and not emergency:
                    emergency = rp["emergency"]
                if rp.get("management") and not management:
                    management = rp["management"]
                if rp.get("equipment_detail"):
                    # rp.equipment_detail = 完整列dict(位号/规格/数量/材质), 合并进 eq_detail
                    # 清洗 x/None → 空; 丢弃无位号且无数量的不完整记录(申请报告PDF设备表), 保留现状报告表13完整设备
                    for e in rp["equipment_detail"]:
                        if not (isinstance(e, dict) and e.get("name")):
                            continue
                        for f in ("qty", "位号", "材质", "车间", "spec"):
                            if e.get(f) in ("x", "None", "x/None"):
                                e[f] = ""
                        if not (e.get("位号") or e.get("qty")):
                            continue
                        key = (e.get("name"), e.get("位号", ""))
                        ex = next((x for x in eq_detail if (x.get("name"), x.get("位号", "")) == key), None)
                        if ex:
                            for f in ("qty", "材质", "车间", "spec"):
                                if (not ex.get(f)) and e.get(f):
                                    ex[f] = e[f]
                        else:
                            eq_detail.append(e)
                if rp.get("emergency_supplies") and not emergency_supplies:
                    emergency_supplies = rp["emergency_supplies"]
            except Exception:
                pass
        try:
            # C1/C2 项目概况/批文: 提取项目名/行业 + 概况细节
            if ("概况" in name or "批文" in name or "立项" in name or "可研" in name):
                if f.suffix in (".json", ".txt"):
                    raw = f.read_text(encoding="utf-8", errors="ignore")
                    try:
                        j = json.loads(raw)
                        if isinstance(j, dict):
                            proj_name = proj_name or (j.get("name") or j.get("项目名称") or "")
                            industry = industry or (j.get("industry") or j.get("行业") or "")
                            investment = investment or (j.get("investment") or j.get("投资") or "")
                            area = area or (j.get("area") or j.get("建筑面积") or "")
                            capacity = capacity or (j.get("capacity") or j.get("产能") or "")
                            nature = nature or (j.get("nature") or j.get("项目性质") or "")
                            location = location or (j.get("location") or j.get("建设地点") or j.get("项目地点") or "")
                    except Exception:
                        for ln in raw.splitlines():
                            if "项目名称" in ln and "：" in ln: proj_name = proj_name or ln.split("：",1)[1].strip()[:60]
                            if "行业" in ln and "：" in ln: industry = industry or ln.split("：",1)[1].strip()[:40]
                            if "投资" in ln and "：" in ln: investment = investment or ln.split("：",1)[1].strip()[:30]
                            if "建筑面积" in ln and "：" in ln: area = area or ln.split("：",1)[1].strip()[:20]
                            if "产能" in ln and "：" in ln: capacity = capacity or ln.split("：",1)[1].strip()[:30]
                            if "项目性质" in ln and "：" in ln: nature = nature or ln.split("：",1)[1].strip()[:10]
                            if "建设地点" in ln and "：" in ln: location = location or ln.split("：",1)[1].strip()[:60]
                elif f.suffix in (".csv", ".xlsx"):
                    rows = _dict_rows(_read_rows(f))
                    for r in rows:
                        proj_name = proj_name or _get(r, "项目名称", "名称")
                        industry = industry or _get(r, "行业", "行业类别", "行业代码")
                        investment = investment or _get(r, "投资", "投资总额")
                        area = area or _get(r, "建筑面积", "占地面积")
                        capacity = capacity or _get(r, "产能", "建设规模")
                        nature = nature or _get(r, "项目性质")
            # C3 原有项目
            if "原有" in name or "现有" in name:
                raw = f.read_text(encoding="utf-8", errors="ignore")
                orig = "；".join(ln.lstrip("- ").strip() for ln in raw.splitlines() if ln.strip())[:400]
                proj_name = proj_name or orig[:60]
            # C4 公辅工程
            if "公辅" in name or "给排水" in name or "三废" in name or "水电气" in name:
                raw = f.read_text(encoding="utf-8", errors="ignore")
                pub = "；".join(ln.lstrip("- ").strip() for ln in raw.splitlines() if ln.strip())[:300]
                if pub:
                    public_works.append(pub)
            # C5 建构筑物
            if "建构筑" in name or "建筑" in name or "总平面" in name:
                rows = _dict_rows(_read_rows(f))
                for r in rows:
                    nm = _get(r, "名称", "建筑物名称", "建筑名称")
                    if nm and nm not in ("名称", "建筑名称", "建筑物名称"):
                        buildings.append({"name": nm, "area": _get(r, "占地面积", "占地"),
                                          "floor_area": _get(r, "建筑面积"),
                                          "floors": _get(r, "层数"),
                                          "height": _get(r, "建筑高度"),
                                          "fire_risk": _get(r, "火灾危险性", "火危"),  # 格兰富表列: 火危类别/耐火等级
                                          "fire_grade": _get(r, "耐火等级")})
            # 设施配置明细 (岗位×设施×数量)
            if "设施" in name or "配置" in name:
                rows = _dict_rows(_read_rows(f))
                for r in rows:
                    nm = _get(r, "配备设施", "设施名称", "岗位名称", "名称")
                    if nm and nm not in ("配备设施", "名称"):
                        facilities.append({"post": _get(r, "岗位名称", "区域", "岗位"), "facility": nm, "count": _get(r, "数量"), "remark": _get(r, "备注")})
            # C9 产品产量
            if ("产品" in name and ("产量" in name or "方案" in name)):
                rows = _dict_rows(_read_rows(f))
                for r in rows:
                    nm = _get(r, "产品", "产品名称", "名称")
                    if nm:
                        products.append({"name": nm, "output": _get(r, "年产量", "产量", "产能")})
            # 原辅材料
            if "原辅材料" in name or "物料" in name or "原料" in name:
                rows = _dict_rows(_read_rows(f))
                for r in rows:
                    # 过滤表头重复行/列名行 (值==列名的行是表头残留)
                    vals = [str(v).strip() for v in r.values()]
                    if any(v in ("原辅料名称", "名称", "物料名称", "原辅材料") for v in vals):
                        continue
                    n = _get(r, "名称", "物料名称", "原辅材料", "原辅料名称")
                    if n:
                        mat = {"name": n, "spec": _get(r, "规格", "规格型号"), "usage": _get(r, "年用量", "使用量", "用量"), "state": _get(r, "状态", "物态"), "msds": _get(r, "MSDS", "是否MSDS")}
                        if n not in seen_mat:
                            mats.append(mat); seen_mat.add(n)
            # 岗位定员
            if "定员" in name or ("岗位" in name and "duty" not in name.lower()):
                rows = _dict_rows(_read_rows(f))
                for r in rows:
                    p = _get(r, "岗位", "岗位名称")
                    if p:
                        st = {"post": p, "dept": _get(r, "车间", "部门"), "count": _get(r, "人数", "人员编制"), "task": _get(r, "作业内容", "工作内容")}
                        if p not in seen_st:
                            staffs.append(st); seen_st.add(p)
            # 防护措施
            if "防护" in name and "个人" not in name:
                raw = f.read_text(encoding="utf-8", errors="ignore")
                prot_lines = [ln.lstrip("- ").strip() for ln in raw.splitlines() if ln.strip()][:40]
                prot = prot or "；".join(prot_lines)
            # 个人防护用品
            if "个人防护" in name or "ppe" in name.lower():
                for r in _dict_rows(_read_rows(f)):
                    pn = _get(r, "名称", "防护用品", "物品")
                    if pn:
                        ppe.append({"item": pn, "post": _get(r, "岗位", "岗位名称"), "frequency": _get(r, "发放频次", "更换周期", "周期")})
            # 应急救援
            if "应急" in name or "救援" in name:
                raw = f.read_text(encoding="utf-8", errors="ignore")
                emg_lines = [ln.lstrip("- ").strip() for ln in raw.splitlines() if ln.strip()][:25]
                emergency = emergency or "；".join(emg_lines)
            # 设备 (存全: 名称+规格+数量)
            if "设备" in name:
                rows = _dict_rows(_read_rows(f))
                for r in rows:
                    n = _get(r, "设备名称", "名称", "设备")
                    if n:
                        item = str(n).split("|")[0].strip()
                        if item and item not in seen_eq:
                            eq.append(item); seen_eq.add(item)
                            # 注: eq_detail 由 parse_report_file 的 equipment_detail(完整列dict) 合并提供,
                            #     不在此处 append (csv 缺位号/材质/车间, qty 可能取到 x → 污染设备明细表)
            # 班制 (C10_班制: 工种/系统×一班..合计)
            if "班制" in name or ("班" in name and "定员" not in name):
                rows = _dict_rows(_read_rows(f))
                for r in rows:
                    sysName = _get(r, "工种", "系统及工种名称", "系统", "岗位")
                    if sysName:
                        shifts.append({"system": sysName,
                                       "b1": _get(r, "一班"), "b2": _get(r, "二班"),
                                       "b3": _get(r, "三班"), "b4": _get(r, "四班"),
                                       "total": _get(r, "合计")})
            # 检测 (排除体检报告: 文件名含"职业健康检查/体检/检查总结/健康监护" ≠ 现场检测)
            # (修复: 05_类比检测文件 实为体检总结报告(康检字第20251354号), "危害因素|CTWA"列
            #  实为体检项目/接触因素标注 → 误提取成检测值 → LLM 写进结论'聚乙烯粉尘超标')
            if "检测" in name and not any(k in name for k in ("职业健康检查", "体检", "检查总结", "健康监护", "检查报告", "健康检查")):
                # ⚠ .pdf 不能走 _read_rows: 它对 PDF 会用 latin-1 解码二进制 → 返回**几万行垃圾**
                #   (实测 125 页扫描件 → 78700 行), 不报错也不为空, 会污染检测数据。
                #   PDF 一律交给 report_parser(有文本层) 或 vision_extract(扫描件), 不走表格读。
                _is_pdf = f.suffix.lower() == ".pdf"
                _rows = [] if _is_pdf else _dict_rows(_read_rows(f))
                for r in _rows:
                    fac = _get(r, "危害因素", "因子", "检测项目")
                    ctwa_raw = _get(r, "CTWA(mg/m3)", "CTWA", "PC-TWA", "检测值")
                    if fac and fac not in seen_det:
                        seen_det.add(fac)
                        if not ctwa_raw: ctwa_f = None
                        elif "<" in ctwa_raw or "＜" in ctwa_raw: ctwa_f = 0.5
                        else:
                            try: ctwa_f = float(ctwa_raw)
                            except ValueError: ctwa_f = None
                        dets.append({"factor": fac, "ctwa": ctwa_f})
                # ---- PDF 检测报告: 有文本层走 report_parser, 扫描件走视觉模型 ----
                # 实测: 05_类比检测报告 125页纯扫描件 (0 字符), 含作业分级必需的游离二氧化硅。
                # 沿用**同一体检排除名单**(不把体检表当检测表 — 历史 bug)。
                # 结果标 source="vision": 机器识别 ≠ 确定提取, 供溯源面板提示人工核对。
                if _is_pdf:
                    try:
                        from web.vision_extract import _is_scanned, vision_detections
                        _scan, _np = _is_scanned(f)
                        if _scan:
                            _vr = vision_detections(f, verbose=False)
                            # 索引已有条目: 同一 factor 可能多次出现(表型B 汇总 + 表型D 二氧化硅),
                            # 且不同页可能有**同名但信息量不同**的条目 (p16 汇总无浓度 / p30 有浓度)。
                            # ⚠ 不能"先到先占": 无浓度的条目先入会把有浓度的**当重复丢掉**。
                            #   (实测 聚乙烯粉尘 p16 无 ctwa 占位 → p30 的 <0.33 被丢)
                            #   → 策略: 同 factor 保留**信息更全**的那条, 其余只做字段补全。
                            _idx = {str(d.get("factor")): d for d in dets
                                    if d.get("source") == "vision"}

                            def _score(x: dict) -> int:
                                """信息量打分: 有浓度/结果/判定即加分 (用于择优保留)"""
                                s = 0
                                if x.get("ctwa"):
                                    s += 4
                                if x.get("results"):
                                    s += 2
                                if x.get("sio2"):
                                    s += 4
                                if x.get("judgement"):
                                    s += 1
                                return s

                            def _norm_name(s: str) -> str:
                                """因素名归一: 全角/半角括号、空格统一 (用于判重)。

                                实测同一因素会以 '其他粉尘(树脂)' / '其他粉尘（树脂）' 两种
                                写法出现 (不同页 OCR 结果) → 不归一会存成两条重复项。
                                """
                                return (str(s).replace("（", "(").replace("）", ")")
                                        .replace(" ", "").strip())

                            def _near_dup(a: str, b: str) -> bool:
                                """近似同名判定: 归一后相等, 或一方是另一方前缀且长度差<=2。

                                实测视觉/OCR 会把 '电焊烟尘' 截成 '电焊烟' → 同一因素两条。
                                只判前缀 + 长度接近, 避免把 '苯' 与 '苯乙烯'(真不同) 合并。
                                """
                                na, nb = _norm_name(a), _norm_name(b)
                                if na == nb:
                                    return True
                                lo, hi = (na, nb) if len(na) <= len(nb) else (nb, na)
                                return len(hi) - len(lo) <= 2 and len(lo) >= 2 and hi.startswith(lo)

                            for _it in (_vr.get("detections") or []):
                                fac = str(_it.get("factor") or "").strip()
                                if not fac:
                                    continue
                                _new = {
                                    "factor": fac,
                                    "ctwa": _it.get("ctwa") or None,
                                    "results": _it.get("results") or [],
                                    "sio2": _it.get("sio2_percent") or None,
                                    "judgement": _it.get("judgement") or "",
                                    "source": "vision",
                                    "_page": _it.get("_page"),
                                    "_file": _it.get("_file", f.name),
                                }
                                # 先找精确同名, 再找近似同名(截断) → 归并到更完整的那条
                                _hit = fac if fac in _idx else next(
                                    (k for k in _idx if _near_dup(k, fac)), None)
                                if _hit is not None and _hit in seen_det:
                                    _old = _idx[_hit]
                                    # 名字更完整的一侧获胜 (按**归一后**长度比较,
                                    # 全角/半角括号不算"更完整")
                                    if len(_norm_name(fac)) > len(_norm_name(_hit)):
                                        _old["factor"] = fac
                                        _idx.pop(_hit, None)
                                        _idx[fac] = _old
                                    if _score(_new) > _score(_old):
                                        _old.update({k: v for k, v in _new.items() if v})
                                    else:
                                        for k, v in _new.items():
                                            if v and not _old.get(k):
                                                _old[k] = v
                                    continue
                                seen_det.add(fac)
                                _idx[fac] = _new
                                dets.append(_new)
                            # 丢弃"碎片": 名字被另一条**完整因素名**包含, 且自身无任何数据。
                            # 实测 '酸酐'(p12) 是 '邻苯二甲酸酐' 的截断 → 无浓度无结果,
                            # 属 OCR 误切; 而 '碳酸钙'/'照度'(真因素只是未测) 不满足"被包含"故保留。
                            def _is_fragment(x: dict, pool: list) -> bool:
                                nm = _norm_name(x.get("factor"))
                                if not nm or len(nm) > 4:
                                    return False
                                if x.get("ctwa") or x.get("sio2") or x.get("results"):
                                    return False
                                for o in pool:
                                    if o is x:
                                        continue
                                    on = _norm_name(o.get("factor"))
                                    if len(on) > len(nm) and nm in on:
                                        return True
                                return False

                            dets[:] = [d for d in dets if not _is_fragment(d, dets)]
                            seen_det.clear()
                            seen_det.update(str(d.get("factor")) for d in dets)
                    except Exception:
                        pass  # 视觉提取失败不应中断导入 (文本层数据已拿到)
            # 工艺
            if "工艺" in name:
                proc = proc + "\n" + f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
    return {"equipment": eq, "detections": dets, "process_text": proc.strip(),
            "material_count": len(eq), "name": proj_name, "industry": industry,
            "materials": mats, "staffing": staffs, "protection": prot,
            "ppe": ppe, "emergency": emergency,
            "buildings": buildings, "facilities": facilities,
            "products": products, "public_works": public_works,
            "investment": investment, "ohy_investment": ohy_investment,
            "area": area, "capacity": capacity, "nature": nature,
            "location": location,
            "company": company, "founded": founded, "registered_capital": reg_cap,
            "registered_capital_all": reg_cap_all,
            "legal_rep": legal_rep, "investor": investor,
            "equipment_detail": eq_detail, "shifts": shifts,
            "health_check": health_checks, "management": management,
            "hazard_grid": hazard_grids, "emergency_supplies": emergency_supplies}


@app.get("/api/projects/{pid}/export", response_class=JSONResponse)
def api_export(pid: str, request: Request):
    if not _assert_owner(request, pid):
        return JSONResponse({"error": "无权访问"}, status_code=403)

    """导出 Word 报告 (附录D格式)"""
    from web.word_export import export_docx
    from web.projects_db import get_project as gp
    from web.materials_import import import_materials
    p = gp(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = p["data"]
    conn = connect()
    project = {"id": pid, "name": p["name"], "industry": data.get("industry", ""),
               "equipment": data.get("equipment", []),
               "detections": data.get("detections", []),
               "process_text": data.get("process_text", ""),
               "materials": data.get("materials", []),
               "staffing": data.get("staffing", []),
               "protection": data.get("protection", ""),
               "ppe": data.get("ppe", []),
               "emergency": data.get("emergency", ""),
               "buildings": data.get("buildings", []),
               "facilities": data.get("facilities", []),
               "products": data.get("products", []),
               "public_works": data.get("public_works", []),
               "investment": data.get("investment", ""),
               "area": data.get("area", ""),
               "capacity": data.get("capacity", ""),
               "nature": data.get("nature", ""),
               "equipment_detail": data.get("equipment_detail", []),
               "shifts": data.get("shifts", []),
               "health_check": data.get("health_check", []),
               "management": data.get("management", ""),
               "hazard_grid": data.get("hazard_grid", []),
               "emergency_supplies": data.get("emergency_supplies", []),
               "built_tables": data.get("built_tables", {}),
               "profile": data.get("profile", {}),
               "location": data.get("location", ""),
               "company": data.get("company", "")}
    assess = assess_project(conn, project)
    # 把完整项目数据塞进 assess._project_data, 供 fill_section 内嵌表格取数
    assess["_project_data"] = dict(project)
    conn.close()
    out = ROOT / "data" / f"report_{pid}.docx"
    export_docx(project, assess, out, section_states=data.get("section_states"))
    return {"ok": True, "path": f"/data/report_{pid}.docx", "size": out.stat().st_size}


@app.get("/api/projects/{pid}/download", response_class=FileResponse)
def api_download_report(pid: str, request: Request):
    if not _assert_owner(request, pid):
        return JSONResponse({"error": "无权访问"}, status_code=403)

    """下载导出的 Word 报告 (受控, 不走裸 /data 路径, 避免nginx拦截)"""
    out = ROOT / "data" / f"report_{pid}.docx"
    if not out.is_file():
        return JSONResponse({"error": "报告不存在, 请先导出"}, status_code=404)
    return FileResponse(out, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        filename=f"report_{pid}.docx")


@app.get("/data/{filename:path}")
def report_file(filename: str):
    """只允许下载生成的 Word 报告，禁止暴露数据库和上传材料。"""
    if not re.fullmatch(r"report_[A-Za-z0-9_-]+\.docx", filename):
        return JSONResponse({"error": "not found"}, status_code=404)
    target = ROOT / "data" / filename
    if not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(
        target,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=target.name,
    )


@app.get("/api/projects/{pid}/draft/{sec}", response_class=JSONResponse)
def api_draft(pid: str, sec: str):
    """LLM 成文草稿 (10.2.3/10.2.5) — 数据来自机械结果, LLM组织语言"""
    from web.llm_draft import draft_section
    if not get_project(pid):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        text = draft_section(pid, sec)
        return {"ok": True, "text": text}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/projects/{pid}/data", response_class=JSONResponse)
def api_project_data(pid: str):
    """项目输入数据 (编辑表单用)"""
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    return p["data"]


@app.get("/api/projects/{pid}/overview", response_class=JSONResponse)
def project_overview(pid: str, request: Request):
    if not _assert_owner(request, pid):
        return JSONResponse({"error": "无权访问"}, status_code=403)

    """项目总览 (统计卡片数据)"""
    if not get_project(pid):
        return JSONResponse({"error": "not found"}, status_code=404)
    result = _get_assess(pid)
    hazards = result.get("hazards", [])
    judgements = result.get("judgements", [])
    passed = sum(1 for j in judgements if j.get("pass") is True)
    failed = sum(1 for j in judgements if j.get("pass") is False)
    risk = (result.get("industry_risk") or {})
    proj = result.get("_project_data") or {}
    dets = proj.get("detections", [])
    has_data = bool(hazards) or bool(dets) or bool((proj.get("process_text") or "").strip())
    # 资料覆盖度 (缺料兜底: 缺什么类别 → 影响哪些章节)
    # 用 _project_data(提取数据) 判断: 提取某字段→对应类别视为覆盖(复合报告只归一类不再误判)
    from web.uploads import coverage_report
    coverage = coverage_report(pid, project=proj) if has_data and proj else \
        (coverage_report(pid) if has_data else
         {"covered": [], "missing": [], "completeness": 0, "core_missing": []})
    return {
        "hazard_count": len(hazards),
        "judgement_count": len(judgements),
        "passed": passed, "failed": failed,
        "pass_rate": round(passed / len(judgements) * 100) if judgements else 0,
        "risk_level": risk.get("level", "—") if has_data else "—",
        "risk_name": risk.get("name", "") if has_data else "",
        "grade_count": len(result.get("grades", [])),
        "diseases": sum(1 for h in hazards if h.get("diseases")),
        "has_data": has_data,
        "coverage": coverage,
    }


@app.post("/api/projects/{pid}/sections/{sec}/generate", response_class=JSONResponse)
def api_generate_section(pid: str, sec: str):
    """生成本页: 机械结果确认后 → LLM 成文 → 保存章节状态(generated)"""
    from web.llm_draft import draft_section
    from web.projects_db import update_section_state
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        text = draft_section(pid, sec)
    except Exception as e:
        text = ""  # LLM失败: 保持机械状态
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    update_section_state(pid, sec, "generated", text)
    return {"ok": True, "text": text}


@app.post("/api/projects/{pid}/sections/{sec}/{sub}/generate", response_class=JSONResponse)
def api_generate_sub(pid: str, sec: str, sub: str):
    """生成本小节: LLM成文 → 保存状态"""
    from web.llm_draft import draft_sub
    from web.projects_db import update_section_state
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        text = draft_sub(pid, sec, sub)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    update_section_state(pid, sub, "generated", text)
    return {"ok": True, "text": text}


# ===== 后台批量生成 (关页面不中断, 进度可轮询) =====
from web import tasks as _tasks  # noqa: E402

_tasks.init_tasks()

# ===== 生命周期自动回收 (C档: 空项目TTL + 孤儿材料目录, 启动时幂等执行) =====
from web.projects_db import cleanup_expired as _cleanup_expired  # noqa: E402

_lifecycle = _cleanup_expired()
if _lifecycle["dropped_rows"] or _lifecycle["dropped_dirs"]:
    print(f"[lifecycle] 回收空项目 {_lifecycle['dropped_rows']} 行, 孤儿材料目录 {_lifecycle['dropped_dirs']} 个")


def _gen_one(pid: str, sec: str, sub: str | None, title: str | None = None, cache: dict | None = None) -> str:
    """统一生成单元: 按 key 层级分发 (一级 draft_section / 二级 draft_sub / 三级四级 draft_detail)"""
    from web.llm_draft import draft_section, draft_sub, draft_detail
    # 条件章节 (sub 是非数字 key, 如'受限空间作业措施'/'防电离辐射设施'/'现有企业概况')
    # → 按标题用 draft_detail 生成 (用标题而非编号)
    if sub and not sub.split(".")[0].isdigit():
        return draft_detail(pid, sec, title or sub, cache)
    # 三级/四级 (key 点数 >= 2 且非纯二级)
    if sub and sub.count(".") >= 2:
        return draft_detail(pid, sub, title or "", cache)
    if sub:
        return draft_sub(pid, sec, sub, cache)
    return draft_section(pid, sec, cache)


def _run_generate_all(pid: str, jid: str):
    """后台 worker: 1-12章 + 二级 + 三级 + 四级 LLM生成 (assess预计算 + 并发)
    进度实时; 用户取消可中断. 四级含固定模板 + 数据驱动(产品/工段级)"""
    from web.llm_draft import draft_section, draft_sub, draft_detail, _assess_cached
    from web.projects_db import update_section_state
    from web.report_struct import CHAPTERS, SUBS, SUBS3, SUBS4, DATA4, _extract_product_units
    from web.projects_db import get_project
    from concurrent.futures import ThreadPoolExecutor

    # units 按报告阅读顺序: 章 → 二级 → 三级 → 固定四级 → 下一章...
    p = get_project(pid)
    proj_data = p["data"] if p else {}
    conn = _conn_tasks()
    units = []
    for ch in CHAPTERS:
        units.append((ch, None, f"{ch} {CHAPTERS[ch]}"))                # 该章
        for sn, t in SUBS.get(ch, []):
            units.append((ch, sn, f"{sn} {t}"))                          # 二级
            # 三级
            for sub3, (parent, t3) in SUBS3.items():
                if parent == sn:
                    units.append((sub3.split(".")[0], sub3, f"{sub3} {t3}"))  # 三级
                    # 固定四级 (SUBS4 key 是三级编号)
                    for num, t4 in SUBS4.get(sub3, []):
                        units.append((num.split(".")[0], num, f"{num} {t4}"))  # 四级
            # 数据驱动四级 (产品/工段级): parent==SN 的三级若在 DATA4, 动态生成四级
            # DATA4 的 key 是三级编号(如 8.4.1), 产品/工段四级 = 8.4.1.1 特殊单体...
    # 数据驱动四级: 从 DATA4 的三级, 按产品/工段动态生成四级 units
    prod_units = _extract_product_units(proj_data or {})
    for sub4, title4 in prod_units:
        units.append((sub4.split(".")[0], sub4, f"{sub4} {title4}"))
    # 条件章节 (按项目字段判断是否生成: 受限空间/电离辐射/现有企业概况)
    # 判断标准: 设备/工艺/性质 字段命中关键词 (来自 conditional_sections.py, 标准库+联网验证)
    try:
        from web.conditional_sections import conditional_units as _cond_units
        for cch, ckey, ctitle in _cond_units(proj_data or {}):
            units.append((cch, ckey, ctitle))
    except Exception:
        pass
    total = len(units)
    conn.execute("UPDATE task_job SET total=? WHERE id=?", (total, jid))
    conn.commit()
    conn.close()

    # 预计算 assess/info 一次, 所有单元复用 (不再每单元重复重算)
    cache = _assess_cached(pid)

    done = fail = 0

    def _finish(f):
        """处理一个完成的 future: 写result (空结果不标记generated, 打勾必须真有内容)
        + 逐节校验器: 生成时检测(数值/要素/幻觉), 问题写 section_states['validation']"""
        nonlocal done, fail
        s, sb = futures.pop(f)
        try:
            text = f.result()
            # 生命周期的关键: 空/占位结果不算"已生成", 否则打勾但无内容
            t = text.strip() if text else ""
            if len(t) < 30 or t.startswith("暂不支持"):
                fail += 1
            else:
                # 逐节校验 (生成过程中检测, 不是事后)
                # all_texts: 用当前已写section_states做重复检测快照 (并发下尽力而为)
                try:
                    from web.validators import validate_section
                    from web.projects_db import get_project as _gp
                    try:
                        _snap = {k: (v or {}).get('text', '')
                                 for k, v in ((_gp(pid) or {}).get('data', {}).get('section_states') or {}).items()
                                 if isinstance(v, dict) and v.get('text')}
                    except Exception:
                        _snap = None
                    _val = validate_section(sb or s, t, proj_data, cache.get("assess"), _snap)
                    update_section_state(pid, sb or s, "generated", text, validation=_val)
                except Exception:
                    update_section_state(pid, sb or s, "generated", text)
                done += 1
        except Exception:
            fail += 1
        _tasks.set_progress(jid, done + fail)

    from concurrent.futures import wait as cf_wait, FIRST_COMPLETED
    import os as _os
    conc = int(_os.environ.get("LLM_CONCURRENCY", "5"))
    # 单单元 LLM 最长等待(秒): 正常几十秒, 超过则放弃该单元(不拖整体)
    UNIT_TIMEOUT = int(_os.environ.get("LLM_UNIT_TIMEOUT", "80"))
    with ThreadPoolExecutor(max_workers=conc, thread_name_prefix="ohsgen") as ex:
        futures: dict = {}
        for sec, sub, title in units:
            if _tasks._should_stop(jid):
                break
            fut = ex.submit(_gen_one, pid, sec, sub, title, cache)
            futures[fut] = (sec, sub)
            # 保持并发: 等任一完成; 单个挂死超时 -> 放弃该单元, 流水继续
            while len(futures) >= 3:
                if _tasks._should_stop(jid):
                    break
                d_f = cf_wait(list(futures), timeout=UNIT_TIMEOUT, return_when=FIRST_COMPLETED)
                for f in d_f.done:
                    _finish(f)
                # 超时仍无完成: 尝试取消挂死单元, 标fail继续(不无限等)
                if not d_f.done and len(futures) >= 3:
                    stuck = [f for f in futures if not f.done()]
                    for f in stuck:
                        if f in futures:
                            futures.pop(f, None)
                            fail += 1
                    # 立即重试内层, 避免死循环
                    continue
        # 收尾: 等剩余全部完成 (带上限, 防零散挂死)
        import time as _t
        _deadline = _t.time() + UNIT_TIMEOUT * 3
        while futures and _t.time() < _deadline:
            d_f = cf_wait(list(futures), timeout=UNIT_TIMEOUT, return_when=FIRST_COMPLETED)
            for f in d_f.done:
                _finish(f)
            if not d_f.done:
                for f in [f for f in futures if not f.done()]:
                    if f in futures:
                        futures.pop(f, None)
                        fail += 1
        # 剩余未完成的(超时放弃)
        for f in list(futures):
            if f in futures:
                futures.pop(f, None)
                fail += 1
    err = None if fail == 0 else f"{fail} 个单元失败 ({done} 成功)"

    # 三级/四级数据驱动单元在 _run_generate_all 主循环生成 (report_struct 的 SUBS3/SUBS4/DATA4)
    # 不再用 extract_units 生成 u:: 单元 (旧格式, 已被危害网格+三级/四级替代)
    return err


def _conn_tasks():
    from web.tasks import _conn
    return _conn()


def _run_bench_after_generate(pid: str):
    """生成后自动评测钩子 (确定性层; Judge 层可选 LLM, 生成时跳过省时)

    结果存 project.data["bench"] = {version, scores, issues, graded_at}
    页面'/api/report' 可读, 前端展示评测分。失败不阻塞生成结果(调用方已捕获)。
    """
    import json as _json
    from pathlib import Path as _P
    from web.projects_db import get_project as _gp

    p = _gp(pid)
    if not p:
        return
    pd = p.get("data") or {}
    if not pd.get("section_states"):
        return
    # 用正式报告文件 (生成时已导出 /app/data/report_{pid}.docx) — 与网页下载/export 一致
    # (修复: 钩子自 export_docx(assess={}) 漏 18 张表 → 评测失真)
    from pathlib import Path as _P2
    out_path = _P2(__file__).resolve().parent.parent / "data" / f"report_{pid}.docx"
    if not out_path.exists():
        # 回退: 现场导出 (assess 从正式 assess 缓存取, 不空)
        try:
            import tempfile
            from web.word_export import export_docx
            out_path = _P2(tempfile.gettempdir()) / f"bench_{pid}.docx"
            export_docx(pd, {}, out_path, section_states=pd.get("section_states"))
        except Exception as _e:
            import logging
            logging.getLogger("ohs").warning(f"bench 导出docx失败: {_e}")
            return
    tmpf = out_path
    # 跑确定性评测 (模块级复用 tools/report_bench)
    try:
        import sys as _sys
        tools_dir = _P(__file__).resolve().parent.parent / "tools"
        if str(tools_dir) not in _sys.path:
            _sys.path.insert(0, str(tools_dir))
        import report_bench as _rb
        std = []
        try:
            from web.report_struct import CHAPTERS, SUBS, SUBS3
            for ch in CHAPTERS:
                std.append(ch)
                for sn, _ in SUBS.get(ch, []):
                    std.append(sn)
        except Exception:
            pass
        text = "\n".join(pp.text for pp in __import__("docx").Document(str(tmpf)).paragraphs)
        issues = {
            "A_internal_consistency": _rb.check_internal_consistency(text),
            "B_traceability": _rb.check_traceability(text, pd),
            "D_structure": _rb.check_structure(tmpf, std),
            "E_tables": _rb.check_tables(tmpf, pd),
            "F_completeness": _rb.check_completeness(tmpf, pd),
        }
        def _score(items):
            if not items:
                return 100
            penalty = sum((25 if i.get("severity") == "high" else 12 if i.get("severity") == "medium" else 6)
                          for i in items)
            return max(0, 100 - penalty)
        scores = {k: _score(v) for k, v in issues.items()}
        total = round(sum(scores.values()) / 5, 1)
        bench = {"version": _rb.BENCH_VERSION, "scores": scores,
                 "total_deterministic": total, "issues": issues,
                 "graded_at": int(__import__("time").time())}
        p2 = _gp(pid)
        if p2:
            d2 = dict(p2.get("data") or {})
            d2["bench"] = bench
            _save_project_data(pid, d2)
        import logging
        logging.getLogger("ohs").info(f"[bench] pid={pid} 确定性评测 {total}/100 (v{bench['version']})")
    except Exception as _e2:
        import logging
        logging.getLogger("ohs").warning(f"bench 评测执行失败: {_e2}")


@app.post("/api/projects/{pid}/generate-all", response_class=JSONResponse)
def api_generate_all(pid: str, request: Request):
    """提交整篇批量生成 → 返回 job_id (后台执行)"""
    user = request.state.user if hasattr(request.state, "user") else {"username": "-"}
    if not get_project(pid):
        return JSONResponse({"error": "not found"}, status_code=404)
    from web.report_struct import all_units
    total = len(all_units())
    jid = _tasks.create_job(user["username"], pid, "generate_all", total)

    def _worker():
        try:
            _run_generate_all(pid, jid)
            _tasks.finish_job(jid, None)
        except Exception as e:
            _tasks.finish_job(jid, traceback.format_exc())

    import threading
    threading.Thread(target=_worker, daemon=True).start()
    return {"ok": True, "job_id": jid, "total": total}


@app.get("/api/tasks/{jid}", response_class=JSONResponse)
def api_task_status(jid: str):
    d = _tasks.get_job(jid)
    if not d:
        return JSONResponse({"error": "not found"}, status_code=404)
    return d


@app.post("/api/tasks/{jid}/cancel", response_class=JSONResponse)
def api_task_cancel(jid: str, request: Request):
    """取消后台生成任务"""
    ok = _tasks.cancel_job(jid)
    return {"ok": ok, "error": None if ok else "任务不存在或已结束"}


@app.get("/api/tasks", response_class=JSONResponse)
def api_task_list(request: Request, limit: int = 30):
    """任务列表 (admin可见全部; 游客看自己的) — 简化: 全部可见"""
    return _tasks.list_jobs(limit)


@app.get("/api/projects/{pid}/sections/{sec}/{sub}", response_class=JSONResponse)
def sub_section_content(pid: str, sec: str, sub: str, request: Request):
    if not _assert_owner(request, pid):
        return JSONResponse({"error": "无权访问"}, status_code=403)

    """二级小节内容 (1.1/2.1/3.1...)"""
    from web.report_struct import SUBS
    if not get_project(pid):
        return JSONResponse({"error": "not found"}, status_code=404)
    result = _get_assess(pid)
    subs = SUBS.get(sec, [])
    if not any(s == sub for s, _ in subs):
        return JSONResponse({"error": "unknown sub-section"}, status_code=404)
    from web.subsection_gen import build_subsection
    built = build_subsection(connect(), sec, sub, result)
    # 三级单元 (数据驱动) + 物质深文
    from web.unit_gen import build_units
    from web.uploads import project_dir
    units = build_units(sec, sub, {"name": result.get("project", ""),
                                   "equipment": result.get("_project_data", {}).get("equipment", []),
                                   "process_text": result.get("_project_data", {}).get("process_text", "")},
                        result)
    # 深文加载 (A2i_物质毒理学.json, 批量LLM生成)
    deep = {}
    pf = project_dir(pid) / "A2i_物质毒理学.json"
    if pf.exists():
        try:
            deep = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            pass
    for u in units:
        if u["type"] == "物质" and u["title"] in deep:
            u["deep"] = deep[u["title"]]
    title = next(t for s, t in subs if s == sub)
    return {"section": sec, "sub": sub, "title": f"{sub} {title}",
            "paragraphs": built["paragraphs"], "tables": built["tables"],
            "units": units,
            "evidence": []}


@app.get("/api/projects/{pid}/sections/{sec}", response_class=JSONResponse)
def section_content(pid: str, sec: str, request: Request):
    if not _assert_owner(request, pid):
        return JSONResponse({"error": "无权访问"}, status_code=403)

    """章节内容 + 依据 (报告11章编号; 数据槽填充)"""
    if not get_project(pid):
        return JSONResponse({"error": "not found"}, status_code=404)
    result = _get_assess(pid)
    from web.report_struct import CHAPTERS
    sk = {"title": CHAPTERS.get(sec, sec), "paragraphs": []}
    from web.section_filler import fill_section
    from web.paragraph_gen import fill_section_paragraphs
    from web.advice_gen import fill_10211
    # 证据映射 (新11章 → 标准10.2.x 条款)
    ev_map = {"5": ["10.2.5.3", "10.2.5.4"], "10": ["10.2.11"],
              "11": ["10.2.12"], "1": ["10.2.1"], "2": ["10.2.2"],
              "3": ["10.2.3"], "4": ["10.2.4"], "6": ["10.2.6", "10.2.7"],
              "7": ["10.2.7"], "8": ["10.2.8"], "9": ["10.2.9"]}
    ev_secs = ev_map.get(sec, [sec])
    paragraphs = fill_section_paragraphs(connect(), sec, result, sk["paragraphs"])
    tables = fill_section(connect(), sec, result)
    evidence = []
    for es in ev_secs:
        evidence += evidence_for_section(connect(), es, result)
    return {"section": sec, "title": sk["title"], "paragraphs": paragraphs,
            "tables": tables, "evidence": evidence}


def _safe_vars(sec: str) -> dict:
    """占位变量 (demo)"""
    return {k: "—" for k in _extract_vars(sec)}


def _extract_vars(sec: str) -> list[str]:
    """新11章结构: 段落模板已由 LLM 生成, 占位变量留空"""
    return []
