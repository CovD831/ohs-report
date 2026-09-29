"""Q2 前提核查: 那"7个识别出但未测"的因素, 到底测没测?

背景: 上一版用 clean_factor_name 全名比对 → 石灰石粉尘（总尘）≠ 石灰石粉尘(总尘、呼尘)
      → 误判"未测"。本版用 **base name 归一** (去括号限定词) 后比对。

归一规则:
  '石灰石粉尘（总尘、呼尘）' → '石灰石粉尘'
  '石灰石粉尘（总尘）'        → '石灰石粉尘'
  '氟及其化合物（不含氟化氢）（按F计）' → '氟及其化合物'

同时检查: 直读式指标(工频电场 V/m、照度 lx) 的测得值是否在缓存任意字段里。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
sys.path.insert(0, "/Users/abaaba/Projects/ohs-report/web")

CACHE = Path("/Users/abaaba/Projects/ohs-report/data/vision_cache/a66517be268181e6.json")
vd = json.loads(CACHE.read_text())
dets = vd.get("detections") or []

from factor_clean import split_factors  # noqa: E402


def base_name(name: str) -> str:
    """去括号限定词 → 基名, 用于比对 (不用于报告展示)"""
    s = name.replace("（", "(").replace("）", ")")
    prev = None
    while prev != s:  # 反复剥离, 处理多层括号
        prev = s
        s = re.sub(r"\([^()]*\)", "", s)
    s = re.sub(r"[\s、,，;；:：]+", "", s)
    return s.strip("-_/·")


print("=" * 78)
print("① 用 base name 重新划分 表型A / 表型B")
print("=" * 78)

A, B = [], []
for x in dets:
    fs = [f for f in split_factors(str(x.get("factor") or "")) if f]
    if not fs:
        continue
    is_b = (len(fs) > 1) or (x.get("pass_ratio") and not x.get("ctwa") and not x.get("results"))
    (B if is_b else A).append(x)

a_base = {}
for x in A:
    for f in split_factors(str(x.get("factor") or "")):
        if f:
            a_base.setdefault(base_name(f), set()).add(f)

b_base = {}
for x in B:
    for f in split_factors(str(x.get("factor") or "")):
        if f:
            b_base.setdefault(base_name(f), set()).add(f)

print(f"表型A: {len(A)} 条 / {len(a_base)} 个基名")
print(f"表型B: {len(B)} 条 / {len(b_base)} 个基名")

print()
print("=" * 78)
print("② B 有 A 无 (用 base name 比对)")
print("=" * 78)
missing = sorted(b for b in b_base if b not in a_base)
if not missing:
    print("  (无)")
for b in missing:
    print(f"  【{b}】  B侧原名: {sorted(b_base[b])}")
    sps = set()
    for x in B:
        for f in split_factors(str(x.get("factor") or "")):
            if f and base_name(f) == b:
                sps.add(str(x.get("sampling_point") or ""))
    print(f"        岗位: {'; '.join(sorted(sps)[:4])}")

print()
print("=" * 78)
print("③ 直读式指标的值是否在缓存里 (V/m, lx, 电场, 照度)")
print("=" * 78)
blob_all = json.dumps(dets, ensure_ascii=False)
for pat in ["V/m", "kV/m", "lx", "勒克斯", "电场强度", "工频", "照度值", "Lux"]:
    print(f"  {pat!r}: {blob_all.count(pat)} 次")

print()
print("=" * 78)
print("④ 照度 / 工频电场 的全部记录 (逐条)")
print("=" * 78)
for tgt in ["照度", "工频电场", "氧化钙"]:
    print(f"\n### {tgt}")
    for i, x in enumerate(dets):
        if tgt in json.dumps(x, ensure_ascii=False):
            flds = {k: v for k, v in x.items() if v not in (None, "", [], {})}
            print(f"  [{i}] type={x.get('table_type')} {json.dumps(flds, ensure_ascii=False)[:260]}")

print()
print("=" * 78)
print("⑤ 泛化检查: 表型A 里带括号限定词的因素 (我的对比是否会误判)")
print("=" * 78)
for b, names in sorted(a_base.items()):
    for n in names:
        if "(" in n.replace("（", "("):
            print(f"  A: {n}  →  base={b}")
