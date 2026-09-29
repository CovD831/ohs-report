"""给 _save_project_data 打桩, 抓出"哪一次保存把字段写没了"

方法: monkeypatch _save_project_data, 每次调用记录 keys 数与调用栈。
"""
import json
import sys
import traceback

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

import web.app as A  # noqa: E402

PID = sys.argv[1] if len(sys.argv) > 1 else "c64c1704bf"
_orig = A._save_project_data
CAUSE = []


def spy(pid, data):
    keys = sorted(data.keys()) if isinstance(data, dict) else "非dict"
    stack = [f"{f.name}:{f.lineno}" for f in traceback.extract_stack()[:-1][-4:]]
    eq = len(data.get("equipment") or []) if isinstance(data, dict) else -1
    CAUSE.append((len(keys), eq, stack))
    print(f"  _save_project_data keys={len(keys)} equipment={eq} ← {' <- '.join(stack)}",
          flush=True)
    return _orig(pid, data)


A._save_project_data = spy
# 模块内其它引用也要换 (app.py 里用模块级名字, 通常能生效)
import web.projects_db as P  # noqa: E402
if hasattr(P, "_save_project_data"):
    P._save_project_data = spy

print(f"=== 打桩后跑一次 generate-all 流程 (pid={PID}) ===", flush=True)
import subprocess  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402

jid = "spy_" + uuid.uuid4().hex[:8]
# 直接调 job 的核心: 先 reparse 再 generate, 与真实一致
r = subprocess.run([sys.executable, "-u", "-m", "web.reparse_worker", PID],
                   capture_output=True, text=True, timeout=900,
                   cwd="/Users/abaaba/Projects/ohs-report")
imported = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
print(f"  worker: equipment={len(imported.get('equipment') or [])}", flush=True)

p2 = A.get_project(PID)
data = dict(p2["data"])
data["equipment"] = imported.get("equipment") or data.get("equipment") or []
data["detections"] = imported.get("detections") or data.get("detections") or []
data["process_text"] = imported.get("process_text") or data.get("process_text") or ""
for k in ("materials", "staffing", "hazard_grid", "equipment_detail", "shifts"):
    if imported.get(k):
        data[k] = imported[k]
print(f"  合并后 equipment={len(data.get('equipment') or [])} keys={len(data)}", flush=True)
A.update_project(PID, p2["name"], data)
after = A.get_project(PID)["data"]
print(f"  update_project 后: equipment={len(after.get('equipment') or [])} keys={len(after)}", flush=True)

A._cache.pop(f"assess:{PID}", None)
print("  调 _get_assess (内含 built_tables 回填)…", flush=True)
A._get_assess(PID)
after2 = A.get_project(PID)["data"]
print(f"  _get_assess 后: equipment={len(after2.get('equipment') or [])} keys={len(after2)}", flush=True)
print()
print("=== 各次保存记录 ===")
for n, eq, st in CAUSE:
    print(f"  keys={n} equipment={eq} ← {st}")
