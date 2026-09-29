"""取证: 原报告标题的排版定义 (字体/字号/加粗/对齐/缩进) + numPr 编号定义

"一切以贴近真实报告为准" → 必须逐项读出原报告的实际值, 不能凭印象。
"""
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

ORIG = Path("/tmp/bench_orig/长兴--预评（备案稿7-31）.docx")


def rfonts(run):
    rpr = run._element.rPr
    if rpr is None:
        return {}
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        return {}
    return {k.split("}")[-1]: v for k, v in rf.attrib.items()}


def heading_detail(doc, maxn=2):
    """按层级收集标题的完整排版属性"""
    from collections import defaultdict
    by = defaultdict(list)
    for ch in doc.element.body.iterchildren():
        if ch.tag != qn("w:p"):
            continue
        p = Paragraph(ch, doc)
        t = p.text.strip()
        if not t or len(t) > 46:
            continue
        m = re.match(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\s、.．]+(\S.*)$", t)
        if not m or m.group(2)[:1].isdigit():
            continue
        lvl = m.group(1).count(".") + 1
        if lvl > 4:
            continue
        runs = p.runs
        r = runs[0] if runs else None
        pPr = p._p.pPr
        numpr = None
        if pPr is not None and pPr.numPr is not None:
            ilvl = pPr.numPr.ilvl
            numid = pPr.numPr.numId
            numpr = (ilvl.get(qn("w:val")) if ilvl is not None else None,
                     numid.get(qn("w:val")) if numid is not None else None)
        sz = None
        if r is not None and r.font.size:
            sz = r.font.size.pt
        by[lvl].append({
            "text": t, "rfonts": rfonts(r) if r else {},
            "size": sz, "bold": (r.font.bold if r else None),
            "align": str(p.alignment), "numPr": numpr,
            "style": p.style.name,
            "indent": (p.paragraph_format.first_line_indent.pt
                       if p.paragraph_format.first_line_indent else None),
        })
    for lvl in sorted(by):
        rows = by[lvl][:maxn]
        print(f"--- L{lvl} ({len(by[lvl])} 个) ---")
        for x in rows:
            print(f"    {x['text'][:30]:32} 字体={x['rfonts']} 字号={x['size']} "
                  f"粗={x['bold']} 对齐={x['align'].split()[0]} numPr={x['numPr']}")


def numbering_defs(doc):
    """读 numbering.xml 里 abstractNum / num 的定义 (编号格式/缩进)"""
    try:
        part = doc.part.package.part_related_by(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering")
    except Exception:
        return "无 numbering part"
    xml = part.blob.decode("utf-8")
    # abstractNum: 各 level 的 numFmt / lvlText / indent
    out = []
    for m in re.finditer(
            r'<w:abstractNum w:abstractNumId="(\d+)"[^>]*>(.*?)</w:abstractNum>', xml, re.S):
        aid, body = m.group(1), m.group(2)
        lv = []
        for lm in re.finditer(r'<w:lvl w:ilvl="(\d+)"[^>]*>(.*?)</w:lvl>', body, re.S):
            ilvl, lb = lm.group(1), lm.group(2)
            fmt = re.search(r'<w:numFmt w:val="([^"]+)"', lb)
            txt = re.search(r'<w:lvlText w:val="([^"]*)"', lb)
            ind = re.search(r'<w:ind[^>]*w:firstLine="([^"]*)"', lb)
            lv.append((ilvl, fmt.group(1) if fmt else "?",
                       txt.group(1) if txt else "?", ind.group(1) if ind else ""))
        out.append((aid, lv[:4]))
    print(f"abstractNum 数量: {len(out)}")
    for aid, lv in out[:6]:
        print(f"  abstractNum {aid}:")
        for ilvl, fmt, txt, ind in lv:
            print(f"    ilvl={ilvl} fmt={fmt:8} lvlText={txt!r:12} firstLine={ind}")
    nums = re.findall(r'<w:num w:numId="(\d+)"[^>]*>\s*<w:abstractNumId w:val="(\d+)"', xml)
    print(f"  num → abstractNum 映射: {nums[:10]}")


def main():
    doc = docx.Document(str(ORIG))
    print("=" * 90)
    print("原报告标题排版取证")
    print("=" * 90)
    heading_detail(doc)
    print()
    print("=" * 90)
    print("原报告 numbering.xml 定义")
    print("=" * 90)
    numbering_defs(doc)


if __name__ == "__main__":
    main()
