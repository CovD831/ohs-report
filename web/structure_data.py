"""结构化项目数据 schema + 按章节取数据子集 (借鉴 law 项目 get_chapter_info)

把项目 data(设备/检测/工艺/物料/定员/防护/PPE/应急/概况) 组织成结构化字段,
每个报告章节只取它需要的数据子集 → 各章"讲该章的事", 互不重复, 数据全量不截断。
"""
from __future__ import annotations
from typing import Any


def _is_physical(name: str) -> bool:
    """物理因素判断 (噪声/高温/振动/工频等, 不走化学限值链, 用GBZ2.2)"""
    n = name or ""
    phys = ["噪声", "高温", "振动", "工频", "紫外", "微波", "激光", "红外",
            "射频", "WBGT", "低气压", "高气压", "局部振动"]
    return any(p in n for p in phys)

# ============ 结构化字段 schema (复用 project_schema 的分组定义, 不再重复) ============
# get_chapter_info 需要 (desc, typ); 从 project_schema.PROJECT_SCHEMA(typ,desc,source,req,subs) 派生
from web.project_schema import PROJECT_SCHEMA as _PROJ_SCHEMA, FIELD_GROUPS, field_group, group_report_loc  # noqa: E402

PROJECT_INFO_SCHEMA: dict[str, tuple[str, str]] = {
    # 派生 (desc, typ)
    f: (_defn[1], _defn[0]) for f, _defn in _PROJ_SCHEMA.items()
}
# 补充 hazard/risk_level 等评估字段 (不进 PROJECT_SCHEMA, 由 assess 输出)
PROJECT_INFO_SCHEMA.setdefault("hazards", ("主要职业病危害因素", "list"))
PROJECT_INFO_SCHEMA.setdefault("risk_level", ("职业病危害风险类别", "str"))
# 辅助工段字段 (3.5.x / 5.1.1.x 的"生产辅助工序"单元专用: 只投喂辅助单元数据)
PROJECT_INFO_SCHEMA.setdefault("aux_units", ("辅助工段(公辅/储运/化验/检维修等)", "list"))
# 现有企业产品字段 (2.1 现有企业概况专用: 只给现有产量, 不写本项目语境)
PROJECT_INFO_SCHEMA.setdefault("existing_products", ("现有企业产品及产量", "list"))
# 企业基本信息 (1.1/2.1 项目背景/现有企业概况 用): 提取层新增字段
PROJECT_INFO_SCHEMA.setdefault("company", ("企业名称", "str"))
PROJECT_INFO_SCHEMA.setdefault("founded", ("成立时间", "str"))
PROJECT_INFO_SCHEMA.setdefault("registered_capital", ("注册资金", "str"))
PROJECT_INFO_SCHEMA.setdefault("legal_rep", ("法定代表人", "str"))
PROJECT_INFO_SCHEMA.setdefault("investor", ("投资方", "str"))
# 注册资金多值集合 (资料多处记载不一致时, LLM 如实标注)
PROJECT_INFO_SCHEMA.setdefault("registered_capital_all", ("注册资金(资料多处记载)", "str"))
# 物料名精简清单 (7.1/7.2 应急: 只引用项目真实物料名)
PROJECT_INFO_SCHEMA.setdefault("materials_short", ("项目主要原辅材料(名称)", "list"))
# 关键控制点 (11章结论: 岗位×关键因子×控制级别)
PROJECT_INFO_SCHEMA.setdefault("critical_points", ("关键控制点(因子/级别/措施)", "list"))
# 附件清单 (11章结论: 标准预评价附件)
PROJECT_INFO_SCHEMA.setdefault("attachment_list", ("报告附件清单", "list"))
# 职业卫生管理 (含标准库特殊作业条款 10.6 受限空间)
PROJECT_INFO_SCHEMA.setdefault("management", ("职业卫生管理制度", "str"))

# ============ 各章节取哪些数据字段 (适配 11 章平铺, 映射到 report_struct 的 key) ============
# 章级 → 字段; 二级小节 → (章字段 + 该小节侧重的字段)
CHAPTER_INFO_MAPPING: dict[str, list[str]] = {
    # 1 总论
    "1": ["name", "industry", "nature", "risk_level", "investment", "capacity", "area",
          "location", "equipment", "staffing", "materials", "hazards"],
    # 2 现有企业概况
    "2": ["name", "industry", "nature", "risk_level", "staffing", "protection", "ppe",
          "emergency", "hazards", "products", "equipment"],
    # 3 建设项目工程分析
    "3": ["name", "industry", "nature", "investment", "capacity", "area", "location",
          "equipment", "equipment_detail", "process_text", "materials", "staffing",
          "buildings", "facilities", "products", "shifts", "public_works", "hazards"],
    # 4 类比企业调查分析
    "4": ["analogous_test", "industry", "hazards", "detections", "ppe", "emergency",
          "protection", "staffing", "equipment", "materials"],
    # 5 危害因素及危害程度分析
    "5": ["hazards", "detections", "materials", "process_text", "equipment", "staffing",
          "risk_level", "hazard_grid"],
    # 6 职业病危害防护设施分析与评价
    "6": ["protection", "facilities", "hazards", "detections", "equipment", "process_text"],
    # 7 应急救援措施的分析与评价
    "7": ["emergency", "hazards", "protection", "ppe"],
    # 8 个人使用的职业病防护用品分析与评价
    "8": ["ppe", "hazards", "detections", "staffing"],
    # 9 职业卫生管理的分析与评价
    "9": ["staffing", "protection", "ppe", "emergency", "hazards", "investment"],
    # 10 职业病防治措施的补充建议
    "10": ["protection", "ppe", "emergency", "hazards", "detections", "staffing",
           "investment"],
    # 11 结论与建议
    "11": ["name", "industry", "risk_level", "nature", "capacity", "hazards", "detections"],
}

# 二级小节 → 侧重字段 (在章字段基础上, 该小节再强调这些)
SUB_EMPHASIS: dict[str, list[str]] = {
    # 1 总论
    "1.1": ["company", "founded", "registered_capital", "registered_capital_all",
            "legal_rep", "investor",
            "name", "industry", "nature", "investment"],
    "1.3": ["name", "industry", "risk_level"],
    "1.4": ["name", "location", "nature", "industry", "process_text"],  # 评价范围: 项目边界数据, 防写成评价内容
    "1.5": ["name", "industry", "nature", "risk_level"],  # 评价内容: 项目属性, 防写成评价结果
    "1.6": ["name", "hazards", "detections"],  # 类比法描述引用真检测事实 (防编造化学毒物检测)
    # 2 现有企业概况
    "2.1": ["company", "founded", "registered_capital", "legal_rep", "investor",
            "name", "industry", "nature", "risk_level", "staffing", "existing_products"],
    "2.2": ["staffing", "hazards"],
    "2.3": ["protection", "facilities"],
    "2.4": ["ppe"],
    "2.5": ["staffing"],
    # 3 工程分析
    "3.1": ["name", "industry", "nature", "investment", "capacity", "area", "location",
            "equipment", "staffing", "buildings", "public_works"],
    "3.2": ["location", "area"],
    "3.3": ["equipment", "buildings", "area"],
    "3.4": ["materials", "products"],
    "3.5": ["process_text", "equipment", "products"],
    "3.6": ["equipment", "equipment_detail"],
    "3.7": ["buildings", "public_works"],
    "3.8": ["staffing"],
    # 4 类比调查
    "4.1": ["analogous_test", "industry"],
    "4.2": ["analogous_test", "hazards", "protection", "ppe", "emergency"],
    "4.3": ["staffing"],
    "4.4": ["analogous_test", "detections"],
    "4.5": ["analogous_test"],
    "4.6": ["analogous_test", "hazards", "detections"],
    # 5 危害分析
    "5.1": ["hazards", "materials", "process_text", "equipment"],
    "5.2": ["hazards"],
    "5.3": ["detections", "hazards"],
    "5.4": ["hazards", "detections"],
    # 6 防护设施
    "6.1": ["protection", "facilities", "hazards"],
    "6.2": ["protection", "detections"],
    # 7 应急救援
    "7.1": ["emergency", "hazards", "materials_short", "process_text"],  # 应急: 用项目真实物料(防LLM从库招硫酸二甲酯/丙酮等不存在物料)
    "7.2": ["emergency", "hazards", "materials_short"],
    # 8 PPE
    "8.1": ["ppe", "hazards"],
    "8.2": ["ppe", "hazards", "detections"],
    # 9 职业卫生管理
    "9.1": ["staffing", "protection", "hazards"],
    "9.2": ["investment", "protection", "ppe", "emergency"],
    # 10 补充建议
    "10.2": ["hazards", "detections", "protection"],
    "10.3": ["staffing"],
    "10.4": ["hazards"],
    "10.5": ["staffing", "hazards"],
    "10.6": ["emergency", "protection", "management", "facilities"],  # 受限空间: 应急/防护/管理(不是定员! 修复错投)
    "10.7": ["emergency", "hazards"],
    # 11 结论
    "11.1": ["name", "industry", "risk_level", "hazards", "detections", "critical_points"],  # 结论精简: 行业/危害/风险/关键点 (别塞物料附件带偏LLM)
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
        if len(parts) >= 200:  # 上限: 全量原则(原报告原料219种全列), 上限仅防极端(>200条病态输入)
            parts.append(f"…(共{len(items)}条)")
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
    # 三级/四级单元(如 3.5.1 产品工艺/5.1.1.2 识别) 向上回溯父级侧重:
    # 3.5.1 → 查 "3.5.1"(无) → "3.5"(有工艺/设备/产品) → 用;
    # 否则整章全量字段 → LLM 每节重述项目概况 → 字数膨胀4-5倍(比对发现)
    emphasis: list[str] = []
    parts = sec.split(".")
    for n in range(len(parts), 1, -1):
        cand = ".".join(parts[:n])
        e = SUB_EMPHASIS.get(cand)
        if e:
            emphasis = e
            break
    # 命中侧重 → 只投喂侧重字段 (每个小节讲该讲的事, 不重复全章字段)
    # 未命中 → 章级全量 (兜底)
    fields = emphasis if emphasis else fields
    # 数据单元为"生产辅助工序" (3.5.x/5.1.1.x) → 只投喂辅助工段数据, 不投喂产品主线
    # (修复: 3.5.2 辅助工序正文写成四条产品线概况 → 主题错位)
    if isinstance(project, dict):
        try:
            from web.report_struct import _extract_product_units
            for uk, ut in _extract_product_units(project):
                if uk == sec and "辅助工序" in (ut or ""):
                    fields = ["aux_units", "hazards", "equipment"]
                    break
        except Exception:
            pass

    # 从 project/assess 取各字段值
    hazards = assess.get("hazards", [])
    dets = project.get("detections", [])
    chain = assess.get("industry_chain") or {}
    risk = assess.get("industry_risk") or {}

    def field_value(field: str) -> Any:
        if field == "hazards":
            return [h.get("factor", "") for h in hazards] or []
        if field == "detections":
            # 检测: factor+ctwa+标准限值 (用 entity_link 的 link_oel: CAS+名字归一化, 物理走GBZ2.2)
            out = []
            for d in dets:
                limit = ""
                try:
                    from .entity_link import link_oel
                    from .llm_draft import connect as _c
                    conn = _c()
                    # link_oel: 物理因素返回[]/化学用CAS+归一化匹配; 物理用phys_standard
                    oels = link_oel(conn, d.get("factor", ""))
                    conn.close()
                    if oels:
                        o = oels[0]
                        limit = f"{o['type']} {o['value']} {o['unit']}"
                    elif not _is_physical(d.get("factor", "")):
                        # 化学但link_oel没查到 → 物理标准兜底
                        from .report_experience import phys_standard
                        limit = phys_standard(d.get("factor", ""))
                except Exception:
                    limit = ""
                item = {"危害因素": d.get("factor", ""), "接触水平": d.get("ctwa"),
                        "单位": d.get("unit", "mg/m³")}
                if limit:
                    item["标准限值"] = limit
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
        if field == "aux_units":
            # 辅助工段数据: hazard_grid 中含辅助关键词的单元行 (公辅/储运/化验/检维修等)
            _AUX = ("公用", "辅助", "污水", "废水", "废气", "储运", "储罐区", "锅炉", "制冷",
                    "供热", "供气", "配电", "化验", "检维修", "压缩", "冷却", "空压", "固废")
            out = []
            for g in (project.get("hazard_grid") or []):
                if not isinstance(g, dict):
                    continue
                u = str(g.get("unit") or "")
                if not u or not any(k in u for k in _AUX):
                    continue
                row = {k: str(v)[:60] for k, v in g.items() if v and k != "unit"}
                row["单元"] = u
                out.append(row)
            return out[:20] or [{"说明": "辅助工段数据待补充"}]
        if field == "existing_products":
            # 现有企业产品: products 的现有产量(扩产前), 不写本项目新增/扩产后语境
            out = []
            for p in (project.get("products") or []):
                if not isinstance(p, dict):
                    continue
                out.append({"name": str(p.get("name") or ""), "现有产量": str(p.get("output") or "—")})
            return out or [{"说明": "现有企业产品数据待补充"}]
        if field == "materials_short":
            # 物料名精简清单 (应急/危害识别: 只引用项目真实物料名, 防LLM引用库/外部物料)
            out = []
            for m in (project.get("materials") or []):
                if isinstance(m, dict):
                    nm = str(m.get("name") or m.get("材料名称") or "").strip()
                else:
                    nm = str(m).split("|")[0].strip()
                if nm and len(nm) >= 2 and nm not in out:
                    out.append(nm)
            return out[:80] or [{"说明": "项目原材料待补充"}]
        if field == "critical_points":
            # 关键控制点 (11章结论/5.4: 从 assess judgements 生成 — 岗位×关键因子×控制级别)
            out = []
            for j in (assess.get("judgements") or []):
                if isinstance(j, dict) and j.get("factor"):
                    lv = (j.get("level") or {}).get("level", "") if isinstance(j.get("level"), dict) else ""
                    out.append({"关键控制因子": str(j["factor"]), "控制级别": str(lv or "—"),
                                "控制措施": str((j.get("level") or {}).get("control", "") if isinstance(j.get("level"), dict) else "")[:60]})
            return out[:15] or [{"说明": "关键控制点待补充(需危害判定完成)"}]
        if field == "attachment_list":
            # 附件清单 (11章结论: 从上传材料分类生成, 通用)
            # 材料分类文件列表由 uploads 提供; 这里用 mapping 描述标准预评价附件
            std_att = [
                ("委托书", "建设单位委托书"),
                ("营业执照", "工商营业执照(副本)"),
                ("项目批文/备案证", "项目备案证/批复文件"),
                ("总平面布置图", "项目总平面布置图/周边关系图"),
                ("工艺及设备资料", "生产工艺/设备清单"),
                ("类比检测报告", "类比企业职业病危害因素检测报告"),
                ("职业健康检查报告", "企业职业健康检查总结报告"),
            ]
            return [{"附件": n, "说明": d} for n, d in std_att]
        if field == "management":
            # 职业卫生管理 + 标准库特殊作业条款 (受限空间/高温/有限空间 10.6 需要)
            # (修复: 10.6信息块只给项目management文本, 无GB 30871条款 → LLM自写18-22%氧含量)
            mg = str(project.get("management") or "")
            if assess is not None:
                try:
                    from .projects_db import _conn as _mconn
                    _mc = _mconn()
                    # 匹配 category=受限空间/特殊作业 或 require 含 盲板/置换/氧含量/通风
                    kws = ("受限空间", "特殊作业", "有限空间", "密闭空间", "盲板", "氧含量", "置换", "上锁")
                    rows = _mc.execute(
                        f"SELECT require, std_code, clause FROM management_rule WHERE " +
                        " OR ".join(["require LIKE ?"] * len(kws)), tuple(f"%{k}%" for k in kws)).fetchall()
                    _mc.close()
                    for r, std, clause in rows[:8]:
                        mg += f"\n- [{std} {clause}] {r[:80]}" if std else f"\n- {r[:80]}"
                except Exception:
                    pass
            return mg or "职业卫生管理制度待补充(需企业提供)"
        return project.get(field, [] if field in ("equipment", "materials", "staffing", "ppe") else "")

    lines = []
    for field in fields:
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
