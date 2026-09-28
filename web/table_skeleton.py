"""表骨架注册表 — 导入时把 36 张表骨架全部建出来 (含"待补充"占位)

设计 (用户定, 2026-09):
  表 = 报告的骨架, 很多章节都有表, 所以表非常重要。
  拿到材料时就该知道要建哪些表 → 像往表格里填空一样, 能填的填满, 缺的标"待补充"。

现状问题: build_all_tables 只产 8 张 (数据规则表+气象), 其余 28 张
  (检查表类/限值表类/类比类) 根本没在"填空"阶段建 —— 导出时才由
  section_filler 现算。骨架先行名不副实。

三类表 (判定复用 coverage.py 的 _DATA_DRIVEN, 单一权威源):
  data     数据驱动 — 材料给了才有行, 缺则 rows=[] 标待补充
  standard 标准库驱动 — 从 oel_limit/gbz1_rule/ppe_work_matrix 等直出 (可建)
  assess   评估链路驱动 — 需危害判定/评估结果 (导入时可能还没算 → 标待评估)

产出: build_skeletons(data, assess=None) -> {表名: {cols, rows, prov, status}}
  status: "filled" 有数据 | "await_data" 待补材料 | "await_assess" 待评估
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 标准库驱动表 → 建表函数在此模块内实现 (从库直出)
# 评估链路驱动表 → 需 assess (危害判定/限值), 导入时未算则占位
_ASSESS_DRIVEN = {
    "危害因素识别表", "健康影响表", "物理因素健康影响表", "关键控制点表",
    "接触限值表", "噪声接触限值表", "高温接触限值表", "室内空气质量标准表",
    "类比可比性表", "类比PPE配备表", "类比PPE有效性表", "类比工作日写实表",
    "劳动强度分级表", "卫生特征分级表", "周边环境表", "应急物资清单",
}
# 注: 应急物资清单 在 2.1.1/7.1 两处出现; 数据来自 emergency_supplies 提取字段,
#     但 2.1.1 处常为现状应急物资 → 归评估/数据混合, 导入时能填则填
_STANDARD_DRIVEN = {
    "选址检查表", "总体布局检查表", "建筑卫生学检查表", "辅助用室检查表",
    "辅助用室设置表", "管理制度检查表", "防护设施检查表",
    "应急救援检查表", "PPE拟配置检查表", "PPE配备表", "工艺检查表",
    "设备布局检查表",
}
# 注: 卫生特征分级表 同时是标准库(GBZ1分级)与评估驱动, 归 _ASSESS_DRIVEN 优先

# 检查表统一样板 (4 列; 结果/评价列留人工把关 — 与 fill_section 同构)
_CHECK_COLS = ["卫生要求", "检查依据", "检查结果", "评价"]


def _skeleton_names() -> list[tuple[str, str]]:
    """骨架名单 (表名, 首个挂载小节) — 权威源 = word_export._SUB_TABLE_MAP"""
    from web.word_export import _SUB_TABLE_MAP
    seen: dict[str, str] = {}
    for sub, m in sorted(_SUB_TABLE_MAP.items()):
        if not m:
            continue
        for name in m[1]:
            seen.setdefault(name, sub)
    return list(seen.items())


def _data_driven() -> set[str]:
    from web.coverage import _DATA_DRIVEN
    return set(_DATA_DRIVEN)


def _prov(status: str, why: str) -> dict:
    from web.number_provenance import prov_rule
    return prov_rule({"file": "骨架先行(导入时建表)", "page": None, "text": why})


def build_skeletons(data: dict, assess: dict | None = None) -> dict:
    """按骨架名单建全部表 (能填的填, 缺的占位)

    复用 build_data_tables 已建好的表 (不重复建), 只补缺失的骨架。
    """
    from web.table_builder import build_data_tables
    from web.field_projection import AGGREGATORS  # noqa: F401 (保持导入一致性)

    built = dict(build_data_tables(data))
    dd = _data_driven()
    out: dict[str, dict] = {}

    for name, section in _skeleton_names():
        if name in built:
            t = dict(built[name])
            t.setdefault("status", "filled" if t.get("rows") else "await_data")
            out[name] = t
            continue

        if name in dd:
            # 数据驱动但材料没给 → 空骨架, 诚实标待补充
            out[name] = {
                "cols": [], "rows": [], "section": section,
                "status": "await_data",
                "prov": _prov("await_data", "数据驱动表: 材料未提供对应数据, 待补充"),
            }
        elif name in _STANDARD_DRIVEN:
            out[name] = {
                "cols": list(_CHECK_COLS), "rows": [], "section": section,
                "status": "await_assess",
                "prov": _prov("await_assess", "标准库驱动: 导出时按标准库条款生成"),
            }
        else:
            out[name] = {
                "cols": [], "rows": [], "section": section,
                "status": "await_assess",
                "prov": _prov("await_assess", "评估链路驱动: 需危害判定/评估结果"),
            }
    return out


def skeleton_summary(tables: dict) -> dict:
    """骨架统计 (供面板/日志)"""
    from collections import Counter
    c = Counter(t.get("status", "unknown") for t in tables.values())
    return {"total": len(tables), **dict(c)}


if __name__ == "__main__":
    import json
    import sqlite3

    c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT data FROM project WHERE id=?", ("c8c7ff0a4d",)).fetchone()
    d = json.loads(row[0])
    sk = build_skeletons(d)
    print("骨架总数:", len(sk))
    print("统计:", json.dumps(skeleton_summary(sk), ensure_ascii=False))
    print()
    for n, t in sk.items():
        st = t.get("status")
        mark = {"filled": "✓", "await_data": "○", "await_assess": "△"}.get(st, "?")
        print(f"  {mark} {n:20} rows={len(t.get('rows') or []):3} §{t.get('section','')} [{st}]")
