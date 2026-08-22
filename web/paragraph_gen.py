"""章节段落生成器 — 模板句 + 引擎数据填充 (解决"只有表格没章节")

原则: 段落 = 固定句式(报告惯用) + 数据变量(引擎输出)
  _safe_vars 占位("—") → 真实数据
每节生成 1-3 句实质性段落:
  10.2.1  评价依据清单/范围/方法
  10.2.2  现有企业概况 (用户数据 + 危害现状)
  10.2.3  工程分析概要 (行业链/原辅/设备/选址)
  10.2.4  类比可比性 (9要素评分结论)
  10.2.6-7 防护/应急措施概述
  10.2.8  PPE配备概述
  10.2.9  制度/监护概述
  10.2.10 关键控制点结论
  10.2.12 结论(类别+可行性)
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def gen_paragraphs(conn: sqlite3.Connection, sec: str, assess: dict) -> list[str]:
    """按节生成段落 (真实数据)"""
    hazards = assess.get("hazards", [])
    names = "、".join(h["factor"] for h in hazards[:8]) if hazards else "—"
    chain = assess.get("industry_chain") or {}
    risk = assess.get("industry_risk") or {}

    if sec == "10.2.1":
        refs = conn.execute("SELECT code, name FROM standard_ref ORDER BY id").fetchall()
        ref_str = "、".join(f"{r[0]}" for r in refs[:10]) + ("等" if len(refs) > 10 else "")
        return [
            f"受建设单位委托，依据《中华人民共和国职业病防治法》及相关法律法规，对拟建项目进行职业病危害预评价。",
            f"评价依据包括《职业病防治法》以及 GBZ 2.1—2019、GBZ 188、GBZ/T 196—2025 等国家职业卫生标准（共 {len(refs)} 项，详见评价依据表）。",
        ]

    if sec == "10.2.2":
        return [
            "本项目建设性质为扩建（改建）项目，现有企业概况参见采集表。",
            f"现有企业行业分类：{chain.get('mid', {}).get('name', '—')}；"
            f"接触职业病危害因素人数、岗位接触水平等现状数据待用户填写。",
        ]

    if sec == "10.2.3":
        return [
            f"本项目属于{chain.get('mid', {}).get('name', '—')}行业（{chain.get('full', '—')}），"
            f"生产规模、选址及主要设备参数详见工程概况。",
            f"主要原辅材料：{names}。",
        ]

    if sec == "10.2.4":
        return [
            "类比调查按 GBZ/T 196—2025 附录 C 进行，从自然环境、原料、规模、定员、制度、工艺、设备、防护、管理九方面评价可比性（详见可比性表，待填写）。",
        ]

    if sec == "10.2.5":
        return [
            f"本项目可能产生的主要职业病危害因素为{names}。",
            "有毒物料、设备密闭不严处跑冒滴漏、蒸汽加热过程废气、设备运行噪声等是主要产生环节。",
        ]

    if sec == "10.2.6":
        return [
            "依据 GBZ/T 194—2007 等标准，本项目拟采取密闭化、局部排风、全面通风等防护设施（详见防护设施检查表）。",
        ]

    if sec == "10.2.7":
        return [
            "本项目应急救援措施按 GBZ/T 205—2007 和 GB/T 38144 配置，包括密闭空间作业规范、应急喷淋洗眼设备等（详见应急救援检查表）。",
        ]

    if sec == "10.2.8":
        return [
            f"各岗位拟配置的个人防护用品：化学危害→防毒面具/化学防护服，粉尘→KN95防尘口罩，噪声→耳塞/耳罩（GB 39800.1—2020）。",
        ]

    if sec == "10.2.9":
        surv = conn.execute("SELECT COUNT(*) FROM surveillance_rule").fetchone()[0]
        mgt = conn.execute("SELECT COUNT(*) FROM management_rule").fetchone()[0]
        return [
            f"职业健康监护按 GBZ 188—2025 执行（{surv} 条监护规则，详见监护表）；"
            f"职业卫生制度按 GBZ/T 225—2010 十二项检查（{mgt} 个检查点，详见制度检查表）。",
        ]

    if sec == "10.2.10":
        return [
            f"本项目职业病危害关键控制点：主要危害因素 {len(hazards)} 项，重点工序的化学危害、粉尘、噪声等需强化控制（详见关键控制点表）。",
        ]

    if sec == "10.2.12":
        return [
            f"该项目属于{risk.get('level', '—')}职业病危害的建设项目（{risk.get('name', '—')}）。"
            f"在采取本报告补充建议后，职业病防治方面基本可行。",
        ]

    return []


def fill_section_paragraphs(conn: sqlite3.Connection, sec: str, assess: dict,
                            skeleton_paras: list[str]) -> list[str]:
    """段落: 优先真实生成, 无则回退骨架占位(未实现的节)"""
    real = gen_paragraphs(conn, sec, assess)
    if real:
        return real
    return skeleton_paras
