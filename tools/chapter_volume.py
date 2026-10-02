#!/usr/bin/env python3
"""按章统计生成稿的内容量 (段落 vs 表格), 定位"光标题无正文"的章"""
import re
import sys
import zipfile
from lxml import etree

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
path = sys.argv[1] if len(sys.argv) > 1 else 'data/report_ada54603a9.docx'
z = zipfile.ZipFile(path)
x = etree.fromstring(z.read('word/document.xml'))
body = x.find(W + 'body')
seq = [(i, etree.QName(el).localname,
        ''.join(n.text or '' for n in el.iter(W + 't')).strip())
       for i, el in enumerate(body)]

marks = []
for i, tag, t in seq:
    if tag == 'p':
        m = re.match(r'^(\d{1,2})\s+\S', t)
        if m:
            marks.append((i, int(m.group(1)), t[:34]))

print(f"{'章':<4}{'段落字':>8}{'表格字':>9}{'表数':>5}{'合计':>9}   标题")
print('-' * 78)
for k, (i, ch, t) in enumerate(marks):
    end = marks[k + 1][0] if k + 1 < len(marks) else len(seq)
    pch = sum(len(t2) for j, g, t2 in seq if i < j < end and g == 'p')
    tch = sum(len(t2) for j, g, t2 in seq if i < j < end and g == 'tbl')
    ntb = sum(1 for j, g, t2 in seq if i < j < end and g == 'tbl')
    flag = '  ⚠无正文' if pch < 20 and tch == 0 else ''
    print(f"{ch:<4}{pch:>8}{tch:>9}{ntb:>5}{pch+tch:>9}   {t}{flag}")
