"""关键区分: "多因素拼接"出现在哪类表?

假设: 真实报告里有两类表
  A. 危害因素**识别**表 (工种×因素, 多因素拼接)  ← 行业通用, 8/8 都有
  B. 检测**结果**表 (因素×浓度, 一因素一行)      ← 长兴是这种
新泰的"检测报告"用的是 A 的形态 (岗位×多因素+合格率), 所以我的管道认不出。

本脚本验证这个假设。
"""
import re
from pathlib import Path

import docx

REPS = sorted(Path("/tmp/rep_probe").glob("*.docx"))


def tables_of(p):
    d = docx.Document(str(p))
    out = []
    for t in d.tables:
        rows = [[c.text.strip() for c in r.cells] for r in t.rows]
        if rows:
            out.append(rows)
    return out


def classify(rows):
    """按表头判断表类型"""
    hdr = " ".join(rows[0])[:120]
    if "主要职业病危害因素" in hdr and "工种" in hdr:
        return "识别表(工种×因素)"
    if re.search(r"(检测结果|浓度|CTWA|C_TWA|时间加权)", hdr):
        return "结果表(因素×浓度)"
    return None


from collections import Counter  # noqa: E402

print("=" * 80)
print("表类型分布 (8 份真实报告)")
print("=" * 80)
total = Counter()
for p in REPS:
    ts = tables_of(p)
    c = Counter()
    for rows in ts:
        k = classify(rows)
        if k:
            c[k] += 1
    total.update(c)
    print(f"  {p.stem[:32]:34} 识别表 {c['识别表(工种×因素)']:>3} | 结果表 {c['结果表(因素×浓度)']:>3}")
print()
print(f"  合计: {dict(total)}")

print()
print("=" * 80)
print("识别表里 因素列 的实际形态 (是否多因素拼接)")
print("=" * 80)
for p in REPS[:4]:
    ts = tables_of(p)
    for rows in ts:
        hdr = " ".join(rows[0])[:120]
        if "主要职业病危害因素" in hdr and "工种" in hdr:
            # 找因素列
            ci = next((i for i, c in enumerate(rows[0]) if "主要职业病危害因素" in c), None)
            print(f"\n--- {p.stem[:28]} (因素列 idx={ci}) ---")
            for r in rows[1:5]:
                if ci is not None and ci < len(r):
                    v = r[ci]
                    print(f"   {v[:90]}")
                    print(f"     → 顿号数 {v.count('、')} {'(多因素)' if v.count('、') >= 1 else '(单因素)'}")
            break
