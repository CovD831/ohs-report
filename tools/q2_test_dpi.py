"""假设验证: 非确定性空返回是否与**渲染 DPI** 有关?

实测: 同页同 prompt 同模型 (temperature=0)
  p30: [14, 0, 0]   p46: [12, 0, 0]   p45: [13, 13, 13]
若高 DPI 能稳定出数据 → 修复方案 = 提高精读 DPI (优于盲目重试)
"""
import sys
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
sys.path.insert(0, "/Users/abaaba/Projects/ohs-report/web")

import vision_extract as V  # noqa: E402

PDF = Path("/Users/abaaba/Desktop/新泰预评价/材料包_生成报告用/05_类比检测_2025职业病危害因素检测报告.pdf")
PAGES = [30, 46, 45]
DPIS = [180, 240, 300]
RUNS = 2

print("DPI × 页 × 重跑 → 有 factor 条数")
print("=" * 74)
print(f"{'dpi':>4} | " + " | ".join(f"p{p:>3}(run1,run2)" for p in PAGES))
print("-" * 74)
for dpi in DPIS:
    row = []
    for pno in PAGES:
        cnts = []
        for k in range(RUNS):
            png = V._render(PDF, pno - 1, dpi)
            try:
                raw, c = V._ask(png, V._EXTRACT_ASK, max_tokens=2000)
                d = V._parse_json(raw)
                items = [x for x in (d.get("items") or []) if isinstance(x, dict)]
                nf = sum(1 for x in items if str(x.get("factor") or "").strip())
                cnts.append(nf)
            except Exception as e:
                cnts.append(-1)
        row.append(f"{cnts[0]:>3},{cnts[1]:>3}" + "   ")
    print(f"{dpi:>4} | " + " | ".join(row))

print()
print("PNG 体积对照 (渲染成本)")
for pno in PAGES:
    sizes = {d: len(V._render(PDF, pno - 1, d)) // 1024 for d in DPIS}
    print(f"  p{pno}: " + "  ".join(f"{d}dpi={s}KB" for d, s in sizes.items()))
