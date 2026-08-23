"""结构化项目数据 schema + 按章节取数据子集 (借鉴 law 项目 get_chapter_info)

把项目 data(设备/检测/工艺/物料/定员/防护/PPE/应急/概况) 组织成结构化字段,
每个报告章节只取它需要的数据子集 → 各章"讲该章的事", 互不重复, 数据全量不截断。
"""
from __future__ import annotations
from typing import Any

# ============ 结构化字段 schema (字段名 → (描述, 类型)) ============
PROJECT_INFO_SCHEMA: dict[str, tuple[str, str]] = {
    # 基本 (data 里现有 + C1/C2 概况)
    "name": ("项目名称", "str"),
    "industry": ("所属行业", "str"),
    "risk_level": ("职业病危害风险类别", "str"),
    "investment": ("投资总额", "str"),
    "capacity": ("建设规模/产能", "str"),
    "area": ("占地面积", "str"),
    "nature": ("项目性质", "str"),
    "location": ("建设地点", "str"),
    # 工程
    "equipment": ("主要设备清单", "list"),
    "process_text": ("生产工艺流程", "str"),
    # 物料/劳动
    "materials": ("主要原辅材料", "list"),
    "staffing": ("岗位定员", "list"),
    # 危害/检测
    "hazards": ("主要职业病危害因素", "list"),
    "detections": ("检测数据(含限值)", "list"),
    # 防护
    "protection": ("拟采取的职业病防护措施", "str"),
    "ppe": ("个人防护用品", "list"),
    "emergency": ("应急救援措施", "str"),
    # 类比
    "analogous_test": ("类比检测数据", "list"),
}

# ============ 各章节取哪些数据字段 (适配 1-12 章, 映射到 report_struct 的 key) ============
# 章级 → 字段; 二级小节 → (章字段 + 该小节侧重的字段)
CHAPTER_INFO_MAPPING: dict[str, list[str]] = {
    # --- 正文 1-6 章 ---
    "1": ["name", "industry", "nature", "investment", "capacity", "area", "location", "risk_level", "equipment", "staffing"],
    "2": ["hazards", "detections", "materials", "process_text", "equipment", "staffing"],
    "3": ["protection", "ppe", "emergency", "hazards"],
    "4": ["equipment", "materials", "staffing", "protection", "area", "location"],
    "5": ["protection", "ppe", "emergency", "hazards"],
    "6": ["name", "industry", "risk_level", "nature", "capacity"],
    # --- 附录 7-12 章 ---
    "7": ["name", "industry", "risk_level", "nature"],
    "8": ["equipment", "process_text", "materials", "staffing", "industry", "name", "nature", "capacity", "area"],
    "9": ["analogous_test", "industry", "ppe", "emergency"],
    "10": ["hazards", "detections", "materials", "process_text", "equipment", "staffing"],
    "11": ["protection", "ppe", "emergency"],
    "12": ["equipment", "materials", "staffing", "protection", "area", "location"],
}

# 二级小节 → 侧重字段 (在章字段基础上, 该小节再强调这些)
SUB_EMPHASIS: dict[str, list[str]] = {
    "1.1": ["name", "industry", "risk_level"],
    "1.2": ["equipment", "process_text", "materials", "staffing"],
    "1.3": [],
    "2.1": ["hazards", "materials", "process_text", "equipment"],
    "2.2": ["hazards", "detections"],
    "3.1": ["protection"],
    "3.2": ["ppe"],
    "3.3": ["emergency"],
    "4.1": ["equipment"],
    "4.4": ["staffing"],
    "8.1": ["equipment", "staffing", "name", "industry"],
    "8.4": ["process_text", "equipment"],
    "8.5": ["materials"],
    "9.4": ["analogous_test"],
    "10.1": ["hazards", "materials", "process_text"],
    "10.2": ["hazards"],
    "10.3": ["detections"],
    "11.1": ["protection"],
    "11.2": ["ppe"],
}


def _fmt_list(items: list) -> str:
    """列表转字符串 (设备/物料/定员等)"""
    if not items:
        return "（暂无数据）"
    parts = []
    for it in items:
        if isinstance(it, dict):
            # 取非空字段拼成 "字段: 值" 行
            kv = [f"{k}: {v}" for k, v in it.items() if v]
            parts.append("，".join(kv))
            # 控制单条长度, 避免 prompt 过大
            if len(parts[-1]) > 160:
                parts[-1] = parts[-1][:157] + "…"
        else:
            parts.append(str(it))
        if len(parts) >= 60:  # 上限: 防止超长 prompt (全量但封顶60条)
            parts.append(f"…(共{len(items)}条, 仅列前60)")
            break
    return "；".join(parts) if parts else "（暂无数据）"


def get_chapter_info(sec: str, project: dict, assess: dict | None = None) -> str:
    """按章节取该项目数据子集, 格式化为 prompt 片段 (全量不截断, 各章讲各章)

    sec: 报告章节号 (章='1', 二级='2.1', 三级='2.1.1')
    project: 项目 data (equipment/materials/detections/staffing/...)
    assess: assess 结果 (hazards/risk_level/industry_chain), 供危害/风险字段
    """
    assess = assess or {}
    # 章号 = sec 第一段 (如 '2.1'→'2', '8.4.1'→'8')
    ch = sec.split(".")[0]
    fields = CHAPTER_INFO_MAPPING.get(ch, list(PROJECT_INFO_SCHEMA.keys()))
    # 二级小节侧重字段 (合并到章字段, 靠前)
    emphasis = SUB_EMPHASIS.get(sec, [])
    order = emphasis + [f for f in fields if f not in emphasis]

    # 从 project/assess 取各字段值
    hazards = assess.get("hazards", [])
    dets = project.get("detections", [])
    chain = assess.get("industry_chain") or {}
    risk = assess.get("industry_risk") or {}

    def field_value(field: str) -> Any:
        if field == "hazards":
            return [h.get("factor", "") for h in hazards] or []
        if field == "detections":
            # 检测: factor+ctwa+所属限值 (从 oel_limit 查)
            out = []
            for d in dets:
                limit = ""
                try:
                    from .llm_draft import connect
                    conn = connect()
                    for r in conn.execute(
                            "SELECT oel_type, value, unit FROM oel_limit WHERE factor_name LIKE ? LIMIT 1",
                            (d.get("factor", "") + "%",)):
                        limit = f"{r[0]} {r[1]} {r[2]}"
                        break
                    conn.close()
                except Exception:
                    limit = ""
                item = {"危害因素": d.get("factor", ""), "接触水平": d.get("ctwa"),
                        "单位": d.get("unit", "mg/m³")}
                if limit:
                    item["限值"] = limit
                out.append(item)
            return out
        if field == "risk_level":
            return risk.get("level", "待判定")
        if field == "industry":
            return chain.get("full", project.get("industry", "")) or project.get("industry", "")
        if field == "analogous_test":
            return dets
        if field == "process_text":
            # 工艺文本压到前1500字(一段长文, 取全部但限制)
            t = (project.get("process_text") or "").strip()
            return t[:1500] + ("…" if len(t) > 1500 else "")
        return project.get(field, [] if field in ("equipment", "materials", "staffing", "ppe") else "")

    lines = []
    for field in order:
        if field not in PROJECT_INFO_SCHEMA:
            continue
        desc, typ = PROJECT_INFO_SCHEMA[field]
        val = field_value(field)
        if val is None or val == "" or val == []:
            continue
        if isinstance(val, list):
            if typ == "list":
                lines.append(f"**{desc}：**\n{_fmt_list(val)}")
            else:
                lines.append(f"**{desc}：** {'、'.join(str(v) for v in val)}")
        else:
            lines.append(f"**{desc}：** {val}")
    return "\n\n".join(lines) if lines else "（暂无项目数据，需甲方补充）"
