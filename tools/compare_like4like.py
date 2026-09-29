"""同类对同类比对 — 修正上一版映射重叠造成的虚高

问题 (上轮): 原报告的 "4.x 综合性评价" 是**短评价结论**, 详细内容在**附录(工程分析)**里。
      我却拿生成报告的 3.x(工程分析, 含全部细节) 去对 4.x(结论) → 比值虚高几个数量级。

正解: 原报告 4.x 应与生成的 **对应"评价"节**比 (3.2/3.3/3.5... 的**评价**性质内容),
      或直接与原报告**附录**比 (附录才是细节所在)。

本脚本按"同类对同类"重算:
  A. 原报告附录 → 生成对应章 (细节 vs 细节)
  B. 原报告 4.x 结论 → 生成对应节的评价段 (结论 vs 结论)
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

_H_ORIG = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\s、.．]+(\S.{0,38})$")
_H_GEN = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s{2,}(\S.{0,38})$")

# A. 原报告附录 → 生成章 (细节对细节)
A_MAP = {
    "7 资料性附录（1）——评价要点": ["1"],
    "8 资料性附录（2）——工程分析": ["3"],
    "9 资料性附录（3）——类比调查分析": ["4"],
    "10 资料性附录（4）——职业病危害因素识别与评价": ["5"],
    "11 资料性附录（5）——职业病危害防护措施分析与评价": ["6", "7", "8"],
    "12 资料性附录（6）——综合性分析与评价": ["3.2", "3.3", "3.5", "3.6", "3.7", "3.8", "9", "10"],
}
# B. 原报告主报告 1-6 章 → 生成等效节
B_MAP = {
    "1 建设项目概况": ["2", "3.1"],
    "2 职业病危害因素识别与评价": ["5"],
    "3 职业病危害防护措施评价": ["6", "7", "8"],
    "4 综合性评价": ["3.2", "3.3", "3.5", "3.6", "3.7", "3.8", "9", "10"],
    "5 职业病补充措施及建议": ["11"],
    "6 评价结论": ["12"],
}


def blocks(doc):
    for ch in doc.element.body.iterchildren():
        if ch.tag == qn("w:p"):
            yield Paragraph(ch, doc)
        elif ch.tag == qn("w:tbl"):
            yield Table(ch, doc)


def collect(path: Path, gen: bool):
    doc = docx.Document(str(path))
    rx = _H_GEN if gen else _H_ORIG
    per, cur, tbl = {}, None, {}
    for b in blocks(doc):
        if isinstance(b, Paragraph):
            t = b.text.strip()
            if not t:
                continue
            m = rx.match(t) if len(t) <= 46 and not t.endswith(("。", "；", "，", "：")) else None
            if m and not m.group(2).strip()[:1].isdigit():
                cur = m.group(1)
                per.setdefault(cur, 0)
                continue
            if cur:
                per[cur] = per.get(cur, 0) + len(t)
        else:
            if cur:
                tbl[cur] = tbl.get(cur, 0) + 1
    return per, tbl, len(doc.tables)


def subtree(per, prefix):
    return sum(v for k, v in per.items() if k == prefix or k.startswith(prefix + "."))


def main():
    op, ot, otb = collect(ORIG, False)
    gp, gt, gtb = collect(GEN, True)
    print(f"原报告: 表格 {otb} | 生成: 表格 {gtb}")
    print()
    for title, mp in (("A. 附录 vs 生成对应章 (细节↔细节)", A_MAP),
                      ("B. 主报告 vs 生成等效节 (结论↔含结论)", B_MAP)):
        print("=" * 88)
        print(title)
        print(f"{'原报告章节':44}{'原字':>7}{'生字':>8}{'比':>8}")
        print("-" * 88)
        T1 = T2 = 0
        for k, gs in mp.items():
            num = k.split()[0]
            ov = subtree(op, num)
            gv = sum(subtree(gp, n) for n in gs)
            T1 += ov
            T2 += gv
            r = f"{gv*100//max(ov,1)}%"
            flag = ""
            if ov and (gv < ov * 0.5):
                flag = "  ⚠偏少"
            print(f"{k:44}{ov:>7}{gv:>8}{r:>8}{flag}")
        print("-" * 88)
        print(f"{'合计':44}{T1:>7}{T2:>8}{f'{T2*100//max(T1,1)}%':>8}")
        print()


if __name__ == "__main__":
    main()
