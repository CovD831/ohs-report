#!/usr/bin/env python3
"""端到端导出 (复刻 app.py 真实导出路径, 供 CLI 回归)

app.py 的 /api/projects/{pid}/export 才是**真实导出路径**;
web/word_export.py 的 __main__ 只传精简 dict → 大量表退化 (非真实)。
本脚本按 app.py:1500-1545 逐字段组装, 保证与线上同源。

用法: .venv/bin/python tools/export_e2e.py <pid> [out.docx]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

pid = sys.argv[1] if len(sys.argv) > 1 else "ada54603a9"
out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / f"report_{pid}.docx"

from knowledge.oel import connect  # noqa: E402
from web.app import _get_project_data  # noqa: E402
from knowledge.project_assess import assess_project  # noqa: E402


def build_project(pid: str) -> dict:
    data = _get_project_data(pid)
    return {"id": pid,
            "name": data.get("name", ""),
            "industry": data.get("industry", ""),
            "equipment": data.get("equipment", []),
            "detections": data.get("detections", []),
            "process_text": data.get("process_text", ""),
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
            "buildings": data.get("buildings", []),
            "products": data.get("products", []),
            "materials": data.get("materials", []),
            "location": data.get("location", ""),
            "company": data.get("company", ""),
            "org_name": data.get("org_name", ""),
            "report_no": data.get("report_no", "")}


def main():
    from web.word_export import export_docx
    data = _get_project_data(pid)
    project = build_project(pid)
    conn = connect()
    assess = assess_project(conn, project)
    assess["_project_data"] = dict(project)
    conn.close()
    export_docx(project, assess, out, section_states=data.get("section_states"))
    print(f"✅ 导出: {out} ({out.stat().st_size} 字节)")


if __name__ == "__main__":
    main()
