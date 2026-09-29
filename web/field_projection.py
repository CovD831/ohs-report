"""字段投影层 — 同一份数据, 按章节投影成"该章需要的关键字段"

设计依据 (2026-09 调研 8 份真实预评价报告 1.1 基本情况):
  真实报告的"基本情况"本身就是 metadata 块 —— 10 个字段的 `字段：值` 列表,
  值都是聚合过的 (如 "劳动定员 10人, 年工作日300天, 单班制"),
  而不是原始清单 (不是 27 行岗位表, 不是 66 个设备名)。

核心原则 (用户定):
  **清单进表格, 摘要进 prompt**
    - 全量清单 → built_tables 的表格 (承载"数据全量不截断")
    - 生成某段正文 → 只喂该段需要的关键字段 (噪声小, LLM 不乱说)
  投影只决定"喂什么", 不改数据本身; 表格照旧拿全量。

三层:
  1. FIELD_VIEWS   字段在各章节的视图 (kv_block / summary / full / raw)
  2. AGGREGATORS   清单 → 聚合量 (纯规则, 不用 LLM)
  3. project_fields(sec, project, assess)  产出该章的投影结果
"""
from __future__ import annotations

import re
from typing import Any

# ============ 1. 各章字段视图 ============
# 视图类型:
#   "raw"     — 原样 (短标量)
#   "kv"      — 输出 "字段：值"
#   "summary" — 走 AGGREGATORS 聚合成一段摘要 (清单类字段)
#   "names"   — 只取名称 (清单类, 但正文只需要名字枚举)
VIEW_RAW, VIEW_KV, VIEW_SUMMARY, VIEW_NAMES = "raw", "kv", "summary", "names"

# 1.1 基本情况 — 对标真实报告的 10 个普遍字段 (7/8 出现) + 经费(3/8)
BASIC_INFO_FIELDS = [
    ("项目名称", "name", VIEW_RAW),
    ("项目性质", "nature", VIEW_RAW),
    ("投资规模", "investment", VIEW_RAW),
    ("生产规模", "capacity", VIEW_RAW),
    ("拟建地点", "location", VIEW_RAW),
    ("建设单位", "company", VIEW_RAW),
    ("所属行业", "industry", VIEW_RAW),
    ("职业病危害风险", "risk_level", VIEW_RAW),
    ("辐射源项", "radiation", VIEW_RAW),
    ("工作制度", "work_system", VIEW_RAW),
    ("职业病防治经费", "ohy_investment", VIEW_RAW),
]

# 章节 → 字段视图表。未列出的章节回退到"章级默认"。
FIELD_VIEWS: dict[str, list[tuple[str, str, str]]] = {
    # 1.1 基本情况 = metadata 块 (对标真实报告)
    "1.1": BASIC_INFO_FIELDS,
    # 3.1.1 基本情况 (2025 版结构下"基本情况"落在 3.1.1) — 与 1.1 同构,
    # 额外带企业主体信息 (来自营业执照/申请报告: 建设单位/法定代表人/注册资本/成立日期)
    # ⚠ 这些字段曾长期为 None (执照是扫描件读不到) → 2026-09 接入营业执照视觉提取后可用
    "3.1.1": BASIC_INFO_FIELDS + [
        ("法定代表人", "legal_rep", VIEW_RAW),
        ("注册资本", "registered_capital", VIEW_RAW),
        ("成立日期", "founded", VIEW_RAW),
        ("经营/建设主体类型", "company_type", VIEW_RAW),
    ],
    # 1.2 项目组成/工程内容: 生产装置/辅助装置/三废 → 设备聚合 + 工艺
    "1.2": [
        ("生产装置", "equipment", VIEW_SUMMARY),
        ("主要产品", "products", VIEW_SUMMARY),
        ("工艺说明", "process_text", VIEW_RAW),
    ],
    # 1 总论: 概况字段 + 聚合量 (不倾原始清单)
    "1": [
        *BASIC_INFO_FIELDS,
        ("主要设备概况", "equipment", VIEW_SUMMARY),
        ("产品概况", "products", VIEW_SUMMARY),
        ("主要原辅材料", "materials", VIEW_SUMMARY),
        ("定员概况", "staffing", VIEW_SUMMARY),
    ],
    # 2.1 现有企业概况: 人员/防护/PPE/应急 的概况
    "2.1": [
        ("项目名称", "name", VIEW_RAW),
        ("建设单位", "company", VIEW_RAW),
        ("所属行业", "industry", VIEW_RAW),
        ("现有产品", "existing_products", VIEW_SUMMARY),
        ("定员概况", "staffing", VIEW_SUMMARY),
        ("主要设备概况", "equipment", VIEW_SUMMARY),
        ("现有危害因素", "hazards", VIEW_NAMES),
        ("现有防护措施", "protection", VIEW_SUMMARY),
    ],
    # 3.4 产品方案与原辅材料: 名称枚举够用 (全量在表格)
    "3.4.1": [("产品", "products", VIEW_SUMMARY)],
    "3.4.2": [("主要原辅材料", "materials", VIEW_SUMMARY)],
    # 3.6 设备: 概况
    "3.6.1": [("主要设备", "equipment", VIEW_SUMMARY)],
    "3.6.2": [("主要设备", "equipment", VIEW_SUMMARY)],
    # 3.7 建构筑物
    "3.7.1": [("建构筑物", "buildings", VIEW_SUMMARY)],
    # 3.1.5 定员
    "3.1.5": [("定员概况", "staffing", VIEW_SUMMARY), ("班制", "shifts", VIEW_SUMMARY)],
    # 5.1 危害识别: 危害因素名称 (全量在表格)
    "5.1.1": [("主要职业病危害因素", "hazards", VIEW_NAMES)],
    "5.1.1.1": [("主要职业病危害因素", "hazards", VIEW_NAMES)],
    "5.1.1.2": [("主要职业病危害因素", "hazards", VIEW_NAMES)],
    # 5.4 关键控制点
    "5.4": [("关键控制点", "critical_points", VIEW_SUMMARY)],
}


# ============ 2. 聚合器 (纯规则, 清单 → 摘要量) ============

def _n(v) -> int:
    """从 '8人' / '8' / 8 取整数"""
    m = re.search(r"\d+", str(v if v is not None else ""))
    return int(m.group(0)) if m else 0


def agg_staffing(v: Any) -> str:
    """定员清单 → 概况 (对标真实报告 '劳动定员10人, 年工作日300天, 单班制')"""
    items = v if isinstance(v, list) else []
    if not items:
        return ""
    rows = [x for x in items if isinstance(x, dict)]
    if not rows:
        return f"共{len(items)}个岗位"
    total = sum(_n(x.get("count")) for x in rows)
    # 部门分布
    depts: dict[str, int] = {}
    for x in rows:
        d = str(x.get("dept") or "").strip()
        if d:
            depts[d] = depts.get(d, 0) + _n(x.get("count"))
    parts = [f"共设{len(rows)}个岗位, 定员{total}人"]
    if depts:
        top = sorted(depts.items(), key=lambda kv: -kv[1])[:5]
        parts.append("主要部门: " + "、".join(f"{d}{c}人" for d, c in top))
    parts.append(f"（明细{len(rows)}行见班制定员表）")
    return "；".join(parts)


def agg_shifts(v: Any) -> str:
    """班制清单 → 概况"""
    items = v if isinstance(v, list) else []
    if not items:
        return ""
    sysms: list[str] = []
    for x in items:
        s = str(x.get("system") or "").strip() if isinstance(x, dict) else ""
        if s and s not in sysms:
            sysms.append(s)
    if not sysms:
        return ""
    txt = "、".join(sysms[:4])
    return f"班制: {txt}（共{len(items)}条岗位班制记录, 明细见班制定员表）"


def agg_equipment(v: Any) -> str:
    """设备清单 → 概况 (总台数 + 主要类型 + 类型分布)"""
    items = v if isinstance(v, list) else []
    if not items:
        return ""
    rows = [x for x in items if isinstance(x, dict)]
    if not rows:
        return f"共{len(items)}台（套）"
    total = 0
    for x in rows:
        q = _n(x.get("qty"))
        total += q if q else 1
    # 同源维护: 与 web/table_builder.py 的过滤口径保持一致 (原料名与厂区无关的通用词)
    _UTIL = ("储罐", "罐区", "仓库", "厂房", "建筑", "办公", "配电", "变电")
    names = []
    for x in rows:
        nm = str(x.get("name") or "").strip()
        if nm and not any(u in nm for u in _UTIL):
            names.append(nm)
    # 名称尾词归类型 (反应釜/冷凝器/泵/风机...)
    suffix: dict[str, int] = {}
    for nm in names:
        m = re.search(r"([\u4e00-\u9fff]{2,6}(釜|器|泵|机|槽|塔|炉|罐|台|线|系统|装置|组))$", nm)
        key = m.group(1)[-1] if m else ""
        if key:
            suffix[key] = suffix.get(key, 0) + 1
    parts = [f"共{len(rows)}种设备, 合计约{total}台（套）"]
    if suffix:
        top = sorted(suffix.items(), key=lambda kv: -kv[1])[:6]
        parts.append("主要类别: " + "、".join(f"{k}{c}项" for k, c in top))
    parts.append(f"（明细{len(rows)}行见设备明细表）")
    return "；".join(parts)


def agg_materials(v: Any) -> str:
    """原辅材料清单 → 概况 (种类数 + 主要物料名)"""
    items = v if isinstance(v, list) else []
    if not items:
        return ""
    names = []
    for x in items:
        nm = str(x.get("name") or "").strip() if isinstance(x, dict) else str(x).strip()
        if nm:
            names.append(nm)
    if not names:
        return ""
    # 明显是表头残留的行剔除
    names = [n for n in names if not re.match(r"^(原辅材料?名称|物料名称|名称)$", n)]
    head = "、".join(names[:12])
    more = f" 等{len(names)}种" if len(names) > 12 else ""
    return f"共{len(names)}种原辅材料: {head}{more}（明细见原辅材料表）"


def agg_products(v: Any) -> str:
    """产品清单 → 概况"""
    items = v if isinstance(v, list) else []
    if not items:
        return ""
    rows = []
    for x in items:
        if isinstance(x, dict):
            nm = str(x.get("name") or "").strip()
            out = str(x.get("output") or "").strip()
            if nm:
                rows.append(f"{nm}（{out}）" if out else nm)
        else:
            s = str(x).strip()
            if s:
                rows.append(s)
    if not rows:
        return ""
    head = "、".join(rows[:10])
    more = f" 等{len(rows)}种" if len(rows) > 10 else ""
    return f"共{len(rows)}种产品: {head}{more}（明细见产品产量表）"


def agg_buildings(v: Any) -> str:
    """建构筑物 → 概况"""
    items = v if isinstance(v, list) else []
    if not items:
        return ""
    rows = [x for x in items if isinstance(x, dict)]
    total_area = 0.0
    names = []
    for x in rows:
        nm = str(x.get("name") or "").strip()
        if nm:
            names.append(nm)
        for k in ("area", "floor_area"):
            m = re.search(r"[\d.]+", str(x.get(k) or ""))
            if m:
                try:
                    total_area += float(m.group(0))
                except ValueError:
                    pass
    if not names:
        return ""
    parts = [f"共{len(names)}项建构筑物"]
    if total_area:
        parts.append(f"合计面积约{total_area:.0f}㎡")
    parts.append("（明细见建构筑物表）")
    return "；".join(parts)


def agg_protection(v: Any) -> str:
    """防护措施 → 概况 (项目名枚举, 不倾倒全文)"""
    if isinstance(v, str) and v.strip():
        t = v.strip()
        return t[:400] + ("…" if len(t) > 400 else "")
    items = v if isinstance(v, list) else []
    names = []
    for x in items:
        s = str(x.get("name") or x.get("measure") or x).strip() if isinstance(x, dict) else str(x).strip()
        if s:
            names.append(s)
    if not names:
        return ""
    head = "、".join(names[:10])
    more = f" 等{len(names)}项" if len(names) > 10 else ""
    return f"共{len(names)}项防护措施: {head}{more}"


AGGREGATORS = {
    "staffing": agg_staffing,
    "shifts": agg_shifts,
    "equipment": agg_equipment,
    "equipment_detail": agg_equipment,
    "materials": agg_materials,
    "materials_short": agg_materials,
    "products": agg_products,
    "existing_products": agg_products,
    "buildings": agg_buildings,
    "facilities": agg_buildings,
    "protection": agg_protection,
    "ppe": agg_protection,
    "emergency": agg_protection,
    "critical_points": agg_protection,
}


def _names_of(v: Any, limit: int = 40) -> str:
    """只取名称列表 (危害因素等)"""
    items = v if isinstance(v, list) else ([] if not v else [v])
    out = []
    for x in items:
        if isinstance(x, dict):
            s = str(x.get("factor") or x.get("name") or "").strip()
        else:
            s = str(x).strip()
        if s and s not in out:
            out.append(s)
    if not out:
        return ""
    head = "、".join(out[:limit])
    return head + (f" 等{len(out)}项" if len(out) > limit else "")


# ============ 3. 溯源传导 ============
# 上游不可信/待核的字段 → 值后加标注, 让 LLM 知道不能照抄
UNTRUSTED_FIELDS = {"investment"}
UNTRUSTED_NOTE = "（⚠来源待核，未在材料中指到出处）"


def _field_value(name: str, project: dict, assess: dict) -> Any:
    """从 project/assess 取字段值 (含少量派生字段)"""
    if name == "risk_level":
        return (assess.get("industry_risk") or {}).get("level") or assess.get("risk_level") or ""
    if name == "hazards":
        return [h.get("factor", "") for h in (assess.get("hazards") or [])]
    if name == "industry":
        chain = assess.get("industry_chain") or {}
        return chain.get("full") or project.get("industry") or ""
    return project.get(name)


def project_fields(sec: str, project: dict, assess: dict | None = None) -> list[dict]:
    """按章节投影出字段列表 → [{"label","field","value","view","note"}]

    未命中 FIELD_VIEWS 的章节返回 [] (调用方决定回退策略)
    """
    assess = assess or {}
    views = FIELD_VIEWS.get(sec)
    if views is None:
        return []
    out = []
    for label, field, view in views:
        raw = _field_value(field, project, assess)
        val: Any
        if view == VIEW_SUMMARY:
            fn = AGGREGATORS.get(field)
            val = fn(raw) if fn else ""
        elif view == VIEW_NAMES:
            val = _names_of(raw)
        else:
            val = raw
        if val in (None, "", [], {}):
            val = ""
        note = UNTRUSTED_NOTE if (field in UNTRUSTED_FIELDS and val) else ""
        out.append({"label": label, "field": field, "value": val,
                    "view": view, "note": note})
    return out


def render_kv_block(fields: list[dict]) -> str:
    """渲染成真实报告的 '字段：值' metadata 块"""
    lines = []
    for f in fields:
        if not f["value"]:
            continue
        v = f"{f['value']}{f['note']}" if f["note"] else str(f["value"])
        lines.append(f"{f['label']}：{v}")
    return "\n".join(lines)


def render_for_prompt(sec: str, project: dict, assess: dict | None = None) -> str:
    """投影 → prompt 片段 (供 get_chapter_info 使用)

    1.1 这类"基本情况"章节渲染成 kv 块 (对标真实报告);
    其余章节渲染成 "**标签：** 值" 小节。
    返回空串 = 未命中投影 → 调用方回退旧逻辑。
    """
    fields = project_fields(sec, project, assess)
    if not fields:
        return ""
    has_any = any(f["value"] for f in fields)
    if not has_any:
        return "（本章节所需字段暂无数据，均需标注'待补充'）"
    if sec.endswith(".1") and len([f for f in fields if f["field"] in
                                   {"name", "nature", "investment", "capacity"}]) >= 3:
        return render_kv_block(fields)
    parts = []
    for f in fields:
        if not f["value"]:
            continue
        v = f"{f['value']}{f['note']}" if f["note"] else str(f["value"])
        parts.append(f"**{f['label']}：** {v}")
    return "\n\n".join(parts)
