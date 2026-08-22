"""职业病分类和目录入库 (国卫职健发〔2024〕39号, 2025-08-01实施)

来源: gov.cn 政策库 https://www.gov.cn/zhengce/zhengceku/202412/content_6992843.htm
      正文含完整目录 (无附件), 12大类+子类+135条职业病
版本: 2024版 (2025-08-01实施) 代替 2013版 (国卫疾控发〔2013〕48号, 同日废止)

结构: 大类(一~十二) → 子类（一）（二）→ 条目(1.矽肺 2.煤工尘肺...)
2024版亮点: 新增 十职业性肌肉骨骼疾病(腕管综合征等)→GBZ 336
            十一职业性精神和行为障碍(PTSD等)→GBZ 337

用途: 10.2.5.2 健康效应→可能导致的职业病映射
输出: data/ohs.db → occupational_disease (类别/子类/名称)
用法: python3 -m knowledge.occupational_disease_loader
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

TXT = Path(__file__).resolve().parent.parent / "acquire/raw/gov_docs/occupational_diseases_2024.txt"
SRC = "国卫职健发〔2024〕39号"

DDL = """
CREATE TABLE IF NOT EXISTS occupational_disease (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,       -- 大类: 职业性尘肺病及其他呼吸系统疾病...
  sub      TEXT,                -- 子类: 尘肺病 / 其他呼吸系统疾病...
  name     TEXT NOT NULL,       -- 职业病名称: 矽肺 / 噪声聋...
  note     TEXT,                -- 备注 (限于刮研作业人员等)
  source   TEXT NOT NULL,
  verified INTEGER DEFAULT 0
);
"""

CAT_RE = re.compile(r"^([一二三四五六七八九十]+)、(.+)$")
SUB_RE = re.compile(r"^（([一二三四五六七八九十]+)）(.+)$")
ITEM_RE = re.compile(r"^(\d+)\.(.+)$")


def main():
    lines = TXT.read_text(encoding="utf-8").splitlines()
    conn = connect()
    conn.execute(DDL)
    conn.execute("DELETE FROM occupational_disease WHERE source=?", (SRC,))
    cat, sub = None, None
    n = 0
    for ln in lines:
        ln = ln.strip()
        if not ln or ln == "职业病分类和目录":
            continue
        m = CAT_RE.match(ln)
        if m:
            cat, sub = ln, None
            continue
        m = SUB_RE.match(ln)
        if m:
            sub = ln
            continue
        m = ITEM_RE.match(ln)
        if m and cat:
            name = m.group(2).strip()
            # 分离备注: 名（备注）→ name + note
            note = None
            mm = re.match(r"^(.*?)（(.*?)）$", name)
            if mm:
                name, note = mm.group(1).strip(), mm.group(2).strip()
            conn.execute("""
                INSERT OR REPLACE INTO occupational_disease
                (category, sub, name, note, source, verified)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (cat, sub, name, note, SRC))
            n += 1
    conn.commit()
    print(f"入库 occupational_disease: {n} 条")
    for r in conn.execute(
            "SELECT category, COUNT(*) FROM occupational_disease GROUP BY category"):
        print(f"  {r[0][:16]}...: {r[1]} 条")
    # 抽查
    for nm in ("矽肺", "噪声聋", "职业性化学中毒", "金属烟热"):
        # 金属烟热在十二类, 查名称
        pass
    for r in conn.execute(
            "SELECT category, name, note FROM occupational_disease "
            "WHERE name IN ('矽肺','噪声聋','金属烟热','职业性腕管综合征')"):
        print(f"  抽查: [{r[0][:8]}...] {r[1]} {r[2] or ''}")
    conn.close()


if __name__ == "__main__":
    main()
