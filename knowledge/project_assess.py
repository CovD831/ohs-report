"""项目评估管线 — 识别引擎 + 判定引擎一体 (职业病危害预评价核心)

输入 (项目数据, 用户提供):
  name        项目名称
  industry    行业码 (GB/T 4754, 如 C261)
  equipment   [设备名称, ...]        设备清单
  processes   [{unit, process}, ...] 生产单元/工序 (可选, 匹配工作单元规则)
  detections  [{factor, ctwa, cste, cme, peak, value, oel_type, rate, labor,
                bio_value, bio_unit, indicator}...]  检测数据 (可选)

输出 (报告 10.2.5 章节数据):
  risk_level          行业风险分类 (risk_category, 严重/一般)
  hazards             [{factor, source(识别来源), oel限值, 检测方法, 需检测}]  (10.2.5.2 识别)
  judgements          [{factor, pass, checks, level}]                        (10.2.5.3 危害程度)
  level_summary       接触水平分布 (6.5.1)

链路: 行业码→风险 | 设备→物料→OEL物质→危害 | 工序→规则→危害
      | 危害×检测数据→判定(纯计算) | 无检测→状态待测
用法: python3 -m knowledge.project_assess
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402
from knowledge.identify_demo import split_materials  # noqa: E402
from knowledge.judge_cli import (judge_chemical, judge_physical, judge_bio,  # noqa: E402
                                 get_oel, level_of)


def identify_hazards(conn, equipment: list[str], processes: list[dict] | None = None) -> list[dict]:
    """识别引擎: 设备→物料→OEL物质 + 工序→规则危害 (来源链保留)"""
    hazards = {}   # factor_name -> {sources:set, via:set}
    # 1) 设备→物料→OEL
    for eq in equipment:
        mats = conn.execute(
            "SELECT DISTINCT material FROM equipment_material WHERE equipment LIKE ?",
            (f"%{eq}%",)).fetchall()
        for (m,) in mats:
            for word in split_materials(m):
                hit = conn.execute(
                    "SELECT oel_factor, fuzzy FROM material_dictionary WHERE material=?",
                    (word,)).fetchone()
                if hit:
                    f = hit[0]
                    h = hazards.setdefault(f, {"sources": set(), "via": set()})
                    h["sources"].add(f"设备[{eq}]")
                    h["via"].add(word)
    # 2) 工序→规则 (work_unit_rule: 生产单元/工序 → 危害因素)
    if processes:
        for pu in processes:
            unit, proc = pu.get("unit", ""), pu.get("process", "")
            rows = conn.execute(
                "SELECT unit, process, factors FROM work_unit_rule "
                "WHERE (unit LIKE ? OR ?='') AND (process LIKE ? OR ?='')",
                (f"%{unit}%", unit, f"%{proc}%", proc)).fetchall()
            for u, p, fx in rows:
                for f in fx.split(";"):
                    f = f.strip()
                    if f:
                        h = hazards.setdefault(f, {"sources": set(), "via": set()})
                        h["sources"].add(f"工序[{u}/{p}]")
    # 3) 附加 OEL 限值/方法/生物限值/目录分类/危化品CAS标注
    out = []
    for f, info in sorted(hazards.items()):
        oels = get_oel(conn, f)
        method = conn.execute(
            "SELECT standard_code FROM method_catalog WHERE factor LIKE ? LIMIT 1",
            (f"%{f}%",)).fetchone()
        bio = conn.execute(
            "SELECT COUNT(*) FROM bio_limit WHERE factor_name=?", (f,)).fetchone()[0]
        # 目录分类标签: exact 直接查 hazard_factor → 否则走可信别名表(exact/core_eq)
        cat_row = conn.execute(
            "SELECT category, name FROM hazard_factor WHERE name=? LIMIT 1", (f,)).fetchone()
        if not cat_row:
            alias = conn.execute(
                "SELECT catalog_name FROM hazard_factor_alias WHERE oel_name=? "
                "AND match_type IN ('exact','core_eq') LIMIT 1", (f,)).fetchone()
            if alias:
                cat_row = conn.execute(
                    "SELECT category, name FROM hazard_factor WHERE name=? LIMIT 1",
                    (alias[0],)).fetchone()
        # 危化品标注: 该因素的CAS → 危化品目录 (剧毒/高毒标记)
        cas = conn.execute(
            "SELECT cas FROM oel_limit WHERE factor_name=? AND cas IS NOT NULL LIMIT 1",
            (f,)).fetchone()
        hazchem = None
        if cas and cas[0]:
            h = conn.execute(
                "SELECT name, note, is_toxic FROM hazchem_item WHERE cas=? LIMIT 1",
                (cas[0],)).fetchone()
            if h:
                hazchem = {"name": h[0], "note": h[1],
                           "is_toxic": bool(h[2])}
        out.append({
            "factor": f,
            "sources": sorted(info["sources"]),
            "via_materials": sorted(info["via"]),
            "oel": [{"type": r["oel_type"], "value": r["value"], "unit": r["unit"]}
                    for r in oels],
            "method": method[0] if method else None,
            "has_bio": bio > 0,
            "needs_test": bool(oels),  # 有OEL需检测(mg/m3等数值对比)
            "catalog": {"category": cat_row[0], "name": cat_row[1]} if cat_row else None,
            "hazchem": hazchem,
        })
    return out


def assess_project(conn, project: dict) -> dict:
    """项目评估: 识别 + 判定 一体化"""
    # 1) 行业风险分类
    risk = None
    ind = project.get("industry")
    if ind:
        r = conn.execute(
            "SELECT industry_code, industry_name, risk_level FROM risk_category "
            "WHERE industry_code=?", (ind,)).fetchone()
        if r:
            risk = {"code": r[0], "name": r[1], "level": r[2]}
    # 2) 危害识别
    hazards = identify_hazards(conn, project.get("equipment", []),
                               project.get("processes"))
    # 3) 判定 (检测数据)
    judgements = []
    for d in project.get("detections", []):
        f = d["factor"]
        if d.get("bio_value") is not None:
            r = judge_bio(conn, f, d.get("indicator"), d["bio_value"], d.get("bio_unit", ""))
        elif any(k in ("噪声", "高温", "手传振动", "工频电场", "微波辐射",
                       "超高频辐射", "高频电磁场", "紫外辐射") for k in [f]):
            r = judge_physical(conn, f, d.get("value"), oel_type=d.get("oel_type"),
                               rate=d.get("rate", "100%"), labor=d.get("labor", "Ⅰ"))
        else:
            r = judge_chemical(conn, f, d.get("ctwa"), d.get("cste"), d.get("cme"),
                               d.get("peak"))
        judgements.append({"factor": f, **r})
    # 4) 接触水平汇总
    levels = {}
    for j in judgements:
        if j.get("level") and isinstance(j["level"], dict):
            lv = j["level"]["level"]
            levels.setdefault(lv, []).append(j["factor"])
    return {
        "project": project.get("name", ""),
        "industry_risk": risk,
        "hazards": hazards,
        "judgements": judgements,
        "level_summary": levels,
    }


def main():
    conn = connect()
    # 演示项目: 长兴特殊材料(苏州) 光固化涂料材料项目 (C261, 真实设备+检测表25/26)
    project = {
        "name": "长兴特殊材料年产27080吨高性能光固化涂料材料项目",
        "industry": "C261",
        "equipment": ["酯化釜", "纯化槽", "洗涤塔", "溶剂回收槽", "中和真空槽",
                      "酯化第一冷凝器", "真空除沫器", "洗釜泵", "油相溶剂泵"],
        "detections": [
            {"factor": "甲苯", "ctwa": 30, "cste": 95},
            {"factor": "环己烷", "ctwa": 0.3, "cste": None, "peak": 4.0},
        ],
    }
    result = assess_project(conn, project)
    print("=" * 60)
    print(f"📋 {result['project']}")
    if result["industry_risk"]:
        r = result["industry_risk"]
        print(f"  行业: {r['code']} {r['name']} → 风险分类: **{r['level']}**")
    print("=" * 60)
    print(f"\n🔍 危害识别 ({len(result['hazards'])} 项):")
    for h in result["hazards"]:
        method = f" | 方法: {h['method']}" if h["method"] else " | 方法: 无GBZ/T300"
        bio = " | BEI:有" if h["has_bio"] else ""
        cat = f" [目录:{h['catalog']['category']}]" if h.get("catalog") else " [目录未收录]"
        hz = ""
        if h.get("hazchem"):
            hz = f" ⚠️危化品[{h['hazchem']['name']}]" + ("·剧毒" if h["hazchem"]["is_toxic"] else "")
        print(f"  🔴 {h['factor']}{cat}{hz} [{'需检测' if h['needs_test'] else '定性'}]"
              f" ← {'; '.join(h['sources'][:3])} (经物料: {'、'.join(h['via_materials'][:3])}){method}{bio}")
    print(f"\n📊 判定 ({len(result['judgements'])} 条):")
    for j in result["judgements"]:
        status = "✅合格" if j.get("pass") else ("❌不合格" if j.get("pass") is False else "⚠️待测")
        lv = f" 级别:{j['level']['level']}" if isinstance(j.get("level"), dict) else ""
        print(f"  {j['factor']}: {status}{lv}")
        for c in j.get("checks", []):
            mark = "✅" if c["pass"] else "❌"
            print(f"    {mark} {c['rule']}: {c['value']} vs {c['limit']}")
    if result["level_summary"]:
        print(f"\n📈 接触水平分布 (GBZ 2.1 6.5.1):")
        for lv, fs in sorted(result["level_summary"].items()):
            print(f"  {lv}级: {', '.join(fs)}")
    conn.close()


if __name__ == "__main__":
    main()
