"""条件章节规则 — 按项目结构化字段判断是否生成某章节

用户核心设计: 章节要不要生成, 由"提取出的结构化字段"决定, 不硬编码。
判断标准全部经 本地标准库(权威分类) + 网络查证 验证, 不靠单份报告反推。

用法:
  should_generate(section_key, project) -> bool   判断该项目是否生成某条件章节
  接入 _run_generate_all: 生成单元时先判断。
"""

# ============ 条件章节规则表 ============
# key: (判断来源字段, 命中关键词, 说明/依据)
CONDITIONAL_SECTIONS: dict[str, dict] = {
    # 受限空间作业措施 — 设备/工艺含密闭容器/地下空间
    # 依据: GB 30871 受限空间三类(密闭设备/地下空间/地上空间)
    "受限空间作业措施": {
        "fields": ["equipment", "process_text", "materials"],
        "keywords": ["反应釜", "储罐", "塔", "仓", "槽车", "管道", "烟道", "污水池",
                     "井", "坑", "沟", "池", "发酵池", "化粪池", "管廊", "隧道",
                     "储槽", "密闭", "有限空间"],
        "source": "GB 30871 受限空间定义(密闭半密闭设备/地下有限空间/地上有限空间)",
    },
    # 防电离辐射设施 — 设备/工艺含电离辐射源
    # 依据: hazard_factor 放射性因素 8类(密封放射源/X射线装置/加速器/中子/氡/铀...)
    "防电离辐射设施": {
        "fields": ["equipment", "process_text"],
        "keywords": ["X射线", "放射源", "探伤", "加速器", "中子", "辐照", "CT",
                     "γ", "钴-60", "铱-192", "密封源", "放射性", "电离辐射", "同位素"],
        "source": "hazard_factor 放射性因素 8类(本地标准库权威分类)",
    },
    # 现有企业概况 — 改扩建/技改才有旧设备
    # 依据: GBZ 改扩建需评"旧设备和改扩建相互影响"(不能当独立新建)
    "现有企业概况": {
        "fields": ["nature"],
        "natures": ["改扩建", "扩建", "技改"],
        "source": "GBZ 改扩建项目需评价旧设备与改扩建相互影响",
    },
    # 类比企业调查分析 — 通用方法(预评价都要类比); 数据源因类型
    # 依据: 预评价通用方法(8份报告全有); 新建用同行业类比/改扩建用现状+类比
    "类比企业调查": {
        "fields": ["nature"],  # 数据源判断
        "natures": ["新建"],  # 新建→需同行业类比; 改扩建→现状+类比
        "source": "预评价通用方法; 新建用类比/改扩建混现状(已查证)",
    },
}

# ============ 判断函数 ============
def _text_hit(text: str, keywords: list) -> bool:
    t = (text or "").lower()
    for kw in keywords:
        if kw.lower() in t:
            return True
    return False


def should_generate(section_key: str, project: dict) -> bool:
    """判断项目是否生成某条件章节"""
    rule = CONDITIONAL_SECTIONS.get(section_key)
    if not rule:
        return True  # 非条件章节默认生成

    # nature 条件 (现有企业概况/类比)
    if "natures" in rule:
        nature = str(project.get("nature") or "")
        return any(n in nature for n in rule["natures"])

    # 关键词条件 (受限空间/电离辐射): 检查 fields 里的文本
    for f in rule["fields"]:
        vals = project.get(f) or []
        for v in vals:
            if isinstance(v, dict):
                # list[dict]: 拼所有非空值
                txt = " ".join(str(x) for x in v.values() if x)
            else:
                txt = str(v)
            if _text_hit(txt, rule["keywords"]):
                return True
    return False


def generate_conditional_sections(project: dict) -> list[str]:
    """返回该项目所有应生成的条件章节key(供生成循环遍历)"""
    return [k for k in CONDITIONAL_SECTIONS if should_generate(k, project)]


# 条件章节 → 挂到哪个二级小节 (章, 二级) + 标题模板
# 受限空间/电离辐射 是"措施"类, 挂到第X章(措施建议)下; 现有企业概况挂第2章
_CONDITIONAL_UNIT = {
    "受限空间作业措施": ("10", "受限空间作业措施"),
    "防电离辐射设施": ("10", "防电离辐射设施"),
    "现有企业概况": ("2", "现有企业概况"),
}


def conditional_units(project: dict) -> list[tuple]:
    """返回应生成的条件章节unit: [(章, 二级编号(暂作标题key), 标题)]"""
    out = []
    for key in generate_conditional_sections(project):
        ch, title = _CONDITIONAL_UNIT.get(key, ("10", key))
        # 用简短 key 作编号(生成时按标题)
        out.append((ch, key, f"{title}"))
    return out
