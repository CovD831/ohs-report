"""验证表题重号修复: 重新导出并检查表题编号唯一性"""
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

import docx  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.text.paragraph import Paragraph  # noqa: E402

import web.app as A  # noqa: E402
from knowledge.oel import connect as oc  # noqa: E402
from knowledge.project_assess import assess_project  # noqa: E402
from web.word_export import export_docx  # noqa: E402

PID = "c8c7ff0a4d"
p = A.get_project(PID)
d = p["data"]
proj = {"id": PID, "data": d, "name": p["name"],
        "built_tables": d.get("built_tables", {}),
        "hazard_grid": d.get("hazard_grid", []),
        "emergency_supplies": d.get("emergency_supplies", []),
        "location": d.get("location", ""), "company": d.get("company", "")}
a = assess_project(oc(), proj)
a["_project_data"] = dict(proj)
out = Path("/tmp/report_fixed.docx")
export_docx(proj, a, out, section_states=d.get("section_states"))
print(f"导出: {out.stat().st_size//1024} KB")

doc = docx.Document(str(out))
caps = []
for ch in doc.element.body.iterchildren():
    if ch.tag == qn("w:p"):
        t = Paragraph(ch, doc).text.strip()
        if re.match(r"^表\s*[\d.]+[-—]", t):
            caps.append(t)
nums = [re.match(r"^表\s*([\d.]+[-—]\d+)", c).group(1) for c in caps]
dup = [k for k, v in Counter(nums).items() if v > 1]
print(f"表题 {len(caps)} 张 | 重号: {dup if dup else '无 ✓'}")
print()
print("=== 5.3 相关表题 ===")
for c in caps:
    if re.match(r"^表\s*5\.3", c):
        print("  ", c[:60])
print()
print("=== 是否还有 '表格由系统' 残留 ===")
allt = "\n".join(Paragraph(ch, doc).text for ch in doc.element.body.iterchildren()
                 if ch.tag == qn("w:p"))
print("  ", "有残留 ✗" if "表格由系统" in allt else "无 ✓")
