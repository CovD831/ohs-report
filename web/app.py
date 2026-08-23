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
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from jinja2 import Environment, FileSystemLoader

from knowledge.evidence_engine import evidence_for_section  # noqa: E402
from knowledge.report_skeleton import SECTION_SKELETON, sub_sections  # noqa: E402
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
    """从数据库读项目输入 (评估管线输入格式)"""
    p = get_project(pid)
    if not p:
        return {}
    d = p["data"]
    return {
        "name": p["name"],
        "industry": d.get("industry", ""),
        "equipment": d.get("equipment", []),
        "detections": d.get("detections", []),
        "processes": d.get("processes", []),
        "process_text": d.get("process_text", ""),
    }


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
    """评估结果缓存 (项目数据变化时失效)"""
    key = f"assess:{pid}"
    if key not in _cache:
        conn = connect()
        _cache[key] = assess_project(conn, _get_project_data(pid))
        conn.close()
    return _cache[key]


# ============ 报告工作台 (上传→一键生成→顺读报告) ============
_upload_cache: dict[str, dict] = {}  # pid -> 上传列表


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
    """一次上传多个文件 → 自动归类 → 存材料目录 → 返回分类结果"""
    from web.uploads import classify_file, save_upload, project_dir
    pid = _resolve_pid(request)
    form = await request.form()
    files = form.getlist("files")
    if not files:
        # 兼容单文件
        files = [f for f in form.getlist("file") if hasattr(f, "read") and hasattr(f, "filename")]
    result: dict[str, list[dict]] = {}
    cats = "C1,C2,C3,C4,C5,C6,C7,C8,C9,C10,C11,C12,C13,C14,C15,C16".split(",")
    for c in cats:
        result[c] = []
    result["uncat"] = []
    for f in files:
        if not hasattr(f, "read") or not hasattr(f, "filename"):
            continue
        content = await f.read(20 * 1024 * 1024 + 1)
        if len(content) > 20 * 1024 * 1024:
            continue
        name = f.filename or "unnamed"
        cat = classify_file(name, content)
        try:
            save_upload(pid, cat if cat in cats else "C2", name, content)
            result.setdefault(cat if cat in cats else "uncat", []).append({
                "name": name, "cat": cat if cat in cats else "uncat", "ok": True})
        except Exception as e:
            result.setdefault("uncat", []).append({"name": name, "cat": cat, "ok": False, "error": str(e)})
    _upload_cache[pid] = result
    counts = {k: len(v) for k, v in result.items() if v}
    return {"ok": True, "pid": pid, "counts": counts, "detail": result}


@app.post("/api/report/generate", response_class=JSONResponse)
def api_report_generate(request: Request):
    """一键生成: 导入已传材料 → 识别/判定/分级 → 后台生成全部章节"""
    from web.report_struct import count_units
    pid = _resolve_pid(request)
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    # 先导入材料 (解析设备/检测/工艺)
    imported = import_materials_from_dir(pid)
    data = dict(p["data"])
    data["equipment"] = imported["equipment"]
    data["detections"] = imported["detections"]
    data["process_text"] = imported["process_text"]
    data["industry"] = data.get("industry", "")
    # 生命周期: 重新生成必须先清空旧的 section_states,
    # 否则打勾的是上次材料的旧内容, 与当前数据不一致 (用户指出的 bug)
    data["section_states"] = {}
    _save_project_data(pid, data)
    _cache.pop(f"assess:{pid}", None)
    total = count_units(data)
    user = request.state.user if hasattr(request.state, "user") else None
    jid = _tasks.create_job((user or {}).get("username", "-"), pid, "generate_all", total)

    def _job():
        try:
            _run_generate_all(pid, jid)
            _tasks.finish_job(jid, None)
        except Exception as e:
            _tasks.finish_job(jid, traceback.format_exc())

    import threading
    threading.Thread(target=_job, daemon=True).start()
    return {"ok": True, "pid": pid, "job_id": jid, "total": total,
            "imported": {"equipment": len(imported["equipment"]),
                         "detections": len(imported["detections"])}}


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
    for sec, sk in SECTION_SKELETON.items():
        sections.append({"id": sec, "title": sk["title"],
                         "tables": len(sk["tables"]), "paragraphs": len(sk["paragraphs"]),
                         "subs": [{"id": s, "title": t} for s, t in sub_sections(sec)]})
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
    # 注入报告整体结构 (1-12章 + 二级 + 三级 + 固定四级), 供前端目录动态渲染
    from web.report_struct import CHAPTERS, SUBS, SUBS3, SUBS4, _extract_product_units
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
            node["children"].append(snode)
        tree.append(node)
    prod = _extract_product_units(p["data"] or {})
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
    for k in ("industry", "equipment", "detections", "processes", "process_text"):
        if k in payload:
            data[k] = payload[k]
    update_project(pid, payload.get("name", p["name"]), data)
    _cache.pop(f"assess:{pid}", None)  # 失效缓存
    return {"ok": True}


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
    update_project(pid, p["name"], merged)
    _cache.pop(f"assess:{pid}", None)
    return {"ok": True, "equipment": len(merged["equipment"]),
            "detections": len(dets), "materials": data.get("material_count", 0)}


def import_materials_from_dir(pid: str) -> dict:
    """从项目材料目录导入 (上传的文件 → 设备/检测/工艺文本)
    列名容错: 检测 CTWA(mg/m3)|CTWA 均可; <1 → 0.5
    """
    import csv
    from web.uploads import project_dir
    pd = project_dir(pid)
    eq, dets = [], []
    proc = ""
    seen_eq, seen_det = set(), set()
    for f in pd.rglob("*"):
        if not f.is_file():
            continue
        name = f.name
        try:
            if "设备" in name:
                with open(f, encoding="utf-8-sig", errors="ignore") as fh:
                    for row in csv.DictReader(fh):
                        n = (row.get("设备名称")
                             or row.get("名称") or "").strip()
                        if n:
                            item = str(n).split("|")[0].strip()
                            if item and item not in seen_eq:
                                eq.append(item)
                                seen_eq.add(item)
            if "检测" in name and f.suffix == ".csv":
                with open(f, encoding="utf-8-sig", errors="ignore") as fh:
                    for row in csv.DictReader(fh):
                        fac = (row.get("危害因素") or row.get("因子") or "").strip()
                        # CTWA 列名容错
                        ctwa_raw = ""
                        for k in ("CTWA(mg/m3)", "CTWA", "PC-TWA", "检测值"):
                            if k in row:
                                ctwa_raw = (row.get(k) or "").strip()
                                break
                        if fac and fac not in seen_det:
                            seen_det.add(fac)
                            if not ctwa_raw:
                                ctwa_f = None
                            elif "<" in ctwa_raw or "＜" in ctwa_raw:
                                ctwa_f = 0.5
                            else:
                                try:
                                    ctwa_f = float(ctwa_raw)
                                except ValueError:
                                    ctwa_f = None
                            dets.append({"factor": fac, "ctwa": ctwa_f})
            if "工艺" in name:
                proc = proc + "\n" + f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
    return {"equipment": eq, "detections": dets, "process_text": proc.strip(), "material_count": len(eq)}


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
               "process_text": data.get("process_text", "")}
    assess = assess_project(conn, project)
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


def _gen_one(pid: str, sec: str, sub: str | None, title: str | None = None, cache: dict | None = None) -> str:
    """统一生成单元: 按 key 层级分发 (一级 draft_section / 二级 draft_sub / 三级四级 draft_detail)"""
    from web.llm_draft import draft_section, draft_sub, draft_detail
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
    total = len(units)
    conn.execute("UPDATE task_job SET total=? WHERE id=?", (total, jid))
    conn.commit()
    conn.close()

    # 预计算 assess/info 一次, 所有单元复用 (不再每单元重复重算)
    cache = _assess_cached(pid)

    done = fail = 0

    def _finish(f):
        """处理一个完成的 future: 写result (空结果不标记generated, 打勾必须真有内容)"""
        nonlocal done, fail
        s, sb = futures.pop(f)
        try:
            text = f.result()
            # 生命周期的关键: 空/占位结果不算"已生成", 否则打勾但无内容
            t = text.strip() if text else ""
            if len(t) < 30 or t.startswith("暂不支持"):
                fail += 1
            else:
                update_section_state(pid, sb or s, "generated", text)
                done += 1
        except Exception:
            fail += 1
        _tasks.set_progress(jid, done + fail)

    from concurrent.futures import wait as cf_wait, FIRST_COMPLETED
    # 并发度可配: 默认5 (DeepSeek支持并发; 过高可能触发限流, 5是稳妥值)
    import os as _os
    conc = int(_os.environ.get("LLM_CONCURRENCY", "5"))
    with ThreadPoolExecutor(max_workers=conc, thread_name_prefix="ohsgen") as ex:
        futures: dict = {}
        for sec, sub, title in units:
            if _tasks._should_stop(jid):
                break
            fut = ex.submit(_gen_one, pid, sec, sub, title, cache)
            futures[fut] = (sec, sub)
            # 保持并发≤3: 阻塞等任一完成 (不设短超时, LLM正常需几十秒)
            while len(futures) >= 3:
                if _tasks._should_stop(jid):
                    break
                d_f = cf_wait(list(futures), return_when=FIRST_COMPLETED)
                for f in d_f.done:
                    _finish(f)
        # 收尾: 等剩余全部完成
        while futures:
            d_f = cf_wait(list(futures), return_when=FIRST_COMPLETED)
            for f in d_f.done:
                _finish(f)
    err = None if fail == 0 else f"{fail} 个单元失败 ({done} 成功)"

    # 三级单元 (数据驱动, 机械生成 → 目录/内容三级节点)
    try:
        from web.unit_gen import extract_units
        cache_result = _assess_cached(pid)
        units = extract_units(cache_result["project"], cache_result["assess"])
        for un in units:
            # key: 挂到报告级 (含类型前缀避免与一级/二级冲突)
            key = f"u::{un['type']}::{un['title']}"
            # 清洗 text 里的 markdown ** 标记(被当字面量), 名称与描述分离
            t = (un.get("text") or "").replace("**", "").replace("：", "：").strip()
            update_section_state(pid, key, "generated", t)
    except Exception:
        pass
    return err


def _conn_tasks():
    from web.tasks import _conn
    return _conn()


@app.post("/api/projects/{pid}/generate-all", response_class=JSONResponse)
def api_generate_all(pid: str, request: Request):
    """提交整篇批量生成 → 返回 job_id (后台执行)"""
    user = request.state.user if hasattr(request.state, "user") else {"username": "-"}
    if not get_project(pid):
        return JSONResponse({"error": "not found"}, status_code=404)
    from knowledge.report_skeleton import SECTION_SKELETON, SUB_SECTIONS
    total = len(SECTION_SKELETON) + sum(len(v) for v in SUB_SECTIONS.values())
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
    from knowledge.report_skeleton import sub_sections
    if not get_project(pid):
        return JSONResponse({"error": "not found"}, status_code=404)
    result = _get_assess(pid)
    subs = sub_sections(sec)
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

    """章节内容 + 依据 (报告1-9编号; 数据槽填充)"""
    if not get_project(pid):
        return JSONResponse({"error": "not found"}, status_code=404)
    result = _get_assess(pid)
    sk = SECTION_SKELETON.get(sec)
    if not sk:
        return JSONResponse({"error": "unknown section"}, status_code=404)
    from web.section_filler import fill_section
    from web.paragraph_gen import fill_section_paragraphs
    from web.advice_gen import fill_10211
    # 证据映射: 2→10.2.5.3+.4, 5→10.2.11, 其余按sec
    ev_map = {"2": ["10.2.5.3", "10.2.5.4"], "5": ["10.2.11"],
              "6": ["10.2.12"], "7": ["10.2.1"], "8": ["10.2.3"],
              "9": ["10.2.4"], "1": ["10.2.3"], "3": ["10.2.6", "10.2.7"],
              "4": ["10.2.9"]}
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
    sk = SECTION_SKELETON.get(sec, {})
    import re
    vars_set = set()
    for p in sk.get("paragraphs", []):
        vars_set |= set(re.findall(r"\{(\w+)\}", p))
    return list(vars_set)
