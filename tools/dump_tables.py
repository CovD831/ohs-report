"""导出指定表为 SQL (结构 + 数据) — 用于服务器表级同步

用法: python tools/dump_tables.py out.sql ghs_class hazard_toxicity ...
"""
import re
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "ohs.db"


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    out = sys.argv[1]
    tables = set(sys.argv[2:])

    conn = sqlite3.connect(DB)
    n_create = n_insert = 0
    with open(out, "w", encoding="utf-8") as f:
        f.write("PRAGMA foreign_keys=OFF;\nBEGIN;\n")
        for t in sorted(tables):
            f.write(f"DROP TABLE IF EXISTS \"{t}\";\n")
        for line in conn.iterdump():
            s = line.strip()
            # iterdump 格式不一致: CREATE TABLE 表名**无引号**, INSERT INTO 表名**有引号**
            #   CREATE TABLE ghs_class (...)      ← 无引号
            #   INSERT INTO "ghs_class" VALUES... ← 有引号
            m = re.match(
                r'^(?:CREATE TABLE(?:\s+IF NOT EXISTS)?|INSERT INTO)\s+"?([A-Za-z_][A-Za-z0-9_]*)"?',
                s,
            )
            if m and m.group(1) in tables:
                f.write(line + "\n")
                n_create += ("CREATE TABLE" in s)
                n_insert += ("INSERT INTO" in s)
        f.write("COMMIT;\n")
    conn.close()
    print(f"✓ {out}: CREATE={n_create} INSERT={n_insert} 表={sorted(tables)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
