"""Q2: 重新推导"识别出但未测"的因素 (避开我上一版脚本的 split 假象)

上一版错误: 用朴素 split('、') 把括号内的顿号也拆了 → 造出
  '石灰石粉尘（总尘' / '呼尘）' 这类**不存在的**残缺名。
本版用 web.factor_clean.split_factors (括号内不拆)。

定义 "识别出但未测":
  出现在 表型B (岗位×多因素, 值在 pass_ratio) 的因素
  但**不在** 表型A (逐因素浓度) 的因素集合里
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
from web.factor_clean import clean_factor_name, split_factors  # noqa: E402

CACHE = Path("/Users/abaaba/Projects/ohs-report/data/vision_cache/a66517be268181e6.json")
vd = json.loads(CACHE.read_text())
dets = vd.get("detections") or []


def _fac_list(x):
    raw = str(x.get("factor") or "")
    return [f for f in split_factors(raw) if f]


A, B = [], []
for x in dets:
    fs = _fac_list(x)
    if not fs:
        continue
    f0 = fs[0]
    _cleaned, flags = clean_factor_name(f0)
    # 表型B 判据: 多因素 或 值在 pass_ratio
    is_b = (len(fs) > 1) or (x.get("pass_ratio") and not x.get("ctwa") and not x.get("results"))
    (B if is_b else A).append(x)

a_factors = set()
for x in A:
    for f in _fac_list(x):
        cleaned, _ = clean_factor_name(f)
        a_factors.add(cleaned)

b_map = {}  # factor -> [sampling_points]
for x in B:
    sp = str(x.get("sampling_point") or "").strip()
    for f in _fac_list(x):
        cleaned, _ = clean_factor_name(f)
        b_map.setdefault(cleaned, set()).add(sp)

untested = sorted(f for f in b_map if f not in a_factors)

print(f"表型A (逐因素浓度): {len(A)} 条, {len(a_factors)} 个因素")
print(f"表型B (岗位×多因素): {len(B)} 条, {len(b_map)} 个因素")
print()
print("=" * 76)
print(f"【识别出但未测】{len(untested)} 个因素 (B 有, A 无)")
print("=" * 76)
for f in untested:
    sps = sorted(b_map[f])
    print(f"  {f}   ← 岗位: {'; '.join(sps[:2])}")
print()
print("=" * 76)
print("对照: B 与 A 共有的因素 (已测)")
print("=" * 76)
both = sorted(f for f in b_map if f in a_factors)
for f in both:
    print(f"  {f}")

Path("/tmp/q2_untested.json").write_text(
    json.dumps({"untested": untested, "tested": both,
                "b_map": {k: sorted(v) for k, v in b_map.items()}},
               ensure_ascii=False, indent=2))
print()
print("→ 明细已写 /tmp/q2_untested.json")
