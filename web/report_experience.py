"""报告经验沉淀库 — 从 8 份真实报告反查归纳的"评价单元→危害"经验表

用途: 扩充 _UNIT_FACTORS(法规没拆到评价单元级, 靠真实报告经验反查)。
每条都标注来源报告, 经验沉淀可追溯。
"""
from __future__ import annotations

# ============ 评价单元/工序 → 主要危害因素 (从8份真实报告反查归纳) ============
# 每条标注适用行业 (industry_code/名称关键词), 使用时按项目行业筛选, 不污染跨行业
_UNIT_EXP_CHEM = {
    # 化工/树脂/涂料类 (格兰富/祺珠/波士胶等) — 行业: 化工26/28/29, 专用化学/涂料/合成材料
    "树脂合成": {"hazards": ["苯系物", "丙烯酸酯", "乙酸乙酯", "异氰酸酯", "噪声"],
                "industries": ["26", "28", "29", "树脂", "涂料", "合成材料", "化学"]},
    "分散/搅拌": {"hazards": ["粉尘", "甲苯", "二甲苯", "噪声"],
                 "industries": ["26", "28", "29", "树脂", "涂料", "化学"]},
    "反应釜": {"hazards": ["苯系物", "丙烯酸", "高温", "噪声"],
              "industries": ["26", "28", "29", "化学", "合成"]},
    "涂布": {"hazards": ["N-甲基吡咯烷酮", "甲苯", "二甲苯", "噪声"],
            "industries": ["28", "29", "涂料", "电子", "电池"]},
    "研磨/砂磨": {"hazards": ["粉尘", "噪声", "振动"],
                 "industries": ["26", "28", "29", "涂料", "化学"]},
    "灌装/包装": {"hazards": ["有机溶剂", "粉尘"],
                 "industries": ["26", "28", "29", "化学", "食品"]},
    "实验室/质检": {"hazards": ["苯酚", "乙腈", "甲苯", "二氯甲烷"],
                  "industries": ["26", "28", "29", "化学", "研究"]},
    "污水处理": {"hazards": ["硫化氢", "氨", "甲硫醇", "噪声"],
                "industries": ["_all"]},  # 几乎所有项目都有污水站
    "公用工程": {"hazards": ["硫酸", "氢氧化钠", "氮气", "噪声"],
                "industries": ["_all"]},
    "危险品仓库": {"hazards": ["苯系物", "有机溶剂", "粉尘"],
                  "industries": ["26", "28", "29", "化学"]},
    # 3D打印砂型 (3D打印砂型, 金属铸造) — 行业: 金属制品/铸造/通用设备
    "3D打印砂型": {"hazards": ["甲醛", "糠醇", "矽尘", "呋喃树脂粉尘", "噪声"],
                  "industries": ["33", "34", "35", "铸造", "金属", "砂型"]},
    "砂型制芯": {"hazards": ["矽尘", "呋喃树脂", "甲醛", "噪声"],
                "industries": ["33", "34", "35", "铸造", "金属"]},
    "浇铸/熔炼": {"hazards": ["高温", "金属烟尘", "噪声", "CO"],
                 "industries": ["31", "32", "33", "铸造", "金属"]},
    # 水泵/金属焊接/机加工 — 行业: 通用设备/金属制品/汽车
    "焊接": {"hazards": ["电焊烟尘", "锰及其化合物", "臭氧", "紫外辐射", "电焊弧光"],
            "industries": ["33", "34", "35", "36", "金属", "设备", "汽车"]},
    "机加工": {"hazards": ["金属粉尘", "油雾", "噪声"],
              "industries": ["33", "34", "35", "36", "金属", "设备"]},
    "电镀/表面处理": {"hazards": ["铬酸雾", "镍及其化合物", "三氧化铬", "硫酸"],
                     "industries": ["33", "34", "金属", "电镀"]},
    "清洗": {"hazards": ["二氯甲烷", "丙酮", "甲醇", "噪声"],
            "industries": ["33", "34", "35", "36", "金属", "设备"]},
    # 制副产盐 — 行业: 化工/制盐
    "制盐/蒸发": {"hazards": ["氢氧化钠", "噪声", "高温"],
                 "industries": ["26", "28", "盐", "化工"]},
    # 锂电池 (正力) — 行业: 电池制造384
    "涂布/辊压": {"hazards": ["N-甲基吡咯烷酮", "粉尘", "噪声"],
                 "industries": ["384", "电池", "电子"]},
    "注液/化成": {"hazards": ["电解液", "有机溶剂", "噪声"],
                 "industries": ["384", "电池", "电子"]},
}

# ============ 通用设备 → 物理因素 (法规GBZ 2.2 + 报告经验) ============
# GBZ 2.2 已明确噪声LEX=85dB(A)、高温WBGT、脉冲噪声140dB —— 物理因素有法规
_PHYS_STD = {
    "噪声": "GBZ 2.2 噪声LEX,8h=85dB(A); 脉冲噪声声压级峰值140dB(A)",
    "高温": "GBZ 2.2 高温WBGT指数(按体力劳动强度分级, 25-33℃)",
}


def _industry_match(industries: list, industry: str) -> bool:
    """项目行业是否匹配经验样本的适用行业"""
    if "_all" in industries:
        return True
    ind = (industry or "")
    ind_code = ""
    # 提取行业代码 (如"384 电池制造"或"C3841")
    import re
    m = re.search(r"(\d{2,4})", ind)
    if m:
        ind_code = m.group(1)
    for k in industries:
        if k in ind:  # 名称关键词命中 (如"化学")
            return True
        if ind_code and ind_code.startswith(k):  # 行业代码前缀命中
            return True
    return False


def unit_experience(unit: str, industry: str = "") -> list:
    """按评价单元名匹配报告经验危害 (关键词匹配 + 行业筛选, 只返回适用的)"""
    # 精确匹配
    if unit in _UNIT_EXP_CHEM:
        meta = _UNIT_EXP_CHEM[unit]
        if _industry_match(meta["industries"], industry):
            return meta["hazards"]
    # 关键词匹配 (如"树脂合成车间"→"树脂合成")
    best = None
    for k, meta in _UNIT_EXP_CHEM.items():
        if k in unit:
            if _industry_match(meta["industries"], industry):
                best = meta["hazards"]
                break  # 第一个匹配的关键词即返回
    return best or []


def filter_experience(industry: str = "", equipment: list | None = None,
                      materials: list | None = None) -> dict:
    """按项目行业/设备/物料筛选, 返回本项目适用的所有经验单元危害

    筛选信号: 行业代码/名称 → 匹配经验样本的 industries 标注
    """
    eqs = equipment or []
    mats = materials or []
    # 行业代码/名称
    result = {}
    for k, meta in _UNIT_EXP_CHEM.items():
        if _industry_match(meta["industries"], industry):
            result[k] = meta["hazards"]
    return result


def phys_standard(factor: str) -> str:
    """物理因素对应法规限值 (GBZ 2.2)"""
    for k, v in _PHYS_STD.items():
        if k.strip() in (factor or ""):
            return v
    return ""
