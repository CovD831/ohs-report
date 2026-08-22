"""章节数据槽填充器 — 每节表格从引擎输出生成 (网页API用)

解决: 目前只有 10.2.5 有表格, 其余节"暂无表格"
按节填充 (数据源 → 表格行):
  10.2.1  评价依据表  ← standard_db(REF_196) 
  10.2.3  原辅材料/设备/照度 ← equipment_material/hazchem/illumination
  10.2.6  防护设施检查表 ← protection_rule(13)
  10.2.7  应急救援检查表 ← emergency_rule(12)
  10.2.8  PPE配备表 ← ppe_for_hazards(危害→装备)
  10.2.9  监护/制度检查 ← surveillance_rule(173)+management_rule(17)
  10.2.10 关键控制点表 ← control_point_engine
  10.2.12 结论要素表 ← conclusion_engine
用户输入类 (10.2.2/10.2.4): 提供规则清单(检查表), 待用户填数据
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402


def _rows_of(conn, sql, args=()):
    return [list(r) for r in conn.execute(sql, args).fetchall()]


def fill_section(conn: sqlite3.Connection, sec: str, assess: dict) -> list[dict]:
    """按节生成表格 (cols+rows)"""
    if sec == "10.2.1":
        # 评价依据表 (standard_ref 26项)
        refs = conn.execute("SELECT code, name FROM standard_ref ORDER BY id").fetchall()
        rows = []
        for i, (code, name) in enumerate(refs, 1):
            cur = _resolve_current_code(conn, code)
            rows.append([i, code, name, cur or "—"])
        return [{"name": "评价依据表", "cols": ["序号", "标准号", "标准名称", "现行状态"],
                 "rows": rows}]

    if sec == "10.2.3":
        tables = []
        # 项目设备清单 (全量, 从项目数据)
        eqs = assess.get("_project_data", {}).get("equipment", [])
        if eqs:
            rows = [[i, str(e).split("|")[0], str(e).split("|")[1] if "|" in str(e) else ""]
                    for i, e in enumerate(eqs, 1)]
            tables.append({"name": "主要设备清单", "cols": ["序号", "设备名称", "内部物料"],
                           "rows": rows})  # 全量
        # 照明照度 (illumination_std)
        ill = conn.execute("SELECT room, plane, lx FROM illumination_std").fetchall()
        if ill:
            rows = [[i, r[0], r[1], r[2]] for i, r in enumerate(ill, 1)]
            tables.append({"name": "照明照度标准", "cols": ["序号", "房间/场所", "参考面", "照度(lx)"], "rows": rows})
        # 主要设备清单 (从识别来源提取)
        dev_names = set()
        for h in assess.get("hazards", []):
            for s in h.get("sources", []):
                dev_names.add(s.split("[")[-1].rstrip("]") if "[" in s else s)
        if dev_names:
            rows = [[i, d] for i, d in enumerate(sorted(dev_names), 1)]
            tables.append({"name": "主要设备清单", "cols": ["序号", "设备名称"], "rows": rows})
        # 行业链 (gate/big/mid dict → 名称)
        chain = assess.get("industry_chain")
        if chain:
            rows = []
            for lvl, info in chain.items():
                if isinstance(info, dict) and "name" in info:
                    rows.append([lvl, info["name"]])
            # full 短语
            if isinstance(chain.get("full"), str):
                rows.append(["完整链", chain["full"]])
            tables.append({"name": "行业链", "cols": ["层级", "名称"], "rows": rows[:5]})
        return tables

    if sec == "10.2.6":
        rows = _rows_of(conn, "SELECT hazard_category, check_point, std_code, clause FROM protection_rule")
        return [{"name": "防护设施检查表", "cols": ["序号", "危害类别", "检查点", "依据"],
                 "rows": [[i, r[0], r[1][:40], f"{r[2]} {r[3]}"] for i, r in enumerate(rows, 1)]}]

    if sec == "10.2.7":
        rows = _rows_of(conn, "SELECT scenario, require, std_code, clause FROM emergency_rule")
        return [{"name": "应急救援检查表", "cols": ["序号", "场景", "要求", "依据"],
                 "rows": [[i, r[0], r[1][:40], f"{r[2]} {r[3]}"] for i, r in enumerate(rows, 1)]}]

    if sec == "10.2.8":
        # PPE 建议 (危害→装备映射规则; hazards无ppe字段时用类别提示)
        import sqlite3 as _sq
        hz = assess.get("hazards", [])
        # 类别→典型装备
        cat_ppe = {"化学": "化学防护服/防毒面具", "粉尘": "防尘口罩(KN95+)",
                   "噪声": "耳塞/耳罩(NRR≥20)", "高温": "隔热服/防护手套",
                   "眼面": "防护眼镜/面屏", "物理": "防冲击眼镜"}
        rows = []
        for i, h in enumerate(hz, 1):
            cat = h.get("category", "")
            ppe = cat_ppe.get(cat, "见GB 39800配备")
            rows.append([i, h["factor"], ppe, "GB 39800.1—2020"])
        return [{"name": "PPE配备建议表", "cols": ["序号", "危害因素", "防护装备", "标准"],
                 "rows": rows}]

    if sec == "10.2.9":
        tables = []
        # 监护规则 (surveillance_rule)
        surv = _rows_of(conn, "SELECT factor, check_type, cycle, must_items FROM surveillance_rule")
        if surv:
            tables.append({"name": "职业健康监护表", "cols": ["序号", "危害因素", "检查类别", "周期", "必检项目"],
                           "rows": [[i, r[0], r[1], r[2] or "—", (r[3] or "")[:30]]
                                    for i, r in enumerate(surv, 1)]})
        # 制度检查 (management_rule)
        mgt = _rows_of(conn, "SELECT category, require, std_code, clause FROM management_rule")
        if mgt:
            tables.append({"name": "管理制度检查表", "cols": ["序号", "制度类别", "检查点", "依据"],
                           "rows": [[i, r[0], r[1][:36], f"{r[2]} {r[3]}"] for i, r in enumerate(mgt, 1)]})
        return tables

    if sec == "10.2.10":
        # 关键控制点 (从 hazards 综合评分简化)
        rows = []
        for i, h in enumerate(assess.get("hazards", [])[:12], 1):
            lv = h.get("grade_level") or h.get("level") or "—"
            rows.append([i, h["factor"], lv, "见判定/分级"])
        return [{"name": "关键控制点表", "cols": ["序号", "危害因素", "级别", "控制建议"],
                 "rows": rows}]

    if sec == "10.2.12":
        risk = assess.get("industry_risk") or {}
        rows = [["1", "职业病危害类别", risk.get("level", "—") + " (" + risk.get("name", "") + ")"],
                ["2", "存在的主要问题", f"{len(assess.get('judgements', []))} 条判定记录"],
                ["3", "可行性", "基本可行 (采取补充建议后)"]]
        return [{"name": "结论要素表", "cols": ["序号", "结论要素", "结论"], "rows": rows}]

    if sec == "10.2.2":
        # 现有企业概况 (用户输入类: 提供字段清单表, 待填)
        fields = [("建厂时间", "date", "必填"), ("所属行业", "text", "必填"),
                  ("企业规模", "select", "必填"), ("职工人数", "int", "必填"),
                  ("生产工人数", "int", "必填"), ("接触人数", "int", "必填"),
                  ("危害因素种类", "list", "必填"), ("危害分布", "text", "必填"),
                  ("岗位接触水平", "float_list", "选填"), ("职业卫生机构", "bool", "必填"),
                  ("管理人员数", "int", "选填"), ("管理制度", "bool", "必填"),
                  ("健康监护情况", "bool", "必填"), ("发病处置", "text", "选填")]
        rows = [[i, f[0], f[1], ("必填" if f[2] == "必填" else "选填")]
                for i, f in enumerate(fields, 1)]
        return [{"name": "现有企业概况采集表", "cols": ["序号", "字段", "类型", "必填"],
                 "rows": rows}]

    if sec == "10.2.4":
        # 类比调查 (用户输入类: 9要素清单)
        elems = ["自然环境状况", "产品及原辅材料", "生产规模", "劳动定员",
                 "生产制度", "生产工艺", "生产设备", "防护措施", "管理水平"]
        rows = [[i, e, "相同/相似/较相似/不相似", "待填写"] for i, e in enumerate(elems, 1)]
        return [{"name": "类比项目可比性表", "cols": ["序号", "比较要素", "拟建项目", "类比项目"],
                 "rows": rows}]

    return []


def _resolve_current_code(conn, code_no_year: str) -> str | None:
    """简单版现行解析 (标准号→最新版本)"""
    import re
    nc = re.sub(r"[—–]", "-", code_no_year).replace(" ", "").upper()
    rows = conn.execute(
        "SELECT code FROM standard_db WHERE code LIKE ? ORDER BY code DESC",
        (nc + "-%",)).fetchall()
    if rows:
        return rows[0][0]
    rows = conn.execute(
        "SELECT code FROM standard_db WHERE code LIKE ? ORDER BY code DESC",
        (nc + ".%",)).fetchall()
    return rows[0][0] if rows else None
