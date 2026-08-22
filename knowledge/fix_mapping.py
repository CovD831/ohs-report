"""映射表 + 物料词典维护 (修正误配 + 补别名 + 补词条)

问题清单 (来自长兴报告比对):
  1. 1,6-己二醇 被切片成 '6-己二醇' 误映射 → 己二醇 (CAS 107-41-5, 另一个物质!)
     GBZ 2.1 表1 无 1,6-己二醇 (CAS 629-11-8), 应无映射
  2. 丙烯酸丁酯 → 丙烯酸正丁酯 (同物质, 别名)
  3. 甲苯二异氰酸酯 → 甲苯-2,4-二异氰酸酯 (TDI)
  4. 碳酸钠: 设备物料无, 补字典词条 (OEL 有 3/6, 报告识别)
  5. 丁酮 → 甲乙酮（2-丁酮）已有 (fuzzy=1, 可提升为 verified)

用法: python3 -m knowledge.fix_mapping
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB = Path(__file__).resolve().parent.parent / "data/ohs.db"


def main():
    conn = sqlite3.connect(str(DB))
    # 1) 修正 1,6-己二醇 误映射: 删除 '6-己二醇' → 己二醇 的模糊词条
    n = conn.execute(
        "DELETE FROM material_dictionary WHERE material='6-己二醇' AND oel_factor='己二醇'"
    ).rowcount
    print(f"1) 删除误映射 '6-己二醇→己二醇': {n} 条")
    # 2) 丙烯酸丁酯 → 丙烯酸正丁酯
    for mat, oel in [("丙烯酸丁酯", "丙烯酸正丁酯"), ("甲苯二异氰酸酯", "甲苯-2,4- 二异氰酸酯 （TDI）")]:
        conn.execute("""
            INSERT OR REPLACE INTO material_dictionary
            (material, oel_factor, cas, fuzzy, source, verified)
            VALUES (?, ?, ?, 1, 'fix_mapping(报告比对别名)', 1)
        """, (mat, oel, None))
        print(f"2) 补词条: {mat} → {oel}")
    # 3) 碳酸钠词条 (OEL 有 3/6)
    conn.execute("""
        INSERT OR REPLACE INTO material_dictionary
        (material, oel_factor, cas, fuzzy, source, verified)
        VALUES ('碳酸钠', '碳酸钠', '497-19-8', 0, 'fix_mapping(报告比对)', 1)
    """)
    print("3) 补词条: 碳酸钠 → 碳酸钠 (CAS 497-19-8, PC-TWA 3/PC-STEL 6)")
    # 4) 丁酮 verified 提升
    conn.execute("""
        UPDATE material_dictionary SET verified=1, fuzzy=0
        WHERE material IN ('丁酮', '2-丁酮') AND oel_factor='甲乙酮（2-丁酮）'
    """)
    print("4) 丁酮/2-丁酮 → 甲乙酮（2-丁酮） 提升 verified")
    conn.commit()
    # 验证
    print("\n=== 验证 ===")
    for r in conn.execute(
            "SELECT material, oel_factor, verified FROM material_dictionary "
            "WHERE material IN ('碳酸钠','丙烯酸丁酯','甲苯二异氰酸酯','丁酮','6-己二醇','1,6-己二醇')"):
        print(f"  {r[0]:12s} → {r[1] if r[1] else '(无映射)'} (v={r[2]})")
    conn.close()


if __name__ == "__main__":
    main()
