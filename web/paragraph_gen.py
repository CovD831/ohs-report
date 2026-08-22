"""段落生成器 v2 — 按报告章节 (1-6) 

章节映射: 旧10.2.x → 新1-6
  10.2.1 总论 → 7 评价要点
  10.2.3 工程分析 → 1 概况(简) + 8 工程分析(详)
  10.2.5 危害分析 → 2 识别与评价
  10.2.6-8 → 3 防护措施评价
  10.2.3.7/8+9 → 4 综合性评价
  10.2.11 → 5 补充措施
  10.2.12 → 6 结论
  10.2.4 → 9 类比
数据全部从 assess 动态取 (不写死)
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def gen_paragraphs(conn: sqlite3.Connection, sec: str, assess: dict) -> list[str]:
    """按报告章节生成段落"""
    hazards = assess.get("hazards", [])
    names = "、".join(h["factor"] for h in hazards[:8]) if hazards else "—"
    chain = assess.get("industry_chain") or {}
    risk = assess.get("industry_risk") or {}

    if sec == "1":
        return [
            f"受建设单位委托，对{assess.get('project', '')}进行职业病危害预评价。",
            f"本项目属于{chain.get('mid', {}).get('name', '—')}行业（{chain.get('full', '—')}），"
            f"生产规模、选址及主要设备参数详见下表及附录工程分析。",
        ]

    if sec == "2":
        return [
            f"本项目可能产生的主要职业病危害因素为{names}。",
            "有毒物料、设备密闭不严处跑冒滴漏、设备运行噪声等是主要产生环节。",
            "对照GBZ 2.1—2019和GBZ 2.2—2007标准，本项目各岗位职业病危害因素预期接触水平判定结果如下：",
        ]

    if sec == "3":
        return [
            "依据GBZ/T 194—2007等标准，本项目拟采取密闭化、局部排风、全面通风等防护措施，"
            "个人防护用品按GB 39800.1—2020配备，应急救援按GBZ/T 205和GB/T 38144配置。",
        ]

    if sec == "4":
        return [
            f"本项目选址、总体布局（GBZ 1—2010 5.1/5.2.1）、建筑卫生学（GB/T 50034—2024照度）、"
            f"辅助用室（GBZ 1表10/11）、职业卫生管理（GBZ/T 225—2010）专项评价如下。",
        ]

    if sec == "5":
        return ["针对本项目存在的问题，提出以下补充建议（每条建议依据现行标准条款）："]

    if sec == "6":
        return [
            f"该项目属于{risk.get('level', '—')}职业病危害的建设项目（{risk.get('name', '—')}）。"
            f"在采取本报告补充建议后，职业病防治方面基本可行。",
        ]

    if sec == "7":
        refs = conn.execute("SELECT code FROM standard_ref ORDER BY id").fetchall()
        return [
            f"评价依据包括《职业病防治法》及GBZ标准体系（共{len(refs)}项，详见评价依据表）。",
            "评价范围：项目投产运行期间主要职业病危害因素及防治措施。评价方法：工程分析法、类比法、检查表法。",
        ]

    if sec == "8":
        return [
            f"本项目属于{chain.get('mid', {}).get('name', '—')}行业（{chain.get('full', '—')}），"
            f"主要原辅材料：{names}。详细工程分析见设备/原料表。",
        ]

    if sec == "9":
        return ["类比调查按GBZ/T 196附录C九要素进行可比性分析，结论见下表。"]

    return []


def fill_section_paragraphs(conn: sqlite3.Connection, sec: str, assess: dict,
                            skeleton_paras: list[str]) -> list[str]:
    """段落: 优先真实生成, 无则回退骨架占位(未实现的节)"""
    real = gen_paragraphs(conn, sec, assess)
    if real:
        return real
    return skeleton_paras


if __name__ == "__main__":
    import json
    from knowledge.project_assess import assess_project
    from knowledge.oel import connect
    conn = connect()
    r = assess_project(conn, {"name": "t", "industry": "261", "equipment": ["酯化釜"]})
    for sec in ("1", "2", "3", "6"):
        print(f"=== {sec} ===")
        for p in gen_paragraphs(conn, sec, r):
            print(f"  {p[:70]}")
    conn.close()
