"""验证限值表逐格 provenance"""
import json
import sqlite3
import sys

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

from knowledge.oel import connect  # noqa: E402
from knowledge.project_assess import assess_project  # noqa: E402
from web.section_filler import fill_section  # noqa: E402

c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
c.row_factory = sqlite3.Row
d = json.loads(c.execute("SELECT data FROM project WHERE id=?",
                         ("c8c7ff0a4d",)).fetchone()["data"])
# ⚠ assess_project(conn, project_data) 收的是**项目 data 本身**,
#   不是 {"id":.., "data":..} 包装体 (早期测试包装错了 → hazards/judgements 全 0)
a = assess_project(connect(), d)
a["_project_data"] = dict(d)
print("hazards:", len(a.get("hazards") or []),
      "| judgements:", len(a.get("judgements") or []),
      "| detections:", len((d.get("detections") or [])))

found = False
for sec in ("5", "3"):
    try:
        ts = fill_section(connect(), sec, a)
    except Exception as e:
        print(f"fill_section({sec}) ERR {type(e).__name__}: {e}")
        continue
    print(f"--- fill_section('{sec}') 返回 {len(ts)} 表: {[t.get('name') for t in ts]}")
    for t in ts:
        if t.get("name") == "接触限值表":
            found = True
            pr = t.get("prov") or {}
            print(f"  行数 {len(t['rows'])} | prov 格数 {len(pr)}")
            for k in list(pr)[:8]:
                print(f"    {k}: {json.dumps(pr[k], ensure_ascii=False)}")
            print("  首行:", t["rows"][0])
if not found:
    print("未找到接触限值表")
