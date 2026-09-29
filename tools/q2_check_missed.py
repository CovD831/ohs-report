"""关键核查: 那 7 个"未测"因素, 真的没测, 还是我的提取漏了?

真实报告段落464 明确说:
  "确定类比企业检测的主要职业病危害因素为: 其他粉尘、石灰石粉尘、
   氟及其化合物、氢氟酸、氯化氢、磷酸、氧化钙、氢氧化钾、噪声、
   工频电场等进行了检测"
→ 石灰石粉尘/氧化钙/工频电场 **都在检测清单里**!
故"未测"很可能是**我的视觉提取漏行**, 不是事实。

核查:
  ① 这7个因素在 vision_cache 的全部记录里出现吗 (不分表型)
  ② 它们在原始 PDF 里出现吗 (文本层可以搜到的话)
  ③ 表型A 的字段里有没有'电场'/'照度'/'氧化钙'相关
"""
import json
import re
from pathlib import Path

CACHE = Path("/Users/abaaba/Projects/ohs-report/data/vision_cache/a66517be268181e6.json")
vd = json.loads(CACHE.read_text())

TARGETS = ["工频电场", "氧化钙", "氯", "氯化氢及盐酸", "照度", "石灰石粉尘", "呼尘"]

print("=" * 78)
print("① vision_cache 全字段扫描 (不分表型)")
print("=" * 78)
dets = vd.get("detections") or []
for t in TARGETS:
    hits = []
    for i, x in enumerate(dets):
        blob = json.dumps(x, ensure_ascii=False)
        if t in blob:
            hits.append((i, x))
    print(f"\n### {t}: {len(hits)} 条记录")
    for i, x in hits[:3]:
        keys = {k: v for k, v in x.items() if v not in (None, "", [], {})}
        print(f"  [{i}] {json.dumps(keys, ensure_ascii=False)[:320]}")

print()
print("=" * 78)
print("② 缓存顶层结构 / 其他记录类型")
print("=" * 78)
print("顶层键:", list(vd.keys()))
for k, v in vd.items():
    if isinstance(v, list):
        print(f"  {k}: {len(v)} 条")
    elif isinstance(v, dict):
        print(f"  {k}: dict keys={list(v.keys())[:8]}")
    else:
        print(f"  {k}: {str(v)[:80]}")

print()
print("=" * 78)
print("③ 表型A 记录里 factor 的全量清单 (含'电场'/'照度'吗)")
print("=" * 78)
from web.factor_clean import clean_factor_name, split_factors  # noqa: E402

a_facs = set()
for x in dets:
    fs = split_factors(str(x.get("factor") or ""))
    is_b = (len(fs) > 1) or (x.get("pass_ratio") and not x.get("ctwa") and not x.get("results"))
    if not is_b:
        for f in fs:
            if f:
                a_facs.add(clean_factor_name(f)[0])
print(f"表型A 因素 ({len(a_facs)}):", " | ".join(sorted(a_facs)))
