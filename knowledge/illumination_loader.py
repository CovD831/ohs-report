"""GB/T 50034—2024 建筑照明设计标准 — 工业建筑照度标准值入库

来源: 华北电力大学镜像官方PDF (zcsys.ncepu.edu.cn/docs/2025-01/d5c197c2837e4892ac7d02faae298500.pdf)
版本: 2024-03-12发布/2024-08-01实施 (现行, 代替2013版)
用途: 10.2.3.7 建筑卫生学-采光照明符合性 (车间照度 vs 标准值)
核心: 表5.4.1 工业建筑照明标准值 (房间/场所→照度标准值→UGR→Uo→Ra)

⚠️ 时效: GB/T 50034-2024 已实施, 报告引用不再用 2013 版
输出: data/ohs.db → illumination_std (场所/参考面/照度/UGR/Uo/Ra/备注)
用法: python3 -m knowledge.illumination_loader
"""
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

PDF = Path(__file__).resolve().parent.parent / "acquire/raw/gov_docs/gbt50034_2024.pdf"
SRC = "GB/T 50034—2024 (表5.4.1)"


def main():
    doc = pymupdf.open(str(PDF))
    conn = connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS illumination_std (
          id     INTEGER PRIMARY KEY AUTOINCREMENT,
          room   TEXT NOT NULL,   -- 房间/场所层级 (涂装车间/总装车间...)
          place  TEXT,            -- 子场所 (输调漆间/生产区...)
          plane  TEXT,            -- 参考平面
          lx     TEXT,            -- 照度标准值(lx)
          ugr   TEXT,            -- 统一眩光值
          uo     TEXT,            -- 照度均匀度
          ra     TEXT,            -- 显色指数
          note   TEXT,            -- 备注 (另加局部照明...)
          source TEXT NOT NULL,
          verified INTEGER DEFAULT 0
        )
    """)
    conn.execute("DELETE FROM illumination_std WHERE source=?", (SRC,))
    # 表5.4.1 在页56-59 (0-indexed) 附近; find_tables 提取全部
    rows_out = []
    for p in range(54, 62):
        tabs = doc[p].find_tables()
        if not tabs.tables:
            continue
        for t in tabs.tables:
            rows = t.extract()
            if not rows:
                continue
            hdr = [str(x).replace("\n", "") if x else "" for x in rows[0]]
            if "照度标" not in "".join(hdr):
                continue
            cur_room = None  # 房间或场所(合并单元格: 涂装车间 跨行)
            for r in rows[1:]:
                room, sub, plane, lx, ugr, uo, ra, note = (
                    [str(x).replace("\n", "").strip() if x else "" for x in r[:8]] + [""] * (8 - len(r)))[:8]
                if room:
                    cur_room = room
                if not cur_room or not lx:
                    continue
                place = sub or plane or cur_room
                rows_out.append({
                    "room": cur_room, "place": place, "plane": plane,
                    "lx": lx, "ugr": ugr, "uo": uo, "ra": ra, "note": note,
                })
    doc.close()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS illumination_std (
          id     INTEGER PRIMARY KEY AUTOINCREMENT,
          room   TEXT NOT NULL,   -- 房间/场所层级 (涂装车间/总装车间...)
          place  TEXT,            -- 子场所 (输调漆间/生产区...)
          plane  TEXT,            -- 参考平面
          lx     TEXT,            -- 照度标准值(lx)
          ugr   TEXT,            -- 统一眩光值
          uo     TEXT,            -- 照度均匀度
          ra     TEXT,            -- 显色指数
          note   TEXT,            -- 备注 (另加局部照明...)
          source TEXT NOT NULL,
          verified INTEGER DEFAULT 0
        )
    """)
    conn.executemany("""
        INSERT INTO illumination_std (room, place, plane, lx, ugr, uo, ra, note, source, verified)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
    """, [(r["room"], r["place"], r["plane"], r["lx"], r["ugr"], r["uo"], r["ra"], r["note"], SRC)
          for r in rows_out])
    conn.commit()
    print(f"入库 illumination_std: {len(rows_out)} 条")
    for r in conn.execute(
            "SELECT room, place, lx FROM illumination_std LIMIT 8"):
        print(f"  {r[0]:12s} | {r[1] or '':14s} | {r[2]} lx")
    conn.close()


if __name__ == "__main__":
    main()
