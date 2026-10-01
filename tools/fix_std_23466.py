"""A1 修复 v2: 纠正 standard_db 的 replace_of 语义 + 18664 版本

v1 的两处错 (本次自查发现):
  1. replace_of 语义 = 「本版代替了谁」(loader 注释: `代替 (GBZ 188-2014)`)
     v1 给旧版 2009 写了 replace_of='GB 23466-2025' → 反了(旧版不代替新版)
     正解: 新版 2025 的 replace_of='GB/T 23466-2009'
  2. GB/T18664-2002 标'现行' 与真稿不符 — 新泰(2026最新稿)引用 GB 18664-2025

取证依据 (3份真稿):
  新泰 Y2026-007 (最新): 《听力防护装备的选择、使用和维护》(GB 23466-2025)
                         《呼吸防护装备的选择、使用和维护》(GB 18664-2025)
  长兴 (较早稿):         《护听器的选择指南》（GB/T23466-2009）← 旧版, 未换版
  浦发 (最早):           《呼吸防护用品的选择、使用与维护》（GB/T 18664-2002）

用法: .venv/bin/python tools/fix_std_23466.py [--db data/ohs.db] [--apply]
"""
import sqlite3
import sys
import argparse

# (旧号去空格, 新号) — 权威值来自真实稿 + user 取证
SUPERSEDE = {
    "GB/T23466-2009": {
        "state": "废止",
        "replace_of": None,           # 被替代方不写 replace_of
        "new_code": "GB 23466-2025",
        "new_name": "听力防护装备的选择、使用和维护",
    },
    "GB/T18664-2002": {
        "state": "废止",
        "replace_of": None,
        "new_code": "GB 18664-2025",
        "new_name": "呼吸防护装备的选择、使用和维护",
    },
}
NEW_VER = {v["new_code"]: v for v in SUPERSEDE.values()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/ohs.db")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = sqlite3.connect(args.db)
    print("=== before ===")
    for r in c.execute(
        "SELECT id, code, name, state, replace_of FROM standard_db "
        "WHERE code LIKE '%23466%' OR code LIKE '%18664%'"
    ):
        print("  ", r)

    for old_norm, meta in SUPERSEDE.items():
        rows = list(c.execute(
            "SELECT id, code FROM standard_db WHERE REPLACE(code,' ','')=?", (old_norm,)
        ))
        for rid, code in rows:
            # 旧版: 标废止, replace_of 清空(v1 误填了新版号)
            print(f"[修] id={rid} {code} -> state=废止, replace_of=NULL")
            if args.apply:
                c.execute(
                    "UPDATE standard_db SET state=?, replace_of=NULL WHERE id=?",
                    (meta["state"], rid),
                )

        # 新版: 确保存在, replace_of 指向旧版
        new_code = meta["new_code"]
        exists = list(c.execute(
            "SELECT id FROM standard_db WHERE REPLACE(code,' ','')=?",
            (new_code.replace(" ", ""),),
        ))
        if exists:
            print(f"[更] id={exists[0][0]} {new_code} -> replace_of={old_norm}")
            if args.apply:
                c.execute(
                    "UPDATE standard_db SET state='现行', replace_of=?, name=? WHERE id=?",
                    (old_norm, meta["new_name"], exists[0][0]),
                )
        else:
            print(f"[增] {new_code} (replace_of={old_norm})")
            if args.apply:
                c.execute(
                    "INSERT INTO standard_db (code, name, state, replace_of, source, verified) "
                    "VALUES (?,?,?,?,?,0)",
                    (new_code, meta["new_name"], "现行", old_norm, "manual:real-reports"),
                )

    if args.apply:
        c.commit()

    print("\n=== after ===")
    for r in c.execute(
        "SELECT id, code, name, state, replace_of FROM standard_db "
        "WHERE code LIKE '%23466%' OR code LIKE '%18664%'"
    ):
        print("  ", r)

    if not args.apply:
        print("\n(dry-run; 加 --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
