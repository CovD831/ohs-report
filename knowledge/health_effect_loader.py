"""危害因素健康效应表 — GBZ 2.1—2019 表1 临界不良健康效应提取入库

来源: acquire/raw/nhc_gbz/pdfs/GBZ_2.1_2019.pdf 表1 (页7-31)
  列: 序号/中文名/英文名/CAS/OELs(MAC/PC-TWA/PC-STEL)/临界不良健康效应/备注
  临界不良健康效应: 甲状腺效应；恶心 (安妥) | 白血病 (苯) | 神经衰弱 (甲苯等)
用途: 10.2.5.2 健康效应分析 (每危害→临界不良健康效应→可能职业病映射)

输出: data/ohs.db → health_effect (factor/english/cas/effect/route/note)
  注: 临界不良健康效应 = 设定该限值所依据的健康损害 (GBZ 2.1 3.x 定义)
用法: python3 -m knowledge.health_effect_loader
"""
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

PDF = Path(__file__).resolve().parent.parent / "acquire/raw/nhc_gbz/pdfs/GBZ_2.1_2019.pdf"
SRC = "GBZ 2.1—2019 表1"


def main():
    doc = pymupdf.open(str(PDF))
    conn = connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_effect (
          id      INTEGER PRIMARY KEY AUTOINCREMENT,
          factor  TEXT NOT NULL,   -- 中文名 (苯)
          english TEXT,            -- 英文名 (Benzene)
          cas     TEXT,            -- CAS 71-43-2
          effect  TEXT NOT NULL,   -- 临界不良健康效应 (白血病;再生障碍性贫血...)
          route   TEXT,            -- 侵入途径 (吸入/皮/经口) — 从备注(皮)推断
          note    TEXT,            -- 备注 (皮/G1/G2B/敏) 原备注列
          source  TEXT NOT NULL,
          verified INTEGER DEFAULT 0
        )
    """)
    conn.execute("DELETE FROM health_effect WHERE source=?", (SRC,))
    n = 0
    # 表1 从页7(0-idx 6) 到页31(0-idx 30), 跨页提取
    for p in range(6, 31):
        tabs = doc[p].find_tables()
        if not tabs.tables:
            continue
        for t in tabs.tables:
            rows = t.extract()
            if not rows:
                continue
            # 找表头 (含'临界不良健康效应')
            hdr_idx = None
            for i, r in enumerate(rows[:4]):
                if any("临界" in str(x) for x in r if x):
                    hdr_idx = i
                    break
            if hdr_idx is None:
                continue
            for r in rows[hdr_idx + 1:]:
                cells = [str(x).replace("\n", "").strip() if x else "" for x in r]
                if len(cells) < 9:
                    continue
                name = cells[1]
                seq = cells[0].strip()
                # 序号列应是数字; (数据行判定)
                if not seq or not seq.isdigit():
                    continue
                factor, english, cas = cells[1], cells[2], cells[3]
                # 9列: [序号,中文名,英文名,CAS,MAC,PC-TWA,PC-STEL,临界不良健康效应,备注]
                effect = cells[7] if len(cells) >= 8 else ""
                note = cells[8] if len(cells) >= 9 else ""
                if effect in ("―", "-", "—", ""):
                    continue
                route = "经皮" if note and "皮" in note else None
                conn.execute("""
                    INSERT INTO health_effect (factor, english, cas, effect, route, note, source, verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """, (factor, english, cas, effect, route, note, SRC))
                n += 1
    conn.commit()
    print(f"入库 health_effect: {n} 条")
    # 抽查
    for q in ("苯", "甲苯", "甲醛", "铅", "环己烷"):
        r = conn.execute(
            "SELECT factor, effect, route FROM health_effect WHERE factor=? LIMIT 1", (q,)).fetchone()
        print(f"  抽查: {r[0] if r else q} → {str(r[1])[:30] if r else '无'}")
    conn.close()


if __name__ == "__main__":
    main()
