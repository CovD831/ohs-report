import sys, zipfile, re
from lxml import etree
sys.stdout.reconfigure(encoding='utf-8')
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
p = sys.argv[1] if len(sys.argv) > 1 else '/tmp/rec16_conv/长兴--预评（备案稿7-31）.docx'
x = etree.fromstring(zipfile.ZipFile(p).read('word/document.xml'))
items = []
for e in x.iter():
    if e.tag == f'{W}p':
        items.append(('p', ''.join(n.text or '' for n in e.iter(f'{W}t'))))
    elif e.tag == f'{W}tbl':
        items.append(('t', ''.join(n.text or '' for n in e.iter(f'{W}t'))))

def nw(s):
    return len(re.sub(r'\s', '', s))

def t_of(i):
    return items[i][1]

def sub(sn, nxt):
    # 真稿前 40 段是目录(TOC) → 跳过 TOC 区, 只在正文区找标题
    heads = [i for i, (k, t) in enumerate(items)
             if k == 'p' and re.match(r'^' + re.escape(sn) + r'\s', t.strip())]
    # 目录行形如 "7.1 项目背景35" (末尾页码) → 排除; 取最后一个匹配
    real = [i for i in heads if not re.search(r'\d$', t_of(i).strip())]
    if not real:
        return []
    a = real[-1]
    b = None
    for i in range(a + 1, len(items)):
        k, t = items[i]
        if k == 'p' and re.match(r'^' + re.escape(nxt) + r'\s', t.strip()) and not re.search(r'\d$', t.strip()):
            b = i
            break
    return items[a:b] if a is not None else []

specs = sys.argv[2:] if len(sys.argv) > 2 else ['1.1|1.2', '3.1.6|3.1.7', '3.1.7|3.2']
for sp in specs:
    sn, nxt = sp.split('|')
    seg = sub(sn, nxt)
    txt = ''.join(t for k, t in seg)
    tbl = sum(len(t) for k, t in seg if k == 't')
    npar = sum(1 for k, _ in seg if k == 'p')
    print(f"=== {sn}: 共{nw(txt)}字, 表{tbl}字, 段{npar}")
    for k, t in seg[:6]:
        if t.strip():
            print(f"   [{k}] {t[:120]}")
    print()
