"""重跑新泰导入 (缓存已热) — 验证竞态修复后数据是否保住"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

PID = "06ea028767"
c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
c.row_factory = sqlite3.Row


def snap(tag):
    d = json.loads(c.execute("SELECT data FROM project WHERE id=?", (PID,)).fetchone()["data"])
    dets = d.get("detections") or []
    print(f"  [{tag}] keys={len(d)} equipment={len(d.get('equipment') or [])} "
          f"detections={len(dets)} materials={len(d.get('materials') or [])}")
    return d


print("=== 导入前 ===")
snap("before")

# 直接调导入逻辑 (等价 /import-materials 内部)
from web.app import import_materials_from_dir  # noqa: E402

t0 = time.time()
data = import_materials_from_dir(PID)
print(f"  import_materials_from_dir: {time.time()-t0:.1f}s "
      f"equipment={len(data.get('equipment') or [])} "
      f"detections={len(data.get('detections') or [])}")

# 模拟修复后的合并 (重新读最新 + 覆盖解析字段)
from web.projects_db import get_project, update_project  # noqa: E402

p = get_project(PID)                      # ← 修复点: 用最新读
dets = [d for d in data["detections"]
        if d.get("ctwa") is not None or d.get("sio2") or d.get("results")]
merged = dict(p["data"])
merged["equipment"] = data["equipment"]
merged["detections"] = dets
merged["process_text"] = data["process_text"]
for k in ("materials", "staffing", "shifts", "ppe", "emergency", "buildings",
          "facilities", "products", "public_works", "health_check", "management",
          "hazard_grid", "emergency_supplies", "equipment_detail", "investment",
          "ohy_investment", "area", "capacity", "nature", "location", "company",
          "founded", "registered_capital", "registered_capital_all", "legal_rep",
          "investor", "protection"):
    v = data.get(k)
    if v not in (None, "", [], {}):
        merged[k] = v
update_project(PID, p["name"], merged)
print()
print("=== 导入后 ===")
d = snap("after")
multi = [x for x in (d.get("detections") or []) if "、" in str(x.get("factor") or "")]
print(f"  factor 含顿号(多因素挤一行): {len(multi)}/{len(d.get('detections') or [])}")
