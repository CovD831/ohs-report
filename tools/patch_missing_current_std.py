"""补齐 standard_db 中缺失的现行版 (有据可查的)

⚠⚠ 2026-10-16 (#17 结案) —— 本脚本曾是幽灵号 **GBZ 2.2—2019 的唯一产地**:
   PATCHES 里有一条把它灌成"实施中", SUPERSEDE 里写着 {GBZ2.2-2007: GBZ2.2-2019},
   而当时"证据"栏的理由是「真实报告正文引用 GBZ 2.2—2019」。**这个理由不成立** ——
   "别的报告这么印"不是标准存在的证据, 它只说明同一个幻觉在行业里互相抄
   (2026-10-16 实测: 生产存量正文里还有 25 处/16 个项目写着这个号,
    清法见 tools/fix_project_std_text.py 的 FIXUPS)。
   一手核实: NHC 目录快照与 std.samr 均查不到 2.2-2019, 2026-08 在线仍是 2007 版现行
   → 生产侧已删掉那行 (standard_db id=6522) 并把 id=5115 回 state='现行'/verified=1。
   **本批把那条 PATCH 与 SUPERSEDE 一起摘掉**: 留着它, 任何人再跑一次这个脚本
   就把刚做完的两处生产修正原样撤回 (部署窗口里查出的这条回放路径)。
   其余两条 (GB 50034-2013 废止、WS/T 757-2016 现行) 有公告/多源一手, 保留。

原则: **不编造** —— 只补取得到**发布平台/公告**的条目;
      "真实报告正文这么写"、"与某号同批发布所以应有对应版" 这两类推法都不算证据。
      查不到确切信息的 (如 GB/T 38144 现行版) 一律不补, 留"待补充"让系统如实标注。

2026-10-09 追加 WS/T 757-2016 —— 两个独立源核验 (chinesestandard.net: 发布2016-08-29/实施2017-03-01;
食品伙伴网: 废止日期"暂无"), 且 C3 现状评价报告正文 4 处引用, 有据可补。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from knowledge.paths import DB_PATH  # noqa: E402  (库路径单一产地; 本脚本按设计写库, 保持可写连接)

DB = DB_PATH

# 仅收有独立证据的条目 (取得到发布平台/公告的) —— 2.2-2019 那条已摘, 理由见文件头
PATCHES = [
    {
        "code": "GB 50034-2013",
        "name": "建筑照明设计标准",
        "state": "废止",
        "source": "gov_pdf",
        "evidence": "住建部公告(acquire/raw/gov_docs/gbt50034_2024.pdf): 「原国家标准《建筑照明设计标准》GB 50034-2013 同时废止」",
    },
    {
        "code": "WS/T757-2016",
        "name": "局部排风设施控制风速检测与评估技术规范",
        "state": "现行",
        "pubdate": "2016-08-29",
        "effdate": "2017-03-01",
        "source": "confirmed_2026-10(多源核实)",
        "evidence": "chinesestandard.net: 发布2016-08-29/实施2017-03-01; 食品伙伴网: 废止日期暂无(现行); C3现状评价报告正文4处引用(para93/625, table52/68)",
    },
]
# 需要改状态的旧版 (被上面新版代替)。
# 原先这里是 {"GBZ2.2-2007": "GBZ2.2-2019"} —— 那是把**一手现行的 2007 版**标成被一个
# 不存在的号代替, 正是生产里 5115 行 state='废止' 的来源 (#17)。2026-10-16 摘掉后留空表:
# 本机制只在"新版有一手出处"时才允许登记旧→新, 不许凭推法填。
SUPERSEDE = {}
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
            for extra in ("source", "evidence", "pubdate", "effdate"):
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
