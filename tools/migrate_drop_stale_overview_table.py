"""迁移: 清掉 built_tables 里陈旧的「项目概况表」骨架

背景（skill §骨架优先覆盖现算表）:
  word_export._tables_for_sub 规则 = 「built_tables 骨架**有行** → 用骨架, 否则用 fill_section 现算」。
  旧代码在 table_builder 里建过 4 行的「项目概况表」骨架 (列=序号/项目名称/单位/指标/备注)
  并沉淀进 project.data.built_tables → 永久覆盖现算的 28 行新版 (列=…/数量/…)。

修法: 2026-10 已把「项目概况表」唯一定义搬到 section_filler (读 data/tech_econ.json 全 15 大项)。
  本脚本删掉 built_tables 里的旧条目 → 导出自动回退现算。

通用判定（不写死项目 id）: 表名 == 项目概况表 且 (列数 != 5 或 行数 <= 5)。
用法: python tools/migrate_drop_stale_overview_table.py [--apply]
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ohs.db"
NAME = "项目概况表"


def is_stale(tbl: dict) -> bool:
    """旧骨架特征: 5 列但只有 <=5 行 (新版是 28 行); 或列头是旧的「指标」"""
    if not isinstance(tbl, dict):
        return False
    cols = tbl.get("cols") or []
    rows = tbl.get("rows") or []
    if "指标" in cols and "数量" not in cols:
        return True
    if len(rows) <= 5:
        return True
    return False


def main():
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB)
    hits = []
    for pid, data in conn.execute("SELECT id, data FROM project").fetchall():
        try:
            d = json.loads(data)
        except Exception:
            continue
        bt = d.get("built_tables") or {}
        if NAME in bt and is_stale(bt[NAME]):
            n = len(bt[NAME].get("rows") or [])
            hits.append((pid, n))
    if not hits:
        print("✅ 无陈旧「项目概况表」骨架 — 无需处理")
        return
    for pid, n in hits:
        print(f"  {pid}: 待删 项目概况表({n}行)")
    if not apply:
        print(f"\n[DRY-RUN] 待清 {len(hits)} 项; 加 --apply 生效")
        return
    bak = Path(f"/tmp/ohs.db.bak_before_overview_{datetime.now():%H%M%S}")
    shutil.copy2(DB, bak)
    print(f"备份 → {bak}")
    for pid, _ in hits:
        row = conn.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
        d = json.loads(row[0])
        d["built_tables"].pop(NAME, None)
        conn.execute("UPDATE project SET data=? WHERE id=?", (json.dumps(d, ensure_ascii=False), pid))
    conn.commit()
    print(f"✅ 已清 {len(hits)} 项陈旧骨架并落库")


if __name__ == "__main__":
    main()
