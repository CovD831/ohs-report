"""批量生成所有二级小节 (46个) — 后台任务"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from knowledge.report_skeleton import SUB_SECTIONS  # noqa: E402

pid = sys.argv[1] if len(sys.argv) > 1 else "demo-cx"

subs = []
for sec, lines in SUB_SECTIONS.items():
    for sub, _ in lines:
        subs.append((sec, sub))

print(f"共 {len(subs)} 个小节", flush=True)
for sec, sub in subs:
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:8000/api/projects/{pid}/sections/{sec}/{sub}/generate",
                method="POST")
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read())
            print(f"  {sub}: {'✓' if d.get('ok') else '✗'} ({len(d.get('text', ''))}字)", flush=True)
            break
        except Exception as e:
            print(f"  {sub}: 重试{attempt+1} {str(e)[:40]}", flush=True)
            time.sleep(5)
    else:
        print(f"  {sub}: FAILED", flush=True)

print("SUBS_DONE", flush=True)
