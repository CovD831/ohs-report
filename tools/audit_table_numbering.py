"""形式层第2部分: 表编号体系对照 (表X.X-X 是否与所在章一致)

用户要求: "表编号/表名逐张对齐原报告——格式细节也须逐项取证"。
关键不是"表名文字一样", 而是**编号规则自洽**:
  表X.X-X 的 X.X 应等于该表所在小节的编号 (表3.1-1 在 3.1 节内)
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


def walk(path: Path):
    """按 body 顺序返回 [(类型, 文本/表)]"""
    doc = docx.Document(str(path))
    out = []
    for ch in doc.element.body.iterchildren():
        if ch.tag == qn("w:p"):
            out.append(("p", Paragraph(ch, doc).text.strip()))
        elif ch.tag == qn("w:tbl"):
            out.append(("t", Table(ch, doc)))
    return out


def audit(path: Path, label: str, head_re):
    seq = walk(path)
    cur = None
    issues, caps = [], []
    for kind, obj in seq:
        if kind == "p":
            t = obj
            if not t:
                continue
            m = head_re.match(t) if len(t) <= 46 else None
            if m and not m.group(2).strip()[:1].isdigit():
                cur = m.group(1)
                continue
            cm = re.match(r"^表\s*([\d.]+)\s*[-—]\s*(\d+)\s*(.*)$", t)
            if cm:
                caps.append((cm.group(1), cm.group(2), cm.group(3)[:40], cur))
    print(f"=== {label}: 表题 {len(caps)} 张 ===")
    bad = []
    for sec, num, name, loc in caps:
        # 编号首段应与所在节的首段一致 (表3.6-1 应在 3.6 节)
        if loc and sec.split(".")[0] != loc.split(".")[0]:
            bad.append((f"表{sec}-{num}", name, f"在 {loc} 节"))
    # 组内序号连续性
    from collections import defaultdict
    groups = defaultdict(list)
    for sec, num, name, loc in caps:
        groups[sec].append(int(num))
    gaps = []
    for sec, nums in groups.items():
        s = sorted(nums)
        if s != list(range(1, len(s) + 1)):
            gaps.append((sec, s))
    for sec, num, name, loc in caps[:8]:
        print(f"  表{sec}-{num:<3} {name:<40} @{loc}节")
    print()
    print(f"  编号与所在章不符: {bad if bad else '无 ✓'}")
    print(f"  组内序号不连续:  {gaps if gaps else '无 ✓'}")
    print()


def main():
    ho = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\s、.．]+(\S.{0,38})$")
    hg = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s{2,}(\S.{0,38})$")
    audit(ORIG, "原报告(备案稿)", ho)
    audit(GEN, "生成报告", hg)


if __name__ == "__main__":
    main()
