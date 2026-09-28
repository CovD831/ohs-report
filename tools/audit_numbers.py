"""真项目跑一遍数字溯源审计 — built_tables 缺失时现场用 build_data_tables 生成"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
from web.number_provenance import audit_tables  # noqa: E402
from web.table_builder import build_data_tables  # noqa: E402

DB = Path("/Users/abaaba/Projects/ohs-report/data/ohs.db")
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

pids = sys.argv[1:] or ["c8c7ff0a4d"]
for pid in pids:
    row = conn.execute("SELECT id, name, data FROM project WHERE id=?", (pid,)).fetchone()
    if not row:
        print(f"[{pid}] 未找到"); continue
    data = json.loads(row["data"])
    bt = data.get("built_tables") or {}
    src = "built_tables"
    if not bt:
        bt = build_data_tables(data)
        src = "现场 build_data_tables"
    print("=" * 72)
    print(f"项目 {row['id']} | {row['name']} | 表来源: {src} | {len(bt)} 张表")
    rep = audit_tables(bt)
    print("SUMMARY:", json.dumps(rep["summary"], ensure_ascii=False))
    for t in rep["tables"]:
        flag = "⚠" if t["unverified"] else "✓"
        print(f"  {flag} {t['name']}: 数字 {t['nums']} / 有据 {t['with_evidence']} / 无据 {t['unverified']}")
    samp = [s for t in rep["tables"] for s in t["unverified_samples"]][:10]
    if samp:
        print("  无据样本:")
        for s in samp:
            print(f"    [{s['table']}] r{s['row']}c{s['col']} = {s['value']!r} (src={s['source']}) ← {s['cell']}")
