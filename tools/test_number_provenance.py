"""数字分类器单元测试 — 覆盖真实报告里数字 vs 型号/化学名的边界"""
import sys

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
from web.number_provenance import cell_numbers  # noqa: E402

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


def main():
    bad = 0
    for text, want in CASES:
        got = set(cell_numbers(text))
        ok = got == want
        if not ok:
            bad += 1
        print(f"{'✓' if ok else '✗'} {text!r:32} → {sorted(got)}  (期望 {sorted(want)})")
    print()
    print(f"通过 {len(CASES) - bad}/{len(CASES)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
