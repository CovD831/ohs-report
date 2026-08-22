"""建设项目职业病危害风险分类管理目录解析入库

来源: 国卫办职健发〔2021〕5号 (2021-03-12, 国家卫健委办公厅)
  官网: https://www.gov.cn/zhengce/zhengceku/2021-03/22/content_5594603.htm
  附件: 5594603/files/c007905dde7f4e4aaaa12004648861bb.doc

转换链: .doc → textutil -convert html (保留<table>列) → HTML表格解析

结构: [序号, 行业编码, 类别名称, 严重, 一般]
  大类行: 一/A (无行业码, 跳过)
  子类行: （一）/A01 (无√, 跳过, 作分组)
  数据行: 行业码 + √ 在 严重 或 一般 列
  子序号行: 行业码='—' 名称带括注, 继承同组编码 (如 牲畜饲养（牛、羊）→A031)

用途: 行业(GB/T 4754 4位码) → 职业病危害风险分类 (严重/一般)
输出: data/ohs.db → risk_category
用法: python3 -m knowledge.risk_category_loader
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

DOC = Path(__file__).resolve().parent.parent / "acquire/raw/gov_docs/risk_catalog.doc"
SRC_HASH = "c007905dde7f4e4aaaa12004648861bb"
STD = "国卫办职健发〔2021〕5号"

DDL = """
CREATE TABLE IF NOT EXISTS risk_category (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  industry_code TEXT NOT NULL,     -- GB/T 4754 4位行业码 (A011...)
  industry_name TEXT NOT NULL,     -- 类别名称 (谷物种植/牲畜饲养（牛、羊）...)
  risk_level    TEXT NOT NULL,     -- 严重 | 一般
  source        TEXT NOT NULL,
  verified      INTEGER DEFAULT 0,
  UNIQUE(industry_code, industry_name, source)
);
"""


def convert():
    """.doc → .html (textutil macOS 原生; 保留表格列)"""
    import subprocess
    tmp = Path("/tmp/risk_catalog")
    subprocess.run(["textutil", "-convert", "html", "-output", str(tmp) + ".html", str(DOC)],
                   check=True, capture_output=True)
    return tmp.with_suffix(".html")


def parse(html_path: Path) -> list[dict]:
    import html as html_mod
    content = html_path.read_text(encoding="utf-8")
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.S)
    rows = []
    group_code = None
    for tr in trs:
        cells = [re.sub(r"<[^>]+>", "", c).strip().replace("\u00a0", "")
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 5:
            continue
        seq, code, name, severe, general = cells[0], cells[1], cells[2], cells[3], cells[4]
        if code in ("", "—", "-"):
            code = None
        if not name or name in ("行业编码", "类别名称", "严重", "一般"):
            continue
        # 页脚/附件污染: 名称含通知尾注或非行业名 (附件/国家卫生健康委/主动公开)
        if any(k in name for k in ("附件：", "国家卫生健康委办公厅", "信息公开形式", "2021年3月12日", "PAGE", "MERGE")):
            continue
        # 行业码合法性: 1字母+2~4位数字 (GB/T 4754: A011..F5265 等)
        if code and not re.fullmatch(r"[A-Z]\d{2,4}", code):
            continue
        # 大类/子类行 (A / A01 / 一 / （一）): 无√ 且 编码为字母或2位数字
        if not severe and not general:
            if code and re.fullmatch(r"[A-Z]\d{2}", code):
                group_code = code
            continue
        # 数据行
        if code is None:
            code = group_code  # 子序号行继承
        risk = "严重" if severe else "一般"
        rows.append({
            "industry_code": code,
            "industry_name": html_mod.unescape(name),
            "risk_level": risk,
        })
    return rows


def main():
    html_path = convert()
    rows = parse(html_path)
    print(f"解析 {len(rows)} 条")
    severe = sum(1 for r in rows if r["risk_level"] == "严重")
    print(f"  严重: {severe} 条, 一般: {len(rows)-severe} 条")
    conn = connect()
    conn.execute(DDL)
    conn.execute("DELETE FROM risk_category WHERE source=?", (STD,))
    conn.executemany("""
        INSERT OR REPLACE INTO risk_category
        (industry_code, industry_name, risk_level, source, verified)
        VALUES (:industry_code, :industry_name, :risk_level, :source, 0)
    """, [{**r, "source": STD} for r in rows])
    conn.commit()
    # 抽查
    for q in ("A011", "C39", "C367", "H54"):
        for r in conn.execute(
                "SELECT industry_code, industry_name, risk_level FROM risk_category "
                "WHERE industry_code=? ORDER BY industry_code LIMIT 2", (q,)):
            print(f"  抽查: {r[0]} {r[1]} → {r[2]}")
    conn.close()


if __name__ == "__main__":
    main()
