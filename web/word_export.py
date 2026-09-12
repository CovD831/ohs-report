"""Word 报告导出 — 附录D格式 (GBZ/T 196—2025)

格式:
  A4 页面 | 仿宋_GB2312 三号标题/四号正文 | 页边距 3.7/3.5/2.8/2.6
  表格: 宋体五号+网格线 | 章节: 10.2.x 编号+标题
内容: 生成的章节 (paragraph_gen+fill_section+advice_gen) → docx
用法: python3 -m web.word_export <project_id> [output.docx]
"""
import json
import re
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
from web.report_struct import CHAPTERS, SUBS, SUBS3, SUBS4  # noqa: E402
from web.paragraph_gen import gen_paragraphs  # noqa: E402
from web.section_filler import fill_section  # noqa: E402
from web.advice_gen import fill_10211  # noqa: E402
from web.projects_db import get_project  # noqa: E402

# 二级小节 → 内嵌哪些表 (复用 fill_section 各章表格, 按小节筛)
# 值 = (该小节所属的fill_section章, 要加的表格名列表; 空=全加该章)
# 2026备案稿11章结构: 表随正文走, 工程分析(3)/类比(4)/危害(5)/防护(6)/应急(7)/PPE(8)/管理(9)
_SUB_TABLE_MAP = {
    "1.3": ("1", ["评价依据表"]),
    "2.1": ("2", ["产品产量表", "建构筑物表"]),
    "2.3": ("6", ["防尘防毒设施检查表", "防噪声振动检查表", "防暑防寒检查表", "防护设施检查表"]),
    "2.4": ("8", ["PPE配备表"]),
    "2.5": ("3", ["辅助用室设置表"]),
    "3.1": ("3", ["主要设备清单", "劳动定员表", "建构筑物表", "产品产量表", "设备明细表", "班制定员表", "项目概况表"]),
    "3.2": ("3", ["选址检查表"]),
    "3.3": ("3", ["总体布局检查表"]),
    "3.4": ("3", ["原辅材料表", "产品产量表"]),
    "3.5": ("3", ["主要设备清单"]),
    "3.6": ("3", ["设备明细表", "主要设备清单"]),
    "3.7": ("3", ["建筑卫生学检查表", "照度标准表", "噪声分级表"]),
    "3.8": ("3", ["辅助用室检查表", "辅助用室设置表"]),
    "4.1": ("4", ["类比可比性表"]),
    "4.2": ("4", ["类比可比性表"]),
    "4.4": ("4", ["检测结果表(化学毒物)", "检测结果表(粉尘)", "检测结果表(噪声)", "检测结果表(高温)"]),
    "4.5": ("4", ["职业健康监护表"]),
    "4.6": ("4", ["类比可比性表"]),
    "5.1": ("5", ["危害因素识别表", "工种危害表"]),
    "5.2": ("5", ["健康影响表"]),
    "5.3": ("5", ["接触限值表"]),
    "5.4": ("5", ["判定表", "检测结果表(化学毒物)", "检测结果表(粉尘)", "检测结果表(噪声)", "检测结果表(高温)", "关键控制点表"]),
    "6.1": ("6", ["防尘防毒设施检查表", "防噪声振动检查表", "防暑防寒检查表", "防护设施检查表", "设施配置表"]),
    "6.2": ("6", ["防尘防毒设施检查表", "防噪声振动检查表"]),
    "7.1": ("7", ["应急救援检查表", "应急物资清单"]),
    "7.2": ("7", ["应急救援检查表", "应急物资清单"]),
    "8.1": ("8", ["PPE配备表"]),
    "8.2": ("8", ["PPE配备表"]),
    "9.1": ("9", ["管理制度检查表"]),
    "9.2": ("9", ["管理制度检查表"]),
    "10.2": ("10", ["问题与建议表"]),
    "10.4": ("10", ["管理制度检查表"]),
    "11.1": ("11", ["结论要素表"]),
    # ===== 三级节表映射 (原报告表挂在三级节, 依 section_style_spec 提取) =====
    "2.1.1": ("2", ["产品产量表", "建构筑物表"]),
    "3.1.3": ("3", ["项目概况表"]),
    "3.1.5": ("3", ["班制定员表", "劳动定员表"]),
    "3.1.7": ("3", ["项目概况表"]),
    "3.2.1": ("3", ["选址检查表"]),
    "3.2.2": ("3", ["选址检查表"]),
    "3.3.3": ("3", ["总体布局检查表"]),
    "3.4.1": ("3", ["产品产量表"]),
    "3.4.2": ("3", ["原辅材料表"]),
    "3.5.3": ("3", ["生产工艺检查表", "主要设备清单"]),
    "3.6.1": ("3", ["设备明细表", "主要设备清单"]),
    "3.6.3": ("3", ["设备布局检查表", "主要设备清单"]),
    "3.7.1": ("3", ["建构筑物表", "建筑卫生学检查表"]),
    "3.7.4": ("3", ["建筑卫生学检查表"]),
    "3.8.1": ("3", ["辅助用室设置表"]),
    "3.8.2": ("3", ["辅助用室检查表"]),
    "4.1.1": ("4", ["类比可比性表"]),
    "4.2.1": ("4", ["类比可比性表", "职业健康监护表"]),
    "4.2.2": ("4", ["类比可比性表", "工种危害表"]),
    "4.2.4": ("4", ["类比可比性表", "PPE配备表"]),
    "5.1.1.2": ("5", ["危害因素识别表", "工种危害表"]),
    "5.2.1": ("5", ["健康影响表"]),
    "5.2.2": ("5", ["健康影响表"]),
    "5.4.3": ("5", ["关键控制点表"]),
}


def _tables_for_sub(conn, sec: str, sn: str, assess: dict) -> list[dict]:
    """按小节返回内嵌表格 (复用 fill_section 各章表格, 全局去重)
    支持二级(3.1)与三级(3.1.3)节: 三级优先用自己的映射, 无则回退父级"""
    sub = sn if "." in sn else sec
    m = _SUB_TABLE_MAP.get(sub)
    if not m and "." in sub:
        m = _SUB_TABLE_MAP.get(sub.rsplit(".", 1)[0])  # 三级回退父级二级
    if not m:
        return []
    fill_ch, wanted = m
    tables = fill_section(conn, fill_ch, assess)
    if not wanted:
        return tables
    # 前缀/包含匹配 (检测结果表(化学毒物) 匹配 '检测结果表')
    res = []
    for t in tables:
        for w in wanted:
            if t["name"] == w or t["name"].startswith(w) or w in t["name"]:
                # 全局去重: 每个表名只出现一次 (避免设备×2/检查表×2/管理制度×3)
                if t["name"] not in _USED_TABLES:
                    _USED_TABLES.add(t["name"])
                    res.append(t)
                break
    return res


_USED_TABLES: set = set()
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
    from web.report_struct import CHAPTERS, SUBS, SUBS3
    _para(doc, "目  录", HEI, 16, True, WD_ALIGN_PARAGRAPH.CENTER)
    # 两级标题手动列出 (打开可见) + TOC域 (F9后带页码)
    for sec, title in CHAPTERS.items():
        _para(doc, f"{sec}  {title}", FANGSONG, 13, True)
        for sn, st in SUBS.get(sec, []):
            _para(doc, f"{sn}  {st}", FANGSONG, 12, False, indent=0.74)
            for sub3, (parent, t3) in SUBS3.items():
                if parent == sn:
                    _para(doc, f"{sub3}  {t3}", FANGSONG, 11, False, indent=1.5)
    _para(doc, "（自动目录：文档中 引用→插入目录 或 全选按F9，页码自动生成）", FANGSONG, 10.5,
          False, WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_page_break()


def _heading(doc, text: str, level: int = 1):
    """带大纲级别的标题 (Word 导航窗格/自动目录可用)
    格式规约: 一级=宋体16居中加粗; 二级=黑体14加粗; 三级=宋体13加粗; 四级=宋体12加粗
    直接用 add_heading 会被 Heading 样式的主题字体覆盖(仿宋13) — 改: 段落+outlineLvl+字体全显式"""
    from docx.shared import Pt as _Pt
    from docx.oxml.ns import qn as _qn
    from docx.enum.text import WD_ALIGN_PARAGRAPH as _AL
    p = doc.add_paragraph()
    # 大纲级别 (Word 导航窗格/自动目录可见)
    pPr = p._p.get_or_add_pPr()
    lvl = pPr.find(_qn('w:outlineLvl'))
    if lvl is None:
        lvl = pPr.makeelement(_qn('w:outlineLvl'), {})
        pPr.append(lvl)
    lvl.set(_qn('w:val'), str(max(0, level - 1)))
    fonts = {1: (SONG, 16, True, _AL.CENTER),
             2: (HEI, 14, True, None),
             3: (SONG, 13, True, None),
             4: (SONG, 12, True, None)}
    fname, size, bold, align = fonts.get(level, (SONG, 13, True, None))
    r = p.add_run(text)
    _set_font(r, fname, size, bold)
    # 段落字体 (防 Normal 样式干扰)
    p.style = doc.styles['Normal']
    if align is not None:
        p.alignment = align
    # 段前段后微调
    pf = p.paragraph_format
    pf.space_before = _Pt(6 if level == 1 else 3)
    pf.space_after = _Pt(6 if level == 1 else 3)
    return p


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
    global _USED_TABLES
    _USED_TABLES = set()  # 每次导出重置去重
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

    # 各章节 (按 report_struct 新结构: 11章 + 二级 + 三级 + 数据四级)
    from web.report_struct import _extract_product_units
    ss = section_states or {}
    # 数据三级(3.5.x产品工艺)/数据四级(5.1.1.x产品) 从项目数据提取
    prod4 = _extract_product_units(project or {})
    prod4_by_parent = {}
    for k, t in prod4:
        prod4_by_parent.setdefault(k.rsplit(".", 1)[0], []).append((k, t))
    for sec in CHAPTERS:
        sec_meta = ss.get(sec, {})
        sec_text = sec_meta.get("text", "")
        _heading(doc, f"{sec}  {CHAPTERS[sec]}", 1)
        if sec_text and sec_meta.get("state") == "generated":
            # 章级文本不 dump — 原报告章级是纯标题(1 总论 后直接 1.1),
            # 章级概述段是生成器多余产物 (v25 章节重复根因: 旧文本嵌'1.1 项目概况'小标题)
            pass
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
                        if re.match(r"^\d+(\.\d+)*\s+\S", line) and len(line) < 40:
                            continue
                        _para(doc, line, FANGSONG, 14, indent=0.74)
            # 内嵌表格: 该小节对应的数据表 (跟真实报告一致, 表格在正文对应位置)
            sub_tables = _tables_for_sub(conn, sec, sn, assess)
            if sub_tables:
                _write_tables_named(doc, sn, sub_tables)
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
                # 三级节内嵌表 (原报告表挂在三级: 3.1.3气象/3.1.7技术经济/3.2.2选址评价/3.4.1产品/3.4.2原辅料…)
                sub3_tables = _tables_for_sub(conn, sec, sub3, assess)
                if sub3_tables:
                    _write_tables_named(doc, sub3, sub3_tables)
                # 固定四级
                for num, t4 in SUBS4.get(sub3, []):
                    _heading(doc, f"{num}  {t4}", 4)
                    m = ss.get(num, {})
                    if m.get("state") == "generated":
                        for line in (m.get("text", "") or "").split("\n"):
                            line = line.strip()
                            if line and not line.startswith("#"):
                                _para(doc, line, FANGSONG, 14, indent=0.74)
                # 数据四级 (产品/工段级): 挂在 5.1.1 三级下
                for num, t4 in prod4_by_parent.get(sub3, []):
                    _heading(doc, f"{num}  {t4}", 4)
                    m = ss.get(num, {})
                    if m.get("state") == "generated":
                        for line in (m.get("text", "") or "").split("\n"):
                            line = line.strip()
                            if line and not line.startswith("#"):
                                _para(doc, line, FANGSONG, 14, indent=0.74)
            # 数据三级 (产品/工段级): 挂在 3.5 二级下 (3.5.1 产品工艺...3.5.n 生产工艺评价)
            for num, t4 in prod4_by_parent.get(sn, []):
                _heading(doc, f"{num}  {t4}", 3)
                m = ss.get(num, {})
                if m.get("state") == "generated":
                    for line in (m.get("text", "") or "").split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            _para(doc, line, FANGSONG, 14, indent=0.74)
    conn.close()
    doc.save(str(out_path))



def _write_tables_named(doc, sn: str, tables: list[dict]):
    """内嵌表格 + 语义化标题: 表{sn}-{idx} {tablename}
    原报告格式: '表3.4-1 本项目扩建前后全厂产品方案对比表' (不再'相关数据表'占位)"""
    idx = 0
    for t in tables:
        if not t.get("rows"):
            continue
        idx += 1
        doc.add_paragraph()
        _para(doc, f"表{sn}-{idx} {t.get('name', '相关数据表')}", HEI, 12, bold=True, align=1)
        _write_tables(doc, [t])


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
