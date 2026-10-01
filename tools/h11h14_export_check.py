#!/usr/bin/env python3
"""验证: 生成的 docx 里 检测结果表/CTWA 列到底在不在"""
import sys
from pathlib import Path
from docx import Document
ROOT = Path('/Users/abaaba/Projects/ohs-report')

for f in sorted((ROOT / 'data' / 'exports').glob('*.docx'))[-3:] if (ROOT/'data'/'exports').exists() else []:
    print('==', f)
for f in [Path('/tmp/test_mount_export.docx')]:
    if not f.exists():
        continue
    doc = Document(str(f))
    print(f'== {f}: {len(doc.tables)} 张表 ==')
    for ti, t in enumerate(doc.tables):
        hdr = ' | '.join(c.text.replace('\n', '/')[:12] for c in t.rows[0].cells)
        if 'CTWA' in hdr or '检测' in hdr or '峰值' in hdr or '判定' in hdr:
            print(f'  表{ti}: {hdr[:120]}  ({len(t.rows)}行)')

# 全量列出 test_mount_export 的所有表(前2行)
f = Path('/tmp/test_mount_export.docx')
if f.exists():
    doc = Document(str(f))
    print()
    print(f'== {f.name} 全表 ==')
    for ti, t in enumerate(doc.tables):
        hdr = ' | '.join(c.text.replace('\n', '/')[:14] for c in t.rows[0].cells)
        print(f'  表{ti}: {len(t.rows)}x{len(t.columns)}  {hdr[:130]}')
