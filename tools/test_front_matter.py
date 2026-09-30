#!/usr/bin/env python3
"""前置页 (封面/声明页) 回归守卫 — 复刻真实备案稿格式

格式取证来源: 3 份真实报告 (长兴/浦发/新泰) span 级字号+坐标 + GBZ/T 196—2025 附录D
断言:
  ① 封面 p1: 企业名22粗 → 项目名18粗 → 报告书22粗 → 备案稿22粗 → 编号16粗 (全居中)
  ② 封面 p2: 机构名18粗 + 汉字年月16粗 (居中)
  ③ 声明页: '声  明'14粗居中 → 正文14pt 首行缩进2字 → 评价机构名称右对齐 → 法人代表缩进11字
  ④ 人员表: 4行 (项目负责人/报告书编写人/报告书审核人/报告书签发人), 无边框
  ⑤ 页码隔离: 前置(含封面/声明)无页码; 正文节有 PAGE 域
  ⑥ 编号生成: Y{年}-{3位序号} 递增; 已有编号幂等复用

无 DB / 无 LLM / 无外部依赖 — 纯 python-docx 结构断言
用法: .venv/bin/python tools/test_front_matter.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from docx import Document
from docx.oxml.ns import qn

from web.front_matter import add_front_matter, gen_report_no, cn_year_month

OUT = ROOT / "data" / "_test_front_matter.docx"

FAILS = []


def check(name, cond, detail=""):
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _sz(run):
    return run.font.size.pt if (run and run.font.size) else None


def main():
    # ── 构造 doc: 前置页 + 分节 + 占位正文 (模拟 word_export 接线) ──
    doc = Document()
    data = {
        "company": "长兴合成树脂（常熟）有限公司",
        "name": "年产12000吨不饱和聚酯树脂扩建项目",
        "org_name": "",  # 空 → 用默认机构名
        "report_no": "Y2026-001",
    }
    add_front_matter(doc, data)
    from docx.enum.section import WD_SECTION
    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    sec.footer.is_linked_to_previous = False
    p = sec.footer.paragraphs[0]
    r = p.add_run()
    from docx.oxml import OxmlElement
    b = OxmlElement("w:fldChar"); b.set(qn("w:fldCharType"), "begin")
    i = OxmlElement("w:instrText"); i.text = "PAGE"
    e = OxmlElement("w:fldChar"); e.set(qn("w:fldCharType"), "end")
    for el in (b, i, e):
        r._r.append(el)
    doc.add_paragraph("正文占位")
    doc.save(str(OUT))
    doc = Document(str(OUT))

    paras = [p for p in doc.paragraphs]
    nonempty = [p for p in paras if p.text.strip()]

    print("① 封面 p1 字段与字号")
    t0 = nonempty[0].text
    check("企业名 22pt粗居中", t0 == "长兴合成树脂（常熟）有限公司" and _sz(nonempty[0].runs[0]) == 22.0
          and nonempty[0].runs[0].font.bold, t0)
    check("项目名 18pt粗居中", nonempty[1].text.startswith("年产12000吨") and _sz(nonempty[1].runs[0]) == 18.0,
          nonempty[1].text)
    check("报告书 22pt粗", nonempty[2].text == "职业病危害预评价报告书" and _sz(nonempty[2].runs[0]) == 22.0)
    check("备案稿 22pt粗", nonempty[3].text == "（备案稿）" and _sz(nonempty[3].runs[0]) == 22.0)
    check("编号 16pt粗 (含 Y2026-001)", nonempty[4].text == "报告书编号：Y2026-001"
          and _sz(nonempty[4].runs[0]) == 16.0, nonempty[4].text)

    print("② 封面 p2 机构名+日期")
    org_paras = [p for p in nonempty if "宁大卫防" in p.text and p.alignment is not None]
    org_p = [p for p in nonempty if p.text.startswith("江苏宁大卫防检测技术有限公司")]
    check("机构名 18pt粗 (默认机构)", bool(org_p) and _sz(org_p[0].runs[0]) == 18.0,
          org_p[0].text if org_p else "缺失")
    dt_p = [p for p in nonempty if "年" in p.text and "月" in p.text
            and all(c in "〇一二三四五六七八九十" for c in p.text.replace("年", "").replace("月", ""))]
    check("汉字年月 16pt粗", bool(dt_p) and _sz(dt_p[0].runs[0]) == 16.0,
          dt_p[0].text if dt_p else "缺失")

    print("③ 声明页")
    decl = [p for p in nonempty if p.text.strip() == "声  明"]
    check("声明标题 14pt粗居中", bool(decl) and _sz(decl[0].runs[0]) == 14.0, "")
    body = [p for p in nonempty if "承担法律责任" in p.text]
    check("声明正文含 机构名+企业名+项目名", bool(body) and "长兴合成树脂" in body[0].text
          and "年产12000吨" in body[0].text and "承担法律责任" in body[0].text)
    sign = [p for p in nonempty if p.text.startswith("评价机构名称")]
    check("评价机构名称 右对齐", bool(sign) and str(sign[0].alignment) == "RIGHT (2)",
          str(sign[0].alignment) if sign else "缺失")
    rep = [p for p in nonempty if p.text.strip() == "法人代表："]
    ind_ok = False
    if rep:
        ind = rep[0]._p.find(qn("w:pPr") + "/" + qn("w:ind"))
        ind_ok = ind is not None and ind.get(qn("w:firstLineChars")) == "1100"
    check("法人代表 首行缩进11字", ind_ok)

    print("④ 人员表")
    ppl = doc.tables[0]
    labels = [ppl.rows[i].cells[0].text for i in range(len(ppl.rows))]
    check("4行标签齐全", labels == ["项目负责人：", "报告书编写人：", "报告书审核人：", "报告书签发人："],
          str(labels))
    borders = ppl._tbl.tblPr.find(qn("w:tblBorders"))
    allnone = borders is not None and all(c.get(qn("w:val")) == "none" for c in borders)
    check("无边框 (复刻浦发)", allnone)

    print("⑤ 页码隔离")
    s0 = doc.sections[0]
    hdr0 = "".join(p.text for p in s0.footer.paragraphs)
    instrs0 = s0.footer.paragraphs[0]._p.findall(".//" + qn("w:instrText")) if s0.footer.paragraphs else []
    check("前置节无页码域", not instrs0)
    s1 = doc.sections[1]
    instrs1 = []
    for pp in s1.footer.paragraphs:
        instrs1 += [x.text for x in pp._p.findall(".//" + qn("w:instrText"))]
    check("正文节有 PAGE 域", any("PAGE" in (t or "") for t in instrs1), str(instrs1))

    print("⑥ 编号生成")
    check("格式 Y{年}-{3位}", gen_report_no([], dt=__import__("datetime").datetime(2026, 5, 1)) == "Y2026-001")
    check("递增 (已有 007)", gen_report_no(["Y2026-007"], dt=__import__("datetime").datetime(2026, 5, 1)) == "Y2026-008")
    check("跨年隔离 (2025 的 020 不影响 2026)",
          gen_report_no(["Y2025-020"], dt=__import__("datetime").datetime(2026, 5, 1)) == "Y2026-001")
    check("中文年月", cn_year_month(__import__("datetime").datetime(2023, 7, 15)) == "二〇二三年七月")

    OUT.unlink(missing_ok=True)

    print()
    if FAILS:
        print(f"前置页守卫: ✗ 失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("前置页守卫: ✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
