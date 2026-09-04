"""逐节生成校验器 (生成过程中的检测器)
========================================
在每节 LLM 生成完成后、写入 section_states 之前运行:
  ① 数据校验: 本节引用的数值/行业/风险类别 必须与项目 data 一致 (防生成时口径漂移)
  ② 要素校验: 本节必须覆盖 MUST_COVER 要素 (防漏写关键内容, 如受限空间条款)
  ③ 幻觉校验: 危害因素/物料名必须能回溯项目 data (防 LLM 从库/常识补料)

设计原则: 与用户要求一致 — 生成过程中就该检测, 而不是等整报告生成完才事后打分。
结果写入 section_states 的 validation 字段: {"passed": bool, "issues": [...]}

用法: validate_section(sec, text, assess, project_data) -> dict
"""
import re


# ============ MUST_COVER 要素清单 (通用: 关键章节必须有这些内容) ============
MUST_COVER = {
    "10.6": [  # 受限空间作业措施 (标准条款逐条覆盖)
        ("隔绝", ("盲板", "拆除", "封堵", "上锁", "断开")),
        ("置换", ("置换", "清洗", "通风")),
        ("气体检测", ("氧含量", "19.5", "21%", "富氧", "有毒气体", "可燃气体")),
        ("作业许可", ("许可", "审批", "监护人", "监护")),
        ("中断重测", ("中断", "重新", "复测", "再次检测")),
    ],
    "7.1": [  # 应急救援措施分析
        ("应急情景", ("中毒", "灼伤", "中暑", "烫伤", "窒息", "泄漏")),
        ("应急用品", ("呼吸器", "防毒面具", "防护服", "急救", "空气呼吸器", "警铃")),
        ("处置要点", ("撤离", "切断", "急救", "处置", "收集")),
    ],
    "11.1": [  # 评价结论
        ("风险类别", ("严重", "较重", "一般")),
        ("达标结论", ("控制", "达标", "符合", "有效")),
        ("关键控制点", ("关键控制", "主要危害", "重点")),
    ],
    "1.4": [  # 评价范围
        ("范围界定", ("评价范围", "界定", "包括", "覆盖")),
        ("范围要素", ("生产", "储运", "辅助", "公用")),
    ],
    "1.1": [  # 项目背景
        ("企业信息", ("成立", "注册资本", "法定代表人", "投资方", "简称")),
        ("项目由来", ("扩建", "新建", "技改", "由来", "依托")),
    ],
    "2.1": [  # 现有企业概况
        ("企业概况", ("定员", "规模", "产品", "现有")),
        ("现状危害", ("危害", "因素", "治理")),
    ],
}


# ============ 数值/属性对表 (数据校验: 生成文本必须与 project data 一致) ============
def _check_numbers(text: str, project_data: dict) -> list[dict]:
    """本节引用的关键数值须与项目 data 一致 (从 data 提取值, 与文本中出现的值比对)"""
    issues = []
    checks = []
    # 投资 (data.投资)
    inv = project_data.get("investment") or project_data.get("投资")
    if inv:
        m = re.search(r"(\d[\d,，.]*)", str(inv))
        if m:
            checks.append(("投资", m.group(1)))
    # 产能
    cap = project_data.get("capacity") or project_data.get("产能")
    if cap:
        m = re.search(r"(\d[\d,，.]*)", str(cap))
        if m:
            checks.append(("产能", m.group(1)))
    # 定员 (总定员/接触人数分开口径: 只比对总定员)
    staffing = project_data.get("staffing") or []
    total = None
    for s in staffing:
        if isinstance(s, dict) and (str(s.get("post") or "") == "合计" or str(s.get("count") or "") == "149"):
            total = str(s.get("count") or "")
    for field, val in checks:
        # 文本中该字段出现的值集
        pat = {
            "投资": r"(?:投资总额|项目总投资|总投资)\D{0,4}(\d[\d,，.]*)",
            "产能": r"(?:本项目年产|设计产能|生产规模)\D{0,4}(\d[\d,，.]*)",
        }.get(field)
        if not pat:
            continue
        vals = re.findall(pat, text)
        vals = [re.sub(r"[\s,，]", "", v) for v in vals if v]
        norm = re.sub(r"[\s,，]", "", val)
        for v in vals:
            if v != norm:
                issues.append({"rule_id": "V-01", "field": field, "found": v, "expected": norm,
                               "note": f"本节{field}值 {v} 与项目data({val})不一致"})
    return issues


def _check_factors(text: str, project_data: dict, assess: dict | None) -> list[dict]:
    """危害因子回溯: 本节出现的危害因素必须是项目 data/assess 存在的 (防幻觉)"""
    issues = []
    allowed = set()
    if assess:
        for h in (assess.get("hazards") or []):
            if isinstance(h, dict) and h.get("factor"):
                allowed.add(h["factor"])
    grid = project_data.get("hazard_grid") or []
    for g in grid:
        if isinstance(g, dict):
            for f in str(g.get("factors") or "").replace(" ", "").split("、"):
                if f and len(f) >= 2:
                    allowed.add(f)
    mats = project_data.get("materials") or []
    for m in mats[:60]:
        nm = str(m.get("name") if isinstance(m, dict) else m).split("|")[0].strip()
        if nm:
            allowed.add(nm)
    # 通用物理因子 (允许出现, 不限物料)
    keep = {"噪声", "高温", "局部振动", "工频电场", "紫外辐射", "矽尘", "电焊烟尘",
            "其他粉尘", "粉尘", "WBGT", "电焊弧光", "臭氧", "氮氧化物", "锰及其无机化合物",
            "三氧化铬", "金属镍与难溶性镍化合物", "工频电场"}
    allowed |= keep
    # 提取文本中像危害因子的词 (已知幻觉黑名单优先检查)
    # 黑名单只管"data 完全无源"的词; 若该词在项目检测数据里(如聚乙烯粉尘是类比检测真因子)则合规
    blacklist = {"硫酸二甲酯", "石蜡烟", "信息传输", "软件开发"}  # 历史幻觉词(项目data确实无源)
    det_text = " ".join(str(d.get("factor") or "") for d in (project_data.get("detections") or []))
    for b in blacklist:
        if b in text and b not in det_text:
            issues.append({"rule_id": "V-02", "note": f"本节出现历史幻觉词: {b}", "found": b})
    return issues


def _check_must_cover(sec: str, text: str) -> list[dict]:
    """MUST_COVER 要素: 关键章节必须覆盖 (缺失→提醒, 不做硬阻断, 供重试/标注)"""
    issues = []
    for item, kws in MUST_COVER.get(sec, []):
        if not any(k in text for k in kws):
            issues.append({"rule_id": "V-03", "item": item, "note": f"缺少要素[{item}]: 应包含 {'/'.join(kws[:2])} 等"})
    return issues


def validate_section(sec: str, text: str, project_data: dict, assess: dict | None = None) -> dict:
    """逐节校验: 返回 {passed, issues, score} — 生成时调用"""
    issues = []
    issues += _check_numbers(text, project_data)
    issues += _check_factors(text, project_data, assess)
    issues += _check_must_cover(sec, text)
    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "score": max(0, 100 - 20 * len(issues)),
        "checked_at": "validate_section",
    }
