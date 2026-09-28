"""诊断: 导出 walk 到底访问了哪些小节 → 解释哪些表没出现

给 _tables_for_sub 打桩, 记录每次调用的 (sec, sn) 与返回表名,
对照 _SUB_TABLE_MAP 的挂载点清单 → 找出"挂了但从没被访问"的小节。
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

import web.word_export as we
from knowledge.oel import connect as oc

PID = "c8c7ff0a4d"

_orig = we._tables_for_sub
CALLS = []


def _spy(conn, sec, sn, assess):
    r = _orig(conn, sec, sn, assess)
    CALLS.append((sec, sn, [t["name"] for t in (r or [])]))
    return r


we._tables_for_sub = _spy

c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
c.row_factory = sqlite3.Row
d = json.loads(c.execute("SELECT data FROM project WHERE id=?", (PID,)).fetchone()["data"])
from knowledge.project_assess import assess_project
a = assess_project(oc(), d)
a["_project_data"] = d

out = Path("/tmp/export_spy.docx")
we.export_docx(d, a, out)

print(f"=== _tables_for_sub 被调用 {len(CALLS)} 次 ===")
visited = {}
for sec, sn, names in CALLS:
    visited[sn] = names

mounts = {}
for sub, m in we._SUB_TABLE_MAP.items():
    if m:
        mounts[sub] = m[1]

print()
print("=== 挂载点 vs 是否被访问 ===")
for sub, wanted in sorted(mounts.items()):
    hit = sub in visited
    got = visited.get(sub, [])
    mark = "✓" if (hit and got) else ("△访问了但无表" if hit else "✗ 从未访问")
    print(f"  {sub:12} {mark:14} 期望={wanted} 实得={got}")
