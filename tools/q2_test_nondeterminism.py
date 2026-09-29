"""量化"同一页重跑结果不同"的非确定性 (最关键的一类)

首探: p30/44/45/46 有数据 (14/18/13/12 条)
量化: p30/40/46 + p26/27 有数据, 但 **p45 变空** (raw=29 = '{"table_type":"C","items":[]}')
→ 同一页, 同样的 prompt, 同样的模型 → 结果不同 = **非确定性**

这解释了为什么用户会看到"数据忽有忽无"。
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
sys.path.insert(0, "/Users/abaaba/Projects/ohs-report/web")

import vision_extract as V  # noqa: E402

PDF = Path("/Users/abaaba/Desktop/新泰预评价/材料包_生成报告用/05_类比检测_2025职业病危害因素检测报告.pdf")
# p45 首探 13 条(照度), 二跑 0 条 → 重点复现; p30/p46 稳定有数据做对照
PAGES = [45, 30, 46]
N = 3

print("=" * 80)
print(f"非确定性测试: 每页连续跑 {N} 次, 看 items 条数是否稳定")
print(f"temperature 已设 0 (但实测仍有波动 → 服务端/推理非确定)")
print("=" * 80)

summary = {}
for pno in PAGES:
    print(f"\n### p{pno}")
    cnt = []
    for k in range(N):
        png = V._render(PDF, pno - 1, 180)
        try:
            raw, c = V._ask(png, V._EXTRACT_ASK, max_tokens=2000)
        except Exception as e:
            print(f"    run{k+1}: ERR {type(e).__name__}")
            cnt.append(-1)
            continue
        d = V._parse_json(raw)
        tt = str(d.get("table_type") or "").strip().upper()[:1]
        items = [it for it in (d.get("items") or []) if isinstance(it, dict)]
        nf = sum(1 for it in items if str(it.get("factor") or "").strip())
        cnt.append(nf)
        trunc = raw.count("{") > raw.count("}")
        print(f"    run{k+1}: 表型={tt or '?'} items={len(items):3} 有factor={nf:3} "
              f"raw={len(raw):5} {'截断?' if trunc else ''}")
    summary[pno] = cnt

print()
print("=" * 80)
print("结论")
print("=" * 80)
for pno, cnt in summary.items():
    uniq = sorted(set(cnt))
    stable = len(uniq) == 1
    print(f"  p{pno}: {cnt}  波动={not stable}  值域={uniq}")
