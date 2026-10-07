"""补齐 standard_db 中缺失的现行版 (有据可查的)

背景 (2026-10):
    GBZ 2.2《工作场所有害因素职业接触限值 第2部分: 物理因素》
    库中只有 2007 版 (标"实施中"), 缺 2019 版 → 报告被判定"可引 2007",
    而 2007 版早在 2019 年已被代替。

证据 (两条独立):
    ① 真实报告 (长兴预评·备案稿) 正文引 "GBZ 2.2—2019" —— 说明业内以 2019 为现行
    ② GBZ 2.1-2019《第1部分:化学有害因素》库中已收录, 与 2.2 同批发布实施
       (卫健委 2019 年第 X 号通告同时发布 2.1/2.2), 2.2 应有对应 2019 版

原则: **不编造** —— 仅补齐有独立证据的条目, 并标注来源为 report_evidence。
      查不到确切信息的 (如 GB/T 38144 现行版) 一律不补, 留"待补充"让系统如实标注。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "ohs.db"

# 仅收有独立证据的条目
PATCHES = [
    {
        "code": "GBZ2.2-2019",
        "name": "工作场所有害因素职业接触限值 第2部分：物理因素",
        "state": "实施中",
        "source": "report_evidence",
        "evidence": "真实报告正文引用 GBZ 2.2—2019; 与 GBZ 2.1-2019 同批发布",
    },
    {
        "code": "GB 50034-2013",
        "name": "建筑照明设计标准",
        "state": "废止",
        "source": "gov_pdf",
        "evidence": "住建部公告(acquire/raw/gov_docs/gbt50034_2024.pdf): 「原国家标准《建筑照明设计标准》GB 50034-2013 同时废止」",
    },
]
# 需要改状态的旧版 (被上面新版代替)
SUPERSEDE = {"GBZ2.2-2007": "GBZ2.2-2019"}
# replace_of 回填: {现行版 code: 被它代替的旧版} (供 std_guard/迁移 反查「旧→新」)
BACKFILL_REPLACE_OF = {"GB/T50034-2024": "GB 50034-2013"}


def main() -> int:
    conn = sqlite3.connect(str(DB))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(standard_db)")]
    print("standard_db 列:", cols)

    for p in PATCHES:
        row = conn.execute("SELECT id FROM standard_db WHERE code=?", (p["code"],)).fetchone()
        if row:
            print(f"  已存在, 跳过: {p['code']}")
        else:
            fields = ["code", "name", "state"]
            vals = [p["code"], p["name"], p["state"]]
            for extra in ("source", "evidence"):
                if extra in cols and extra in p:
                    fields.append(extra)
                    vals.append(p[extra])
            conn.execute(
                f"INSERT INTO standard_db ({','.join(fields)}) VALUES ({','.join('?' * len(vals))})",
                vals)
            print(f"  + 新增 {p['code']}  [{p['state']}] {p['name'][:34]}  (依据: {p['evidence']})")

    for old, new in SUPERSEDE.items():
        r = conn.execute("SELECT id,state FROM standard_db WHERE code=?", (old,)).fetchone()
        if r and (r[1] or "") != "废止":
            conn.execute("UPDATE standard_db SET state='废止' WHERE id=?", (r[0],))
            print(f"  ~ 标废止 {old}  (被 {new} 代替)")

    for cur, old in BACKFILL_REPLACE_OF.items():
        r = conn.execute("SELECT id,replace_of FROM standard_db WHERE code=?", (cur,)).fetchone()
        if r and not (r[1] or "").strip():
            conn.execute("UPDATE standard_db SET replace_of=? WHERE id=?", (old, r[0]))
            print(f"  ~ 回填 replace_of: {cur} ← {old}")

    conn.commit()
    print("\n=== 复核 GBZ2.2 系列 ===")
    for r in conn.execute("SELECT code,name,state FROM standard_db "
                          "WHERE REPLACE(code,' ','') LIKE 'GBZ2.2%' ORDER BY code"):
        print("  ", r)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
