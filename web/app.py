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
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from knowledge.evidence_engine import evidence_for_section  # noqa: E402
from knowledge.report_skeleton import SECTION_SKELETON  # noqa: E402
from knowledge.project_assess import assess_project  # noqa: E402
from knowledge.oel import connect  # noqa: E402
from web.projects_db import list_projects, get_project, create_project, update_project, seed_demo

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ohs.db"
TEMPLATES = Path(__file__).resolve().parent.parent / "web" / "templates"

app = FastAPI(title="职业病危害预评价报告工作台")
env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
# 静态文件 (导出的 docx)
app.mount("/data", StaticFiles(directory=str(ROOT / "data")), name="data")

# 首次启动植入演示项目
seed_demo()

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


def _get_assess(pid: str = "demo-cx") -> dict:
    """评估结果缓存 (项目数据变化时失效)"""
    key = f"assess:{pid}"
    if key not in _cache:
        conn = connect()
        _cache[key] = assess_project(conn, _get_project_data(pid))
        conn.close()
    return _cache[key]


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    projects = list_projects()
    return env.get_template("index.html").render(projects=projects)


@app.get("/projects/{pid}", response_class=HTMLResponse)
def project_page(request: Request, pid: str):
    p = get_project(pid)
    if not p:
        return HTMLResponse("项目不存在", status_code=404)
    sections = []
    for sec, sk in SECTION_SKELETON.items():
        sections.append({"id": sec, "title": sk["title"],
                         "tables": len(sk["tables"]), "paragraphs": len(sk["paragraphs"])})
    return env.get_template("project.html").render(
        project=p, sections=sections, pid=pid)


@app.post("/api/projects", response_class=JSONResponse)
def api_create_project(payload: dict):
    """新建项目"""
    name = payload.get("name", "未命名项目")
    industry = payload.get("industry", "")
    p = create_project(name, industry)
    if not p:
        return JSONResponse({"error": "create failed"}, status_code=500)
    return {"id": p["id"], "name": p["name"]}


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
    """从材料文件导入项目数据 (设备/检测/工艺) → 保存+缓存失效"""
    from web.materials_import import import_materials
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = import_materials()
    # 只保留有CTWA的化学检测
    dets = [d for d in data["detections"] if d.get("ctwa") is not None]
    merged = dict(p["data"])
    merged["equipment"] = data["equipment"][:200]
    merged["detections"] = dets
    merged["process_text"] = data["process_text"]
    update_project(pid, p["name"], merged)
    _cache.pop(f"assess:{pid}", None)
    return {"ok": True, "equipment": len(merged["equipment"]),
            "detections": len(dets), "materials": data["material_count"]}


@app.get("/api/projects/{pid}/export", response_class=JSONResponse)
def api_export(pid: str):
    """导出 Word 报告 (附录D格式)"""
    from web.word_export import export_docx
    from web.projects_db import get_project as gp
    from web.materials_import import import_materials
    p = gp(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = p["data"]
    conn = connect()
    project = {"name": p["name"], "industry": data.get("industry", ""),
               "equipment": data.get("equipment", []),
               "detections": data.get("detections", []),
               "process_text": data.get("process_text", "")}
    assess = assess_project(conn, project)
    conn.close()
    out = ROOT / "data" / f"report_{pid}.docx"
    export_docx(project, assess, out)
    return {"ok": True, "path": f"/data/report_{pid}.docx", "size": out.stat().st_size}


@app.get("/api/projects/{pid}/data", response_class=JSONResponse)
def api_project_data(pid: str):
    """项目输入数据 (编辑表单用)"""
    p = get_project(pid)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    return p["data"]


@app.get("/api/projects/{pid}/overview", response_class=JSONResponse)
def project_overview(pid: str):
    """项目总览 (统计卡片数据)"""
    result = _get_assess()
    hazards = result.get("hazards", [])
    judgements = result.get("judgements", [])
    passed = sum(1 for j in judgements if j.get("pass") is True)
    failed = sum(1 for j in judgements if j.get("pass") is False)
    risk = (result.get("industry_risk") or {})
    return {
        "hazard_count": len(hazards),
        "judgement_count": len(judgements),
        "passed": passed, "failed": failed,
        "pass_rate": round(passed / len(judgements) * 100) if judgements else 0,
        "risk_level": risk.get("level", "—"),
        "risk_name": risk.get("name", ""),
        "grade_count": len(result.get("grades", [])),
        "diseases": sum(1 for h in hazards if h.get("diseases")),
    }


@app.get("/api/projects/{pid}/sections/{sec}", response_class=JSONResponse)
def section_content(pid: str, sec: str):
    """章节内容 + 依据 (数据槽填充: 引擎/规则表 → 表格)"""
    result = _get_assess(pid)
    sk = SECTION_SKELETON.get(sec)
    if not sk:
        return JSONResponse({"error": "unknown section"}, status_code=404)
    from web.section_filler import fill_section
    from web.paragraph_gen import fill_section_paragraphs
    from web.advice_gen import fill_10211
    from knowledge.report_template import build_1025_section
    if sec == "10.2.5":
        built = build_1025_section(result)
        paragraphs = built["paragraphs"]
        tables = built["tables"]
        ev_secs = ["10.2.5.3", "10.2.5.4"]
    elif sec == "10.2.11":
        built = fill_10211(connect(), result)
        paragraphs = built["paragraphs"]
        tables = built["tables"]
        ev_secs = ["10.2.11"]
    else:
        paragraphs = fill_section_paragraphs(connect(), sec, result, sk["paragraphs"])
        tables = fill_section(connect(), sec, result)
        ev_secs = [sec]
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
