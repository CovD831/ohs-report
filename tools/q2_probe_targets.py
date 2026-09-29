"""Q2 取证: 真实报告怎么处理那 7 个"识别出但未测"的因素

针对具体因素搜: 工频电场 / 照度 / 氧化钙 / 氯 / 石灰石粉尘 / 粉尘
看它们出现在报告的哪些位置、用什么措辞。
"""
import re
from docx import Document

DOC = "/tmp/xt_probe/6-1新泰--预评（备案稿）.docx"
doc = Document(DOC)

paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

TARGETS = ["工频电场", "照度", "氧化钙", "石灰石粉尘", "呼尘", "其他粉尘"]

print("=" * 78)
print("① 各目标因素在正文段落中的出现情况")
print("=" * 78)
for t in TARGETS:
    hits = [(i, p) for i, p in enumerate(paras) if t in p]
    print(f"\n### {t}  (正文命中 {len(hits)} 段)")
    for i, p in hits[:4]:
        print(f"  [{i}] {p[:230]}")

print()
print("=" * 78)
print("② 表格中的出现情况 (重点: 检测结果表 vs 识别表)")
print("=" * 78)
for ti, tb in enumerate(doc.tables):
    txt = "\n".join(c.text for r in tb.rows for c in r.cells)
    hits = [t for t in TARGETS if t in txt]
    if not hits:
        continue
    try:
        hdr = " | ".join(c.text.strip()[:22] for c in tb.rows[0].cells)
    except Exception:
        hdr = "?"
    print(f"\n--- 表{ti} ({len(tb.rows)}行) 含: {hits}")
    print(f"    表头: {hdr[:170]}")

print()
print("=" * 78)
print("③ 检测结果章节的相关表述 (含'未' / '不测' / '无' 的句子)")
print("=" * 78)
n = 0
for i, p in enumerate(paras):
    if re.search(r"(识别|确定).{0,30}(危害因素|因素)", p) and re.search(r"检测|测量", p) and len(p) < 400:
        print(f"[{i}] {p[:300]}\n")
        n += 1
        if n >= 10:
            break
print(f"(命中 {n} 段)")
