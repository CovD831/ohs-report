"""数字分类器单元测试 — 覆盖真实报告里数字 vs 型号/化学名的边界"""
import sys
from pathlib import Path

# 可移植根定位 (本地 repo 或容器 /app 均可跑)
ROOT = next((p for p in (Path("/app"), Path(__file__).resolve().parent.parent)
             if (p / "web" / "number_provenance.py").exists()),
            Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
from web.number_provenance import (  # noqa: E402
    audit_tables, cell_numbers, prov_llm, prov_rule, prov_std,
)

# (输入, 期望命中的数字集合)  — 期望空集 = 该串里没有数据性数字
CASES = [
    # === 应命中: 真实数据 ===
    ("2054.4", {"2054.4"}),
    ("12", {"12"}),
    ("8", {"8"}),
    ("—", set()),
    ("待补充", set()),
    ("/", set()),
    ("≤0.5 mg/m³", {"0.5"}),
    ("<0.5", {"0.5"}),
    ("≥3", {"3"}),
    ("1.5 mg/m3", {"1.5"}),
    ("0.5 dB(A)", {"0.5"}),
    ("温度 25℃", {"25"}),
    ("12人", {"12"}),
    ("3台", {"3"}),
    ("5000万元", {"5000"}),
    ("年耗量 2054.4 t/a", {"2054.4"}),
    ("占地 1200 平方米", {"1200"}),
    ("8人/班", {"8"}),
    ("2层", {"2"}),
    ("15.8", {"15.8"}),
    ("+2", {"2"}),          # 扩产后+2 → 是数量数据
    ("+1", {"1"}),

    # === 不应命中: 型号/编号/化学名位次/标准号 ===
    ("2-甲基-13-丙二醇", set()),
    ("CT6.5  CT8.5", set()),
    ("SEC-115", set()),
    ("AYFBJ-40-200", set()),
    ("GBZ 2.1—2019", set()),
    ("GBZ 2—2007", set()),
    ("2024年", set()),
    ("—", set()),
]


def test_provenance_honesty():
    """核心诚实性约束: 不许给可疑数字盖'可信'章

    用户实证: investment=5000 是 LLM 拼的。若建表侧把上游不可信的字段
    一律标 rule, 审计会误报"全部可溯源" → 等于给可疑数字洗白。
    """
    bad = []
    ok_ev = {"file": "材料.pdf", "page": 2, "text": "乙醇 12.5 t/a"}

    # 1. 带出处的 rule → 有据
    t1 = {"A表": {"cols": ["名称", "量"], "rows": [["乙醇", "12.5"]],
                  "prov": {"_default": prov_rule(ok_ev)}}}
    r1 = audit_tables(t1)["summary"]
    if not r1["ok"]:
        bad.append("带出处的 rule 应判有据, 实际 " + str(r1))

    # 2. 无出处的 rule (traceable=False) → 必须判无据 (untraced)
    t2 = {"B表": {"cols": ["项", "值"], "rows": [["投资", "5000"]],
                  "prov": {"_default": prov_rule({"file": "x", "text": ""},
                                                 field="investment", traceable=False)}}}
    r2 = audit_tables(t2)["summary"]
    if r2["ok"] or r2["unverified"] != 1:
        bad.append("无出处的 rule(investment) 必须判无据, 实际 " + str(r2))

    # 3. LLM 无出处 → 无据
    t3 = {"C表": {"cols": ["项", "值"], "rows": [["投资", "5000"]],
                  "prov": {"_default": prov_llm()}}}
    r3 = audit_tables(t3)["summary"]
    if r3["ok"]:
        bad.append("LLM 无出处数字必须判无据, 实际 " + str(r3))

    # 4. std 标准库 → 有据 (可复算)
    t4 = {"D表": {"cols": ["物质", "限值"], "rows": [["苯", "6"]],
                  "prov": {"_default": prov_std("oel_limit:苯")}}}
    r4 = audit_tables(t4)["summary"]
    if not r4["ok"]:
        bad.append("std 标准库来源应判有据, 实际 " + str(r4))

    # 5. 完全无 prov → 无据 (不能默默放过)
    t5 = {"E表": {"cols": ["项", "值"], "rows": [["投资", "5000"]]}}
    r5 = audit_tables(t5)["summary"]
    if r5["ok"]:
        bad.append("无任何 prov 的表必须判无据, 实际 " + str(r5))

    for b in bad:
        print("  ✗ " + b)
    print("溯源诚实性: %s" % ("通过 5/5" if not bad else "失败 %d 项" % len(bad)))
    return len(bad)


def main():
    bad = 0
    for text, want in CASES:
        got = set(cell_numbers(text))
        ok = got == want
        if not ok:
            bad += 1
        print(f"{'✓' if ok else '✗'} {text!r:32} → {sorted(got)}  (期望 {sorted(want)})")
    print()
    print(f"分类器: 通过 {len(CASES) - bad}/{len(CASES)}")
    print()
    bad += test_provenance_honesty()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
