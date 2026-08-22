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
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

from knowledge.evidence_engine import evidence_for_section  # noqa: E402
from knowledge.report_skeleton import SECTION_SKELETON  # noqa: E402
from knowledge.project_assess import assess_project  # noqa: E402
from knowledge.oel import connect  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ohs.db"
TEMPLATES = Path(__file__).resolve().parent.parent / "web" / "templates"

app = FastAPI(title="职业病危害预评价报告工作台")
env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)

# 演示项目 (长兴, 从报告提取) — 后续移到数据库
DEMO_PROJECT = {
    "id": "demo-cx",
    "name": "长兴特殊材料年产27080吨高性能光固化涂料材料项目",
    "industry": "261",
    "status": "生成中",
    "equipment": ["酯化釜", "纯化槽", "洗涤塔", "溶剂回收槽", "中和真空槽",
                  "酯化第一冷凝器", "真空除沫器", "洗釜泵", "油相溶剂泵"],
    "detections": [{"factor": "甲苯", "ctwa": 30, "cste": 95},
                   {"factor": "环己烷", "ctwa": 0.3, "peak": 4.0}],
}

_cache = {}


def _get_assess() -> dict:
    """评估结果缓存 (项目数据变化时失效)"""
    if "assess" not in _cache:
        conn = connect()
        _cache["assess"] = assess_project(conn, DEMO_PROJECT)
        conn.close()
    return _cache["assess"]


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    projects = [DEMO_PROJECT]
    return env.get_template("index.html").render(projects=projects)


@app.get("/projects/{pid}", response_class=HTMLResponse)
def project_page(request: Request, pid: str):
    result = _get_assess()
    sections = []
    for sec, sk in SECTION_SKELETON.items():
        sections.append({"id": sec, "title": sk["title"],
                         "tables": len(sk["tables"]), "paragraphs": len(sk["paragraphs"])})
    return env.get_template("project.html").render(
        project=DEMO_PROJECT, sections=sections, pid=pid)


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
    """章节内容 + 依据 (数据槽填充演示)"""
    result = _get_assess()
    sk = SECTION_SKELETON.get(sec)
    if not sk:
        return JSONResponse({"error": "unknown section"}, status_code=404)
    # 段落填充 (演示: 仅10.2.5 填充, 其余用占位)
    from knowledge.report_template import build_1025_section
    if sec == "10.2.5":
        built = build_1025_section(result)
        paragraphs = built["paragraphs"]
        tables = built["tables"]
        # 10.2.5 由 .3(判定)+.4(分级) 提供依据
        ev_secs = ["10.2.5.3", "10.2.5.4"]
    else:
        paragraphs = [p.format_map(_safe_vars(sec)) for p in sk["paragraphs"]]
        tables = []
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
