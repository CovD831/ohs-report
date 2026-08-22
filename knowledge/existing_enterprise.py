"""现有企业概况采集与校验 (10.2.2) — 扩建/改建项目

依据: GBZ/T 196—2025 10.2.2 (原文要求要素)
  建厂时间/所属行业/企业规模/职工人数/生产工人数/接触职业病危害因素人数
  职业病危害因素种类/分布/接触水平 现状
  职业卫生组织机构/管理人员/制度/监护/职业病发病处置 现状

用途:
  1) 数据模板 (采集表: 字段+说明+必填项)
  2) 一致性校验 (人数逻辑/行业码规范性/接触人数≥0)
  3) 完整性检查 (缺项标注, 10.2.2 报告'说明现有企业与建设项目相关危害因素现状'
     所需的字段齐全性)

纯数据加工: 无计算规则, 校验=机械自动 + 人工补数据
用法: python3 -m knowledge.existing_enterprise
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 采集模板 (字段名/类型/必填/说明)
FIELDS = [
    ("建厂时间", "date", True, "现有企业建厂年月"),
    ("所属行业", "text", True, "行业码(GB/T 4754, 如 261), 用于行业分类"),
    ("企业规模", "select", True, "大型/中型/小型/微型 (按国家统计局划型)"),
    ("职工人数", "int", True, "总职工人数"),
    ("生产工人数", "int", True, "生产车间工人数 (≤职工人数)"),
    ("接触职业病危害因素人数", "int", True, "接触职业危害人数 (≤生产工人数)"),
    ("职业病危害因素种类", "list", True, "现有项目主要危害因素 (清单, 可复用识别引擎)"),
    ("职业病危害因素分布", "text", True, "各车间/岗位分布"),
    ("岗位接触水平", "float_list", False, "现有检测数据 (检测值/限值, 可接入判定引擎)"),
    ("职业卫生组织机构", "bool", True, "有无职业卫生管理机构"),
    ("管理人员数", "int", False, "专(兼)职职业卫生管理人员"),
    ("管理制度与操作规程", "bool", True, "有无职业卫生管理制度"),
    ("职业健康监护情况", "bool", True, "是否开展职业健康检查"),
    ("职业病发病及病人处置", "text", False, "历年职业病发病情况 (无则'无')"),
    ("现有防护设施", "text", False, "现有防尘防毒降噪设施 (可接protection_rule检查)"),
]


def validate(data: dict) -> list[str]:
    """数据一致性校验 (机械自动)"""
    issues = []
    # 人数逻辑: 接触 ≤ 生产 ≤ 职工
    try:
        total = int(data.get("职工人数", 0) or 0)
        prod = int(data.get("生产工人数", 0) or 0)
        expo = int(data.get("接触职业病危害因素人数", 0) or 0)
        if prod > total:
            issues.append(f"⚠️ 生产工人数({prod}) > 职工人数({total}) — 数据矛盾")
        if expo > prod:
            issues.append(f"⚠️ 接触人数({expo}) > 生产工人数({prod}) — 数据矛盾")
        if expo < 0 or prod < 0 or total < 0:
            issues.append("⚠️ 人数不得为负")
    except (ValueError, TypeError):
        issues.append("⚠️ 人数字段非数字")
    # 行业码: 2-4位数字
    code = str(data.get("所属行业", ""))
    if code and not (code.isdigit() and 2 <= len(code) <= 4):
        issues.append(f"⚠️ 行业码'{code}'非规范(应为GB/T 4754 2-4位数字码)")
    # 必填项
    for f, typ, req, desc in FIELDS:
        if req and (not data.get(f) in (True,) and not data.get(f)):
            issues.append(f"⚠️ 必填缺失: {f} ({desc})")
    return issues


def template() -> dict:
    """采集模板 (带空值, 用户填充)"""
    return {f: (None if typ != "bool" else None) for f, typ, _, _ in FIELDS}


def check_completeness(data: dict) -> tuple[int, int]:
    """完整性: 已填/总必填"""
    req_fields = [f for f, t, r, _ in FIELDS if r]
    filled = sum(1 for f in req_fields if data.get(f) not in (None, "", False))
    return filled, len(req_fields)


if __name__ == "__main__":
    print("=== 现有企业概况 (10.2.2) 采集与校验 ===\n")
    # 演示: 长兴报告 (改扩建项目, 表8 有基本情况)
    import json
    d = json.load(open("/Users/abaaba/Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json"))
    # 表8: 项目基本情况
    demo = {}
    for r in d["tables"][8]["rows"][1:]:
        if r and len(r) >= 2:
            demo[str(r[0]).strip()[:20]] = str(r[1])[:40]
    print("模板字段 (10.2.2):")
    for f, typ, req, desc in FIELDS:
        print(f"  {'*' if req else ' '} {f:16s} <{typ}> — {desc}")
    print("\n=== 长兴报告表8 基本情况 (可映射) ===")
    for k, v in list(demo.items())[:12]:
        print(f"  {k}: {v}")
    # 校验示例
    sample = {"职工人数": 120, "生产工人数": 85, "接触职业病危害因素人数": 63,
              "所属行业": "261", "建厂时间": "2005", "企业规模": "中型",
              "职业卫生组织机构": True, "管理制度与操作规程": True,
              "职业健康监护情况": True, "职业病危害因素种类": ["甲苯", "环己烷"]}
    print("\n=== 校验示例 (长兴-改扩建) ===")
    issues = validate(sample)
    filled, total = check_completeness(sample)
    if not issues:
        print("  无问题 ✓")
    else:
        for i in issues:
            print("  " + i)
    print(f"  完整性: {filled}/{total} 必填项")
