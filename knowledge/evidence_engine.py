"""依据索引引擎 — 每个机械化判断 → 标准条款引用 (网页"依据侧栏"数据逻辑)

原理: 引擎输出(判定/分级/监护/PPE...) 带条款号 → 组装成
  {判断结论, 依据条款, 条款原文, basis_type(standard/design)}
按章节组织 (10.2.1~10.2.12), 供网页右栏渲染

数据源 (各引擎的条款字段):
  judge_cli.checks.rule     '6.3.2 CTWA≤PC-TWA'   → GBZ 2.1—2019 6.3.2
  grade_engine.note         'G=WD×WB×WL (229.2 式4/表5)'
  protection_rule.clause    '4.2.2' (GBZ/T 194)
  emergency_rule.clause     '6.1.2.1'
  management_rule.clause    '4.1'
  analogy_engine            '附录C C.1'
  control_point             basis_type='design'
  conclusion                hazard_category=standard / feasibility=design

条款原文: get_clause_text(标准, 条款) 从标准PDF文本层提取 (GBZ 2.1等)
  注: 扫描件标准无法提取原文, 用摘要(条款文字来自视觉直读/引擎注释)

用法: python3 -m knowledge.evidence_engine
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

# === 条款原文提取 (GBZ 2.1 文本层可用) ===
PDFS = {
    "GBZ 2.1—2019": "acquire/raw/nhc_gbz/pdfs/GBZ_2.1_2019.pdf",
    "GBZ/T 229.2—2025": "acquire/raw/nhc_gbz/pdfs/GBZ_T_229.2_2025.pdf",
}


def get_clause_text(std: str, clause: str) -> str | None:
    """从标准PDF提取条款原文 (文本层可用时)"""
    pdf = PDFS.get(std)
    if not pdf:
        return None
    try:
        import pymupdf
        doc = pymupdf.open(pdf)
        full = ""
        for p in range(doc.page_count):
            full += doc[p].get_text() + "\n"
        doc.close()
        # 条款: 6.3.2 → 提取 '6.3.2' 后 至 下一条款
        m = re.search(re.escape(clause) + r"\s*\n", full)
        if m:
            start = m.end()
            # 截到下一个编号
            nxt = re.search(r"\n(6\.3\.\d|6\.5\.\d)\s", full[start:])
            end = start + (nxt.start() if nxt else min(len(full) - start, 300))
            return re.sub(r"\s+", " ", full[start:end]).strip()[:150]
    except Exception:
        pass
    return None


# === 组装判断→依据 ===
def evidence_for_judgements(judgements: list[dict]) -> list[dict]:
    """判定条目 → 依据清单 (GBZ 2.1 6.3.x + 6.5.1)"""
    out = []
    for j in judgements:
        for c in j.get("checks", []):
            rule = c.get("rule", "")
            m = re.search(r"(\d\.\d\.\d+)", rule)
            clause = m.group(1) if m else rule[:12]
            std = "GBZ 2.1—2019"
            out.append({
                "judgement": f"{j['factor']}: {c.get('value')} {'≤' if c.get('pass') else '>'} {c.get('limit')}",
                "conclusion": "合格" if c.get("pass") else "超标",
                "basis": f"{std} {clause}",
                "basis_type": "standard",
                "text": rule,  # rule即条款摘要
                "clause_raw": get_clause_text(std, clause),
            })
        # 接触水平 (6.5.1)
        if isinstance(j.get("level"), dict):
            out.append({
                "judgement": f"{j['factor']} 接触水平 {j['level']['level']}级",
                "conclusion": j["level"]["desc"],
                "basis": "GBZ 2.1—2019 6.5.1",
                "basis_type": "standard",
                "text": f"接触水平分级: {j['level']['level']} ({j['level']['desc']})",
                "clause_raw": None,
            })
    return out


def evidence_for_grades(grades: list[dict]) -> list[dict]:
    """作业分级 → 依据 (GBZ/T 229 式4/表5)"""
    out = []
    for g in grades:
        out.append({
            "judgement": f"{g['factor']} 作业分级: {g.get('name', '')}",
            "conclusion": g.get("name", ""),
            "basis": g.get("note", ""),
            "basis_type": "standard",
            "text": f"G={g.get('g')} ({g.get('note','')})",
            "clause_raw": None,
        })
    return out


def evidence_for_rules(conn, table: str, cat_field: str, std_field: str,
                       clause_field: str, req_field: str, category: str | None = None) -> list[dict]:
    """规则表 (protection/emergency/management) → 依据清单"""
    out = []
    q = f"SELECT {cat_field}, {req_field}, {std_field}, {clause_field} FROM {table}"
    if category:
        q += f" WHERE {cat_field}=?"
        rows = conn.execute(q, (category,)).fetchall()
    else:
        rows = conn.execute(q).fetchall()
    for cat, req, std, clause in rows:
        out.append({
            "judgement": f"[{cat}] {req[:24]}",
            "conclusion": "检查点",
            "basis": f"{std} {clause or ''}".strip(),
            "basis_type": "standard",
            "text": req,
            "clause_raw": None,
        })
    return out


def evidence_for_section(conn, section: str, assess_result: dict) -> list[dict]:
    """按章节组织依据 (10.2.5.3 / 10.2.6 / 10.2.7 / 10.2.9 / 10.2.10 / 10.2.12)"""
    if section == "10.2.5.3":
        return evidence_for_judgements(assess_result.get("judgements", []), )
    if section == "10.2.5.4":
        return evidence_for_grades(assess_result.get("grades", []))
    if section == "10.2.6":
        return evidence_for_rules(conn, "protection_rule", "hazard_category",
                                  "std_code", "clause", "check_point")
    if section == "10.2.7":
        return evidence_for_rules(conn, "emergency_rule", "scenario",
                                  "std_code", "clause", "require")
    if section == "10.2.9":
        return evidence_for_rules(conn, "management_rule", "category",
                                  "std_code", "clause", "require")
    if section == "10.2.10":
        cps = assess_result.get("control_points", [])
        return [{"judgement": f"{cp.get('factor')} 综合{cp.get('score')} → {cp.get('level')}",
                 "conclusion": cp.get("level", ""),
                 "basis": "GBZ/T 196—2025 10.2.10 (设计权重)",
                 "basis_type": "design",
                 "text": cp.get("basis_note", ""), "clause_raw": None} for cp in cps]
    if section == "10.2.12":
        return [{"judgement": "职业病危害类别判定",
                 "conclusion": (assess_result.get("industry_risk") or {}).get("level", ""),
                 "basis": "GBZ/T 196—2025 4.4 + 国卫办职健发〔2021〕5号",
                 "basis_type": "standard",
                 "text": "行业码→风险分类(严重/一般)", "clause_raw": None}]
    return []


if __name__ == "__main__":
    print("=== 依据索引引擎演示 ===\n")
    # 判定依据 (GBZ 2.1)
    import sqlite3
    conn = sqlite3.connect("data/ohs.db")
    print("[10.2.5.3 判定依据]")
    from knowledge.judge_cli import judge_chemical
    j = judge_chemical(conn, "甲苯", 30, 95, None)
    for e in evidence_for_judgements([j]):
        raw = f" | 原文: {e['clause_raw'][:60]}..." if e["clause_raw"] else ""
        print(f"  {e['judgement']:32s} → {e['basis']} [{e['basis_type']}]{raw}")
    print("\n[10.2.6 防护设施依据]")
    for e in evidence_for_rules(conn, "protection_rule", "hazard_category",
                                "std_code", "clause", "check_point", "化学毒物")[:4]:
        print(f"  {e['judgement']:32s} → {e['basis']} [{e['basis_type']}]")
    conn.close()
