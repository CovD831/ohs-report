"""重建一个项目的表骨架 (走 v41-v43 新链路) 并核对 coverage 三态

用途: 老项目 (built_tables 由旧代码建) 重跑一次, 让面板反映真实状态。
在本地库副本上做, 不污染真库。
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

SRC = Path("/Users/abaaba/Projects/ohs-report/data/ohs.db")
TMP = Path(tempfile.mkdtemp()) / "ohs.db"
shutil.copy(SRC, TMP)
for suf in ("-wal", "-shm"):
    p = Path(str(SRC) + suf)
    if p.exists():
        shutil.copy(p, Path(str(TMP) + suf))

import web.projects_db as pdb  # noqa: E402
pdb.DB = TMP

from web.derived_fields import derive_fields  # noqa: E402
from web.table_skeleton import build_skeletons, skeleton_summary  # noqa: E402
from web.coverage import coverage_report  # noqa: E402
from web.external_tables import fill_one  # noqa: E402

c = sqlite3.connect(str(TMP))
c.row_factory = sqlite3.Row
pid = sys.argv[1] if len(sys.argv) > 1 else "c8c7ff0a4d"
data = json.loads(c.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()["data"])

# 1. 派生字段
data.update({k: v for k, v in derive_fields(data).items() if v})
print("派生字段:", {k: data[k] for k in ("radiation", "work_system")})

# 2. 建骨架
sk = build_skeletons(data)
print("骨架:", json.dumps(skeleton_summary(sk), ensure_ascii=False))

# 3. 同步补外部表 (等价于后台异步跑完)
for name in list(sk):
    if sk[name].get("status") == "filling":
        t = fill_one(name, data)
        if t:
            sk[name] = t
print("补外部表后:", json.dumps(skeleton_summary(sk), ensure_ascii=False))

data["built_tables"] = sk
cov = coverage_report(data)
print()
print("coverage summary:", json.dumps(cov["summary"], ensure_ascii=False))
print("自洽:", cov["summary"]["ready"] + cov["summary"]["filling"]
      + cov["summary"]["await_assess"] + cov["summary"]["pending"], "== 36?")

print()
print("非 ready 明细:")
for t in cov["tables"]:
    if t["status"] != "ready":
        print(f"   [{t['status']:12}] {t['name']:18} §{t['section']}")

shutil.rmtree(TMP.parent, ignore_errors=True)
