"""核查 _det_verdict 自身漏洞: results 可能是"采样时间"而非测得值

背景: 300dpi 取证 —
  长兴 p14 的 0.5/1.5/2 → 列标题 = **采样时间(小时)**
  新泰 p27 的 0.5/2/1.5/6 → 无表头数值列, 语义不可判
⇒ `results` 里可能是**非测量值**。
  而 _det_verdict 原实现: results 非空 → "符合" ⇒ **为采样时长断言合格** = 编造!

本脚本量化: 有多少记录会因此被错误断言?
  A. results 非空 且 judgement 非空 → judgement 兜底, 安全
  B. results 非空 且 judgement 空 且 ctwa 空 → **危险** (仅凭 results 断言)
  C. ctwa 非空 → 按定义是浓度, 断言语义成立
"""
import json
from collections import Counter
from pathlib import Path

CACHES = {
    "长兴": Path("/Users/abaaba/Projects/ohs-report/data/vision_cache/32e58e26097c8e58.json"),
    "新泰": Path("/Users/abaaba/Projects/ohs-report/data/vision_cache/a66517be268181e6.json"),
}

for tag, cp in CACHES.items():
    if not cp.exists():
        print(f"{tag}: 缓存不存在")
        continue
    ds = json.loads(cp.read_text())["detections"]
    a = b = c = 0
    dang = []
    for d in ds:
        has_r = bool([x for x in (d.get("results") or []) if str(x).strip()])
        has_j = bool(str(d.get("judgement") or "").strip())
        has_c = bool(str(d.get("ctwa") or "").strip())
        if not has_r:
            continue
        if has_j:
            a += 1
        elif has_c:
            c += 1
        else:
            b += 1
            dang.append(d)

    print("=" * 74)
    print(f"{tag}: {len(ds)} 条")
    print(f"  A. 有 results 且有 judgement → 安全        : {a}")
    print(f"  C. 有 results 且有 ctwa      → 语义成立    : {c}")
    print(f"  B. 有 results 但无 judgement/ctwa → ★危险  : {b}")
    if dang:
        print(f"\n  ★危险记录样本 (会仅凭 results 断言'符合'):")
        for d in dang[:6]:
            print(f"    p{d.get('_page')} 表型{d.get('table_type')} "
                  f"{str(d.get('factor'))[:26]!r} results={d.get('results')}")
    print()
