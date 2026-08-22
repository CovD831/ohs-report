"""GBZ 188—2025 职业健康监护技术规范 — 危害因素→监护配置入库

来源: acquire/raw/nhc_gbz/pdfs/GBZ_188_2025.pdf (139页, 全文文本层)
状态: 2025-08-20发布 / 2026-08-01实施 (代替GBZ 188—2014, 强制性标准)
⚠️ 实施期 2026-08-01, 早于该日期的报告仍应按 GBZ 188—2014 (时效校验)
结构: 第5章 5.1~5.65 化学因素 (上岗前/在岗期间/离岗时/应急检查)
  每节: 5.x.1.1 目标疾病(禁忌证) / 5.x.1.2 检查内容 / 5.x.2.3 健康检查周期

用途: 10.2.9 职业卫生管理 (危害因素→体检项目/周期 确定性映射)
输出: data/ohs.db → surveillance_rule (因素/节/周期/项目)
用法: python3 -m knowledge.gbz188_loader
"""
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

PDF = Path(__file__).resolve().parent.parent / "acquire/raw/nhc_gbz/pdfs/GBZ_188_2025.pdf"
SRC = "GBZ 188—2025"

DDL = """
CREATE TABLE IF NOT EXISTS surveillance_rule (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  factor       TEXT NOT NULL,     -- 危害因素名 (铅及其无机化合物/苯/甲苯...)
  section      TEXT,              -- 节号 (5.1/5.19...)
  check_type   TEXT NOT NULL,     -- 上岗前 | 在岗期间 | 离岗时 | 应急
  target_disease TEXT,            -- 目标疾病/职业禁忌证
  cycle        TEXT,              -- 健康检查周期 (在岗期间)
  must_items   TEXT,              -- 必检项目
  source       TEXT NOT NULL,
  verified     INTEGER DEFAULT 0
);
"""


def parse_section(text: str, factor: str, section_no: str) -> list[dict]:
    """解析一个 5.x 节: 提取各检查类型的 目标疾病/周期/必检项目"""
    out = []
    # 切分检查类型
    for ctype, pat in [("上岗前", r"(\d+\.1)\s*上岗前职业健康检查"),
                       ("在岗期间", r"(\d+\.2)\s*在岗期间职业健康检查"),
                       ("离岗时", r"(\d+\.3)\s*离岗时职业健康检查"),
                       ("应急", r"(\d+\.4)\s*应急健康检查")]:
        m = re.search(pat, text)
        if not m:
            continue
        # 找该类型段的边界 (下一个检查类型)
        segs = list(re.finditer(r"(\d+\.\d)\s*(上岗前|在岗期间|离岗时|应急)职业?健?康?检?查", text))
        start = m.start()
        end = next((s.start() for s in segs if s.start() > start), len(text))
        seg = text[start:end]
        # 目标疾病
        td = re.search(r"目标疾病[：:]\s*([^\n]+(?:\n[^\n]+){0,3})", seg)
        # 检查周期
        cy = re.search(r"健康检查周期[：:]\s*([^\n]+(?:\n[^\n]+){0,4})", seg)
        # 必检项目
        mi = re.search(r"必检项目[：:为]?\s*([^\n;；]+)", seg)
        out.append({
            "factor": factor, "section": section_no, "check_type": ctype,
            "target_disease": re.sub(r"\s+", " ", td.group(1)) if td else None,
            "cycle": re.sub(r"\s+", " ", cy.group(1)) if cy else None,
            "must_items": re.sub(r"\s+", " ", mi.group(1)) if mi else None,
        })
    return out


def main():
    doc = pymupdf.open(str(PDF))
    # 提取全文 (切片: 第5章 页14~ end of chapter)
    full = []
    for p in range(10, doc.page_count):
        full.append(doc[p].get_text())
    full_text = "\n".join(full)
    doc.close()
    # 找所有 5.x 节
    factors = []
    for m in re.finditer(r"^5\.(\d+)\s*\n(.{2,60})", full_text, re.M):
        no = int(m.group(1))
        if no >= 1:
            factors.append((m.group(0).strip(), "5" + m.group(0).replace("\n", " ")))
    # 用节号定位段落: 简化, 逐节提取
    conn = connect()
    conn.execute(DDL)
    conn.execute("DELETE FROM surveillance_rule WHERE source=?", (SRC,))
    total = 0
    # 定位各节文本区间
    sec_markers = list(re.finditer(r"^5\.(\d+)\s*\n([^\n]{2,60})$", full_text, re.M))
    for i, m in enumerate(sec_markers):
        no = m.group(1)
        name = m.group(2).strip()
        start = m.start()
        end = sec_markers[i + 1].start() if i + 1 < len(sec_markers) else len(full_text)
        if end - start > 20000:
            continue  # 异常长跳过
        seg_text = full_text[start:end]
        rows = parse_section(seg_text, name, f"5.{no}")
        for r in rows:
            conn.execute("""
                INSERT OR REPLACE INTO surveillance_rule
                (factor, section, check_type, target_disease, cycle, must_items, source, verified)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (r["factor"], r["section"], r["check_type"],
                  r["target_disease"], r["cycle"], r["must_items"], SRC))
            total += 1
    conn.commit()
    print(f"入库 surveillance_rule: {total} 条")
    # 抽查
    for f in ("铅及其无机化合物（铅，CAS 号：7439-92-", "苯（CAS 号：71-43-2）", "甲苯（甲苯，CAS 号：108-88-3。二甲苯参"):
        for r in conn.execute(
                "SELECT factor, check_type, cycle, must_items FROM surveillance_rule "
                "WHERE factor LIKE ? LIMIT 2", (f"{f}%",)):
            print(f"  {r[0][:15]} [{r[1]}] {str(r[2])[:30]} | {str(r[3])[:30]}")
    conn.close()


if __name__ == "__main__":
    main()
