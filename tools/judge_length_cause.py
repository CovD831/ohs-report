"""判定: 生成偏长的章节, 是"注水套话" 还是"信息密度更高"?

客观指标 (不看主观感觉):
  ① 数字密度   = 数值token数 / 字数   (高 → 在引数据, 不是空话)
  ② 套话特征词 = 综上所述/一般来说/值得注意的是/极大地/有效地 ...
  ③ 具体名物   = 设备/物料/标准号 出现次数 (GBZ/GB/T/具体化学名)

对照: 原报告同节 vs 生成同节, 看三项指标谁高。
"""
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

GEN = Path("/Users/abaaba/Projects/ohs-report/data/report_c8c7ff0a4d.docx")
ORIG = Path("/tmp/bench_orig/长兴--预评（备案稿7-31）.docx")

_H_ORIG = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\s、.．]+(\S.{0,38})$")
_H_GEN = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s{2,}(\S.{0,38})$")

# 套话 (AI腔/空话) 特征
CLICHE = ["综上所述", "一般来说", "值得注意的是", "极大地", "有效地", "为了更好地",
          "随着", "众所周知", "不言而喻", "由此可见", "总而言之", "从而", "进而",
          "大力", "高度重视", "不断加强", "切实提高", "全面推进"]
# 具体性标记 (标准号/单位/量)
SPECIFIC = re.compile(r"(GBZ|GB/T|GB\s?\d|mg/m|dB|℃|吨/年|平方米|㎡|台\(套\)|《[^》]{2,20}》)")
NUM = re.compile(r"\d+(?:\.\d+)?")


def paras(path: Path, gen: bool, prefix: str):
    doc = docx.Document(str(path))
    rx = _H_GEN if gen else _H_ORIG
    cur, out = None, []
    for ch in doc.element.body.iterchildren():
        if ch.tag != qn("w:p"):
            continue
        t = Paragraph(ch, doc).text.strip()
        if not t:
            continue
        m = rx.match(t) if len(t) <= 46 and not t.endswith(("。", "；", "，", "：")) else None
        if m and not m.group(2).strip()[:1].isdigit():
            cur = m.group(1)
            continue
        if cur == prefix:
            out.append(t)
    return out


def metrics(txt: str) -> dict:
    n = max(len(txt), 1)
    return {
        "字符": len(txt),
        "数字密度‰": round(len(NUM.findall(txt)) * 1000 / n, 1),
        "具体标记‰": round(len(SPECIFIC.findall(txt)) * 1000 / n, 1),
        "套话‰": round(sum(txt.count(c) for c in CLICHE) * 1000 / n, 1),
    }


# 同构配对 (原附录节 ↔ 生成对应节)
PAIRS = [
    ("9.1.1", "4.1.1"), ("9.2.1", "4.2.1"), ("9.2.5", "4.2.5"),
    ("9.4", "4.4"), ("9.5.1", "4.5.1"), ("9.6.6", "4.6.6"),
]


def main():
    print(f"{'原节':8}{'生节':8}{'字符(原/生)':>16}{'数字密度‰':>16}{'具体标记‰':>16}{'套话‰':>14}")
    print("-" * 92)
    for o, g in PAIRS:
        ot = " ".join(paras(ORIG, False, o))
        gt = " ".join(paras(GEN, True, g))
        if not ot or not gt:
            print(f"{o:8}{g:8}  (缺: 原{len(ot)}/生{len(gt)})")
            continue
        mo, mg = metrics(ot), metrics(gt)
        print(f"{o:8}{g:8}{mo['字符']:>7}/{mg['字符']:<8}"
              f"{mo['数字密度‰']:>7}/{mg['数字密度‰']:<8}"
              f"{mo['具体标记‰']:>7}/{mg['具体标记‰']:<8}"
              f"{mo['套话‰']:>6}/{mg['套话‰']:<7}")


if __name__ == "__main__":
    main()
