"""调研: 8 份真实预评报告里"检测结果"如何呈现 (为表型B泛化定依据)

用户原则: 关于规则/模板"看已有的报告", 不拍脑袋。
本脚本只取证, 不改代码。

关注:
  ① 检测结果表是"一因素一行"还是"一岗位多因素"
  ② 是否出现"合格率/符合率"这类表述 (x/y)
  ③ 未检出的因素怎么写
  ④ 多因素岗位在正文里怎么表述
"""
import re
from pathlib import Path

import docx

REPS = sorted(Path("/tmp/rep_probe").glob("*.docx"))


def all_text(p):
    d = docx.Document(str(p))
    paras = [x.text for x in d.paragraphs]
    tbl = []
    for t in d.tables:
        for row in t.rows:
            tbl.append([c.text.strip() for c in row.cells])
    return paras, tbl


print("=" * 82)
print("① 检测结果表的形态 (看表头)")
print("=" * 82)
for p in REPS:
    paras, tbl = all_text(p)
    hit = [r for r in tbl if any("危害因素" in c or "检测项目" in c for c in r[:4])]
    print(f"\n--- {p.stem[:30]} ({len(tbl)} 表) ---")
    for r in hit[:2]:
        print(f"   表头/首行: {r[:9]}")

print()
print("=" * 82)
print("② '合格率/符合率' 类表述 (x/y 形式)")
print("=" * 82)
for p in REPS:
    paras, tbl = all_text(p)
    txt = "\n".join(paras)
    hits = re.findall(r"[\d.]+\s*/\s*[\d.]+\s*(?:合格|符合|超标)", txt)
    # 表格里也找
    for r in tbl:
        for c in r:
            if re.search(r"\d+\s*/\s*\d+", c):
                hits.append(c[:30])
    print(f"  {p.stem[:30]:32} {len(hits)} 处  {hits[:3]}")

print()
print("=" * 82)
print("③ 多因素挤在一个单元格 (顿号分隔 >=2 个因素)")
print("=" * 82)
for p in REPS:
    paras, tbl = all_text(p)
    n = 0
    samples = []
    for r in tbl:
        for c in r:
            if c.count("、") >= 2 and re.search(r"(粉尘|噪声|高温|酸|碱|氟|氯|苯|醛|酮)", c):
                n += 1
                if len(samples) < 3:
                    samples.append(c[:40])
    print(f"  {p.stem[:30]:32} {n} 处  {samples}")

print()
print("=" * 82)
print("④ 正文里'多因素岗位'的表述 (含顿号 + 危害因素)")
print("=" * 82)
for p in REPS:
    paras, _ = all_text(p)
    hits = [x for x in paras
            if x.count("、") >= 2 and re.search(r"(接触|存在).{0,20}(危害因素|因素)", x)
            and 20 < len(x) < 120]
    print(f"\n--- {p.stem[:28]} ---")
    for x in hits[:2]:
        print(f"   {x[:150]}")
