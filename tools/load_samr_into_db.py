"""把 std_samr 采集结果 (acquire/raw/std_samr/standards_status.json) 入库 standard_db

背景 (2026-10): 正文引用的 GB 系列在 standard_db 里大量缺失 (库源 nhc_catalog 只覆盖
WS/GBZ 卫生标准; GB 归市场监管总局管)。缺失 → 校验层只能报"查不到", 无法判定
引用的是否废止版。本脚本把采集结果补进 standard_db, 补齐 GB 覆盖。

用法:
    .venv/bin/python tools/load_samr_into_db.py [--apply]

原则:
    - 以 SAMR 平台返回的 state 为准 (现行/废止 权威源)
    - 同 code 已存在 → **更新 state/name** (不新增重复行)
    - 不删任何行
    - dry-run 打印全部变更
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "ohs.db"
RAW = ROOT / "acquire" / "raw" / "std_samr" / "standards_status.json"


def _norm(code: str) -> str:
    s = (code or "").strip().upper()
    s = re.sub(r"\s", "", s)
    s = re.sub(r"[—–−－]", "-", s)
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not RAW.exists():
        print(f"⚠ 采集结果不存在: {RAW}\n  先跑: .venv/bin/python -c "
              f"\"from acquire.fetchers.std_samr import fetch; fetch()\"")
        return 1
    data = json.loads(RAW.read_text(encoding="utf-8"))

    # 汇总去重: {norm_code: {code,name,state,nature,issue,act}}
    best: dict[str, dict] = {}
    for query, rows in data.items():
        for r in rows or []:
            code = (r.get("code") or "").strip()
            if not code or not code.upper().startswith("GB"):
                continue
            k = _norm(code)
            # 同一 code 多次返回时, 保留第一个 (查询串精确命中的在前)
            best.setdefault(k, {"code": code, "name": r.get("name", ""),
                                "state": r.get("state", ""), "issue": r.get("issue_date", ""),
                                "act": r.get("act_date", "")})
    print(f"采集结果去重后: {len(best)} 个 GB 标准")

    conn = sqlite3.connect(str(DB))
    existing = {}
    for rid, code, name, state in conn.execute("SELECT id,code,name,state FROM standard_db"):
        existing.setdefault(_norm(code), (rid, code, name, state))

    to_insert, to_update = [], []
    for k, it in best.items():
        if k in existing:
            rid, ecode, ename, estate = existing[k]
            if (estate or "") != (it["state"] or "") or (ename or "") != (it["name"] or ""):
                to_update.append((rid, ecode, estate, it))
        else:
            to_insert.append(it)

    print(f"\n=== 新增 {len(to_insert)} 条 ===")
    for it in sorted(to_insert, key=lambda x: x["code"]):
        print(f"  + {it['code']:<24} [{it['state']}] {it['name'][:36]}")
    print(f"\n=== 更新 {len(to_update)} 条 ===")
    for rid, code, old_state, it in sorted(to_update, key=lambda x: x[1]):
        print(f"  ~ {code:<24} {old_state} → {it['state']}   {it['name'][:34]}")

    if not args.apply:
        print("\n[dry-run] 未写库。加 --apply 执行。")
        return 0

    for it in to_insert:
        conn.execute("""INSERT INTO standard_db (code,name,state,pubdate,effdate,source,verified)
                        VALUES (?,?,?,?,?,'samr',1)""",
                     (it["code"], it["name"], it["state"], it["issue"], it["act"]))
    for rid, code, old_state, it in to_update:
        conn.execute("UPDATE standard_db SET state=?, name=? WHERE id=?",
                     (it["state"], it["name"], rid))
    conn.commit()
    print(f"\n✅ 已写库: 新增 {len(to_insert)} / 更新 {len(to_update)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
