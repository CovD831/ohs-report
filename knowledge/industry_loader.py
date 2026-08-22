"""GB/T 4754—2017 国民经济行业分类全文入库 (含第1号修改单修订)

来源: 国家统计局官方PDF (2025-01上传, 按第1号修改单修订版)
  https://www.stats.gov.cn/sj/tjbz/gmjjhyfl/202501/P020250116506795831658.pdf
版本: GB/T 4754—2017 (2017-06-30发布/2017-10-01实施) + 2023年第1号修改单
      代替 GB/T 4754—2011 (2002/2011废止; 2026-08 std.samr仍标现行)
用途: 10.2.3.1 工程概况 (项目行业归类: 门类→大类→中类→小类全层级)
      与 risk_category(2021-5号文, 用中类码) 联动
输出: data/ohs.db → industry_class (level/代码/名称/说明)
用法: python3 -m knowledge.industry_loader
"""
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

PDF = Path(__file__).resolve().parent.parent / "acquire/raw/gov_docs/gbt4754_2017_modified.pdf"
SRC = "GB/T 4754—2017 (含第1号修改单修订, 国家统计局官方版)"

DDL = """
CREATE TABLE IF NOT EXISTS industry_class (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  level    TEXT NOT NULL,     -- 门类 | 大类 | 中类 | 小类
  code     TEXT NOT NULL,     -- A / 01 / 011 / 0111
  name     TEXT NOT NULL,     -- 类别名称
  note     TEXT,              -- 说明 (指以收获籽实为主的农作物...)
  source   TEXT NOT NULL,
  verified INTEGER DEFAULT 0
);
"""


def parse_pdf() -> list[dict]:
    doc = pymupdf.open(str(PDF))
    text = ""
    for p in range(9, doc.page_count):  # 表1 从页10 (0-indexed 9) 开始
        text += doc[p].get_text() + "\n"
    doc.close()
    rows = []
    lines = text.split("\n")
    i = 0
    LEVEL_PAT = {
        "门类": r"[A-Z]",
        "大类": r"\d{2}",
        "中类": r"\d{3}",
        "小类": r"\d{4}",
    }
    while i < len(lines):
        ln = lines[i].strip()
        # 跳过跨页重复表头: "12 | 代 | 码 | 类别名称..." (行内含表头词)
        if ln in ("代", "码", "类别名称", "说", "明") or (
                len(ln) <= 6 and any(w in ln for w in ("代", "码", "类别", "名称", "说", "明"))):
            i += 1
            continue
        if not ln:
            i += 1
            continue
        level = None
        if re.fullmatch(r"[A-Z]", ln):
            level = "门类"
        elif re.fullmatch(r"\d{2}", ln) and int(ln) <= 99:
            level = "大类"
        elif re.fullmatch(r"\d{3}", ln):
            level = "中类"
        elif re.fullmatch(r"\d{4}", ln):
            level = "小类"
        if level is None:
            i += 1
            continue
        # 名称 = 下一个非空行; 说明 = 再下一个非空行(仅当它不是代码且以'指'或'本门类'或长句)
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            i += 1
            continue
        name = lines[j].strip()
        # 名称过滤: 不能是代码(避免错位) 不能是表头词
        if (re.fullmatch(r"[A-Z]|\d{2,4}", name)
                or name in ("代", "码", "类别名称", "代码", "说", "明", "说明")):
            i = j + 1
            continue
        # 说明: 下一个非空行
        k = j + 1
        while k < len(lines) and not lines[k].strip():
            k += 1
        note = None
        if k < len(lines):
            cand = lines[k].strip()
            # 说明特征: 以'指'/'本门类'开头 或 含'指'且长度>10, 且不是代码/名称
            if (not re.fullmatch(r"[A-Z]|\d{2,4}", cand)
                    and len(cand) > 10
                    and (cand[0] in "指本" or "指" in cand[:8])):
                note = cand
        rows.append({"level": level, "code": ln, "name": name, "note": note})
        i = j + 1
    return rows


def main():
    rows = parse_pdf()
    conn = connect()
    conn.execute(DDL)
    conn.execute("DELETE FROM industry_class WHERE source=?", (SRC,))
    conn.executemany("""
        INSERT OR REPLACE INTO industry_class
        (level, code, name, note, source, verified)
        VALUES (?, ?, ?, ?, ?, 0)
    """, [(r["level"], r["code"], r["name"], r["note"], SRC) for r in rows])
    conn.commit()
    print(f"入库 industry_class: {len(rows)} 条")
    for r in conn.execute("SELECT level, COUNT(*) FROM industry_class GROUP BY level"):
        print(f"  {r[0]}: {r[1]} 条")
    # 抽查
    for q in ("A", "01", "011", "0111", "C26", "C261"):
        r = conn.execute(
            "SELECT code, name FROM industry_class WHERE code=? LIMIT 1", (q,)).fetchone()
        print(f"  抽查: {q} → {r[1] if r else '未找到'}")
    conn.close()


if __name__ == "__main__":
    main()
