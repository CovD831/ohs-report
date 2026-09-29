"""验证 v59 修复: 重试逻辑能否找回原静默丢弃的页?

目标页 (原精读丢失, 已证实有数据):
  新泰: 26,27,30,40,44(截断),46
  长兴: 21(截断),23(截断),25
对照: 确认"本来就没数据"的页不会被误报为有数据 (如新泰 p54/p90)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
sys.path.insert(0, "/Users/abaaba/Projects/ohs-report/web")

import vision_extract as V  # noqa: E402

CASES = [
    ("新泰", Path("/Users/abaaba/Desktop/新泰预评价/材料包_生成报告用/05_类比检测_2025职业病危害因素检测报告.pdf"),
     [26, 27, 30, 40, 44, 46], [54, 90]),          # 有数据 / 应为空
    ("长兴", Path("/Users/abaaba/Desktop/长兴合成树脂/材料包_生成报告用/05_类比检测_职业卫生检测2025.pdf"),
     [21, 23, 25], [50]),                           # 有数据 / 应为空
]

grand_recovered = 0
for tag, pdf, have, empty in CASES:
    print("=" * 78)
    print(f"{tag}  —— 期望有数据 {have} | 期望为空 {empty}")
    print("=" * 78)
    res = V.extract_pages(pdf, have + empty, dpi=180, verbose=True)
    by_page = {}
    for it in res["items"]:
        by_page.setdefault(it["_page"], []).append(it)

    print()
    print(f"  --- 结果 ---")
    ok = True
    for p in have:
        n = len(by_page.get(p, []))
        grand_recovered += n
        flag = "✓" if n else "✗ 仍丢!"
        if not n:
            ok = False
        print(f"    p{p:3} 有数据? {flag}  {n} 条")
    for p in empty:
        n = len(by_page.get(p, []))
        flag = "✓" if not n else f"⚠ 意外有 {n} 条"
        print(f"    p{p:3} 应为空? {flag}")
    print(f"  重试页: {res['retried']}")
    print(f"  重试后仍空: {res['empty_after_retry']}")
    print(f"  始终失败: {res['unresolved']}")
    print(f"  credit: {res['credit']:.2f}  elapsed: {res['elapsed']:.0f}s")
    print()
