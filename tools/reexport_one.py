#!/usr/bin/env python3
"""局部验证脚本: 复刻 app.py 的导出链路导出单项目 docx (供 XML 核验)

⚠ 与 /tmp/reexport.py 的区别: 这里**照抄 app.py 的取数口径**
  - project 必须含 built_tables / section_states 等 (fill_section 内嵌表取数)
  - assess["_project_data"] = dict(project)  (llm_draft._fixed_text 需要)
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.word_export import export_docx  # noqa: E402

pid = sys.argv[1] if len(sys.argv) > 1 else "ada54603a9"
out = Path(sys.argv[2]) if len(sys.argv) > 2 else (ROOT / "data" / f"report_{pid}.docx")

conn = sqlite3.connect(ROOT / "data" / "ohs.db")
row = conn.execute("SELECT name, data FROM project WHERE id=?", (pid,)).fetchone()
conn.close()
name, data = row[0], json.loads(row[1])

project = dict(data)
project["id"] = pid
project["name"] = name
# 完整项目数据 → assess._project_data (fill_section 内嵌表取数所需)
assess = {"_project_data": dict(project)}

export_docx(project, assess, out, section_states=data.get("section_states"))
print(f"✅ 导出 {out} size={out.stat().st_size}")
