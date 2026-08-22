"""类比法 9 要素可比性规则引擎 (GBZ/T 196—2025 附录C C.1)

原理: 类比项目 vs 拟建项目 → 9 要素逐项可比性评分 → 总评分 → 可比等级
  (每要素: 2=相似 | 1=较相似 | 0=不相似; 0分要素需说明/调整)

可比等级 (保守法):
  总分 ≥ 14 (9要素全相似或多数相似) → 可比
  总分 9~13 → 基本可比 (需针对非相似要素做不确定度说明)
  总分 < 9 或任一要素 0 → 不可比 (换类比项目)

输入 (类比项目数据, 用户提供): 类比项目 9 要素值
输出: 可比性评分表 + 等级 + 需说明要素

用法: python3 -m knowledge.analogy_engine
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# C.1 五个可比性维度 (GBZ/T 196 附录C C.1)
ELEMENTS = [
    ("自然环境状况", "地质/水文/气象等, 类比项目地域相近性"),
    ("产品及原辅材料", "产品/原辅材料种类和危害相似性"),
    ("生产规模", "生产规模/产能相近性 (数量级差距≤5倍为宜)"),
    ("劳动定员", "劳动定员/岗位设置相似性"),
    ("生产制度", "工作制度(班次/工时)相似性"),
    ("生产工艺", "工艺流程/技术路线相似性 (重要要素)"),
    ("生产设备", "设备类型/自动化程度相似性"),
    ("职业病防护措施", "防护设施(通风/除尘/排毒/降噪)相似性 (重要要素)"),
    ("管理水平", "职业卫生管理/监护 相似性"),
]
CORE_ELEMENTS = ("生产工艺", "职业病防护措施")


def score_comparability(compare: dict[str, int | float]) -> dict:
    """可比性评分
    compare: {要素名: 分数} — 支持 0/1/2 (不相似/较相似/相似)
            或 报告术语映射: 相同=2 相似=1.5 较相似=1 不相似=0
    未提供要素按 1 (较相似, 待现场确认)"""
    scores = {}
    for name, desc in ELEMENTS:
        v = compare.get(name, 1)
        # 术语映射 (报告用'相同'/'相似')
        if isinstance(v, str):
            m = {"相同": 2, "相似": 1.5, "较相似": 1, "不相似": 0, "基本相似": 1.5}
            v = m.get(v, 1)
        scores[name] = float(v)
    total = sum(scores.values())
    max_score = len(ELEMENTS) * 2
    # 核心要素 (工艺/防护) 为 0 → 直接不可比
    zero_core = [n for n in CORE_ELEMENTS if scores.get(n, 0) <= 0]
    if zero_core:
        level, name = "不可比", "核心要素(工艺/防护)不相似, 需更换类比项目"
    elif total >= 14:
        level, name = "可比", "9要素全相似或多数相似"
    elif total >= 9:
        level, name = "基本可比", "需对非相似要素做不确定度说明 (附录C C.4)"
    else:
        level, name = "不可比", "多数要素不相似, 建议更换类比项目"
    return {
        "scores": scores, "total": total, "max": max_score,
        "cent": round(total / max_score * 100, 1),
        "level": level, "name": name,
        "zero_elements": [n for n, s in scores.items() if s == 0],
        "low_elements": [n for n, s in scores.items() if s <= 1],
    }


def main():
    # 演示: 长兴项目类比 (类比企业=同行业光固化材料企业)
    demo = {
        "自然环境状况": 2, "产品及原辅材料": 2, "生产规模": 1,
        "劳动定员": 1, "生产制度": 2, "生产工艺": 2,
        "生产设备": 2, "职业病防护措施": 2, "管理水平": 1,
    }
    print("=== 类比法 9 要素可比性评分 (GBZ/T 196 附录C C.1) ===")
    for name, desc in ELEMENTS:
        sv = demo.get(name, 1)
        tag = {2: "相似", 1: "较相似", 0: "不相似"}[sv]
        print(f"  {name:12s} [{desc[:26]:28s}] → {tag}")
    r = score_comparability(demo)
    print(f"\n总分: {r['total']}/{r['max']} ({r['cent']}%)")
    print(f"等级: **{r['level']}** — {r['name']}")
    if r["low_elements"]:
        print(f"需说明: {', '.join(r['low_elements'])} (较相似, 不确定度评估)")
    # 反例: 核心要素0
    print("\n--- 反例: 防护措施不相似 ---")
    bad = dict(demo, **{"职业病防护措施": 0})
    r2 = score_comparability(bad)
    print(f"  总分: {r2['total']} → **{r2['level']}** — {r2['name']}")


if __name__ == "__main__":
    main()
