"""细查 >300% 的章节: 拆解映射构成 + 抽样生成内容

对上轮 compare_full 里比值异常的章节, 逐个拆开:
  ① 原报告该节的实际内容 (是什么)
  ② 生成侧映射到的多个小节各自多少字 (哪些在贡献)
  ③ 判定: 映射重叠虚高 / 生成确实偏长
"""
import json
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

GEN = Path("/Users/abaaba/Projects/ohs-report/data/report_c8c7ff0a4d.docx")
ORIG = Path("/tmp/bench_orig/长兴--预评（备案稿7-31）.docx")

# 上轮 >300% 的章节 (仅列主要)
SUSPECT = [
    ("4.1 选址、总体布局评价", "4.1", ["3.2", "3.3"]),
    ("4.2 生产工艺及设备布局评价", "4.2", ["3.5", "3.6"]),
    ("4.3 建筑卫生学评价", "4.3", ["3.7"]),
    ("4.4 辅助用室评价", "4.4", ["3.8"]),
    ("4.5 职业卫生管理评价", "4.5", ["9"]),
]

_H_ORIG = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\s、.．]+(\S.{0,38})$")
_H_GEN = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s{2,}(\S.{0,38})$")


def blocks(doc):
    for ch in doc.element.body.iterchildren():
        if ch.tag == qn("w:p"):
            yield Paragraph(ch, doc)
        elif ch.tag == qn("w:tbl"):
            yield Table(ch, doc)


def collect(path: Path, gen: bool):
    """{节号: [段落...]}"""
    doc = docx.Document(str(path))
    rx = _H_GEN if gen else _H_ORIG
    per = {}
    cur = None
    for b in blocks(doc):
        if isinstance(b, Paragraph):
            t = b.text.strip()
            if not t:
                continue
            m = rx.match(t) if len(t) <= 46 and not t.endswith(("。", "；", "，", "：")) else None
            if m and not m.group(2).strip()[:1].isdigit():
                cur = m.group(1)
                per.setdefault(cur, [])
                continue
            if cur:
                per.setdefault(cur, []).append(t)
    return per


def sub(per: dict, prefix: str):
    return {k: v for k, v in per.items()
            if k == prefix or k.startswith(prefix + ".")}


def main():
    o = collect(ORIG, gen=False)
    g = collect(GEN, gen=True)
    for label, onum, gnums in SUSPECT:
        ov = sub(o, onum)
        oc_ = sum(len("".join(v)) for v in ov.values())
        print("=" * 84)
        print(f"原报告 {label}  = {oc_} 字")
        # 原报告该节的正文样本
        for k, v in list(ov.items())[:3]:
            txt = " ".join(v)
            print(f"   原[{k}] {len(txt):>5}字: {txt[:120]}")
        print()
        print(f"  → 生成侧映射 {gnums}:")
        tot = 0
        for gn in gnums:
            sg = sub(g, gn)
            s = sum(len("".join(v)) for v in sg.values())
            tot += s
            print(f"     [{gn}] 合计 {s} 字 (子节 {sorted(sg.keys())})")
            # 抽一子节看内容
            for k2, v2 in list(sg.items())[:2]:
                t2 = " ".join(v2)
                if t2:
                    print(f"        {k2} {len(t2):>5}字: {t2[:100]}")
        print(f"  ⇒ 生成合计 {tot} 字 / 原 {oc_} 字 = {tot*100//max(oc_,1)}%")
        print()


if __name__ == "__main__":
    main()
