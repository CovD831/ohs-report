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
# 职业卫生专项经费 (9.2 专项投资: 与项目总投资严格区分)
PROJECT_INFO_SCHEMA.setdefault("ohy_investment", ("职业卫生专项经费(万元)", "str"))

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
    "1.5": ["name", "industry", "nature", "risk_level", "capacity", "investment",
            "materials_short", "hazards"],  # 评价内容: 属性+规模+物料+危害 (数据化, 减'待补充'空话)
    "1.6": ["name", "hazards", "detections"],  # 类比法描述引用真检测事实 (防编造化学毒物检测)
    # 2 现有企业概况
    "2.1": ["company", "founded", "registered_capital", "legal_rep", "investor",
            "name", "industry", "nature", "risk_level", "staffing", "existing_products"],
    "2.2": ["management", "staffing", "hazards"],  # 管理台账文本(现状报告)是2.2主体数据源
    "2.3": ["protection", "facilities", "emergency"],  # emergency含防护设施评价文本(现状报告)
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
    "5.4": ["hazards", "detections", "critical_points"],  # 危害程度: 加关键控制点判定(因子×工序×级别)
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
    "9.2": ["ohy_investment", "investment", "protection", "ppe", "emergency"],  # 专项投资=ohy(防治经费), 总投资仅作比照(防混用)
    # 10 职业病危害关键控制点分析 (标准 10.2.10, 2026-09 独立成章)
    "10.1": ["hazards", "detections", "critical_points"],
    "10.2": ["critical_points", "protection"],
    # 11 职业病防治措施的补充建议 (标准 10.2.11; 原 10 章平移)
    "11.2": ["hazards", "detections", "protection"],
    "11.3": ["staffing"],
    "11.4": ["hazards"],
    "11.5": ["staffing", "hazards"],
    "11.6": ["emergency", "protection", "management", "facilities"],  # 受限空间: 应急/防护/管理(不是定员! 修复错投)
    "11.7": ["emergency", "hazards"],
    # 12 结论与建议 (标准 10.2.12; 原 11 章平移)
    "12.1": ["name", "industry", "risk_level", "hazards", "detections", "critical_points"],  # 结论精简: 行业/危害/风险/关键点 (别塞物料附件带偏LLM)
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

    # 字段投影层 (2026-09): 命中投影表 → 只喂该节需要的关键字段 (清单进表格/摘要进prompt)
    # 依据: 8份真实报告 1.1 基本情况 = 10字段 metadata 块, 不是原始清单倾倒。
    # 未命中 → 回退下面的旧逻辑 (按 CHAPTER_INFO_MAPPING 全量字段)。
    try:
        from .field_projection import render_for_prompt as _proj
        _p = _proj(sec, project, assess)
        if _p:
            return _p
    except Exception:
        pass  # 投影失败不阻断 (回退旧逻辑)

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


# ============ 章节要素清单 (MUST_COVER: 每节必须覆盖的内容要点) ============
# 通用: 与 web/validators.py 的 MUST_COVER 同源; 投喂给 LLM 让生成时知道该写全什么
# 原则: 骨架(要素名/要素词)写在代码; 具体数值/限值一律从标准库动态取(单一数据源,
#       标准换版只改库不改代码 — 修复: 10.6氧含量数值曾硬编码, 会与标准库分叉)
MUST_COVER_INFO = {
    # 标准 10.2.10 关键控制点 (2026-09 独立成章; 原在 5.4.3/11.1 里)
    "10.1": "【本节必须覆盖】① 关键控制因子(按危害程度/接触人数/严重程度筛出的主要因素)；"
            "② 关键控制点(因子×工序/岗位, 如酯化/稀释/过滤/包装)；"
            "③ 控制级别与对应措施(工程防护/个体防护/应急)；④ 依据标准条款",
    "11.6": "【本节必须覆盖】① 作业前安全隔绝(插入盲板/拆除管道/封堵孔洞/断电上锁挂牌)；"
            "② 清洗置换后气体检测(以标准库受限空间条款为准)；"
            "③ 通风(自然/强制风机/管道送风先分析)；④ 作业许可证+专人监护；⑤ 作业中断超时重新检测",
    "7.1": "【本节必须覆盖】① 应急情景(中毒/灼伤/中暑/烫伤/密闭空间窒息/泄漏)；"
           "② 应急用品清单(空气呼吸器/防毒面具/化学防护服/急救箱/对讲机/警铃)；"
           "③ 应急处置要点(切断物料/撤离人员/收集泄漏/中毒急救)；④ 依据标准(以标准库应急条款为准)",
    "12.1": "【本节必须覆盖】① 风险类别判定(严重/较重/一般)；② 关键控制点(因子×工序: 酯化/稀释/过滤/包装)；"
            "③ 达标结论(经工程分析与类比调查, 预计正常运行情况下各危害因素浓度能符合GBZ 2.1/2.2限值要求)；"
            "④ 合规性评价(拟采取防护措施基本合理可行, 符合职业病防治法/GBZ 1要求)；"
            "⑤ 存在问题与建议(待完善项/施工设计阶段补充项)；⑥ 附件清单(委托书/营业执照/批文/总平面图/检测报告)",
    "11.5": "【本节必须覆盖】① 监护制度(岗前/在岗/离岗)；② 体检项目(按接触危害); ③ 职业禁忌证; ④ 档案管理",
    "4.4": "【本节必须覆盖】① 类比企业名称/规模；② 检测条件(满负荷生产)；③ 检测因素(化学毒物+粉尘+物理)；"
           "④ 检测结果对比(GBZ 2.1限值)与可比性分析；⑤ 检测数据来源标注",
    "1.1": "【本节必须覆盖】只写信息块明确给出的企业信息, 顺序: ①企业全称②成立时间③投资方④注册资金⑤法定代表人⑥厂址⑦行业分类⑧项目名称/性质(扩建/新建)⑨产能/投资(信息块有则必写)。"
            "【写法】信息铺陈: 企业→项目→意义, 一句一实据, 不写空评价性语句;"
            "【严禁(信息块没有就严禁编造)】认证历程(BV/ISO/清洁生产审核/安全标准化)、集团经验年限、产品应用领域、市场背景/产能不足类描述 — "
            "该类信息一律写'待补充（需企业提供）'；(已确认原报告有但材料源缺: 认证历程/集团40年/应用领域/市场动力 — 不编造)",
    "2.1": "【本节必须覆盖】① 企业基本情况(建厂/产品/规模/定员)；② 现有岗位设置与接触人数；"
           "③ 现有主要职业病危害因素；④ 现有防护/管理现状(机构/PPE/监护)",
    "2.2": "【本节必须覆盖】① 管理机构(安环部门/专职人员)；② 管理制度(制度清单/操作规程)；"
           "③ 健康监护执行(岗前/在岗/离岗)；④ 培训与档案管理",
    "2.5": "【本节必须覆盖】① 辅助用室类别(更衣室/浴室/盥洗/休息室)；② 设置依据(卫生特征分级 GBZ 1第8章)；"
           "③ 配置数量(按最大班人数)；④ 位置要求(清洁区/便利)",
    "3.5": "【本节必须覆盖】① 各产品线工艺流程(投料→反应→稀释/分散→过滤→包装)；② 每工序使用物料与产生的危害因素；"
           "③ 生产设备(釜/泵/研磨/包装)；④ 关键控制工序(投料/取样/检维修)与接触方式",
    "4.1": "【本节必须覆盖】① 类比企业选择依据(工艺/物料/规模/管理可比)；② 类比企业基本情况；"
           "③ 生产工艺与本项目对应关系；④ 可比性分析结论",
    "5.4": "【本节必须覆盖】① 各危害因素危害程度分级(按GBZ 230/危害程度)；② 关键控制点判定(因子×工序)；"
           "③ 分级依据(接触水平/毒性/接触人数)；④ 高毒作业特殊要求",
    "6.1": "【本节必须覆盖】① 防尘措施(密闭投料/局部排风/湿式作业)；② 防毒措施(通风排毒/密闭化/无泄漏泵)；"
           "③ 防噪声(低噪设备/隔声消声减振 GB/T 50087)；④ 防暑(通风降温/工间休息)；"
           "⑤ 各措施依据标准与预期效果",
    "6.2": "【本节必须覆盖】① 防尘防毒设施检查表(要求/依据/结果)；② 防噪声振动检查表；"
           "③ 防暑防寒检查表；④ 设施配置明细(岗位×设施×数量)；⑤ 运行维护要求",
    "7.2": "【本节必须覆盖】① 应急设施配置(洗眼器/喷淋/报警仪/通风联锁)；② 气体检测报警(点位/报警值 GB/T 50493)；"
           "③ 应急撤离通道与安全出口；④ 应急设施运行维护管理",
    "9.1": "【本节必须覆盖】① 管理制度清单(制度/操作规程/档案)；② 培训计划(主要负责人/管理人员/劳动者)；"
           "③ 健康监护计划(体检项目/周期 GBZ 188)；④ 职业病危害告知(合同告知/公告栏/警示标识 GBZ 158)",
    "10.1": "【本节必须覆盖】① 三同时定义与法律依据(职业病防治法)；② 各阶段要求(设计/施工/验收阶段职业病防护设施同步)；"
            "③ 建设单位主体责任；④ 验收程序",
    "10.2": "【本节必须覆盖】① 针对性补充措施(按危害因素逐项: 工程防护/个体防护/管理)；② 措施责任主体与时限；"
            "③ 应急救援完善建议；④ 检测与监护建议",
    "10.3": "【本节必须覆盖】① 培训内容与周期(法规/操作规程/应急处置/防护用品使用)；② 培训对象与考核；"
            "③ PPE正确使用指导；④ 岗位操作规程培训",
    "9.2": "【本节必须覆盖】① 职业卫生专项经费(职业病防治经费/健康监护+防护设施+检测+应急培训投入)；"
            "② 科目构成(防护设施/个人防护/监测检测/健康监护/应急/培训/管理)；"
            "③ 经费依据(企业投入计划/职业病防治法要求)；④ 注意: 专项投资≠项目总投资(总投资仅作比照, "
            "缺专项数据写'待补充(需企业提供)'，严禁把项目总投资写为专项投资)",
    "1.4": "【本节必须覆盖】① 评价范围界定(与评价内容区分: 只圈边界)；② 覆盖范围(生产系统/储运/辅助/公用工程)；"
            "③ 施工期: 按GBZ/T 196要求, 含建设安装施工阶段职业病危害分析(施工期间接触: 电焊烟尘/噪声/高温/水泥粉尘/"
            "有机溶剂等, 与投产运行期分开评价)；④ 评价期间(投产运行期正常生产)；⑤ 范围外说明(不包含哪些)",
}

# 章节 → 标准库条款关键词 (动态取数值/限值: 单一数据源)
_MUST_COVER_CLAUSES = {
    "10.6": ("受限空间", "盲板", "氧含量", "置换", "上锁", "气体分析", "通风", "监护人"),
    "7.1": ("应急", "救援", "预案", "报警"),
}


def _std_clauses(sec: str) -> str:
    """从标准库动态取该章节相关条款 (数值/限值唯一来源; 库无条款时返回空, 骨架仍生效)
    检索优化(第一层): 条款按相关性排序, 不依赖数据库返回序 —
      ① 命中章节核心词次数多者优先  ② std_code 含年份新者优先(现行版在前)
      ③ 条款号精细者优先(8.4.2a 比 8.4.2 细)"""
    kws = _MUST_COVER_CLAUSES.get(sec)
    if not kws:
        return ""
    try:
        from .projects_db import _conn as _mc
        conn = _mc()
        # category='受限空间' 直接命中全文化条款(8.4.7清点/8.4.9储罐等行为细则不含关键词)
        rows = conn.execute(
            "SELECT require, std_code, clause FROM management_rule WHERE category='受限空间' OR " +
            " OR ".join(["require LIKE ?"] * len(kws)), tuple(f"%{k}%" for k in kws)).fetchall()
        conn.close()
    except Exception:
        return ""

    import re as _re

    def _score(r):
        require, std, clause = r[0] or "", r[1] or "", r[2] or ""
        s = sum(1 for k in kws if k in require) * 10       # 核心词命中数
        m = _re.search(r"(\d{4})", std)                     # 标准年份越新越优先
        s += int(m.group(1)) // 100 if m else 0
        s += min(len(clause), 12)                           # 条款号精细度(长=细分条款)
        if _re.search(r"\d\.\d+\.\d+[a-z]", clause):        # 字母子项(8.4.2a) = 最细颗粒, 强制保留
            s += 50
        return -s                                           # 升序排 → 取负

    rows = sorted(rows, key=_score)[:24]
    lines = [f"- [{r[1]} {r[2]}] {r[0][:90]}" for r in rows if r and r[0]]
    # 条款展开指令: 逐条覆盖 (颗粒度通用化 — LLM按条款号逐条展开, 不许挑着写)
    head = "\n【标准依据(数值以此为准, 逐条覆盖, 每条给出条款号引用)】\n"
    return head + "\n".join(lines) if lines else ""


_STYLE_SPEC = None


def _style_spec(sec: str) -> str:
    """逐节写法规范 (从原报告笔法提取, section_style_spec.json)
    返回"本节写法"指令: 正文长度区间 + 表要求 — 让生成层按真实报告形态输出"""
    global _STYLE_SPEC
    if _STYLE_SPEC is None:
        try:
            import json as _j
            from pathlib import Path as _P
            f = _P(__file__).parent / "section_style_spec.json"
            _STYLE_SPEC = _j.loads(f.read_text(encoding="utf-8")).get("sections", {})
        except Exception:
            _STYLE_SPEC = {}
    sp = _STYLE_SPEC.get(sec)
    if not sp:
        return ""

    st = sp.get("style", "")
    lo, hi = sp.get("chars_range", [0, 0])
    ntab, hdr = sp.get("tables", 0), sp.get("table_header") or []
    hdr_s = " | ".join(hdr[:6]) if hdr else ""
    if st == "table_only":
        return (f"\n【本节写法(依真实报告笔法)】表承载型: 正文**只写1句引导语**(如\"本项目X见表X.X-X\"), "
                f"**不展开叙述**; 核心内容由表格承载(对照表结构: {hdr_s})")
    if st == "brief_table":
        return (f"\n【本节写法(依真实报告笔法)】简述+表: 正文{lo}-{hi}字, 简述要点后由表格承载明细"
                f"(对照表结构: {hdr_s})")
    if st == "brief":
        return f"\n【本节写法(依真实报告笔法)】简述型: 正文{lo}-{hi}字, 简明扼要, 不铺陈不重复"
    if st == "medium":
        return f"\n【本节写法(依真实报告笔法)】中篇: 正文{lo}-{hi}字, 分点论述, 每点一句实据"
    if st == "long":
        return f"\n【本节写法(依真实报告笔法)】论述型: 正文{lo}-{hi}字, 完整论述分析"
    if st == "long_table":
        return (f"\n【本节写法(依真实报告笔法)】论述+表: 正文{lo}-{hi}字论述分析, 并用表格汇总"
                f"(对照表结构: {hdr_s})")
    if st == "heading_only":
        return ""
    return ""


def chapter_must_cover(sec: str) -> str:
    """返回章节要素清单 (无则空串) — 生成时拼到信息块尾部
    骨架来自 MUST_COVER_INFO(代码); 数值/限值来自标准库(动态, 单一数据源);
    写法规范来自 section_style_spec.json (原报告笔法)"""
    base = MUST_COVER_INFO.get(sec, "")
    style = _style_spec(sec)
    if not base:
        return style
    return base + style + _std_clauses(sec)
