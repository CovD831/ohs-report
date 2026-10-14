#!/usr/bin/env python3
"""危害程度格子口径回归 (2026-10-14 C 方案定稿)

口径: 权威来源(真实报告/GHS名录已给四级之一) → 原样单值;
      我们算的(THI) 或 库里存的不是等级 → 按注6 出等级 + 实测项数 + 待复核;
      完全无源 → 待补充。
用法: .venv/bin/python tools/test_toxicity_cell.py
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from web.section_filler import lookup_toxicity  # noqa: E402
from web.thi_rating import rate_substance  # noqa: E402

conn = sqlite3.connect(os.path.join(ROOT, "data", "ohs.db"))
n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got!r}\n      期望: {want!r}")


print("### 库里存的是 THI 下界值 → 显示必须走注6, 不再印下界")
row = conn.execute("select level, source from hazard_toxicity where factor_name='苯醌'").fetchone()
check("苯醌 DB 存的是下界(轻度危害) + thi 来源", (row[0], "thi" in row[1].lower()), ("轻度危害", True))
check("苯醌 显示 = 极度危害（按注6；实测4/8项，待复核）",
      lookup_toxicity(conn, "苯醌"), "极度危害（按注6；实测4/8项，待复核）")

print("\n### 库里无行但可算 → 同样按注6 出等级并标注")
check("丙酮 (实测2/8项)", lookup_toxicity(conn, "丙酮"),
      "极度危害（按注6；实测2/8项，待复核）")
check("二聚环戊二烯 注6 落高度而非极度", lookup_toxicity(conn, "二聚环戊二烯"),
      "高度危害（按注6；实测2/8项，待复核）")

print("\n### 权威定级行原样单值, 不加标注 (对齐真稿体裁)")
for fac, want in [("苯乙烯", "中度危害"), ("甲醇", "中度危害"), ("异丙醇", "轻度危害")]:
    check(f"{fac} 原样", lookup_toxicity(conn, fac), want)

print("\n### 库里存的不是等级 (致癌/剧毒 标签) → 禁止进「危害程度」列")
bad = conn.execute("select factor_name, level from hazard_toxicity "
                   "where level in ('致癌','剧毒','待补充') order by factor_name limit 4").fetchall()
check("存在非等级标签行 (前提成立)", len(bad) >= 1, True)
for name, lv in bad:
    out = lookup_toxicity(conn, name)
    check(f"{name} 不再印出标签「{lv}」", out != lv, True)
    check(f"{name} 结果为注6等级或待补充",
          out == "待补充" or "（按注6" in out, True)

print("\n### 完全无源 → 待补充 (不编造)")
for fac in ("乙二醇", "双酚A", "对苯二甲酸"):
    check(f"{fac} 无 GHS 行 → 待补充", lookup_toxicity(conn, fac), "待补充")

print("\n### min_have 语义未被破坏 (入库工具仍走严格门槛)")
check("默认门槛下 have<4 仍返回 None (不擅自写库)",
      rate_substance(conn, "丙酮", "67-64-1"), None)
check("显式 min_have=0 才拿到结果 (报告侧专用)",
      bool(rate_substance(conn, "丙酮", "67-64-1", min_have=0)), True)
check("空因子名不炸", lookup_toxicity(conn, ""), "待补充")

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
