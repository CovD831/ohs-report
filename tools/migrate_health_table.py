"""迁移 built_tables 旧表头污染 — 交回现算 builder

背景(2026-10):
  word_export 采用「骨架优先」: built_tables 里已有行则覆盖现算表。
  早期代码用错误表头建过 健康影响表(5.2-1 化学物质), 冻结在 project.data 里:
      旧: 职业病危害因素|主要侵入途径|毒理学资料|对人体健康的主要影响|临界不良健康效应|可能引起的主要职业病
      真: 名称|形态|危害特性|对人体健康的影响|危害程度|可能引起的职业病或职业性病损
  三份真稿(新泰/长兴/浦发)交叉验证: 正确表头含「危害程度」列, 旧表头是错的。
  遂清空旧 cols+rows → 交回 section_filler 现算 builder。

判定特征(**结构化, 非项目 id 写死**):
  built_tables[k].cols 含 '毒理学资料' 或 '临界不良健康效应'
  (这两个字段名在任何现行真稿 5.2-1 中都不存在 → 是旧表头指纹)

CLI:
  python tools/migrate_health_table.py            # 预演
  python tools/migrate_health_table.py --apply    # 写入
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "ohs.db"

# 旧表头指纹(现行真稿中不存在的字段名)
STALE_FIELDS = ("毒理学资料", "临界不良健康效应")
TARGET_KEY = "健康影响表"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    conn = sqlite3.connect(str(DB))
    hits = []
    for pid, data in conn.execute("SELECT id, data FROM project").fetchall():
        if not data:
            continue
        try:
            d = json.loads(data)
        except (ValueError, TypeError):
            continue
        bt = d.get("built_tables") or {}
        t = bt.get(TARGET_KEY)
        if not t:
            continue
        cols = t.get("cols") or []
        if any(f in cols for f in STALE_FIELDS):
            hits.append((pid, d, t, cols, len(t.get("rows") or [])))

    print(f"命中(旧表头指纹) 项目数: {len(hits)}")
    for pid, _d, _t, cols, nrow in hits:
        print(f"  {pid[:12]}  旧 cols={cols}  rows={nrow}")

    if not hits:
        print("无污染，无需迁移。")
        return 0
    if not a.apply:
        print("\n[预演] 加 --apply 执行清理。")
        return 0

    for pid, d, t, _cols, _n in hits:
        t["cols"] = []
        t["rows"] = []
        t["_migrated"] = "cleared stale cols (migrate_health_table.py 2026-10)"
        conn.execute("UPDATE project SET data=? WHERE id=?", (json.dumps(d, ensure_ascii=False), pid))
        print(f"  ✓ 已清空 {pid[:12]}")
    conn.commit()

    # 复检
    left = 0
    for pid, data in conn.execute("SELECT id, data FROM project"):
        if not data:
            continue
        try:
            d = json.loads(data)
        except (ValueError, TypeError):
            continue
        t = (d.get("built_tables") or {}).get(TARGET_KEY)
        if t and any(f in (t.get("cols") or []) for f in STALE_FIELDS):
            left += 1
    print(f"\n复检: 仍污染 {left} 个 (应为 0)")
    conn.close()
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
