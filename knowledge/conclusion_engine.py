"""结论与建议要素规则 (10.2.12) — 汇总各引擎 → 结论要素

依据: GBZ/T 196—2025 10.2.12 (原文要素):
  1) 汇总: 工程分析/重点危害/危害程度/防护/PPE/应急/管理
  2) 指出存在的问题
  3) 确定职业病危害关键控制点
  4) 预测接触水平范围, 判定职业病危害类别 (4.4)
  5) 明确可行性 (采取补充建议后)

规则化 (机械自动):
  A 危害类别判定: risk_category (行业码→严重/一般) — 标准明确
  B 问题清单: 从 判定超标/制度缺项/防护缺项/应急缺项 自动汇总
  C 关键控制点: control_point_engine 输出 (建议级)
  D 可行性判定: 问题均为"可整改类"(无'不可行') → 可行
     (结论: '建设项目在采取预评价报告提出的职业病防治措施补充建议后,
      在职业病防治方面基本可行' 格式)

⚠️ 依据标注: 类别判定(4.4/risk_category)=标准; 问题汇总/可行性=设计规则
用法: python3 -m knowledge.conclusion_engine
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def conclusion(industry_risk: dict, hazards: list[dict],
               judgements: list[dict], grades: list[dict],
               management_gaps: list[str], protection_gaps: list[str],
               emergency_gaps: list[str]) -> dict:
    """结论要素 (10.2.12)
    industry_risk: 行业风险分类 {code,name,level}
    hazards/judgements/grades: 管线输出
    management_gaps/protection_gaps/emergency_gaps: 制度/防护/应急缺项"""
    problems = []
    # B1 超标问题
    for j in judgements:
        if j.get("pass") is False:
            problems.append(f"{j['factor']} 超标 (GBZ 2.1 6.3判定)")
    # B2 分级问题 (高度/极度)
    for g in grades:
        if g.get("name", "").startswith(("中度", "重度", "高度", "极重度")):
            problems.append(f"{g['factor']} 作业分级: {g['name']}")
    # B3 制度缺项 (已带图标)
    problems += list(management_gaps)
    # B4 防护/应急缺项 (已带图标)
    problems += list(protection_gaps)
    problems += list(emergency_gaps)
    # C 关键控制点 (前3)
    from knowledge.control_point_engine import control_points as cp_fn
    # (演示: 简化, 用judgements+风险)
    # A 危害类别
    level = (industry_risk or {}).get("level", "未判定")
    # D 可行性 (无'不可行'类问题 → 可行)
    fatal = [p for p in problems if "超标" in p or "重置" in p]
    feasible = "基本可行" if not fatal else "需整改后可行"
    return {
        "hazard_category": level,       # 严重/一般 (4.4 + 2021-5号文)
        "category_basis": "risk_category(2021-5号文, 行业码→严重/一般) — 标准依据",
        "problems": problems,           # 存在的主要问题
        "control_points": None,         # 关键控制点 (10.2.10, 设计级)
        "feasibility": feasible,        # 可行性 (结论句)
        "conclusion_text": (
            f"该项目属于职业病危害{('严重' if level == '严重' else '一般')}的建设项目。"
            f"存在问题 {len(problems)} 项; 在采取预评价报告提出的职业病防治措施补充建议后, "
            f"职业病防治方面{feasible}。"
        ),
        # 依据标注
        "basis": {
            "hazard_category": "standard",  # 标准 (4.4+5号文)
            "problems": "standard+design",  # 超标=standard(6.3), 缺项=检查点(标准), 汇总=design
            "feasibility": "design",        # 设计规则 (标准无判定公式)
        },
    }


if __name__ == "__main__":
    # 演示: 长兴项目 (用假设的缺项)
    demo = conclusion(
        industry_risk={"code": "C261", "name": "基础化学原料制造", "level": "严重"},
        hazards=[], judgements=[{"factor": "甲苯", "pass": True}, {"factor": "环己烷", "pass": True}],
        grades=[{"factor": "甲苯", "name": "轻度危害作业"}],
        management_gaps=["⚠️ 警示标识未设置 (GBZ 158 3)"],
        protection_gaps=["🛡 盐酸储罐区无围堰 (GBZ/T 194 4.1.2)"],
        emergency_gaps=["🚨 无应急喷淋洗眼设备 (GB/T 38144)"],
    )
    print("=== 10.2.12 结论要素 (长兴-改扩建) ===")
    print(f"危害类别: {demo['hazard_category']} ({demo['category_basis'][:30]}...)")
    print(f"可行判定: {demo['feasibility']} (basis={demo['basis']['feasibility']})")
    print(f"结论句: {demo['conclusion_text']}")
    print(f"问题清单 ({len(demo['problems'])} 项):")
    for p in demo["problems"]:
        print(f"  {p}")
