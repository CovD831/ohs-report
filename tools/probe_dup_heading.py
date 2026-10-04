import sys, zipfile, re
from lxml import etree
sys.stdout.reconfigure(encoding='utf-8')
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
p = sys.argv[1] if len(sys.argv) > 1 else 'data/report_ada54603a9.docx'
PATS = sys.argv[2:] or [r'^1\.1\s', r'^1\.2\s', r'^9\.2\s', r'^12\.1\s']
x = etree.fromstring(zipfile.ZipFile(p).read('word/document.xml'))
ps = []
for e in x.iter(W + 'p'):
    txt = ''.join(n.text or '' for n in e.iter(W + 't'))
    st = e.find(W + 'pPr/' + W + 'pStyle')
    sty = st.get(W + 'val') if st is not None else '-'
    lv = e.find(W + 'pPr/' + W + 'outlineLvl')
    lvv = lv.get(W + 'val') if lv is not None else '-'
    ps.append((txt, sty, lvv))
for i, (t, sty, lvv) in enumerate(ps):
    s = t.strip()
    for pat in PATS:
        if re.match(pat, s):
            print(f"{i:>5} style={sty:<10} lvl={lvv} | {s[:75]}")
            # 打印紧随其后的 2 段 (看是否重复标题)
            for j in range(i + 1, min(i + 3, len(ps))):
                print(f"      +{j-i} [{ps[j][1]}] {ps[j][0].strip()[:75]}")
            break
