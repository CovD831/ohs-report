"""复核: 正文里"非检出值"的数, 是否都能对上标准库限值

若全部对上 → 正文数字来源只有两类: 检出值(检测报告) + 限值(标准库), 无一处编造。
"""
import json
import re
import sqlite3
import sys

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

PID = "c8c7ff0a4d"
c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
c.row_factory = sqlite3.Row
d = json.loads(c.execute("SELECT data FROM project WHERE id=?", (PID,)).fetchone()["data"])
ss = d.get("section_states") or {}
prose = " ".join((v or {}).get("text") or "" for v in ss.values())

# 源1: 检测值
src = set()
for x in (d.get("detections") or []):
    for k in ("ctwa", "cste", "cme"):
        v = x.get(k)
        if v:
            n = re.sub(r"[^\d.]", "", str(v))
            if n:
                src.add(n)
    for r in (x.get("results") or []):
        n = re.sub(r"[^\d.]", "", str(r))
        if n:
            src.add(n)

# 源2: 标准库限值 (全库, 不只本项目表)
limvals = set()
for r in c.execute("SELECT value FROM oel_limit"):
    n = re.sub(r"[^\d.]", "", str(r["value"]))
    if n:
        limvals.add(n)

pv = set(re.findall(r"(\d+(?:\.\d+)?)\s*(?:mg/m|毫克每立方)", prose))
unknown = sorted(v for v in pv if v not in src and v not in limvals
                 and v not in ("3", "20", "2019", "2007", "2010"))
print(f"正文浓度值 {len(pv)} 种")
print(f"  能对上检测值 {len([v for v in pv if v in src])}")
print(f"  能对上标准库限值 {len([v for v in pv if v in limvals])}")
print(f"  都无法对上: {unknown if unknown else '无 ✓'}")
print()
# 上下文看一眼
for v in unknown[:6]:
    m = re.search(re.escape(v) + r"\s*毫克每立方", prose)
    if m:
        print(f"  [{v}] ...{prose[max(0,m.start()-80):m.start()+30]}...")
