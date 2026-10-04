import sys, zipfile, re
from lxml import etree
sys.stdout.reconfigure(encoding='utf-8')
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
p = sys.argv[1] if len(sys.argv) > 1 else 'data/report_ada54603a9.docx'
x = etree.fromstring(zipfile.ZipFile(p).read('word/document.xml'))
items = []
for e in x.iter():
    if e.tag == f'{W}p':
        items.append(('p', ''.join(n.text or '' for n in e.iter(f'{W}t'))))
    elif e.tag == f'{W}tbl':
        items.append(('t', ''.join(n.text or '' for n in e.iter(f'{W}t'))))

def nw(s): return len(re.sub(r'\s', '', s))

# 表号 / 表题  (表3.1-3 / 表 3.1 - 3 皆可)
TNO = re.compile(r'^表\s*([\d.]+)\s*[-–]\s*(\d+)\s*(.*)')
tbl_titles = []
for k, t in items:
    if k == 'p':
        m = TNO.match(t.strip())
        if m:
            tbl_titles.append((f"表{m.group(1)}-{m.group(2)}", m.group(3)[:40]))
print(f"共 {len(items)} 块 | 表 {sum(1 for k,_ in items if k=='t')} 张 | 表题 {len(tbl_titles)}")
print("--- 表题清单 ---")
from collections import Counter
cnt = Counter(n for n, _ in tbl_titles)
for no, name in tbl_titles:
    dup = "  ⚠重号" if cnt[no] > 1 else ""
    print(f"  {no}  {name}{dup}")

# 悬空引用
nums = set(cnt)
refs = set()
for k, t in items:
    if k == 'p':
        for m in re.finditer(r'见表\s*([\d.]+)\s*[-–]\s*(\d+)', t):
            refs.add(f"表{m.group(1)}-{m.group(2)}")
dangling = sorted(r for r in refs if r not in nums)
print(f"--- 引用 {len(refs)} 个表号; 悬空 {len(dangling)}: {dangling}")

# 3.1.6 区段
print("\n--- 3.1.6 区段 ---")
a = None
for i, (k, t) in enumerate(items):
    if k == 'p' and re.match(r'^3\.1\.6\s', t.strip()):
        a = i
    if a and k == 'p' and re.match(r'^3\.1\.7\s', t.strip()):
        break
if a:
    for k, t in items[a:]:
        if k == 'p' and re.match(r'^3\.1\.7\s', t.strip()):
            break
        if k == 'p' and t.strip():
            print(f"  [p] {t.strip()[:160]}")
        elif k == 't':
            print(f"  [表] {t[:120]}")
