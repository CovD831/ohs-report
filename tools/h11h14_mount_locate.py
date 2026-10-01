#!/usr/bin/env python3
"""两备案稿: 检测结果表所在章节定位 (表题/标题上下文) — h11/h14 挂载点 ground truth"""
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn


def walk(path, label, table_ids=None, hdr_kw=('检测', '噪声', '照度', 'LEX', 'CTWA', '峰值', '紫外', '工频')):
    doc = Document(path)
    body = doc.element.body
    seq = []
    ti = 0
    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            p = Paragraph(child, doc)
            t = p.text.strip()
            if t:
                style = ''
                try:
                    style = str(p.style.name or '')
                except Exception:
                    pass
                seq.append(('p', t, style))
        elif child.tag == qn('w:tbl'):
            tb = Table(child, doc)
            seq.append(('t', ti, len(tb.rows)))
            ti += 1
    print(f'######## {label}: {ti} 张表 ########')
    # 默认: 找出所有表头含关键字的表
    if table_ids is None:
        table_ids = []
        for i, s in enumerate(seq):
            if s[0] == 't':
                pass
        # 遍历表格对象判断
        for k, tb in enumerate(doc.tables):
            hdr = ' '.join(c.text for c in tb.rows[0].cells) if len(tb.rows) else ''
            if any(w in hdr for w in hdr_kw):
                table_ids.append(k)
        print('  含检测关键字的表:', table_ids)
    for want in table_ids:
        pos = next((i for i, s in enumerate(seq) if s[0] == 't' and s[1] == want), None)
        if pos is None:
            print(f'  表{want}: 未找到')
            continue
        ctx = []
        for j in range(pos - 1, max(-1, pos - 60), -1):
            s = seq[j]
            if s[0] == 'p':
                t = s[1]
                style = s[2]
                num_like = t[:1].isdigit() and ('.' in t[:8] or '、' in t[:8])
                if ('Heading' in style) or ('标题' in style) or num_like or t.startswith('表'):
                    ctx.append((pos - j, t[:90], style))
                    if len(ctx) >= 5:
                        break
        print(f'  表{want} ({seq[pos][2]}行):')
        for d, t, style in ctx:
            print(f'     [向上{d}行] {t}   <{style}>')
    print()


walk('/tmp/bench_orig/长兴--预评（备案稿7-31）.docx', '长兴', None)
walk('/tmp/xt_probe/6-1新泰--预评（备案稿）.docx', '新泰', None)
