"""浦发整篇批量生成 — 一级9章 + 二级46小节"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from knowledge.report_skeleton import SECTION_SKELETON, SUB_SECTIONS  # noqa: E402

pid = sys.argv[1] if len(sys.argv) > 1 else "e6e3853629"

tasks = list(SECTION_SKELETON.keys())
for sec, subs in SUB_SECTIONS.items():
    for sub, _ in subs:
        tasks.append((sec, sub))


def gen(url):
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


ok = fail = 0
for t in tasks:
    if isinstance(t, tuple):
        sec, sub = t
        url = f"http://127.0.0.1:8000/api/projects/{pid}/sections/{sec}/{sub}/generate"
        label = sub
    else:
        url = f"http://127.0.0.1:8000/api/projects/{pid}/sections/{t}/generate"
        label = f"第{t}章"
    for attempt in range(3):
        try:
            d = gen(url)
            if d.get("ok"):
                print(f"  {label}: {len(d.get('text',''))}字 ✓", flush=True)
                ok += 1
                break
        except Exception as e:
            print(f"  {label}: retry {str(e)[:30]}", flush=True)
            time.sleep(4)
    else:
        print(f"  {label}: FAILED", flush=True)
        fail += 1

print(f"PF_FULL_DONE ok={ok} fail={fail}", flush=True)
