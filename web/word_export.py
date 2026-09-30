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
    # 全部对齐原报告 40 张表取证 (orig_table_map.py + /tmp/orig_captions.json)
    "2.1.1": ("7", ["应急物资清单"]),  # 表2.1-1/2/3 应急药品/应急物资/洗眼器 (拆3张)
    "3.1.3": ("3", ["气象因素表"]),  # 表3.1-1 所在地常年主要气象因素
    "3.1.5": ("3", ["班制定员表"]),  # 表3.1-2 生产岗位定员
    "3.1.7": ("3", ["项目概况表"]),  # 表3.1-3 主要经济技术指标
    "3.2.1": ("3", ["周边环境表"]),  # 表3.2-1 厂区周边环境
    "3.2.2": ("3", ["选址检查表"]),  # 表3.2-2 选址检查结果评价一览
    "3.3.3": ("3", ["总体布局检查表"]),  # 表3.3-1 总体布局检查及评价
    "3.4.1": ("3", ["产品产量表"]),  # 表3.4-1 扩建前后产品方案对比
    "3.4.2": ("3", ["原辅材料表"]),  # 表3.4-2 主要原辅材料
    # 3.5.3 → 3.5: 报告结构里 3.5(生产工艺分析与评价) 是二级节, 无 3.5.x 子节
    # (SUBS3 无 3.5.*) → 原挂 3.5.3 永不可达, 工艺检查表被静默丢弃。
    # 挂到真实存在的 3.5 (与 3.6.3 设备布局检查表 同层, 语义一致)。
    "3.5": ("3", ["工艺检查表"]),  # 表3.5-1 工艺检查及评价
    "3.6.1": ("3", ["设备明细表"]),  # 表3.6-1 扩建后全厂设备设施一览
    "3.6.3": ("3", ["设备布局检查表"]),  # 表3.6-3 生产设备及布局分析与评价 (原跳过3.6-2)
    "3.7.1": ("3", ["建构筑物表"]),  # 表3.7-1 技改范围建(构)筑物
    "3.7.4": ("3", ["建筑卫生学检查表"]),  # 表3.7-2 建筑物卫生学检查
    "3.8.1": ("3", ["卫生特征分级表", "辅助用室设置表"]),  # 表3.8-1 分级 + 表3.8-2 辅助用室设置情况
    "3.8.2": ("3", ["辅助用室检查表"]),  # 表3.8-3 辅助用室检查
    "4.1.1": ("4", ["类比可比性表"]),  # 表4.1-1 类比项目评价参数比较
    "4.2.1": ("4", ["类比工作日写实表", "劳动强度分级表"]),  # 表4.2-1 写实(时段无实测标—) + 表4.2-2 分级
    "4.2.2": ("4", ["类比危害分布表"]),  # 表4.2-3 类比企业危害因素识别及分布
    "4.2.4": ("4", ["类比PPE配备表", "类比PPE有效性表"]),  # 表4.2-4/5 类比企业PPE (ppe数据派生)
    # h11/h14: 4.4 类比企业职业病危害因素检测 → 检测表落点 = 4.4.2 检测结果与评价
    # (4.4 有三级子节 4.4.1/4.4.2 → walk 的叶子规则: 二级不挂表, 表跟随叶子三级节;
    #  挂 "4.4" 会被静默跳过 — 即 test_table_mounts 防的那类失败)。
    # 检测结果表(化学毒物/粉尘…)前缀匹配 + h14 物理因素独立表, 同节一次挂出。
    "4.4.2": ("4", ["检测结果表", "物理因素检测结果表"]),
    "5.1.1.2": ("5", ["危害因素识别表"]),  # 表5.1-1 危害因素分布一览
    "5.2.1": ("5", ["健康影响表"]),  # 表5.2-1 化学因素健康影响
    "5.2.2": ("5", ["物理因素健康影响表"]),  # 表5.2-2 物理因素健康影响
    "5.3": ("5", ["接触限值表", "噪声接触限值表", "高温接触限值表"]),  # 表5.3-1/2/3
    # 5.4.3 关键控制点 → 已按标准 10.2.10 提为独立章 (见下方 "10.1")
    "6.2": ("6", ["防护设施检查表"]),  # 表6.2-1 防护设施评价
    "7.1": ("7", ["应急物资清单"]),  # 表7.1-1 现有企业应急救援器材一览 (全量1张不拆)
    "7.2": ("7", ["应急救援检查表"]),  # 表7.2-1 应急措施检查及评价
    "8.1": ("8", ["PPE配备表"]),  # 表8.1-1 本项目PPE配备
    "8.2": ("8", ["PPE拟配置检查表"]),  # 表8.2-1 拟配置检查表 (5列)
    "9.1": ("9", ["管理制度检查表"]),  # 表9.1-1 现有企业管理检查
    "9.2": None,  # 原表9.2-1 经费: 材料无明细, 不硬造
    # 标准 10.2.10 职业病危害关键控制点分析 (独立章, 2026-09 从 5.4.3 提上来)
    "10.1": ("10", ["关键控制点表"]),  # 表10.1-1 职业病危害关键控制点一览
    # 标准 10.2.11 补充建议 (原 10 章 → 现 11 章)
    "11.2": ("11", ["室内空气质量标准表"]),  # 表11.2-1 室内空气质量标准节选
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
    # 表骨架先行: 项目数据里已沉淀的表 (导入时建) 优先, 其余回退现算
    pd_data = assess.get("_project_data") or {}
    built = pd_data.get("built_tables") or {}
    tables = fill_section(conn, fill_ch, assess)
    # 骨架优先 = 复用沉淀的表 (导出纯读取); ⚠ 但**空骨架不得覆盖现算行** ——
    # 导入时无 assess, 评估驱动骨架恒为空 (rows=[]) 且 status=await_assess;
    # 若照单全换, 导出会丢掉 fill_section 现算出的真实行。
    # (实测: 新鲜导入长兴后导出, 17 张表 computed>0 被空骨架抹平 —— 含
    #  h14「物理因素检测结果表」→ 整张表从 docx 消失; 接触限值表 45 行 → 0。)
    # 空骨架 → 用现算表兜底; 骨架有行 → 沿用骨架 (原语义)。
    _merged: list[dict] = []
    for t in tables:
        w = t["name"]
        sk = built.get(w)
        if isinstance(sk, dict) and (sk.get("rows") or []):
            _merged.append(dict(sk) | {"name": w})
        else:
            _merged.append(t)
    tables = _merged
    # 骨架补插: built 里已沉淀但现算缺失的表 (filler 因空数据跳过) 按原节内顺序插入 —
    # 骨架永远在, 数据待补充; 表名顺序依 wanted 名单 (导出编号顺序)
    have = {t["name"] for t in tables}
    for w in wanted:
        if w in built and w not in have:
            tables.append(dict(built[w]) | {"name": w})
            have.add(w)
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
            # es行(4列): 类别/名称/数量(如"2瓶")/点位(药品行实为完好程度)
            eye_rows = [r for r in t["rows"] if "洗眼" in (str(r[0]) + str(r[1]))]
            med_rows = [r for r in t["rows"] if "洗眼" not in (str(r[0]) + str(r[1])) and str(r[0]) == "应急药品"]
            oth_rows = [r for r in t["rows"] if "洗眼" not in (str(r[0]) + str(r[1])) and str(r[0]) != "应急药品"]
            # 表2.1-1 应急药品: 序号/药品名称/单位/数量/完好程度 (数量拆"2瓶"→2+瓶)
            med_split = []
            for i2, r in enumerate(med_rows, 1):
                q = str(r[2] or "")
                m2 = re.match(r"^(\d+)\s*(.*)$", q)
                num, unit = (m2.group(1), m2.group(2)) if m2 else (q, "")
                med_split.append([i2, r[1], unit or "—", num, r[3] or "—"])
            # 表2.1-3 洗眼器: 序号/部门/规格/位置 (名称→规格, 点位→位置; 部门从点位推断—无数据标"/")
            eye_split = [[i2, "/", r[1], r[3]] for i2, r in enumerate(eye_rows, 1)]
            oth4 = [[r[0], r[1], r[2], r[3]] for r in oth_rows]  # 类别/名称/数量/放置点位
            for nm, cols, rows in (("应急药品清单", ["序号", "药品名称", "单位", "数量", "完好程度"], med_split),
                                   ("应急物资清单", ["类别", "名称", "数量", "放置点位"], oth4),
                                   ("洗眼器一览表", ["序号", "部门", "规格", "位置"], eye_split)):
                if rows:
                    t2 = dict(t); t2["name"] = nm; t2["cols"] = cols; t2["rows"] = rows
                    split.append(t2)
        return split
    if not wanted:
        return tables
    # 前缀/包含匹配 (检测结果表(化学毒物) 匹配 '检测结果表')
    res = []
    for t in tables:
        for w in wanted:
            hit = t["name"] == w
            if not hit and "(" not in w and "（" not in w:
                # wanted 无括号前缀 (如 检测结果表) → 匹配 检测结果表(粉尘) 变体; 但禁止子串误配 (健康影响表 ⊂ 物理因素健康影响表)
                hit = t["name"].startswith(w) and not any(t["name"].startswith(x) and x != w and len(x) > len(w) for x in wanted)
            if hit:
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


_NUM_LIST_STATE = {}  # list_key -> (aid, nid) 同一清单共享同一 numId → Word 自动递增 (1)(2)(3)…
_NUM_COUNTER = [99]   # 全局递增计数器, 从 100 起 (避开默认模板占用的 0-8)


def _numbered_para(doc, text: str, font: str, size: float, list_key: str = None):
    """Word 自动编号段落 (复刻原报告: numFmt=decimal, lvlText=(%1) → (1)(2)(3)…一条一段自动递增)
    list_key 相同的段落共享同一 numId (编号连续递增); 不同 list_key 各自从 1 起号"""
    from docx.oxml import OxmlElement as _OE
    root = _ensure_numbering_part(doc)
    key = list_key or '_default'
    if key in _NUM_LIST_STATE:
        aid, nid = _NUM_LIST_STATE[key]
    else:
        _NUM_COUNTER[0] += 1
        aid = nid = _NUM_COUNTER[0]
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
        # schema 顺序: abstractNum* 必须全部位于 num* 之前 → 插到第一个 num 前面
        first_num = root.find(qn('w:num'))
        if first_num is not None:
            first_num.addprevious(abs_el)
        else:
            root.append(abs_el)
        num_el = _OE('w:num'); num_el.set(qn('w:numId'), str(nid))
        aref = _OE('w:abstractNumId'); aref.set(qn('w:val'), str(aid))
        num_el.append(aref)
        root.append(num_el)
        _NUM_LIST_STATE[key] = (aid, nid)
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
        # 默认模板自带 numbering.xml: 把我们的定义插进去 (id 从100起, 不冲突)
        # schema 顺序: abstractNum* 必须全部位于 num* 之前 — 违反时 Word 会重置编号(全变1)
        existing = items['word/numbering.xml'].decode('utf-8')
        ours = num_bytes.decode('utf-8')
        extra_abs = ''.join(re.findall(r'<w:abstractNum .*?</w:abstractNum>', ours, re.S))
        extra_num = ''.join(re.findall(r'<w:num .*?</w:num>', ours, re.S))
        if extra_abs or extra_num:
            m_first_num = re.search(r'<w:num [^>]*/>|<w:num .*?</w:num>', existing, re.S)
            if m_first_num:
                existing = existing[:m_first_num.start()] + extra_abs + existing[m_first_num.start():]
            else:
                existing = existing.replace('</w:numbering>', extra_abs + '</w:numbering>')
            existing = existing.replace('</w:numbering>', extra_num + '</w:numbering>')
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

    格式规约 —— **以真实备案稿为准** (2026-09 取证 /tmp/bench_orig/长兴--预评（备案稿7-31）.docx):
      字体: 各级**统一 仿宋_GB2312** (L1-L4 全部, 实测 187 个标题无一例外)
      字号: **14pt** (各级统一, 从 Normal 样式继承 sz=28 半磅)
      加粗: 各级均加粗
      对齐: 一级居中, 二~四级左对齐
    早期版本用 "宋体16/黑体14/宋体13/宋体12" 做层级视觉区分, 但**与原报告不符** →
    用户要求"一切以贴近真实报告为准", 故改为各级统一仿宋14。
    直接用 add_heading 会被 Heading 样式的主题字体覆盖 — 改: 段落+outlineLvl+字体全显式"""
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
    # 各级统一仿宋 14pt 加粗 (对齐原报告); 仅对齐方式按层级区分
    fonts = {1: (FANGSONG, 14, True, _AL.CENTER),
             2: (FANGSONG, 14, True, None),
             3: (FANGSONG, 14, True, None),
             4: (FANGSONG, 14, True, None)}
    fname, size, bold, align = fonts.get(level, (FANGSONG, 14, True, None))
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


def export_docx(project: dict, assess: dict, out_path: Path, section_states: dict | None = None,
                front_data: dict | None = None):
    global _USED_TABLES, _TABLE_SEQ, _NUM_LIST_STATE, _NUM_COUNTER
    _USED_TABLES = set()  # 每次导出重置去重
    _TABLE_SEQ = {}  # 表编号计数器重置 (表{二级}-{序号})
    _NUM_LIST_STATE = {}  # 自动编号清单状态重置 (1.3.1 laws / 1.3.2 stds)
    _NUM_COUNTER = [99]
    global _NUM_ROOT
    _NUM_ROOT = None  # numbering root 重置 (防跨导出重复定义)
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

    # 页脚页码 (PAGE 域, 每页底部居中) — 只挂"正文节"(前置页无页码, 复刻真实报告)
    def _add_page_footer(section):
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

    # 封面 + 声明页 (复刻真实备案稿格式: 企业名/项目名/报告书/备案稿/编号 + 机构/日期 + 声明)
    # 取证: 3份真实报告 (长兴/浦发/新泰) span 级字号; 数据源 company/name/org_name/report_no
    from web.front_matter import add_front_matter
    _front = front_data if front_data is not None else {
        "company": (project or {}).get("company", ""),
        "name": (project or {}).get("name", ""),
        "org_name": (project or {}).get("org_name", ""),
        "report_no": (project or {}).get("report_no", ""),
    }
    add_front_matter(doc, _front)

    # ── 分节: 正文节 (目录+章节) — 前置页(封面/声明)独立成节, 页码只从正文起算 ──
    from docx.enum.section import WD_SECTION
    body_sec = doc.add_section(WD_SECTION.NEW_PAGE)
    body_sec.page_width, body_sec.page_height = Cm(21), Cm(29.7)
    body_sec.left_margin, body_sec.right_margin = Cm(3.7), Cm(2.6)
    body_sec.top_margin, body_sec.bottom_margin = Cm(3.5), Cm(2.8)
    body_sec.footer.is_linked_to_previous = False
    _add_page_footer(body_sec)
    # 页码从 1 起算 (前置页不计入) — pgNumType 须在 w:cols 之前
    _sectPr = body_sec._sectPr
    _pg = OxmlElement("w:pgNumType")
    _pg.set(qn("w:start"), "1")
    _cols = _sectPr.find(qn("w:cols"))
    if _cols is not None:
        _cols.addprevious(_pg)
    else:
        _sectPr.append(_pg)

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
                _lkey = "laws" if sn == "1.3.1" else "stds"
                for ln in fixed_lines:
                    if ln.startswith("《"):
                        _numbered_para(doc, ln, FANGSONG, 12, list_key=_lkey)
                    elif not ln.startswith("#"):
                        _para(doc, ln, FANGSONG, 14, indent=0.74)
            elif sub_text and sub_meta.get("state") == "generated":
                # 父子章节规律 (原报告 45 个二级节全量取证):
                #   叶子节(无子节) 17 个: 全部有正文(54~5149字) → 全量输出, 无字数上限
                #   父节 28 个: 25 纯标题 + 3 引导语(86/102/116字) → 超长正文只取首句作引导语, 其余与子节重复不输出
                # 父子规律 (两份真实报告交叉取证): 父节 83% 纯标题; 少量引导语(86~120字)出现在
                # 方法/结论概述型父节, 但节号无规律 (长兴1.6/4.1/5.2 vs 浦发7.6/10.2/10.3)
                # → 按用户规则"没有通用规律就不强求": 超长 LLM 概述不输出 (与子节必然重复);
                #   ≤150 字短文本 (真引导语形态) 保留输出
                if not (has_sub3 and len(sub_text.strip()) > 150):
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
                        _lkey3 = "laws" if sub3 == "1.3.1" else "stds"
                        for ln in [x.strip() for x in _fx3.split("\n") if x.strip()]:
                            if ln.startswith("《"):
                                _numbered_para(doc, ln, FANGSONG, 12, list_key=_lkey3)
                            elif not ln.startswith("#"):
                                _para(doc, ln, FANGSONG, 14, indent=0.74)
                    sub3_tables = _tables_for_sub(conn, sec, sub3, assess)
                    if sub3_tables:
                        _write_tables_named(doc, sub3, sub3_tables)
                    continue
                t3_text = (sub3_meta.get("text", "") or "").strip()
                if sub3_meta.get("state") == "generated" and (not has_sub4 or len(t3_text) <= 150):
                    # 同二级规律: 叶子三级全量输出; 父三级(有四级子节)仅≤150字引导语 (原报告5.1.1=145字)
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
                            if _is_llm_table_title(line):
                                continue
                            _para(doc, line, FANGSONG, 14, indent=0.74)
                # 数据三级表挂载 (3.5.3 工艺检查及评价 = 原表3.5-1)
                d3_tables = _tables_for_sub(conn, sec, num, assess)
                if d3_tables:
                    _write_tables_named(doc, num, d3_tables)
    conn.close()
    doc.save(str(out_path))
    _inject_numbering(out_path)



def _is_llm_table_title(line: str) -> bool:
    """LLM 写进正文的表标题行 (系统会插真正的表+标题, 这些行跳过)

    ⚠ "表" 与编号间**可能有空格** (实测 LLM 写出 "表 5.3-1 XXX（表格由系统自动插入）"),
      旧正则 ^表\\d 要求紧跟数字 → 漏网 → 与系统插入的表题**重号** (表5.3-1 出现两次)。
      同时覆盖全角括号/中文空格/半角空格几种变体。
    """
    s = line.strip().replace("\u3000", " ").replace(" ", "")
    if re.match(r"^表\d{1,2}(\.\d+)*[-—－]\d+", s):
        return True
    # 兜底: 明确喊话"表格由系统插入"的行, 无论形态一律丢弃
    return "表格由系统" in line or "表格由系统自动插入" in line


_TABLE_SEQ: dict = {}


def _write_tables_named(doc, sn: str, tables: list[dict]):
    """内嵌表格 + 语义化标题: 表{二级节}-{序号} {语义名}
    编号优先对齐原报告显式表号 (ORIG_TABLE_NOS, 含跳号取证如表3.6-3);
    无取证的节回退 表{二级}-{序号} 连续计数 (3.1.5/3.1.7 都编 表3.1-x)"""
    from web.orig_table_map import ORIG_TABLE_NOS, ORIG_CAPTIONS
    cap = ".".join(sn.split(".")[:2]) if "." in sn else sn
    nos = ORIG_TABLE_NOS.get(sn, [])
    caps = ORIG_CAPTIONS.get(sn, [])
    for idx, t in enumerate(tables):
        if not t.get("rows"):
            continue
        if idx < len(nos):
            no = nos[idx]  # 原报告显式编号 (表3.6-3)
        else:
            _TABLE_SEQ[cap] = _TABLE_SEQ.get(cap, 0) + 1
            no = f"表{cap}-{_TABLE_SEQ[cap]}"
        title = caps[idx] if idx < len(caps) else t.get("name", "相关数据表")  # 原报告标题逐字对齐
        doc.add_paragraph()
        # 表题格式对齐原报告 (取证: 36/48 为 仿宋_GB2312 12pt 不加粗 居中)
        _para(doc, f"{no} {title}", FANGSONG, 12, bold=False, align=1)
        _write_tables(doc, [t])


def _write_tables(doc, tables):
    """写表格; 支持 t["header2"] 第二行表头 + t["merge_top"] [(c1,c2)] 顶行横向合并(双层表头复刻)"""
    for t in tables:
        if not t["rows"]:
            continue
        h2 = t.get("header2")
        nrows_head = 2 if h2 else 1
        dt = doc.add_table(rows=nrows_head, cols=len(t["cols"]))
        dt.style = "Table Grid"
        dt.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, c in enumerate(t["cols"]):
            cell = dt.rows[0].cells[i]
            cell.text = ""
            r = cell.paragraphs[0].add_run(str(c))
            # 表头: 仿宋_GB2312 10.5pt 加粗 (原报告取证)
            _set_font(r, FANGSONG, 10.5, True)
        if h2:
            for i, c in enumerate(h2):
                cell = dt.rows[1].cells[i]
                cell.text = ""
                r = cell.paragraphs[0].add_run(str(c))
                # 表头: 仿宋_GB2312 10.5pt 加粗 (原报告取证)
                _set_font(r, FANGSONG, 10.5, True)
            # merge_rect: [(r1,c1,r2,c2)] 矩形区合并 — 先清空非首格文本再合并 (python-docx merge 会拼接被并格文本)
            for r1, c1, r2, c2 in t.get("merge_rect") or []:
                for rr in range(r1, r2 + 1):
                    for cc in range(c1, c2 + 1):
                        if (rr, cc) != (r1, c1):
                            dt.rows[rr].cells[cc].text = ""
                dt.rows[r1].cells[c1].merge(dt.rows[r2].cells[c2])
        for row in t["rows"]:
            cells = dt.add_row().cells
            for i, v in enumerate(row):
                if i >= len(cells):
                    break
                cells[i].text = ""
                r = cells[i].paragraphs[0].add_run(str(v))
                # 表格正文: 仿宋_GB2312 10.5pt (原报告取证: FangSong/仿宋 sz=21 半磅)
                _set_font(r, FANGSONG, 10.5)
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
