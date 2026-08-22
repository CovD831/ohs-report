"""批量生成所有章节 (LLM草稿→状态generated) — 后台任务"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".")
pid = sys.argv[1] if len(sys.argv) > 1 else "demo-cx"
secs = ["10.2.1", "10.2.2", "10.2.3", "10.2.4", "10.2.5", "10.2.6",
        "10.2.7", "10.2.8", "10.2.9", "10.2.10", "10.2.11", "10.2.12"]

for sec in secs:
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:8000/api/projects/{pid}/sections/{sec}/generate",
                method="POST")
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read())
            print(f"  {sec}: {'✓' if d.get('ok') else '✗'} ({len(d.get('text', ''))}字)", flush=True)
            break
        except Exception as e:
            print(f"  {sec}: 重试{attempt+1} {str(e)[:40]}", flush=True)
            time.sleep(5)
    else:
        print(f"  {sec}: FAILED", flush=True)

print("ALL_DONE", flush=True)
