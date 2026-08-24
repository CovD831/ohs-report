"""项目信息结构化字段 schema — 机械提取结果的制度化约束

借鉴 law 项目 PROJECT_INFO_SCHEMA(19字段), 适配本项目实际字段。
方案A: 逻辑分组(不拆文件), 每组一个子schema, 每类对应报告位置。
每个字段: (类型, 描述, 材料来源类别, 是否必填, 子字段列表)
子字段用于字段级完整性校验: '缺文件X的字段Y' 粒度。
"""
from __future__ import annotations


# ============ 字段分组 → 子schema (方案A: 逻辑分组) ============
# 每组 = 该分组下的字段集合 + 该分组进报告的哪个位置 + 判断章节(条件章节)
FIELD_GROUPS: dict[str, dict] = {
    # 基本信息 → 第1章工程分析/概况
    "基本信息": {
        "report_loc": "第1章 建设项目概况(1.1/1.2)", "fields": {
            "name": ("str", "项目名称", "C2项目概况", True, []),
            "industry": ("str", "所属行业", "C2项目概况", True, []),
            "nature": ("str", "项目性质(新改扩)", "C2项目概况", True, []),
            "location": ("str", "建设地点", "C2项目概况", True, []),
            "investment": ("str", "投资总额", "C2项目概况", False, []),
            "capacity": ("str", "生产规模/产能", "C2项目概况", True, []),
            "area": ("str", "占地面积", "C2项目概况/C5总平面图", False, []),
        }},
    # 工程信息 → 第1章工程分析/第8章
    "工程信息": {
        "report_loc": "第1章 工程分析 / 第8章 工程分析附录", "fields": {
            "equipment": ("list", "主要设备", "C6设备清单", True, []),
            "equipment_detail": ("list", "设备明细(名称/规格/数量)", "C6设备清单", False, ["name", "spec", "qty"]),
            "process_text": ("str", "生产工艺流程", "C7生产工艺", True, []),
            "buildings": ("list", "建构筑物(名称/占地/建面/层数)", "C5建构筑物", False, ["name", "area", "floor_area", "floors"]),
            "public_works": ("list", "公辅工程", "C4公辅工程", False, []),
        }},
    # 物料产品 → 第3章工程分析/原辅材料
    "物料产品": {
        "report_loc": "第3章 产品及原辅材料分析(3.4)", "fields": {
            "materials": ("list", "原辅材料(名称/规格/用量/MSDS)", "C8原辅材料", True, ["name", "spec", "usage", "msds"]),
            "products": ("list", "产品及产量", "C9产品产量", False, ["name", "output"]),
        }},
    # 劳动组织 → 第3章 生产制度岗位定员
    "劳动组织": {
        "report_loc": "第3章 生产制度及岗位定员(3.1.5)", "fields": {
            "staffing": ("list", "岗位定员(部门/岗位/人数)", "C10岗位定员", True, ["post", "count", "dept"]),
            "shifts": ("list", "班制定员(工种/各班组/合计)", "C10班制", False, ["system", "b1", "b2", "b3", "b4", "total"]),
        }},
    # 防护措施 → 第6章防护设施/第7章应急/第8章个人防护
    "防护措施": {
        "report_loc": "第6章 防护设施 / 第7章 应急 / 第8章 个人防护", "fields": {
            "protection": ("str", "拟采取防护措施", "C13防护措施", False, []),
            "facilities": ("list", "防护设施配置(岗位/设施/数量)", "C13设施配置", False, ["post", "facility", "count", "remark"]),
            "ppe": ("list", "个人防护用品(名称/岗位/周期)", "C14个人防护用品", False, ["item", "post", "frequency"]),
            "emergency": ("str", "应急救援措施", "C15应急救援", False, []),
        }},
    # 检测监护 → 第5章危害分析/类比/健康监护
    "检测监护": {
        "report_loc": "第5章 危害分析 / 第4章 类比 / 健康监护", "fields": {
            "detections": ("list", "类比检测数据(因素/CTWA)", "C17类比检测", False, ["factor", "ctwa"]),
            "analogous_test": ("list", "类比检测(同detections)", "C17类比检测", False, []),
            "health_check": ("list", "职业健康检查(因素/类别/周期)", "C11/C16", False, []),
        }},
}

# ============ 扁平 schema (字段 → 定义, 供字段级校验用) ============
PROJECT_SCHEMA: dict[str, tuple] = {
    field: defn
    for group in FIELD_GROUPS.values()
    for field, defn in group["fields"].items()
}



# ============ 字段分组别名 (兼容旧引用, 返回字段列表) ============
def group_fields(group_name: str) -> list[str]:
    """返回某分组下的字段列表"""
    return list(FIELD_GROUPS.get(group_name, {}).get("fields", {}).keys())


def field_group(field: str) -> str:
    """字段属于哪个分组"""
    for g, meta in FIELD_GROUPS.items():
        if field in meta["fields"]:
            return g
    return ""


def group_report_loc(group_name: str) -> str:
    """分组进报告的哪个位置"""
    return FIELD_GROUPS.get(group_name, {}).get("report_loc", "")


def field_desc(field: str) -> str:
    s = PROJECT_SCHEMA.get(field)
    return s[1] if s else field


def field_source(field: str) -> str:
    s = PROJECT_SCHEMA.get(field)
    return s[2] if s else ""


def field_required(field: str) -> bool:
    s = PROJECT_SCHEMA.get(field)
    return s[3] if s else False


def field_subfields(field: str) -> list:
    s = PROJECT_SCHEMA.get(field)
    return s[4] if s else []


def validate_project(project: dict) -> dict:
    """项目字段完整性校验 (字段级: 缺文件X的字段Y)

    project: 提取后的项目数据 dict
    返回: {
      "required_missing": [{field, desc, source}],      # 必填字段缺/空
      "partial": [{field, desc, source, empty_subfields}],  # 字段有但子字段空的
      "completeness": 0-100                              # 字段完整度
    }
    """
    required_missing = []
    partial = []
    total, filled = 0, 0
    for field, (typ, desc, source, req, subs) in PROJECT_SCHEMA.items():
        val = project.get(field)
        # 字段是否有值
        has_val = _has_value(val)
        if req:
            total += 1
            if has_val:
                filled += 1
            else:
                required_missing.append({"field": field, "desc": desc, "source": source})
        # 子字段级校验 (字段有值, 但内部子字段空)
        if subs and isinstance(val, list) and val:
            empty_subs = set()
            for item in val[:10]:  # 抽样前10条
                if isinstance(item, dict):
                    for s in subs:
                        if not item.get(s):
                            empty_subs.add(s)
            if empty_subs:
                partial.append({"field": field, "desc": desc, "source": source,
                                "empty_subfields": sorted(empty_subs)})
    completeness = round(filled / total * 100) if total else 0
    return {"required_missing": required_missing, "partial": partial,
            "completeness": completeness}


def _has_value(val) -> bool:
    """判断字段是否有值"""
    if val is None:
        return False
    if isinstance(val, str):
        return bool(val.strip())
    if isinstance(val, (list, dict)):
        return bool(val)
    return bool(val)
