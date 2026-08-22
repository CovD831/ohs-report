"""Word 报告导出 — 附录D格式 (GBZ/T 196—2025)

格式:
  A4 页面 | 仿宋_GB2312 三号标题/四号正文 | 页边距 3.7/3.5/2.8/2.6
  表格: 宋体五号+网格线 | 章节: 10.2.x 编号+标题
内容: 生成的章节 (paragraph_gen+fill_section+advice_gen) → docx
用法: python3 -m web.word_export <project_id> [output.docx]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
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


def add_toc(doc: Document):
    """目录页 (手动生成: 章节编号+标题, 无页码)"""
    _para(doc, "目  录", HEI, 16, True, WD_ALIGN_PARAGRAPH.CENTER)
    for sec, sk in SECTION_SKELETON.items():
        if sec == "10.2.13":
            continue
        _para(doc, f"{sec}  {sk['title']}", FANGSONG, 13)
    doc.add_page_break()


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

    # 封面标题
    _para(doc, "职业病危害预评价报告书", HEI, 22, True, WD_ALIGN_PARAGRAPH.CENTER)
    _para(doc, "（附录D格式 演示版）", FANGSONG, 14, False, WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_page_break()

    # 目录
    add_toc(doc)

    # 各章节
    for sec, sk in SECTION_SKELETON.items():
        if sec in ("10.2.13",):
            continue
        _para(doc, f"{sec}  {sk['title']}", HEI, 16, True)
        # 段落: 优先 LLM 生成正文, 否则机械模板句
        generated = (section_states or {}).get(sec, {}).get("text", "")
        if generated:
            # LLM 正文 → 分段写入
            for line in generated.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    _para(doc, line, FANGSONG, 14, indent=0.74)
        else:
            paras = gen_paragraphs(conn, sec, assess) if sec != "10.2.11" else None
            if sec == "10.2.11":
                built = fill_10211(conn, assess)
                paras = built["paragraphs"]
            for p in paras:
                _para(doc, p, FANGSONG, 14, indent=0.74)
        # 表格
        tables = fill_section(conn, sec, assess) if sec != "10.2.11" else None
        if sec == "10.2.11":
            tables = built["tables"]
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
            for row in t["rows"][:20]:
                cells = dt.add_row().cells
                for i, v in enumerate(row):
                    if i >= len(cells):
                        break
                    cells[i].text = ""
                    r = cells[i].paragraphs[0].add_run(str(v))
                    _set_font(r, SONG, 10.5)
            doc.add_paragraph()

    conn.close()
    doc.save(str(out_path))
    return out_path


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
