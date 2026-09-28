"""原报告章节树 — 兼容自定义样式 (备案稿不用 Heading N 样式)

判据: 段落文本形如 '1 总论' / '3.1 工程概况分析' / '3.1.1 xxx' 且序号连续,
      且不含句末标点、长度短 → 视为标题。
"""
import json
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else
           "/tmp/bench_orig/长兴--预评（备案稿7-31）.docx")

_HEAD = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\s、.．]+(\S.{0,38})$")


def iter_blocks(doc):
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def is_head(t: str):
    t = t.strip()
    if not t or len(t) > 46:
        return None
    if t.endswith(("。", "；", "，", ",", ";", "：", ":")):
        return None
    m = _HEAD.match(t)
    if not m:
        return None
    num, txt = m.group(1), m.group(2).strip()
    if not txt or txt[0].isdigit():
        return None
    return num, txt


def main():
    doc = docx.Document(str(SRC))
    heads = []
    caps = []
    per = {}
    cur = None
    for b in iter_blocks(doc):
        if isinstance(b, Paragraph):
            t = b.text.strip()
            if not t:
                continue
            h = is_head(t)
            if h:
                num, txt = h
                heads.append((num.count(".") + 1, num, txt))
                cur = num
                per.setdefault(num, {"chars": 0, "tables": 0, "title": txt})
            if re.match(r"^表\s*[\d.\-—]+", t):
                caps.append(t[:60])
                if cur:
                    per.setdefault(cur, {"chars": 0, "tables": 0, "title": ""})["tables"] += 1
            if cur:
                per.setdefault(cur, {"chars": 0, "tables": 0, "title": ""})["chars"] += len(t)
        else:
            if cur:
                per.setdefault(cur, {"chars": 0, "tables": 0, "title": ""})["tables"] += 0

    print(f"标题 {len(heads)} 个 | 表题 {len(caps)}")
    print()
    print("=== 一级/二级章节树 ===")
    for lvl, num, txt in heads:
        if lvl <= 2:
            n = num.count(".")
            d = per.get(num, {})
            print(f"  {'  ' * (lvl - 1)}{num} {txt:34} 字数{d.get('chars', 0):>6} 表{d.get('tables', 0)}")
    Path("/tmp/orig_tree.json").write_text(
        json.dumps({"heads": heads, "caps": caps, "per": per},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n→ /tmp/orig_tree.json")


if __name__ == "__main__":
    main()
