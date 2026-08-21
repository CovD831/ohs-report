"""GBZ 2.1—2019 生物监测指标(BEI) + 生物因素 + 接触水平 解析入库

数据来源: acquire/raw/nhc_gbz/pdfs/GBZ_2.1_2019.pdf
  表3 页35  生物因素职业接触限值 (白僵蚕孢子/枯草杆菌蛋白酶/工业酶) → oel_limit
  表4 页36-38 生物监测指标和职业接触生物限值 (28物质+指标, 序号1-28) → bio_limit
  第1号修改单: 表4 增序号29 TMT(三甲基氯化锡, 尿中500µg/g Cr 血中200µg/L) → bio_limit
  表5 页40  职业接触水平及其分类控制 (0/Ⅰ/Ⅱ/Ⅲ/Ⅳ级) → exposure_level

输出: data/ohs.db → oel_limit(生物因素部分) + bio_limit + exposure_level
用法: python3 -m knowledge.bei_parser
"""
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect, insert  # noqa: E402

PDF = Path(__file__).resolve().parent.parent / "acquire/raw/nhc_gbz/pdfs/GBZ_2.1_2019.pdf"
SRC = "GBZ 2.1—2019"

BIO_DDL = """
CREATE TABLE IF NOT EXISTS bio_limit (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  factor_name  TEXT NOT NULL,     -- 接触的化学有害因素 (中文)
  english_name TEXT,
  indicator    TEXT NOT NULL,     -- 生物监测指标 (中文)
  indicator_en TEXT,
  limit_value  TEXT NOT NULL,     -- 职业接触生物限值 (原文, 含单位折算)
  sample_time  TEXT,              -- 采样时间
  source_standard TEXT NOT NULL,
  verified     INTEGER DEFAULT 0,
  UNIQUE(factor_name, indicator, source_standard)
);
"""

LEVEL_DDL = """
CREATE TABLE IF NOT EXISTS exposure_level (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  level_code   TEXT NOT NULL UNIQUE,   -- 0 | Ⅰ | Ⅱ | Ⅲ | Ⅳ
  range_desc   TEXT NOT NULL,          -- ≤1% OEL 等
  description  TEXT,
  control_note TEXT,                   -- 推荐的控制措施
  source_standard TEXT NOT NULL,
  verified     INTEGER DEFAULT 0
);
"""


def ensure_tables(conn):
    conn.executescript(BIO_DDL)
    conn.executescript(LEVEL_DDL)


def _x(v):
    if v is None:
        return None
    s = str(v).replace("\n", "").strip()
    return s if s not in ("", "―", "—", "-", "－") else None


def parse_beis(conn):
    """表4 BEI: 序号前向填充 + 指标继承 (同一指标多采样行/多限值)"""
    doc = pymupdf.open(str(PDF))
    rows = []
    cur_factor, cur_en, cur_ind, cur_ind_en = None, None, None, None
    n = 0
    for p in range(35, 38):
        tabs = doc[p].find_tables()
        if not tabs.tables:
            continue
        for r in tabs.tables[0].extract():
            seq0 = _x(r[0])
            if seq0 and seq0.startswith("注"):
                continue  # 表尾注解行 (注：Cr，肌酐…)
            seq = seq0
            if seq and str(seq).replace(".", "").isdigit():
                cur_factor, cur_en = _x(r[1]), _x(r[2])
                cur_ind, cur_ind_en = _x(r[3]), _x(r[4])
            indicator = _x(r[3])
            if indicator in ("中文名", "英文名", "生物监测指标"):
                continue  # 表头行 (页首/页中重复表头)
            if indicator:
                cur_ind, cur_ind_en = indicator, _x(r[4])
            if cur_ind and cur_ind.startswith("注"):
                continue
            if not cur_factor or not cur_ind:
                continue
            lv = _x(r[5])
            if not lv:
                print(f"  DEBUG-NULL: page{p+1} row={[str(x)[:20] if x else '' for x in r]}")
            rows.append({
                "factor_name": cur_factor, "english_name": cur_en,
                "indicator": cur_ind, "indicator_en": cur_ind_en,
                "limit_value": lv, "sample_time": _x(r[6]),
            })
            n += 1
    doc.close()
    # 第1号修改单 TMT (表4 原28条, 修改单增29)
    rows.append({
        "factor_name": "三甲基氯化锡", "english_name": "Trimethyltin chloride (TMT)",
        "indicator": "尿中三甲基氯化锡", "indicator_en": "Trimethyltin chloride in urine",
        "limit_value": "500 µg/g Cr", "sample_time": "不做严格限定",
    })
    rows.append({
        "factor_name": "三甲基氯化锡", "english_name": "Trimethyltin chloride (TMT)",
        "indicator": "血中三甲基氯化锡", "indicator_en": "Trimethyltin chloride in blood",
        "limit_value": "200 µg/L", "sample_time": "不做严格限定",
    })
    conn.executemany("""
        INSERT OR REPLACE INTO bio_limit
        (factor_name, english_name, indicator, indicator_en, limit_value,
         sample_time, source_standard, verified)
        VALUES (:factor_name, :english_name, :indicator, :indicator_en,
                :limit_value, :sample_time, :source_standard, :verified)
    """, [{**r, "source_standard": SRC, "verified": 0} for r in rows])
    conn.commit()
    return rows


def parse_biologics(conn):
    """表3 生物因素 → oel_limit (3条, 值带单位)"""
    doc = pymupdf.open(str(PDF))
    t = doc[34].get_text()
    lines = [l.strip() for l in t.split("\n")]
    # 表3 的数据: 序号/名称/英文/CAS/MAC/PC-TWA/PC-STEL/效应/备注 9列布局
    rows = []
    # 简化: 直接硬编码3条 (已从文本提取确认)
    rows = [
        {"factor_name": "白僵蚕孢子", "factor_type": "生物因素", "cas": None,
         "english_name": "Beauveria bassiana", "oel_type": "PC-TWA",
         "value": "6×10⁷", "unit": "孢子数/m3", "conditions": None, "note": "表3"},
        {"factor_name": "枯草杆菌蛋白酶", "factor_type": "生物因素", "cas": None,
         "english_name": "Subtilisins", "oel_type": "PC-TWA",
         "value": "15", "unit": "ng/m3", "conditions": None, "note": "表3; 致敏"},
        {"factor_name": "枯草杆菌蛋白酶", "factor_type": "生物因素", "cas": None,
         "english_name": "Subtilisins", "oel_type": "PC-STEL",
         "value": "30", "unit": "ng/m3", "conditions": None, "note": "表3; 致敏"},
        {"factor_name": "工业酶", "factor_type": "生物因素", "cas": None,
         "english_name": "Industrial enzyme", "oel_type": "PC-TWA",
         "value": "1.5", "unit": "μg/m3", "conditions": None, "note": "表3; 肺功能下降, 致敏"},
        {"factor_name": "工业酶", "factor_type": "生物因素", "cas": None,
         "english_name": "Industrial enzyme", "oel_type": "PC-STEL",
         "value": "3", "unit": "μg/m3", "conditions": None, "note": "表3; 肺功能下降, 致敏"},
    ]
    doc.close()
    insert(conn, [{**r, "source_standard": SRC, "verified": 0} for r in rows])
    return rows


def parse_levels(conn):
    """表5 职业接触水平分类控制 → exposure_level"""
    levels = [
        ("0", "≤1% OEL", "基本无接触", "不需采取行动"),
        ("Ⅰ", "＞1%，≤10% OEL", "接触极低，根据已有信息无相关效应", "一般危害告知，如标签、SDS 等"),
        ("Ⅱ", "＞10%，≤50% OEL", "有接触但无明显健康效应", "一般危害告知，特殊危害告知，即针对具体因素的危害进行告知"),
        ("Ⅲ", "＞50%，≤OEL", "显著接触，需采取行动限制活动", "一般危害告知、特殊危害告知、职业卫生监测、职业健康监护、作业管理"),
        ("Ⅳ", "＞OEL", "超过OELs", "一般危害告知、特殊危害告知、职业卫生监测、职业健康监护、作业管理、个体防护用品和工程、工艺控制"),
    ]
    conn.executemany("""
        INSERT OR REPLACE INTO exposure_level
        (level_code, range_desc, description, control_note, source_standard, verified)
        VALUES (?, ?, ?, ?, ?, 0)
    """, [(c, r, d, n, SRC) for c, r, d, n in levels])
    conn.commit()
    return levels


def main():
    conn = connect()
    ensure_tables(conn)
    conn.execute("DELETE FROM oel_limit WHERE source_standard=? AND factor_type='生物因素'", (SRC,))
    conn.execute("DELETE FROM bio_limit WHERE source_standard=?", (SRC,))
    conn.execute("DELETE FROM exposure_level WHERE source_standard=?", (SRC,))
    bei = parse_beis(conn)
    bio = parse_biologics(conn)
    lv = parse_levels(conn)
    print(f"表4 BEI: {len(bei)} 条 (含修改单 TMT×2)")
    print(f"表3 生物因素: {len(bio)} 条")
    print(f"表5 接触水平: {len(lv)} 档 (0/Ⅰ/Ⅱ/Ⅲ/Ⅳ)")
    # 抽查
    for name in ("苯", "铅及其化合物", "一氧化碳", "三甲基氯化锡"):
        for r in conn.execute(
                "SELECT factor_name, indicator, limit_value, sample_time FROM bio_limit "
                "WHERE factor_name=? ", (name,)):
            print("  BEI:", " | ".join(str(x) for x in r))
    conn.close()


if __name__ == "__main__":
    main()
