"""10.2.11 建议生成 v2 — 标准条款驱动 (非模板句)

原理:
  问题(超标/缺项) → 从标准条款库匹配措施 (protection_rule/management_rule/
  emergency_rule/surveillance_rule) → 每条建议带标准号+条款号
  LLM 可选(无则模板), 但建议依据=标准条款(可追溯)

区别旧版:
  旧: 通用句('建议完善管理制度') 无具体条款
  新: 具体措施+条款 ('产生毒物设备应密闭化 GBZ/T 194—2007 4.2.2')
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def query_measures(conn, issue_type: str) -> list[dict]:
    """按问题类别查询标准措施条款"""
    m = []
    if issue_type in ("超标", "化学"):
        for r in conn.execute("SELECT check_point, std_code, clause FROM protection_rule "
                              "WHERE hazard_category IN ('化学毒物','粉尘')"):
            m.append({"measure": r[0], "basis": f"{r[1]} {r[2]}"})
    if issue_type in ("噪声", "物理"):
        for r in conn.execute("SELECT check_point, std_code, clause FROM protection_rule "
                              "WHERE hazard_category IN ('噪声','高温','振动','辐射')"):
            m.append({"measure": r[0], "basis": f"{r[1]} {r[2]}"})
    if issue_type in ("制度", "管理"):
        for r in conn.execute("SELECT require, std_code, clause FROM management_rule"):
            m.append({"measure": r[0], "basis": f"{r[1]} {r[2] or ''}".strip()})
    return m


def build_advice(conn, problems: list[str]) -> tuple[list[str], list[dict]]:
    """问题清单 → 建议 (标准条款驱动)"""
    paras = []
    rows = []
    for p in problems:
        if "超标" in p or "化学" in p:
            measures = query_measures(conn, "化学")
        elif "噪声" in p or "物理" in p:
            measures = query_measures(conn, "物理")
        elif "制度" in p or "管理" in p:
            measures = query_measures(conn, "制度")
        else:
            measures = []
        # 取前3条措施作为建议
        for m in measures[:3]:
            rows.append([len(rows) + 1, p[:30], m["measure"], m["basis"]])
    if rows:
        paras.append(f"针对本项目存在的问题（共 {len(problems)} 项），提出以下补充建议（每条建议依据现行标准条款）：")
        paras.append("上述建议均落实后可有效降低职业病危害风险，达到国家职业卫生标准要求。")
    return paras, rows


def fill_10211(conn, assess: dict) -> dict:
    """10.2.11 生成: 段落+表格 (标准条款驱动)"""
    problems = []
    for j in assess.get("judgements", []):
        if j.get("pass") is False:
            problems.append(f"{j['factor']} 超标 (GBZ 2.1 6.3)")
    # 缺项 (检查点 demo, 去重)
    seen = set()
    for r in conn.execute("SELECT category FROM management_rule LIMIT 3"):
        p = f"{r[0]} 制度检查"
        if p not in seen:
            problems.append(p)
            seen.add(p)
    for r in conn.execute("SELECT hazard_category FROM protection_rule LIMIT 3"):
        p = f"{r[0]} 防护检查"
        if p not in seen:
            problems.append(p)
            seen.add(p)
    paras, rows = build_advice(conn, problems)
    return {"paragraphs": paras,
            "tables": [{"name": "问题与建议表", "cols": ["序号", "存在问题", "建议措施(标准条款)", "依据"],
                        "rows": rows}]}


if __name__ == "__main__":
    import json
    from knowledge.project_assess import assess_project
    from knowledge.oel import connect
    conn = connect()
    r = assess_project(conn, {"name": "t", "industry": "261", "equipment": ["酯化釜"]})
    out = fill_10211(conn, r)
    print("=== 10.2.11 标准条款驱动演示 ===")
    for p in out["paragraphs"]:
        print(f"  {p[:60]}")
    print(f"\n建议表 ({len(out['tables'][0]['rows'])} 行):")
    for row in out["tables"][0]["rows"][:6]:
        print(f"  {row[0]}. {row[1][:24]:26s} → {row[2][:32]:36s} [{row[3]}]")
    conn.close()
