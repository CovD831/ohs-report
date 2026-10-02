import sys, zipfile, re
from lxml import etree
sys.stdout.reconfigure(encoding='utf-8')
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
p = sys.argv[1] if len(sys.argv) > 1 else '/tmp/ohs_115.docx'
x = etree.fromstring(zipfile.ZipFile(p).read('word/document.xml'))
ps = [''.join(n.text or '' for n in pp.iter(f'{W}t')) for pp in x.iter(f'{W}p')]
tl = [''.join(n.text or '' for n in t.iter(f'{W}t')) for t in x.iter(f'{W}tbl')]

def clean(s):
    return re.sub(r'\s+', '', s)

# 11.5 子树
s_idx = e_idx = None
for i, t in enumerate(ps):
    if re.match(r'^11\.5\s', t.strip()):
        s_idx = i
    elif s_idx and i > s_idx and re.match(r'^11\.6\s', t.strip()):
        e_idx = i
        break
if s_idx is not None:
    seg = ps[s_idx:(e_idx or s_idx + 80)]
    body = ''.join(seg)
    print(f"11.5 段落数 {len(seg)}, 正文字(去空白) {len(clean(body))}")
    for t in seg[:6]:
        print("   |", t[:90])

allt = '\n'.join(ps) + '\n' + '\n'.join(tl)
print("\n=== 红线自查 ===")
print("废止 GBZ 2.2—2007/2007 引用:", allt.count('GBZ 2.2—2007') + allt.count('GBZ 2.2-2007'))
print("字面 None:", len(re.findall(r'\bNone\b', allt)))
print("待补充:", allt.count('待补充'))
print("总表数:", len(list(etree.fromstring(zipfile.ZipFile(p).read('word/document.xml')).iter(f'{W}tbl'))))
