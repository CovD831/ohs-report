"""职业病危害因素分类目录入库 (国卫疾控发〔2015〕92号)

来源: nhc 官方 https://www.nhc.gov.cn/jkj/c100063/201511/750aa8743bea4c618dda21aade2af32c.shtml
附件: files/1733139304788_94581.docx (docx 直接解析, 6表)
结构: 一 粉尘 | 二 化学因素 | 三 物理因素 | 四 放射性因素 | 五 生物因素 | 六 其他因素
      序号列合并为空 → 用行序号补 (表X行号从1起, 与官方编号一致)

用途: 识别引擎判据 (10.2.5.1) — 危害因素→官方分类 (粉尘/化学/物理/放射性/生物/其他)
输出: data/ohs.db → hazard_factor (分类/名称/CAS/备注)
用法: python3 -m knowledge.hazard_factor_loader
"""
import sys
from pathlib import Path

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

DOC = Path(__file__).resolve().parent.parent / "acquire/raw/gov_docs/hazard_factors_catalog.docx"
SRC = "国卫疾控发〔2015〕92号"

DDL = """
CREATE TABLE IF NOT EXISTS hazard_factor (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  category  TEXT NOT NULL,       -- 粉尘 | 化学因素 | 物理因素 | 放射性因素 | 生物因素 | 其他因素
  seq       INTEGER,             -- 目录序号 (该分类内)
  name      TEXT NOT NULL,       -- 因素名称 (矽尘（游离SiO2含量≥10%）...)
  cas       TEXT,                -- CAS 号
  note      TEXT,                -- 备注 (放射性/生物因素)
  source    TEXT NOT NULL,
  verified  INTEGER DEFAULT 0
);
"""


def main():
    doc = Document(str(DOC))
    # 表格顺序与分类对应 (段落: 一粉尘二化学三物理四放射性五生物六其他)
    cats = ["粉尘", "化学因素", "物理因素", "放射性因素", "生物因素", "其他因素"]
    conn = connect()
    conn.execute(DDL)
    conn.execute("DELETE FROM hazard_factor WHERE source=?", (SRC,))
    total = 0
    for ti, table in enumerate(doc.tables):
        cat = cats[ti] if ti < len(cats) else f"表{ti}"
        seq = 0
        for r in table.rows:
            cells = [c.text.strip() for c in r.cells]
            # 表头行: 序号/名称/CAS
            if cells[0] == "序号" or (len(cells) > 1 and cells[1] == "名    称"):
                continue
            name = cells[1] if len(cells) > 1 else ""
            if not name:
                continue
            seq += 1
            cas = cells[2] if len(cells) > 2 else ""
            note = cells[2] if (len(cells) == 3 and cat in ("放射性因素", "生物因素", "其他因素")) else ""
            conn.execute("""
                INSERT OR REPLACE INTO hazard_factor
                (category, seq, name, cas, note, source, verified)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            """, (cat, seq, name, cas or None, note or None, SRC))
            total += 1
    conn.commit()
    print(f"入库 hazard_factor: {total} 条")
    for r in conn.execute(
            "SELECT category, COUNT(*) FROM hazard_factor GROUP BY category"):
        print(f"  {r[0]}: {r[1]} 条")
    # 抽查
    for r in conn.execute(
            "SELECT category, seq, name, cas FROM hazard_factor "
            "WHERE name IN ('矽尘（游离SiO2含量≥10%）','苯','噪声','布鲁氏菌')"):
        print(f"  抽查: {r[0]} #{r[1]} {r[2]} (CAS {r[3]})")
    conn.close()


if __name__ == "__main__":
    main()
