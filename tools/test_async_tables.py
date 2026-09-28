"""端到端验证: 表骨架 + 异步外部表填充 (含真实落库 patch)

流程模拟导入:
  1. build_skeletons → 36 张, 气象表 = filling 占位
  2. fill_external_async → 后台线程联网/读缓存 → patch 回库
  3. 轮询库, 确认气象表变 filled 且 rows 落地
"""
import json
import sqlite3
import sys
import tempfile
import time
import shutil
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

# 用临时库副本, 不污染真库
SRC = Path("/Users/abaaba/Projects/ohs-report/data/ohs.db")
TMP = Path(tempfile.mkdtemp()) / "ohs.db"
shutil.copy(SRC, TMP)
for suf in ("-wal", "-shm"):
    p = Path(str(SRC) + suf)
    if p.exists():
        shutil.copy(p, Path(str(TMP) + suf))

from web import projects_db  # noqa: E402
projects_db.DB = TMP  # 指向临时库

import sqlite3 as _s  # noqa: E402
c = _s.connect(str(TMP))
c.row_factory = _s.Row
row = c.execute("SELECT id, name, data FROM project WHERE id=?", ("c8c7ff0a4d",)).fetchone()
pid, name = row["id"], row["name"]
data = json.loads(row["data"])

from web.derived_fields import derive_fields  # noqa: E402
from web.table_skeleton import build_skeletons, skeleton_summary  # noqa: E402

data.update({k: v for k, v in derive_fields(data).items() if v})

# --- 1. 建骨架 (应含 filling 占位) ---
t0 = time.time()
sk = build_skeletons(data)
t_build = time.time() - t0
print(f"[1] build_skeletons: {t_build:.3f}s | {json.dumps(skeleton_summary(sk), ensure_ascii=False)}")
print(f"    气象因素表 status = {sk['气象因素表'].get('status')!r} rows={len(sk['气象因素表'].get('rows') or [])}")

# 落库 (模拟导入已保存)
data["built_tables"] = sk
c.execute("UPDATE project SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), pid))
c.commit()
c.close()

# --- 2. 异步填充 ---
from web.external_tables import fill_external_async, fill_one  # noqa: E402

done = {}
t0 = time.time()
fill_external_async(pid, data, on_done=lambda p, r: done.update(r))
t_dispatch = time.time() - t0
print(f"[2] fill_external_async 派发返回: {t_dispatch:.4f}s (应立即返回, 不阻塞)")
print(f"    后台 inflight, 等待落库…")

# --- 3. 轮询确认 ---
ok = False
for i in range(40):
    time.sleep(0.25)
    cc = _s.connect(str(TMP))
    cc.row_factory = _s.Row
    d2 = json.loads(cc.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()["data"])
    cc.close()
    w = (d2.get("built_tables") or {}).get("气象因素表") or {}
    if w.get("status") == "filled" and w.get("rows"):
        ok = True
        print(f"[3] {0.25*(i+1):.2f}s 后气象表已落库: status=filled rows={len(w['rows'])}")
        for r in w["rows"][:3]:
            print("      ", r)
        break
if not ok:
    print("[3] ✗ 超时未落库")

# 关掉临时库
for f in (TMP, Path(str(TMP) + "-wal"), Path(str(TMP) + "-shm")):
    try:
        f.unlink()
    except OSError:
        pass
shutil.rmtree(TMP.parent, ignore_errors=True)
print()
print("结论:", "✓ 异步填充闭环通过" if ok else "✗ 失败")
