"""量化"正文罗列清单"问题 — 该进表格的内容被写进正文

判据: 单段内 顿号分隔项 >= 8 且该段无"见表"引用 → 疑似罗列清单
      这类内容原报告用表格呈现, 生成版写进正文 → 冗长且重复
"""
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

GEN = Path("/Users/abaaba/Projects/ohs-report/data/report_c8c7ff0a4d.docx")
ORIG = Path("/tmp/bench_orig/长兴--预评（备案稿7-31）.docx")


def all_paras(path: Path):
    doc = docx.Document(str(path))
    return [Paragraph(ch, doc).text.strip()
            for ch in doc.element.body.iterchildren()
            if ch.tag == qn("w:p") and Paragraph(ch, doc).text.strip()]


def scan(path: Path, label: str):
    ps = all_paras(path)
    total = sum(len(p) for p in ps)
    hits = []
    for p in ps:
        if len(p) < 80:
            continue
        segs = [s for s in re.split(r"[、，,；;]", p) if s.strip()]
        # 长且切分项多, 且没有表格引用
        if len(segs) >= 12 and "见表" not in p and "表" + "" not in p[:2]:
            hits.append((len(p), len(segs), p[:100]))
    hl = sum(h[0] for h in hits)
    print(f"=== {label} ===")
    print(f"  正文总字符 {total} | 疑似罗列段 {len(hits)} 段, 合计 {hl} 字 "
          f"({hl*100//max(total,1)}% 正文)")
    for ln, ns, s in sorted(hits, reverse=True)[:6]:
        print(f"    [{ln:>5}字 / {ns:>3}项] {s}")
    print()


def main():
    scan(GEN, "生成报告")
    scan(ORIG, "原报告")


if __name__ == "__main__":
    main()
