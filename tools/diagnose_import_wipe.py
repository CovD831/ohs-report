"""复现: generate-all 后 equipment/detections 变 0

用户路径: import-materials (写入) → generate-all (再解析+保存)
实测: 生成完成后项目里 equipment=0 / detections=0, 只剩 section_states
"""
import json
import sqlite3
import sys

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

PID = "c64c1704bf"
c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
c.row_factory = sqlite3.Row


def snapshot(tag):
    d = json.loads(c.execute("SELECT data FROM project WHERE id=?", (PID,)).fetchone()["data"])
    print(f"  [{tag}] keys={len(d)} equipment={len(d.get('equipment') or [])} "
          f"detections={len(d.get('detections') or [])} "
          f"section_states={len(d.get('section_states') or {})}")
    return d


print("=== 当前状态 ===")
snapshot("now")

print()
print("=== 跑 reparse_worker, 看它返回什么 ===")
import subprocess
r = subprocess.run([sys.executable, "-u", "-m", "web.reparse_worker", PID],
                   capture_output=True, text=True, timeout=900,
                   cwd="/Users/abaaba/Projects/ohs-report")
print(f"  returncode={r.returncode} stdout={len(r.stdout)}B stderr={r.stderr[-200:]!r}")
try:
    imp = json.loads(r.stdout)
    print(f"  worker 返回: equipment={len(imp.get('equipment') or [])} "
          f"detections={len(imp.get('detections') or [])} keys={len(imp)}")
    print(f"  equipment 类型: {type(imp.get('equipment')).__name__}")
    print(f"  detections 样例: {(imp.get('detections') or [])[:2]}")
except Exception as e:
    print(f"  ✗ 解析 worker 输出失败: {type(e).__name__}: {e}")
    print(f"  stdout 前 200: {r.stdout[:200]!r}")

print()
print("=== 模拟 job 的合并逻辑 (app.py:310-313) ===")
p2 = c.execute("SELECT data FROM project WHERE id=?", (PID,)).fetchone()
data = dict(json.loads(p2["data"]))
data["equipment"] = imp.get("equipment") or data.get("equipment") or []
data["detections"] = imp.get("detections") or data.get("detections") or []
print(f"  合并后: equipment={len(data.get('equipment') or [])} "
      f"detections={len(data.get('detections') or [])}")
