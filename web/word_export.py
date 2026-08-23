"""Word 报告导出 — 附录D格式 (GBZ/T 196—2025)

格式:
  A4 页面 | 仿宋_GB2312 三号标题/四号正文 | 页边距 3.7/3.5/2.8/2.6
  表格: 宋体五号+网格线 | 章节: 10.2.x 编号+标题
内容: 生成的章节 (paragraph_gen+fill_section+advice_gen) → docx
用法: python3 -m web.word_export <project_id> [output.docx]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Cm, Pt  # noqa: E402

from knowledge.project_assess import assess_project  # noqa: E402
from knowledge.oel import connect  # noqa: E402
from knowledge.report_skeleton import SECTION_SKELETON  # noqa: E402
from web.paragraph_gen import gen_paragraphs  # noqa: E402
from web.section_filler import fill_section  # noqa: E402
from web.advice_gen import fill_10211  # noqa: E402
from web.projects_db import get_project  # noqa: E402

FANGSONG = "仿宋_GB2312"
SONG = "宋体"
HEI = "黑体"


def _set_font(run, name: str, size: float, bold: bool = False):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    run.font.size = Pt(size)
    run.font.bold = bold


def _para(doc, text: str, font=FANGSONG, size=14, bold=False, align=None, indent=None):
    p = doc.add_paragraph()
    if align:
        p.alignment = align
    if indent:
        p.paragraph_format.first_line_indent = Cm(indent)
    r = p.add_run(text)
    _set_font(r, font, size, bold)
    return p


def _add_field(doc, instr: str):
    """插入 Word 域 (TOC/PAGE) — 打开后按 F9 更新"""
    p = doc.add_paragraph()
    run = p.add_run()
    b = OxmlElement("w:fldChar"); b.set(qn("w:fldCharType"), "begin")
    i = OxmlElement("w:instrText"); i.set(qn("xml:space"), "preserve"); i.text = instr
    s = OxmlElement("w:fldChar"); s.set(qn("w:fldCharType"), "separate")
    t = OxmlElement("w:t"); t.text = "（打开文档后 右键→更新域 生成带页码目录）"
    e = OxmlElement("w:fldChar"); e.set(qn("w:fldCharType"), "end")
    for el in (b, i, s, t, e):
        run._r.append(el)
    return p


def add_toc(doc: Document):
    """目录: Word TOC 域 (自动目录, 1-3级带页码, 打开后更新域生成)"""
    from knowledge.report_skeleton import sub_sections
    _para(doc, "目  录", HEI, 16, True, WD_ALIGN_PARAGRAPH.CENTER)
    # 两级标题手动列出 (打开可见) + TOC域 (F9后带页码)
    for sec, sk in SECTION_SKELETON.items():
        _para(doc, f"{sec}  {sk['title']}", FANGSONG, 13, True)
        for sub, st in sub_sections(sec):
            _para(doc, f"{sub}  {st}", FANGSONG, 12, False, indent=0.74)
    _para(doc, "（自动目录：文档中 引用→插入目录 或 全选按F9，页码自动生成）", FANGSONG, 10.5,
          False, WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_page_break()


def _heading(doc, text: str, level: int = 1):
    """带大纲级别的标题 (Word 导航窗格/自动目录可用)"""
    h = doc.add_heading(text, level=level)
    for r in h.runs:
        _set_font(r, HEI, {1: 16, 2: 14, 3: 13}.get(level, 13), True)
    return h


def add_units_section(doc, units: list[dict]):
    """三级单元 (物质深文等) — 小节内标题+内容"""
    for u in units:
        if u.get("deep"):
            _heading(doc, u["title"], 3)
            for line in u["deep"].split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    _para(doc, line, FANGSONG, 14, indent=0.74)
        else:
            _para(doc, u["text"], FANGSONG, 14, indent=0.74)


def export_docx(project: dict, assess: dict, out_path: Path, section_states: dict | None = None):
    conn = connect()
    doc = Document()
    # 页面 A4 + 边距
    for section in doc.sections:
        section.page_width, section.page_height = Cm(21), Cm(29.7)
        section.left_margin, section.right_margin = Cm(3.7), Cm(2.6)
        section.top_margin, section.bottom_margin = Cm(3.5), Cm(2.8)
    # 默认样式
    style = doc.styles["Normal"]
    style.font.name = FANGSONG
    style._element.rPr.rFonts.set(qn("w:eastAsia"), FANGSONG)
    style.font.size = Pt(14)

    # 页脚页码 (PAGE 域, 每页底部居中)
    for section in doc.sections:
        fp = section.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = fp.add_run()
        b = OxmlElement("w:fldChar"); b.set(qn("w:fldCharType"), "begin")
        i = OxmlElement("w:instrText"); i.set(qn("xml:space"), "preserve"); i.text = "PAGE"
        e = OxmlElement("w:fldChar"); e.set(qn("w:fldCharType"), "end")
        for el in (b, i, e):
            run._r.append(el)
        r = fp.add_run(" / ")
        _set_font(r, FANGSONG, 10.5)
        run2 = fp.add_run()
        b2 = OxmlElement("w:fldChar"); b2.set(qn("w:fldCharType"), "begin")
        i2 = OxmlElement("w:instrText"); i2.set(qn("xml:space"), "preserve"); i2.text = "NUMPAGES"
        e2 = OxmlElement("w:fldChar"); e2.set(qn("w:fldCharType"), "end")
        for el in (b2, i2, e2):
            run2._r.append(el)

    # 封面标题
    _para(doc, "职业病危害预评价报告书", HEI, 22, True, WD_ALIGN_PARAGRAPH.CENTER)
    _para(doc, "（附录D格式 演示版）", FANGSONG, 14, False, WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_page_break()

    # 目录
    add_toc(doc)

    # 各章节 (按 report_struct 新结构: 1-12章 + 二级 + 三级 + 四级)
    from web.report_struct import CHAPTERS, SUBS, SUBS3, SUBS4, _extract_product_units
    from knowledge.report_skeleton import SUB_SECTIONS
    ss = section_states or {}
    # 数据四级 (产品/工段级) 从项目数据提取
    prod4 = _extract_product_units(project or {})
    prod4_by_parent = {}
    for k, t in prod4:
        prod4_by_parent.setdefault(k.rsplit(".", 1)[0], []).append((k, t))
    for sec in CHAPTERS:
        sec_meta = ss.get(sec, {})
        sec_text = sec_meta.get("text", "")
        _heading(doc, f"{sec}  {CHAPTERS[sec]}", 1)
        if sec_text and sec_meta.get("state") == "generated":
            for line in sec_text.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    _para(doc, line, FANGSONG, 14, indent=0.74)
        if not SUBS.get(sec):
            # 无小节的章 (如第6章结论): 章节级表格
            tables = fill_section(conn, sec, assess)
            _write_tables(doc, tables)
            continue
        for sn, st in SUBS.get(sec, []):
            sub_meta = ss.get(sn, {})
            sub_text = sub_meta.get("text", "")
            _heading(doc, f"{sn}  {st}", 2)
            if sub_text and sub_meta.get("state") == "generated":
                for line in sub_text.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        _para(doc, line, FANGSONG, 14, indent=0.74)
            # 三级 + 固定四级
            for sub3, (parent, t3) in SUBS3.items():
                if parent != sn:
                    continue
                sub3_meta = ss.get(sub3, {})
                _heading(doc, f"{sub3}  {t3}", 3)
                if sub3_meta.get("state") == "generated":
                    for line in (sub3_meta.get("text", "") or "").split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            _para(doc, line, FANGSONG, 14, indent=0.74)
                # 固定四级
                for num, t4 in SUBS4.get(sub3, []):
                    _heading(doc, f"{num}  {t4}", 4)
                    m = ss.get(num, {})
                    if m.get("state") == "generated":
                        for line in (m.get("text", "") or "").split("\n"):
                            line = line.strip()
                            if line and not line.startswith("#"):
                                _para(doc, line, FANGSONG, 14, indent=0.74)
            # 数据四级 (产品/工段级): 挂在 8.4.1 / 10.1.1 下的 sub3
            for sub3_name, items in prod4_by_parent.items():
                # sub3_name 形如 8.4.1, 找对应 SUBS3 的 key
                for sub3, (parent, t3) in SUBS3.items():
                    if sub3 != sub3_name:
                        continue
                    _heading(doc, f"{sub3}  {t3}", 3)
                    for num, t4 in items:
                        _heading(doc, f"{num}  {t4}", 4)
    conn.close()
    doc.save(str(out_path))


def _write_tables(doc, tables):
    """写表格"""
    for t in tables:
        if not t["rows"]:
            continue
        dt = doc.add_table(rows=1, cols=len(t["cols"]))
        dt.style = "Table Grid"
        dt.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, c in enumerate(t["cols"]):
            cell = dt.rows[0].cells[i]
            cell.text = ""
            r = cell.paragraphs[0].add_run(c)
            _set_font(r, SONG, 10.5, True)
        for row in t["rows"]:
            cells = dt.add_row().cells
            for i, v in enumerate(row):
                if i >= len(cells):
                    break
                cells[i].text = ""
                r = cells[i].paragraphs[0].add_run(str(v))
                _set_font(r, SONG, 10.5)
        doc.add_paragraph()


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else "demo-cx"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/report_export.docx")
    p = get_project(pid)
    if not p:
        print("项目不存在")
        return
    conn = connect()
    project = {"name": p["name"], "industry": p["data"].get("industry", ""),
               "equipment": p["data"].get("equipment", []),
               "detections": p["data"].get("detections", []),
               "process_text": p["data"].get("process_text", "")}
    assess = assess_project(conn, project)
    conn.close()
    export_docx(project, assess, out)
    print(f"✅ 导出成功: {out} ({out.stat().st_size} 字节)")


if __name__ == "__main__":
    main()
