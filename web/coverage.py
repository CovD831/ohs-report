"""材料覆盖度/数据完整度面板 — 表骨架先行的可视化

数据源:
  - web.word_export._SUB_TABLE_MAP: 40 张取证表的骨架名单权威源 (去重 36 唯一表名;
    应急物资清单在 2.1.1/7.1 两次出现, 9.2 经费表无数据源不硬造, map 值为 None)
  - project.data[built_tables]: 导入时规则建好的表 (骨架先行沉淀)

status 判定:
  ready   = built_tables 有该表且 rows 非空, 或该表由标准库/评估链路必产 (检查表类/限值表类)
  pending = 数据驱动表但 built_tables 缺失或行空 (骨架在, 待数据)

用途: GET /api/projects/{pid}/coverage → 前端左栏「数据完整度」卡片
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 数据驱动表: 材料数据没到就不产行 (诚实 pending, 不硬造)
_DATA_DRIVEN = {
    "原辅材料表", "产品产量表", "设备明细表", "建构筑物表", "班制定员表",
    "类比危害分布表", "气象因素表", "项目概况表", "周边环境表",
    "类比工作日写实表", "劳动定员表",
}
# 注: 类比PPE配备表/类比PPE有效性表/PPE配备表 从 ppe_work_matrix 标准库直出 (v37), 必产 → ready


def coverage_report(data: dict) -> dict:
    from web.word_export import _SUB_TABLE_MAP

    built = data.get("built_tables") or {}
    seen, tables = {}, []
    for sub, m in sorted(_SUB_TABLE_MAP.items()):
        if not m:  # 9.2 经费表: 无数据源, 不硬造, 不入面板
            continue
        _, wanted = m
        for name in wanted:
            if name in seen:
                seen[name]["section"] += f",{sub}"
                continue
            row = {"name": name, "section": sub, "rows": 0, "status": "pending"}
            bt = built.get(name)
            if bt and bt.get("rows"):
                row["status"] = "ready"
                row["rows"] = len(bt["rows"])
            elif bt and bt.get("status") == "filling":
                # 外部取数表: 后台联网填充中 (异步, 不阻塞导入)
                row["status"] = "filling"
            elif bt and bt.get("status") == "await_assess":
                # 评估链路表: 骨架已建, 待危害判定后填充
                row["status"] = "await_assess"
            elif name not in _DATA_DRIVEN:
                # 标准库/评估链路必产: 导出时 filler 现算兜底
                row["status"] = "ready"
            seen[name] = row
            tables.append(row)

    ready = sum(1 for t in tables if t["status"] == "ready")
    filling = sum(1 for t in tables if t["status"] == "filling")
    await_assess = sum(1 for t in tables if t["status"] == "await_assess")
    qr = data.get("quality_report") or {}
    return {
        "summary": {
            "ready": ready,
            "filling": filling,           # 后台联网填充中
            "await_assess": await_assess,  # 待评估后填充 (骨架已建)
            "pending": len(tables) - ready - filling - await_assess,
            "total": len(tables),
        },
        "tables": tables,
        "quality": {
            "errors": len(qr.get("errors") or []),
            "warns": len(qr.get("warns") or []),
        },
    }
