"""前置页 (封面/声明页) — 复刻真实备案稿格式 (GBZ/T 196—2025 附录D + 3份真实报告取证)

格式取证来源 (长兴/浦发/新泰 .docx → LO 转 PDF → span 级字号/坐标):
  封面 p1: 企业名 22pt粗 → 项目名 18pt粗(自动折行) → '职业病危害预评价报告书' 22pt粗
           → '（备案稿）' 22pt粗 → '报告书编号：Y2023-002' 16pt粗
           [全居中; 前4行 line=2.0倍, 编号 1.5倍]
  封面 p2: 机构名 18pt粗 → '二〇二三年七月' 16pt粗 [居中, 机构名在页面约 63% 高度处, 下方留白]
  声明 p3: '声  明' 14pt粗居中 → 正文 14pt 首行缩进2字 (行距 exact 24.5pt) →
           空1行 → '评价机构名称：…' 右对齐 → 空1行 → '法人代表：' 首行缩进11字 →
           人员表 (结构仿浦发: 5列无边框, 全空待填)
  页码: 封面/声明页无页码 (不含页眉页脚); 页眉页脚在第二步分节做

数据来源 (项目级 data 可覆盖):
  company   → 封面第1行 (企业名, 导入时解析落库)
  name      → 封面项目名 (权威: 可研/申请报告 > 现状报告)
  org_name  → 机构名 (默认 江苏宁大卫防检测技术有限公司常熟分公司)
  report_no → 报告书编号 (默认自动生成 Y{年}-{序号}; 生成后持久化不重复)
"""
import re
from datetime import datetime

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

FANGSONG = "仿宋_GB2312"

# 机构名默认值 (三份真实报告均为此机构; 项目级 data.org_name 可覆盖)
DEFAULT_ORG = "江苏宁大卫防检测技术有限公司常熟分公司"

# 声明页行距 (取证: PDF 行 top 间距 24.5pt exact = 311150 EMU)
_DECL_LINE_PT = 24.5
# 封面第2页: 机构名前的空段 (目标 y≈534/842; 本导出 top margin=99pt → 垫 9×48pt=432)
_P2_SPACER_PT = 48.0
_P2_SPACER_N = 9

_CN_DIGITS = "〇一二三四五六七八九"


def cn_year_month(dt: datetime | None = None) -> str:
    """日期 → 汉字年月 (复刻真实: '二〇二三年七月')"""
    dt = dt or datetime.now()
    y = "".join(_CN_DIGITS[int(d)] for d in str(dt.year))
    if dt.month < 10:
        m = _CN_DIGITS[dt.month]
    else:
        m = ("十", "十一", "十二")[dt.month - 10]
    return f"{y}年{m}月"


def gen_report_no(existing: list[str] | None = None, dt: datetime | None = None) -> str:
    """生成报告书编号 Y{年}-{3位序号} (复刻真实: Y2023-002 / Y2026-007)
    序号 = 当年已有编号最大值+1 (existing 传当前库中已有编号列表)"""
    dt = dt or datetime.now()
    y = dt.year
    used = set()
    for s in (existing or []):
        m = re.match(rf"Y{y}-(\d+)$", str(s).strip())
        if m:
            used.add(int(m.group(1)))
    n = max(used) + 1 if used else 1
    return f"Y{y}-{n:03d}"


def _set_font(run, name: str, size: float, bold: bool = False):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    run.font.size = Pt(size)
    run.font.bold = bold


def _para(doc, text="", font=FANGSONG, size=14, bold=False, align=None,
          line=None, indent_chars=None):
    """段落助手 (自包含, 避免与 word_export 循环依赖)
    line: None=默认 | float(>4)=倍数 | Pt对象=exact 定值
    indent_chars: 首行缩进字符数 (2 → firstLineChars=200; '法人代表' 真实为 1100=11字)"""
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    if line is not None:
        pf.line_spacing = line
    if indent_chars is not None:
        ind = p._p.get_or_add_pPr().get_or_add_ind()
        ind.set(qn("w:firstLineChars"), str(int(indent_chars * 100)))
        ind.set(qn("w:firstLine"), str(int(size * 20 * indent_chars)))
    if text:
        r = p.add_run(text)
        _set_font(r, font, size, bold)
    return p


def render_org(project_data: dict) -> str:
    """机构名: data.org_name > 默认宁大常熟"""
    v = str(project_data.get("org_name") or "").strip()
    return v or DEFAULT_ORG


def add_front_matter(doc, project_data: dict, report_no: str = "") -> None:
    """写 封面(2页) + 声明页 → 到 doc (当前光标处), 末尾加分页 (下一页接目录)

    project_data 消费键: company(企业名) / name(项目名) / org_name / report_no
    """
    company = str(project_data.get("company") or "").strip()
    name = str(project_data.get("name") or "").strip()
    org = render_org(project_data)
    no = str(report_no or project_data.get("report_no") or "").strip()

    C = WD_ALIGN_PARAGRAPH.CENTER

    # ── 封面 第1页 (行距 2.0/1.5 复刻取证) ──
    if company:
        _para(doc, company, FANGSONG, 22, True, C, line=2.0)
    if name:
        _para(doc, name, FANGSONG, 18, True, C, line=2.0)
    _para(doc, "职业病危害预评价报告书", FANGSONG, 22, True, C, line=2.0)
    _para(doc, "（备案稿）", FANGSONG, 22, True, C, line=2.0)
    _para(doc, f"报告书编号：{no}" if no else "报告书编号：", FANGSONG, 16, True, C, line=1.5)
    doc.add_page_break()

    # ── 封面 第2页: 机构名+日期 (真实: 机构名 y≈534/842 ≈ 63%, 下方留盖章空间) ──
    for _ in range(_P2_SPACER_N):
        _para(doc, "", size=14, line=Pt(_P2_SPACER_PT))
    _para(doc, org, FANGSONG, 18, True, C)
    _para(doc, cn_year_month(), FANGSONG, 16, True, C)
    doc.add_page_break()

    # ── 声明页 (行距 exact 24.5pt 复刻取证) ──
    _para(doc, "声  明", FANGSONG, 14, True, C, line=Pt(_DECL_LINE_PT))
    body = (f"{org}遵守国家有关法律、法规、标准，在{company}{name}"
            f"职业病危害预评价过程坚持科学、客观、真实、公正的原则，并对所出具的"
            f"《{company}{name}职业病危害预评价报告》承担法律责任。")
    _para(doc, body, FANGSONG, 14, False, None, line=Pt(_DECL_LINE_PT), indent_chars=2)
    _para(doc, "", size=14, line=Pt(_DECL_LINE_PT))
    # 签名区: 评价机构名称 右对齐; 法人代表 首行缩进11字 (取证 rec33: x=225/595)
    _para(doc, f"评价机构名称：{org}", FANGSONG, 14, False, WD_ALIGN_PARAGRAPH.RIGHT, line=Pt(_DECL_LINE_PT))
    _para(doc, "", size=14, line=Pt(_DECL_LINE_PT))
    _para(doc, "法人代表：", FANGSONG, 14, False, None, line=Pt(_DECL_LINE_PT), indent_chars=11)
    _add_personnel_table(doc)
    # 不在此处 page break: 调用方紧跟 add_section(NEW_PAGE) — 分节符负责翻页且隔离页码


def _add_personnel_table(doc) -> None:
    """声明页人员表 (结构仿浦发人员表: 5列 无边框 [角色, 姓名, 职称, 资质号, 签名])
    4 行: 项目负责人/报告书编写人/报告书审核人/报告书签发人 — 全空待填 (人工填写盖章)"""
    rows = ["项目负责人：", "报告书编写人：", "报告书审核人：", "报告书签发人："]
    tbl = doc.add_table(rows=len(rows), cols=5)
    # 固定布局 + 总宽 ≤ 可用宽度 (21-3.7-2.6=14.7cm=416pt=8320twips), 否则 LO/Word 压缩列致标签截断
    tbl.autofit = False
    tblPr = tbl._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    # 无边框 (复刻浦发人员表: tblPr 无 tblBorders)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "none")
        el.set(qn("w:sz"), "0")
        borders.append(el)
    tblPr.append(borders)
    for i, label in enumerate(rows):
        # 行高 exact 28pt (复刻浦发 trHeight=567 twips)
        trPr = tbl.rows[i]._tr.get_or_add_trPr()
        h = OxmlElement("w:trHeight")
        h.set(qn("w:val"), "560")
        h.set(qn("w:hRule"), "exact")
        trPr.append(h)
        cells = tbl.rows[i].cells
        cells[0].text = ""
        r = cells[0].paragraphs[0].add_run(label)
        _set_font(r, FANGSONG, 14)
        for c in cells[1:]:
            c.text = ""
    # 列宽 (合计=8320 twips=416pt=可用宽 14.7cm; 固定布局下同时写 tblGrid, 防 LO 重排截断)
    widths = [2600, 1400, 1600, 1800, 920]
    grid = tbl._tbl.find(qn("w:tblGrid"))
    if grid is not None:
        for i, gc in enumerate(grid.findall(qn("w:gridCol"))):
            if i < len(widths):
                gc.set(qn("w:w"), str(widths[i]))
    for i, w in enumerate(widths):
        for row in tbl.rows:
            row.cells[i].width = Pt(w / 20)


def ensure_report_no(pid: str) -> str:
    """报告书编号持久化: 项目有则用, 无则生成 Y{年}-{序号} 写回 data 并返回。
    序号跨项目递增 (扫全库已有编号), 保证不重号; 幂等 (已有直接返回)。"""
    from web.projects_db import get_project, update_project
    p = get_project(pid)
    if not p:
        return ""
    data = dict(p.get("data") or {})
    cur = str(data.get("report_no") or "").strip()
    if cur:
        return cur
    existing = []
    try:
        from web.projects_db import list_all_reports
        for r in list_all_reports():
            v = str((r.get("data") or {}).get("report_no") or "").strip()
            if v:
                existing.append(v)
    except Exception:
        pass
    no = gen_report_no(existing)
    data["report_no"] = no
    update_project(pid, p["name"], data)
    return no
