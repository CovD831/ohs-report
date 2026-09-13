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

# 节 → 内嵌哪些表 (复用 fill_section 各章表格, 按节筛)
# 【挂载规则·依原报告取证】表只挂"叶子节"(无子节的二级/三级/四级);
#   有子节的二级纯标题(无正文无表) — 原报告 2.1(有2.1.1)下无正文, 表2.1-1~3 挂 2.1.1
# 【编号规则·依原报告取证】表{二级节}-{序号}: 表2.1-1/2/3(应急), 表3.1-1~3, 表4.2-1~5…
#   序号在同一二级节内连续 (3.1.5/3.1.7 的表都编 表3.1-x)
# 值 = (fill_section章, 表名列表)
_SUB_TABLE_MAP = {
    "1.3.2": ("1", ["评价依据表"]),  # 标准清单正文后附一览表
    "2.1.1": ("7", ["应急物资清单"]),  # 拆3张: 应急药品/应急物资/洗眼器 (原报告表2.1-1~3)
    "3.1.3": ("3", ["项目概况表"]),  # 表3.1-1 气象因素 (概况表含气象)
    "3.1.5": ("3", ["劳动定员表"]),  # 表3.1-2 生产岗位定员
    "3.1.7": ("3", ["项目概况表"]),  # 表3.1-x 主要经济技术指标
    "3.2.1": ("3", ["选址检查表"]),  # 表3.2-1 厂区周边环境
    "3.2.2": ("3", ["选址检查表"]),  # 表3.2-2 选址检查结果评价
    "3.3.3": ("3", ["总体布局检查表"]),  # 表3.3-1
    "3.4.1": ("3", ["产品产量表"]),  # 表3.4-1 扩建前后产品方案对比
    "3.4.2": ("3", ["原辅材料表"]),  # 表3.4-2 主要原辅材料
    "3.5.3": ("3", ["主要设备清单"]),  # 表3.5-1 工艺检查及评价
    "3.6.1": ("3", ["设备明细表"]),  # 表3.6-1 设施一览
    "3.6.3": ("3", ["主要设备清单"]),  # 表3.6-x 设备布局分析评价
    "3.7.1": ("3", ["建构筑物表"]),  # 表3.7-1 建(构)筑物情况
    "3.7.4": ("3", ["建筑卫生学检查表"]),  # 表3.7-2 卫生学检查
    "3.8.1": ("3", ["辅助用室设置表"]),  # 表3.8-1/2 卫生特征分级+现有设置
    "3.8.2": ("3", ["辅助用室检查表"]),  # 表3.8-3
    "4.1.1": ("4", ["类比可比性表"]),  # 表4.1-1
    "4.2.2": ("4", ["检测结果表(粉尘)", "检测结果表(化学毒物)"]),  # 表4.2-3 类比企业危害因素识别及分布
    "4.2.4": ("8", ["PPE配备表"]),  # 表4.2-x 类比企业PPE配备
    "4.4": None,  # 原报告4.4类无表 (检测结果在4.2类比调查)
    "4.5": ("4", ["职业健康监护表"]),
    "5.1.1.2": ("5", ["危害因素识别表"]),  # 表5.1-1 危害因素分布一览
    "5.2.1": ("5", ["健康影响表"]),  # 表5.2-1 化学因素健康影响 (无数据自动跳过)
    "5.3": ("5", ["接触限值表", "物理因素职业接触限值表(GBZ 2.2)"]),  # 表5.3-1/2
    "5.4.3": ("5", ["判定表"]),  # 表5.4-1 关键控制点
    "6.2": ("6", ["防护设施检查表"]),  # 表6.2-1 防护设施评价 (原报告6.2仅1张)
    "7.1": ("7", ["应急物资清单"]),  # 表7.1-1 应急救援器材一览
    "7.2": ("7", ["应急救援检查表"]),  # 表7.2-1 应急措施检查及评价
    "8.1": ("8", ["PPE配备表"]),  # 表8.1-1
    "8.2": ("8", ["PPE配备表"]),  # 表8.2-1 拟配置检查表
    "9.1": ("9", ["管理制度检查表"]),  # 表9.1-1
    "10.2": ("10", ["问题与建议表"]),  # 表10.2-1
}


def _tables_for_sub(conn, sec: str, sn: str, assess: dict) -> list[dict]:
    """按小节返回内嵌表格 (复用 fill_section 各章表格, 全局去重)
    支持二级(3.1)与三级(3.1.3)节: 三级优先用自己的映射, 无则回退父级"""
    sub = sn if "." in sn else sec
    m = _SUB_TABLE_MAP.get(sub)
    # 表编号节号: 二级(3.1.5→3.1, 5.1.1.2→5.1) — 原报告 表{二级}-{序号} 规则
    cap_sn = ".".join(sub.split(".")[:2]) if "." in sub else sec
    # 三/四级节: 只用自己的专属映射, 不回退父级 (防每节挂整章表×N)
    if not m:
        return []
    fill_ch, wanted = m
    tables = fill_section(conn, fill_ch, assess)
    # 应急物资三表拆分 (原报告: 表2.1-1应急药品/表2.1-2应急物资/表2.1-3洗眼器; 7.1应急救援器材)
    if "应急物资清单" in wanted and fill_ch == "7" and sub == "2.1.1":
        # 仅 2.1.1 拆3张 (原报告表2.1-1/2/3 应急药品/应急物资/洗眼器)
        # 7.1 = 现有企业应急救援器材一览 (全量1张, 不拆)
        split = []
        for t in tables:
            if t["name"] != "应急物资清单":
                if "应急救援检查表" not in wanted:  # 只要应急物资(拆3张)时丢弃检查表
                    continue
                split.append(t)
                continue
            eye_rows = [r for r in t["rows"] if "洗眼" in (str(r[1]) + str(r[2]))]
            med_rows = [r for r in t["rows"] if "洗眼" not in (str(r[1]) + str(r[2])) and str(r[1]) == "应急药品"]
            oth_rows = [r for r in t["rows"] if "洗眼" not in (str(r[1]) + str(r[2])) and str(r[1]) != "应急药品"]
            for nm, rows in (("应急药品清单", med_rows), ("应急物资清单", oth_rows), ("洗眼器一览表", eye_rows)):
                if rows:
                    t2 = dict(t); t2["name"] = nm; t2["rows"] = rows
                    split.append(t2)
        return split
    if not wanted:
        return tables
    # 前缀/包含匹配 (检测结果表(化学毒物) 匹配 '检测结果表')
    res = []
    for t in tables:
        for w in wanted:
            if t["name"] == w or t["name"].startswith(w) or w in t["name"]:
                # 去重: 同一"节+表名"只出现一次; 不同节允许复用同名表
                # (原报告同一张检查表在 2.3 现状/6.2 评价/10.4 建议多处出现)
                key = f"{sub}:{t['name']}"
                if key not in _USED_TABLES:
                    _USED_TABLES.add(key)
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
    """目录: 真 Word TOC 域 (与原报告同款: TOC \\o "1-2" \\h \\z \\u)
    我们的标题已带 outlineLvl, Word 打开后 F9 (或 引用→更新目录) 自动生成条目+页码"""
    _para(doc, "目  录", HEI, 16, True, WD_ALIGN_PARAGRAPH.CENTER)
    p = doc.add_paragraph()
    run = p.add_run()
    fld_b = OxmlElement("w:fldChar"); fld_b.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText"); instr.set(qn("xml:space"), "preserve")
    instr.text = ' TOC \\o "1-2" \\h \\z \\u '
    fld_sep = OxmlElement("w:fldChar"); fld_sep.set(qn("w:fldCharType"), "separate")
    t = OxmlElement("w:t"); t.text = "（目录域：在 Word 中全选后按 F9，自动生成条目与页码）"
    fld_e = OxmlElement("w:fldChar"); fld_e.set(qn("w:fldCharType"), "end")
    for el in (fld_b, instr, fld_sep, t, fld_e):
        run._r.append(el)
    _set_font(run, FANGSONG, 12)
    doc.add_page_break()


def _numbered_para(doc, text: str, font: str, size: float):
    """Word 自动编号段落 (复刻原报告: numFmt=decimal, lvlText=(%1) → (1)(2)(3)…一条一段自动递增)
    每个列表新建独立 numbering 定义, 从1起号"""
    from docx.oxml import OxmlElement as _OE
    from docx.shared import Pt as _Pt
    root = _ensure_numbering_part(doc)
    # abstractNum (格式: (%1) 左对齐 悬挂缩进)
    ids = [int(e.get(qn('w:abstractNumId'))) for e in root.findall(qn('w:abstractNum'))]
    aid = (max(ids) + 1) if ids else 100  # 模板 numbering 占 0-8, 我们从 100 起
    abs_el = _OE('w:abstractNum'); abs_el.set(qn('w:abstractNumId'), str(aid))
    m0 = _OE('w:nsid'); m0.set(qn('w:val'), f'{aid:08X}')
    tmpl = _OE('w:multiLevelType'); tmpl.set(qn('w:val'), 'singleLevel')
    lvl0 = _OE('w:lvl'); lvl0.set(qn('w:ilvl'), '0')
    start = _OE('w:start'); start.set(qn('w:val'), '1')
    fmt = _OE('w:numFmt'); fmt.set(qn('w:val'), 'decimal')
    ltxt = _OE('w:lvlText'); ltxt.set(qn('w:val'), '(%1)')
    lvlj = _OE('w:lvlJc'); lvlj.set(qn('w:val'), 'left')
    ppr = _OE('w:pPr')
    ind = _OE('w:ind'); ind.set(qn('w:left'), '420'); ind.set(qn('w:hanging'), '420')
    ppr.append(ind)
    for el in (start, fmt, ltxt, lvlj, ppr):
        lvl0.append(el)
    abs_el.append(m0); abs_el.append(tmpl); abs_el.append(lvl0)
    root.append(abs_el)
    nids = [int(e.get(qn('w:numId'))) for e in root.findall(qn('w:num'))]
    nid = (max(nids) + 1) if nids else 100
    num_el = _OE('w:num'); num_el.set(qn('w:numId'), str(nid))
    aref = _OE('w:abstractNumId'); aref.set(qn('w:val'), str(aid))
    num_el.append(aref)
    root.append(num_el)
    p = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    numpr = _OE('w:numPr')
    ilvl = _OE('w:ilvl'); ilvl.set(qn('w:val'), '0')
    numid = _OE('w:numId'); numid.set(qn('w:val'), str(nid))
    numpr.append(ilvl); numpr.append(numid)
    pPr.append(numpr)
    r = p.add_run(text)
    _set_font(r, font, size)
    pf = p.paragraph_format
    pf.left_indent = _Pt(15)
    pf.first_line_indent = _Pt(-15)
    return p


def _ensure_numbering_part(doc):
    """编号定义累积在内存 root (不建 OPC Part — python-docx 对自建 Part 会双写 zip);
    docx 保存后由 _inject_numbering() post-process 注入 numbering.xml"""
    from lxml import etree as _ET
    global _NUM_ROOT
    if _NUM_ROOT is None:
        _NUM_ROOT = _ET.fromstring(
            '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    return _NUM_ROOT


_NUM_ROOT = None


def _inject_numbering(docx_path):
    """保存后处理: 向 docx zip 注入 numbering.xml + [Content_Types].xml override + document.xml.rels 关系
    (Word 自动编号 (1)(2)(3) 的定义文件)"""
    import zipfile as _zf
    import shutil as _sh
    from lxml import etree as _ET
    num_bytes = _ET.tostring(_NUM_ROOT, xml_declaration=True, encoding='UTF-8', standalone=True) if _NUM_ROOT is not None and len(_NUM_ROOT) else None
    if num_bytes is None:
        # 无编号定义: 仍注入 updateFields (目录域自动更新) — 若 settings 未设
        p = str(docx_path)
        try:
            with _zf.ZipFile(p, 'r') as zin:
                items = {n: zin.read(n) for n in zin.namelist()}
            if 'word/settings.xml' in items and b'updateFields' not in items['word/settings.xml']:
                s = items['word/settings.xml'].decode('utf-8')
                s = s.replace('</w:settings>', '<w:updateFields w:val="true"/></w:settings>')
                items['word/settings.xml'] = s.encode('utf-8')
                tmp = p + '.tmp'
                with _zf.ZipFile(tmp, 'w', _zf.ZIP_DEFLATED) as zout:
                    for n, b in items.items():
                        zout.writestr(n, b)
                _sh.move(tmp, p)
        except Exception:
            pass
        return
    p = str(docx_path)
    tmp = p + '.tmp'
    with _zf.ZipFile(p, 'r') as zin:
        names = zin.namelist()
        items = {n: zin.read(n) for n in names}
    if 'word/numbering.xml' in items:
        # 默认模板自带 numbering.xml: 把我们的 abstractNum/num 追加进去 (id 从100起, 不冲突)
        existing = items['word/numbering.xml'].decode('utf-8')
        ours = num_bytes.decode('utf-8')
        # 提取我们 root 的子元素
        extra = ''.join(re.findall(r'<w:abstractNum .*?</w:abstractNum>|<w:num .*?</w:num>', ours, re.S))
        if extra:
            existing = existing.replace('</w:numbering>', extra + '</w:numbering>')
            items['word/numbering.xml'] = existing.encode('utf-8')
    else:
        items['word/numbering.xml'] = num_bytes
        # [Content_Types].xml
        ct = items['[Content_Types].xml'].decode('utf-8')
        if 'numbering+xml' not in ct:
            ct = ct.replace('</Types>',
                '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/></Types>')
            items['[Content_Types].xml'] = ct.encode('utf-8')
        # document.xml.rels
        rels = items['word/_rels/document.xml.rels'].decode('utf-8')
        if 'numbering.xml' not in rels:
            rels = rels.replace('</Relationships>',
                '<Relationship Id="rIdNum1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/></Relationships>')
            items['word/_rels/document.xml.rels'] = rels.encode('utf-8')
    # settings.xml 注入 updateFields → Word 打开时自动更新 TOC 域 (目录自动生成页码)
    if 'word/settings.xml' in items and b'updateFields' not in items['word/settings.xml']:
        s = items['word/settings.xml'].decode('utf-8')
        if '</w:settings>' in s:
            s = s.replace('</w:settings>', '<w:updateFields w:val="true"/></w:settings>')
            items['word/settings.xml'] = s.encode('utf-8')
    with _zf.ZipFile(tmp, 'w', _zf.ZIP_DEFLATED) as zout:
        for n, b in items.items():
            zout.writestr(n, b)
    _sh.move(tmp, p)


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
    global _USED_TABLES, _TABLE_SEQ
    _USED_TABLES = set()  # 每次导出重置去重
    _TABLE_SEQ = {}  # 表编号计数器重置 (表{二级}-{序号})
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
        # 章级文本: 11章结论的正文在章级(SUBS[11]仅11.1, 章级是主体) → 输出;
        # 1-10章章级文本是生成器概述残留(原报告章级纯标题) → 不输出
        if sec_text and sec_meta.get("state") == "generated" and sec == "11":
            for line in sec_text.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("表"):
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
            # 有子节的二级节 = 中间层级: 纯标题不dump正文 (原报告2.1下无正文, 内容在2.1.1)
            has_sub3 = any(parent == sn for _, (parent, _) in SUBS3.items())
            # 固定文本清单节 (1.3.1法规/1.3.2标准): 引导段普通段, 《条目每条一段Word自动编号 (原报告取证: numPr lvlText=(%1))
            from web.llm_draft import _fixed_text as _ftx
            fixed_lines = None
            if sn in ("1.3.1", "1.3.2"):
                _fx = _ftx(sn)
                if _fx:
                    fixed_lines = [ln.strip() for ln in _fx.split("\n") if ln.strip()]
            if sn in ("1.3.1", "1.3.2") and fixed_lines:
                for ln in fixed_lines:
                    if ln.startswith("《"):
                        _numbered_para(doc, ln, FANGSONG, 12)
                    elif not ln.startswith("#"):
                        _para(doc, ln, FANGSONG, 14, indent=0.74)
            elif sub_text and sub_meta.get("state") == "generated" and not has_sub3:
                for line in sub_text.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        if re.match(r"^\d+(\.\d+)*\s+\S", line) and len(line) < 40:
                            continue
                        if _is_llm_table_title(line):
                            continue
                        _para(doc, line, FANGSONG, 14, indent=0.74)
            # 内嵌表格: 该小节对应的数据表 (跟真实报告一致, 表格在正文对应位置)
            # 若该二级节有三级子节: 不在二级挂表 (表跟随叶子三级节, 原报告如此; 防同表×2)
            if not has_sub3:
                sub_tables = _tables_for_sub(conn, sec, sn, assess)
                if sub_tables:
                    _write_tables_named(doc, sn, sub_tables)
            # 三级 + 固定四级
            for sub3, (parent, t3) in SUBS3.items():
                if parent != sn:
                    continue
                sub3_meta = ss.get(sub3, {})
                _heading(doc, f"{sub3}  {t3}", 3)
                # 有四级子节的三级节同样纯标题 (5.1.1 下内容在 5.1.1.x)
                has_sub4 = bool(SUBS4.get(sub3)) or any(p4 == sub3 for p4, _ in prod4_by_parent.items())
                # 固定文本清单三级节 (1.3.1法规/1.3.2标准): 《条目每条一段Word自动编号
                if sub3 in ("1.3.1", "1.3.2"):
                    from web.llm_draft import _fixed_text as _ftx3
                    _fx3 = _ftx3(sub3)
                    if _fx3:
                        for ln in [x.strip() for x in _fx3.split("\n") if x.strip()]:
                            if ln.startswith("《"):
                                _numbered_para(doc, ln, FANGSONG, 12)
                            elif not ln.startswith("#"):
                                _para(doc, ln, FANGSONG, 14, indent=0.74)
                    sub3_tables = _tables_for_sub(conn, sec, sub3, assess)
                    if sub3_tables:
                        _write_tables_named(doc, sub3, sub3_tables)
                    continue
                if sub3_meta.get("state") == "generated" and not has_sub4:
                    for line in (sub3_meta.get("text", "") or "").split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            if _is_llm_table_title(line):
                                continue
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
                                if _is_llm_table_title(line):
                                    continue
                                _para(doc, line, FANGSONG, 14, indent=0.74)
                # 数据四级 (产品/工段级): 挂在 5.1.1 三级下
                for num, t4 in prod4_by_parent.get(sub3, []):
                    _heading(doc, f"{num}  {t4}", 4)
                    m = ss.get(num, {})
                    if m.get("state") == "generated":
                        for line in (m.get("text", "") or "").split("\n"):
                            line = line.strip()
                            if line and not line.startswith("#"):
                                if _is_llm_table_title(line):
                                    continue
                                _para(doc, line, FANGSONG, 14, indent=0.74)
                # 数据四级表 (原报告表5.1-1 危害因素分布一览 挂5.1.1.2)
                p4_tables = _tables_for_sub(conn, sec, "5.1.1.2", assess) if sub3 == "5.1.1" else []
                if p4_tables:
                    _write_tables_named(doc, "5.1.1.2", p4_tables)
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
    _inject_numbering(out_path)



def _is_llm_table_title(line: str) -> bool:
    """LLM 写进正文的表标题行 (系统会插真正的表+标题, 这些行跳过)"""
    return bool(re.match(r"^表\d{1,2}(\.\d+)*-\d+", line.strip()))


_TABLE_SEQ: dict = {}


def _write_tables_named(doc, sn: str, tables: list[dict]):
    """内嵌表格 + 语义化标题: 表{二级节}-{序号} {语义名}
    原报告编号规则: 表2.1-1 应急药品清单 / 表3.1-2 定员 / 表3.1-3 经济技术指标
    (3.1.5/3.1.7 的表都编 表3.1-x — 序号在同一二级节内连续)"""
    cap = ".".join(sn.split(".")[:2]) if "." in sn else sn
    for t in tables:
        if not t.get("rows"):
            continue
        _TABLE_SEQ[cap] = _TABLE_SEQ.get(cap, 0) + 1
        doc.add_paragraph()
        _para(doc, f"表{cap}-{_TABLE_SEQ[cap]} {t.get('name', '相关数据表')}", HEI, 12, bold=True, align=1)
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
