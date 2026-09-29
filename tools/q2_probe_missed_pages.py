"""决定性测试: 那些"漏读"页, 到底是**代码漏了**还是**本来就没数据**?

背景: 新泰缓存标称 read_pages=63, 但只有 11 页产出记录 (漏 52 页)。
       长兴 104 → 32 页产出 (漏 72 页)。

假设验证:
  H1(代码bug): 漏读页其实有检测行 → 数据丢失
  H2(正常):    漏读页是方法/说明页, 模型正确返回 []

做法: 取几个漏读页, 用**完全相同的 prompt** 重新问一遍, 看原始返回。
      注意: 这是**只读诊断**, 不写任何缓存。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
sys.path.insert(0, "/Users/abaaba/Projects/ohs-report/web")

import vision_extract as V  # noqa: E402

PDF = Path("/Users/abaaba/Desktop/新泰预评价/材料包_生成报告用/05_类比检测_2025职业病危害因素检测报告.pdf")
# 选取: 漏读页(索引说有因素) + 一个疑似方法页
PROBE = [26, 30, 44, 45, 46, 54, 90, 93]

print("=" * 80)
print("重新精读漏读页 — 原始返回 (不写缓存)")
print("=" * 80)
tot = 0.0
for pno in PROBE:
    png = V._render(PDF, pno - 1, 180)
    try:
        txt, c = V._ask(png, V._EXTRACT_ASK, max_tokens=2000)
        tot += c
    except Exception as e:
        print(f"\n### p{pno}: ERR {type(e).__name__}: {e}")
        continue
    d = V._parse_json(txt)
    tt = str(d.get("table_type") or "").strip().upper()[:1]
    items = d.get("items") or []
    print(f"\n### p{pno}  表型={tt or '?'}  items={len(items)}  (credit {c:.3f})")
    if items:
        for it in items[:3]:
            print(f"    {str(it)[:230]}")
        if len(items) > 3:
            print(f"    ... 共 {len(items)} 条")
    else:
        print(f"    raw前200: {txt[:200]!r}")
    time.sleep(0.3)

print()
print(f"总 credit: {tot:.3f}")
