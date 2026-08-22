"""全量指标比对 — 生成报告 vs 原报告 (字数/表格/数据完善度)

指标:
  1. 字数: 原报告全文 vs 生成报告全文 (段落+表格)
  2. 段落: 数量/主题构成
  3. 表格: 数量/行数 (数据量)
  4. 数据缺漏: 原报告关键数据表 vs 生成表
输出: 完善度% / 字数差 / 缺漏清单
"""
import json
import re
from pathlib import Path

from docx import Document

REPORT_JSON = "/Users/abaaba/Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json"
GEN_DOCX = Path("data/report_demo-cx.docx")


def count_original() -> dict:
    """原报告统计 (extracted JSON: 段落+表格)"""
    d = json.load(open(REPORT_JSON))
    paras = [p.get("text", "") if isinstance(p, dict) else str(p) for p in d["paragraphs"]]
    para_text = "".join(paras)
    table_rows = 0
    table_cells = 0
    for t in d["tables"]:
        rows = t.get("rows", [])
        table_rows += len(rows)
        for r in rows:
            table_cells += sum(len(str(c)) for c in r if c)
    total_chars = len(re.sub(r"\s+", "", para_text + str(table_cells)))
    return {
        "段落数": len(paras), "段落字数": len(re.sub(r"\s+", "", para_text)),
        "表格数": len(d["tables"]), "表格行数": table_rows,
        "总字数(约)": total_chars,
    }


def count_generated() -> dict:
    """生成报告统计 (docx: 段落+表格)"""
    doc = Document(str(GEN_DOCX))
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    para_text = "".join(paras)
    table_rows = 0
    table_cells = 0
    for t in doc.tables:
        table_rows += len(t.rows)
        for r in t.rows:
            for c in r.cells:
                table_cells += len(c.text)
    total_chars = len(re.sub(r"\s+", "", para_text + str(table_cells)))
    return {
        "段落数": len(paras), "段落字数": len(re.sub(r"\s+", "", para_text)),
        "表格数": len(doc.tables), "表格行数": table_rows,
        "总字数(约)": total_chars,
    }


def main():
    old = count_original()
    gen = count_generated()
    print("=" * 66)
    print("全量指标比对: 原报告(长兴) vs 生成报告")
    print("=" * 66)
    for k in ("段落数", "段落字数", "表格数", "表格行数", "总字数(约)"):
        ov, gv = old[k], gen[k]
        pct = f"{gv/ov*100:.1f}%" if ov else "—"
        print(f"  {k:8s}: 原 {ov:6d} | 生成 {gv:6d} | 占比 {pct}")

    # 完善度综合
    avg = sum(gen[k] / old[k] * 100 for k in ("段落数", "表格数", "表格行数")) / 3
    print(f"\n  ★ 内容完善度 (段落/表格/数据行 平均): {avg:.1f}%")
    print(f"  ★ 字数差: 原 {old['总字数(约)']} vs 生成 {gen['总字数(约)']} "
          f"({-old['总字数(约)'] + gen['总字数(约)']:+d})")
    # 缺漏
    print("\n  [分析] 生成报告缺少的 (原报告有):")
    # 原报告表号与内容
    d = json.load(open(REPORT_JSON))
    gaps = []
    for i, t in enumerate(d["tables"]):
        rows = t.get("rows", [])
        hdr = t.get("headers") or []
        first = rows[0] if rows else []
        hint = " ".join(str(x)[:14] for x in (hdr[:4] if hdr else first[:4]))
        if not hint.strip():
            continue
        # 判断内容类别
        if any(k in hint for k in ("设备", "产品", "原辅", "定员", "岗位", "工序", "检测", "危害", "防护", "照度", "雨", "绿化")):
            gaps.append(f"    表{i} [{len(rows)}行]: {hint[:36]}")
    for g in gaps[:14]:
        print(g)


if __name__ == "__main__":
    main()
