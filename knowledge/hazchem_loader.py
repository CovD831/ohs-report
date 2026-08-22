"""危险化学品目录 (2015版, 十部门公告2015年第5号) 入库

来源: mem.gov.cn 官方公告附件 (W020240524642146440778.doc, 4052KB)
  https://www.mem.gov.cn/gk/gwgg/xgxywj/wxhxp_228/201503/t20150309_232632.shtml
  (2015-02-27公布, 2015-05-01施行, 代替2002版危险化学品名录+剧毒化学品目录2002版)
转换: .doc → textutil -convert html (保留表格列) → HTML表格解析
结构: 序号/品名/别名/CAS号/备注(剧毒标记等), 2828条
用途: 10.2.3.4 原辅材料分析 (物料→CAS→危险化学品→危害识别)
      与 material_dictionary 联动: 物料词→CAS→危化品目录→危害
输出: data/ohs.db → hazchem_item (序号/品名/别名/CAS/备注/剧毒)
用法: python3 -m knowledge.hazchem_loader
"""
import html as html_mod
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

DOC = Path(__file__).resolve().parent.parent / "acquire/raw/gov_docs/hazchem_2015.doc"
SRC = "危险化学品目录(2015版)十部门公告2015年第5号"

DDL = """
CREATE TABLE IF NOT EXISTS hazchem_item (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  seq       INTEGER,             -- 目录序号 (1~2828)
  name      TEXT NOT NULL,       -- 品名 (氨/苯/甲苯...)
  alias     TEXT,                -- 别名 (液氨；氨气...)
  cas       TEXT,                -- CAS 号
  note      TEXT,                -- 备注 (剧毒/高毒等)
  is_toxic  INTEGER DEFAULT 0,   -- 1=剧毒 (备注含剧毒)
  source    TEXT NOT NULL,
  verified  INTEGER DEFAULT 0
);
"""


def convert() -> Path:
    tmp = Path("/tmp/hazchem")
    subprocess.run(["textutil", "-convert", "html", "-output", str(tmp) + ".html", str(DOC)],
                   check=True, capture_output=True)
    return tmp.with_suffix(".html")


def parse(html_path: Path) -> list[dict]:
    content = html_path.read_text(encoding="utf-8")
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.S)
    rows = []
    for tr in trs:
        cells = [re.sub(r"<[^>]+>", "", c).strip().replace("\u00a0", "")
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 5:
            continue
        seq, name, alias, cas, note = cells[0], cells[1], cells[2], cells[3], cells[4]
        if not name or name == "品名":
            continue
        if not seq.isdigit():
            continue
        rows.append({
            "seq": int(seq), "name": html_mod.unescape(name),
            "alias": html_mod.unescape(alias) or None,
            "cas": html_mod.unescape(cas) or None,
            "note": html_mod.unescape(note) or None,
            "is_toxic": 1 if "剧毒" in note else 0,
        })
    return rows


def main():
    html_path = convert()
    rows = parse(html_path)
    print(f"解析 {len(rows)} 条 (剧毒 {sum(1 for r in rows if r['is_toxic'])} 条)")
    conn = connect()
    conn.execute(DDL)
    conn.execute("DELETE FROM hazchem_item WHERE source=?", (SRC,))
    conn.executemany("""
        INSERT OR REPLACE INTO hazchem_item
        (seq, name, alias, cas, note, is_toxic, source, verified)
        VALUES (:seq, :name, :alias, :cas, :note, :is_toxic, :source, 0)
    """, [{**r, "source": SRC} for r in rows])
    conn.commit()
    for q in ("氨", "苯", "甲苯", "硫化氢", "丙烯酸"):
        for r in conn.execute(
                "SELECT seq, name, cas, note FROM hazchem_item WHERE name=? LIMIT 1", (q,)):
            print(f"  抽查: #{r[0]} {r[1]} CAS={r[2]} {r[3] or ''}")
    conn.close()


if __name__ == "__main__":
    main()
