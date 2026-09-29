"""形式层取证: 逐项提取原报告的格式特征

用户要求: "格式细节也须逐项取证, '大概像'不行" / 目录=真Word TOC域 /
         表编号/表名逐张对齐 / 法规清单一条一段自动编号 / 中间层级纯标题

本脚本只做**取证**(不改任何东西), 输出两报告的形式特征供逐项对照。
"""
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

GEN = Path("/Users/abaaba/Projects/ohs-report/data/report_c8c7ff0a4d.docx")
ORIG = Path("/tmp/bench_orig/长兴--预评（备案稿7-31）.docx")


def blocks(doc):
    for ch in doc.element.body.iterchildren():
        if ch.tag == qn("w:p"):
            yield Paragraph(ch, doc)
        elif ch.tag == qn("w:tbl"):
            yield Table(ch, doc)


def font_of(run):
    """取 run 的字体: 中文用 eastAsia, 西文用 ascii"""
    f = run.font
    rpr = run._element.rPr
    ea = None
    if rpr is not None:
        rf = rpr.find(qn("w:rFonts"))
        if rf is not None:
            ea = rf.get(qn("w:eastAsia")) or rf.get(qn("w:ascii"))
    return ea or (f.name if f else None)


def has_toc_field(doc) -> dict:
    """TOC 域检测: w:fldChar instrText 里是否含 TOC"""
    xml = doc.element.body.xml
    toc = "TOC" in xml
    # 找 instrText 内容
    m = re.findall(r"<w:instrText[^>]*>([^<]*)</w:instrText>", xml)
    toc_instr = [x for x in m if "TOC" in x.upper()]
    # 页码域
    pg = [x for x in m if "PAGE" in x.upper()]
    return {"有TOC域": toc, "TOC指令": toc_instr[:3], "字段总数": len(m),
            "PAGE域": len(pg)}


def numbering_info(doc) -> dict:
    """自动编号: numPr 使用情况"""
    xml = doc.element.body.xml
    numpr = xml.count("<w:numPr>")
    return {"numPr段落数": numpr}


def caption_patterns(doc) -> list:
    """表题: 匹配 '表X.X-X' 或 '表X-X' 形式"""
    caps = []
    for b in blocks(doc):
        if isinstance(b, Paragraph):
            t = b.text.strip()
            m = re.match(r"^(表\s*[\d.\-—]+)\s*(.*)$", t)
            if m and len(t) < 80:
                caps.append((m.group(1), m.group(2)[:44], font_of(b.runs[0]) if b.runs else None))
    return caps


def heading_fonts(doc) -> dict:
    """各级标题的样式/字体/字号"""
    out = {}
    for b in blocks(doc):
        if not isinstance(b, Paragraph):
            continue
        t = b.text.strip()
        if not t or len(t) > 46:
            continue
        m = re.match(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\s、.．]{1,3}(\S.*)$", t)
        if not m or m.group(2)[:1].isdigit():
            continue
        lvl = m.group(1).count(".") + 1
        if lvl > 4:
            continue
        r = b.runs[0] if b.runs else None
        key = f"L{lvl}"
        if key not in out:
            out[key] = {"样式": b.style.name, "字体": font_of(r) if r else None,
                        "字号": (r.font.size.pt if r and r.font.size else None),
                        "样例": t[:26]}
    return out


def body_font(doc) -> dict:
    """正文段落字体/字号 (取最多的组合)"""
    from collections import Counter
    cnt = Counter()
    for b in blocks(doc):
        if isinstance(b, Paragraph):
            t = b.text.strip()
            if len(t) < 30:
                continue
            r = b.runs[0] if b.runs else None
            if r:
                sz = r.font.size.pt if r.font.size else None
                cnt[(font_of(r), sz)] += 1
    return {"最常见": cnt.most_common(3)}


def main():
    for label, path in (("原报告(备案稿)", ORIG), ("生成报告", GEN)):
        if not path.exists():
            print(f"缺失: {path}")
            continue
        doc = docx.Document(str(path))
        print("=" * 88)
        print(f"### {label}")
        print("=" * 88)
        print(f"TOC:      {has_toc_field(doc)}")
        print(f"编号:     {numbering_info(doc)}")
        print(f"正文字体: {body_font(doc)}")
        print("标题字体:")
        for k, v in sorted(heading_fonts(doc).items()):
            print(f"  {k}: 样式={v['样式']:<12} 字体={str(v['字体']):<14} "
                  f"字号={v['字号']} | {v['样例']}")
        caps = caption_patterns(doc)
        print(f"表题 ({len(caps)}):")
        for c in caps[:12]:
            print(f"  {c[0]:<12} | {c[1]:<44} | 字体={c[2]}")
        if len(caps) > 12:
            print(f"  ... 余 {len(caps)-12} 条")
        print()


if __name__ == "__main__":
    main()
