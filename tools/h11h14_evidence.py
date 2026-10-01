#!/usr/bin/env python3
"""h11/h14 实现取证 (落盘, 输出紧凑):
A. 两缓存 物理行/斜杠行/时长行 全字段
B. 长兴备案稿 表24-29 头部 + 表25/27 完整行样本
"""
import json
from pathlib import Path
from docx import Document

W = Path('/Users/abaaba/Projects/ohs-report')
OUT = []
def P(s=''):
    OUT.append(str(s))

PHY_KW = ('噪声', '照度', '工频', '高温', '紫外', '微波', '振动', '磁场', 'WBGT', '激光', '红外', '射频', '辐射')
caches = {'新泰': 'data/vision_cache/a66517be268181e6.json',
          '长兴': 'data/vision_cache/32e58e26097c8e58.json'}

for tag, rel in caches.items():
    dets = json.loads((W / rel).read_text())['detections']
    P(f'########## {tag}: {len(dets)} 条 ##########')
    # 物理因素行: 全字段
    n_val = n_empty = 0
    shown = 0
    for d in dets:
        fac = d.get('factor') or ''
        if not any(k in fac for k in PHY_KW):
            continue
        rs = [str(x).strip() for x in (d.get('results') or [])
              if str(x).strip() not in ('', '—', '-', '/', '／')]
        if rs:
            n_val += 1
            if shown < 40:
                shown += 1
                keep = {k: v for k, v in d.items()
                        if k in ('factor', 'ctwa', 'cstel', 'results', 'judgement',
                                 'table_type', 'source', '_page', 'post', 'sampling_point',
                                 'unit', 'point', 'site', 'location', 'position')
                        and v not in (None, '', [])}
                P(f'  V{shown}: {json.dumps(keep, ensure_ascii=False)}')
        else:
            n_empty += 1
    P(f'  >> 物理行: 有值 {n_val} / 无值 {n_empty}')
    # 斜杠行
    sl = [d for d in dets if any('/' in str(x) for x in (d.get('results') or [])
                                 if str(x).strip() not in ('', '—', '-'))]
    P(f'  >> 斜杠行 {len(sl)}:')
    for d in sl[:14]:
        keep = {k: v for k, v in d.items()
                if k in ('factor', 'ctwa', 'cstel', 'results', 'judgement', 'table_type', '_page', 'post')
                and v not in (None, '', [])}
        P(f'     {json.dumps(keep, ensure_ascii=False)}')
    P()

# ---- 长兴备案稿 ----
doc = Document('/tmp/bench_orig/长兴--预评（备案稿7-31）.docx')
P('########## 长兴备案稿 表24-29 首行普查 ##########')
for ti in range(23, min(30, len(doc.tables))):
    t = doc.tables[ti]
    h = ' | '.join(c.text.replace('\n', '/')[:16] for c in t.rows[0].cells)
    P(f'  表{ti}: {len(t.rows)}x{len(t.columns)}  {h[:130]}')
P()
for ti in (25, 27):
    t = doc.tables[ti]
    P(f'########## 长兴 表{ti} 完整 {len(t.rows)}x{len(t.columns)} ##########')
    for ri, r in enumerate(t.rows):
        P(f'  r{ri}: ' + ' | '.join(c.text.replace('\n', '/')[:20] for c in r.cells))
    P()

(W / 'data' / 'h11h14_evidence.txt').write_text('\n'.join(OUT))
print(f'written {len(OUT)} lines')
