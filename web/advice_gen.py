"""10.2.11 补充建议生成 — 问题(结论引擎) → 针对性建议

原理:
  问题来源: 判定超标(6.3) / 分级高(229) / 制度缺项(管理检查点) /
            防护缺项(防护检查点) / 应急缺项(应急检查点)
  建议映射: 问题类别 → 建议措施模板 (GBZ/T 196 10.2.11: 提出有针对性的建议)
  依据: 建议措施引标准条款(与问题同源)

输出: 问题与建议表 (序号/存在问题/建议措施/依据)
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 问题类别 → 建议模板
ADVICE_TMPL = {
    "超标": "针对{factor}超标，建议：①加强密闭化控制；②增设局部排风/全面通风；"
            "③缩短接触时间并加强个体防护（防毒面具/耳塞）；④定期检测跟踪趋势。",
    "分级高": "针对{factor}作业分级为{level}，建议：①优先工艺改进（自动化/密闭化）；"
              "②加强通风排毒除尘；③缩短作业时间；④强化个体防护和健康监护。",
    "警示标识": "建议在产生职业病危害的作业岗位设置警示标识和中文警示说明（GBZ 158—2003）。",
    "制度": "建议按 GBZ/T 225—2010 完善职业卫生管理制度（{issue}）。",
    "防护": "建议增设/完善{issue}（相关标准）。",
    "应急": "建议配置{issue}，并定期开展应急演练（GBZ/T 196—2025 10.2.7）。",
    "监护": "建议按 GBZ 188—2025 开展上岗前、在岗期间、离岗时健康检查。",
    "培训": "建议对负责人、管理人员和劳动者开展职业卫生培训（GBZ/T 225—2010 4.10）。",
}


def problems_to_advice(problems: list[str]) -> list[dict]:
    """问题清单 → 建议 (表格行)"""
    out = []
    for p in problems:
        if "超标" in p:
            factor = p.split(" ")[0]
            advice = ADVICE_TMPL["超标"].format(factor=factor)
            basis = "GBZ 2.1—2019 6.3"
        elif "分级" in p:
            factor = p.split(" ")[0]
            level = p.split(":")[-1].strip()
            advice = ADVICE_TMPL["分级高"].format(factor=factor, level=level)
            basis = "GBZ/T 229—2010"
        elif "警示" in p:
            advice = ADVICE_TMPL["警示标识"]
            basis = "GBZ 158—2003"
        elif "制度" in p or "管理" in p:
            advice = ADVICE_TMPL["制度"].format(issue=p)
            basis = "GBZ/T 225—2010"
        elif "防护" in p:
            advice = ADVICE_TMPL["防护"].format(issue=p)
            basis = "GBZ/T 194—2007"
        elif "应急" in p:
            advice = ADVICE_TMPL["应急"].format(issue=p)
            basis = "GBZ/T 196—2025 10.2.7"
        elif "监护" in p:
            advice = ADVICE_TMPL["监护"]
            basis = "GBZ 188—2025"
        else:
            advice = f"针对{p[:24]}进一步细化。"
            basis = "—"
        out.append({"issue": p, "advice": advice, "basis": basis})
    return out


def fill_10211(conn: sqlite3.Connection, assess: dict) -> dict:
    """10.2.11 生成: 段落+表格

    问题来源: ①判定超标 ②规则检查点(未满足的, 演示列出全部) ③结论引擎
    演示: 用判定+管理/防护/应急表生成问题清单(实际以用户审核结果为准)
    """
    problems = []
    # 判定超标
    for j in assess.get("judgements", []):
        if j.get("pass") is False:
            problems.append(f"{j['factor']} 超标 (GBZ 2.1 6.3判定)")
    # 制度/防护/应急检查点 (演示: 全部列为"建议确认项")
    for r in conn.execute("SELECT require FROM management_rule LIMIT 4"):
        problems.append(f"制度检查: {r[0][:26]}")
    for r in conn.execute("SELECT check_point FROM protection_rule LIMIT 3"):
        problems.append(f"防护检查: {r[0][:26]}")
    for r in conn.execute("SELECT require FROM emergency_rule LIMIT 2"):
        problems.append(f"应急检查: {r[0][:26]}")

    advice_items = problems_to_advice(problems)
    paras = [
        f"针对本项目存在的问题（共 {len(problems)} 项），提出以下补充建议：",
        "采取上述建议后，可有效降低职业病危害风险，达到国家职业卫生标准要求。",
    ]
    rows = [[i, a["issue"], a["advice"], a["basis"]] for i, a in enumerate(advice_items, 1)]
    return {"paragraphs": paras,
            "tables": [{"name": "问题与建议表", "cols": ["序号", "存在问题", "建议措施", "依据"],
                        "rows": rows}]}


if __name__ == "__main__":
    import json
    from knowledge.project_assess import assess_project
    from knowledge.oel import connect
    conn = connect()
    r = assess_project(conn, {"name": "t", "industry": "261",
                              "equipment": ["酯化釜"], "process_text": ""})
    out = fill_10211(conn, r)
    print("=== 10.2.11 补充建议演示 ===")
    for p in out["paragraphs"]:
        print(f"  {p[:50]}")
    print(f"\n问题与建议表 ({len(out['tables'][0]['rows'])} 行):")
    for row in out["tables"][0]["rows"][:5]:
        print(f"  {row[0]}. {row[1][:30]} → {row[2][:50]} [{row[3]}]")
    conn.close()
