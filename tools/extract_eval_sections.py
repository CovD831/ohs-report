"""提取真实报告"评价章"逐节写法 — 供用户人工审读后定结构迁移口径

重点节区: 11.x (各项评价) + 12.x (分析与评价, 检查表块)
按真实报告原样输出: 标题 → 正文段落 → 表格(表题+表头+前几行)
"""
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

D = Path("/tmp/ohs_bench")
FILES = [
    "1、正力（银河基地）职业卫生预评（备案稿）0818",
    "奥绮斯摩预评-1-28修编（备案稿）",
    "格兰富  职业卫生预评（备案稿）",
    "波士胶--职业卫生预评（备案稿）0510",
    "祺珠预评-（备案稿）",
    "聚和--预评（备案稿）",
    "苏州鼎沛自动化科技有限公司  新建项目 职业卫生预评（备案稿）",
    "长兴--预评（备案稿7-31）",
]

HEAD = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\s*[、.．]?\s*(\S.{0,48})$")


def blocks(doc):
    """按文档流产出 ('h', 号, 标题) / ('p', 文本) / ('t', 表格)"""
    for el in doc.element.body.iterchildren():
        if el.tag == qn("w:p"):
            p = Paragraph(el, doc)
            t = p.text.strip()
            if not t:
                continue
            m = HEAD.match(t)
            if m and len(t) < 60:
                yield ("h", m.group(1), m.group(2).strip())
            else:
                yield ("p", t)
        elif el.tag == qn("w:tbl"):
            yield ("t", Table(el, doc))


def dump_section(doc, want: str) -> str:
    """输出 want 节 (含子节) 的完整内容"""
    out, on, base = [], False, None
    for item in blocks(doc):
        if item[0] == "h":
            _, num, title = item
            if num == want or (num.startswith(want + ".") and base is None):
                on = True
                base = num
                out.append(f"\n{'#' * (num.count('.') + 2)} {num} {title}")
                continue
            if on:
                # 到下一个同级或更高级 (不在 want 前缀下) → 停
                if not num.startswith(want):
                    break
                out.append(f"\n{'#' * (num.count('.') + 2)} {num} {title}")
        elif on:
            if item[0] == "p":
                out.append(item[1])
            else:
                tb = item[1]
                if not tb.rows:
                    continue
                hdr = " | ".join(c.text.strip().replace("\n", " ")[:16]
                                 for c in tb.rows[0].cells)
                out.append(f"  〔表格 {len(tb.rows)}行〕表头: {hdr}")
                for r in tb.rows[1:4]:
                    row = " | ".join(c.text.strip().replace("\n", " ")[:22] for c in r.cells)
                    out.append(f"      {row}")
    return "\n".join(out)


def main():
    want = sys.argv[1:] or ["12.1", "12.2", "12.3", "12.4", "12.5"]
    for f in FILES:
        p = D / f"{f}.docx"
        if not p.exists():
            continue
        doc = docx.Document(str(p))
        found = []
        for w in want:
            s = dump_section(doc, w)
            if s.strip():
                found.append((w, s))
        if not found:
            continue
        print("=" * 84)
        print(f"【{f[:52]}】")
        for w, s in found:
            print(s[:2600])
        print()


if __name__ == "__main__":
    main()
