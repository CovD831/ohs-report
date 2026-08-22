"""GBZ/T 300.x 检测方法引用链 — 危害因素→检测标准号映射

数据来源: acquire/raw/nhc_gbz/pdfs/GBZ*300*.pdf (105份, 已下载)
提取: 封面页 标准号 + 标题(第NNN部分：XXX)

引用链用途 (报告"检测依据"章节):
  危害因素(糠醛) → GBZ/T 300.100—2018 (标准号) → 方法/采样/分析
  与 oel_limit 联动: 识别出的危害因素 → 检测方法 → 限值判定

输出: data/ohs.db → method_catalog
用法: python3 -m knowledge.method_catalog_loader
"""
import glob
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

PDFS = Path(__file__).resolve().parent.parent / "acquire/raw/nhc_gbz/pdfs"
SRC = "GBZ/T 300.x 系列 (2017/2018)"

DDL = """
CREATE TABLE IF NOT EXISTS method_catalog (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  standard_code TEXT NOT NULL,     -- GBZ/T 300.100—2018
  part_num      INTEGER,           -- 100
  factor        TEXT,              -- 检测对象 (从标题提取: 糠醛和二甲氧基甲烷)
  title_raw     TEXT,              -- 标题原文
  file_name     TEXT,              -- 源 PDF 文件名
  source        TEXT NOT NULL,
  verified      INTEGER DEFAULT 0,
  UNIQUE(standard_code, source)
);
"""


def normalise_code(fname: str) -> str:
    """GBZT_300.100_2018.pdf → GBZ/T 300.100—2018"""
    m = re.match(r"GBZ_?T?_?300\.(\d+)(?:_|\-)(\d{4})", fname, re.I)
    if m:
        return f"GBZ/T 300.{m.group(1)}—{m.group(2)}"
    return Path(fname).stem


def parse_title(text: str) -> tuple[int | None, str]:
    """提取 第NNN部分：检测对象"""
    m = re.search(r"第\s*(\d+)\s*部分\s*[：:]\s*([^\n]+)", text)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, ""


def main():
    rows = []
    for f in sorted(glob.glob(str(PDFS / "GBZ*300*.pdf"))):
        try:
            doc = pymupdf.open(f)
            t = doc[0].get_text()
            doc.close()
        except Exception:
            continue
        code = normalise_code(Path(f).name)
        part, factor = parse_title(t)
        rows.append({
            "standard_code": code, "part_num": part, "factor": factor or None,
            "title_raw": f"第{part}部分：{factor}" if part else "",
            "file_name": Path(f).name, "source": SRC,
        })
    conn = connect()
    conn.execute(DDL)
    conn.execute("DELETE FROM method_catalog WHERE source=?", (SRC,))
    conn.executemany("""
        INSERT OR REPLACE INTO method_catalog
        (standard_code, part_num, factor, title_raw, file_name, source, verified)
        VALUES (:standard_code, :part_num, :factor, :title_raw, :file_name, :source, 0)
    """, rows)
    conn.commit()
    print(f"入库: {len(rows)} 份检测方法标准")
    # 抽查
    for q in (100, 106, 128):
        for r in conn.execute(
                "SELECT standard_code, factor FROM method_catalog WHERE part_num=?", (q,)):
            print(f"  抽查: {r[0]} → {r[1]}")
    # 引用链联动样例: 糠醛在 oel_limit? (表1有糠醛)
    print("--- 联动测试: 糠醛 OEL ---")
    for r in conn.execute(
            "SELECT factor_name, oel_type, value, unit FROM oel_limit WHERE factor_name LIKE '%糠醛%'"):
        print(f"  {r[0]} {r[1]}={r[2]} {r[3]}")
    conn.close()


if __name__ == "__main__":
    main()
