#!/usr/bin/env python3
"""h11/h14 实现取证2: 物理行全清单 + cstel 行全清单 + 本地项目 detections 形态"""
import json
from pathlib import Path
from collections import Counter, defaultdict

W = Path('/Users/abaaba/Projects/ohs-report')
OUT = []
def P(s=''):
    OUT.append(str(s))

PHY_KW = ('噪声', '照度', '工频', '紫外', '高温', 'WBGT', '振动', '微波', '激光',
          '红外', '射频', '电磁', '磁场', '辐射', '弧光', '脉冲')
DUR = {0.25, 0.5, 0.65, 0.75, 1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 8}

def real(vals):
    return [str(x).strip() for x in vals if str(x).strip() not in ('', '—', '-', '--', '/', '／', '无')]

caches = {'新泰': 'data/vision_cache/a66517be268181e6.json',
          '长兴': 'data/vision_cache/32e58e26097c8e58.json'}

all_phys = {}
all_cstel = {}
for tag, rel in caches.items():
    dets = json.loads((W / rel).read_text())['detections']
    phys = []
    for d in dets:
        fac = str(d.get('factor') or '')
        if not any(k in fac for k in PHY_KW):
            continue
        rs = real(d.get('results') or [])
        ct = str(d.get('ctwa') or '').strip()
        cst = str(d.get('cstel') or '').strip()
        j = str(d.get('judgement') or '').strip()
        if rs or ct or cst:
            phys.append((d.get('_page'), fac, '/'.join(rs) or '-', ct or '-', cst or '-', j or '-', d.get('table_type')))
    all_phys[tag] = phys
    cst_rows = []
    for d in dets:
        cst = str(d.get('cstel') or '').strip()
        if cst and cst not in ('—', '-', '/', '／'):
            cst_rows.append((d.get('_page'), d.get('factor'), str(d.get('ctwa') or '-'), cst, str(d.get('judgement') or '-'), d.get('table_type')))
    all_cstel[tag] = cst_rows

for tag in caches:
    P(f'########## {tag}: 物理行(有值) {len(all_phys[tag])} ##########')
    P(f'  {"page":>5} | {"factor":<22} | {"results":<26} | {"ctwa":<8} | {"cstel":<8} | {"judgement":<14} | typ')
    for row in sorted(all_phys[tag], key=lambda x: (x[0] or 0)):
        P(f'  {str(row[0]):>5} | {str(row[1])[:22]:<22} | {row[2][:26]:<26} | {row[3][:8]:<8} | {row[4][:8]:<8} | {row[5][:14]:<14} | {row[6]}')
    P()
    P(f'########## {tag}: cstel 有值 {len(all_cstel[tag])} ##########')
    for row in sorted(all_cstel[tag], key=lambda x: (x[0] or 0)):
        P(f'  p{row[0]} | {str(row[1])[:24]:<24} | ctwa={row[2][:8]:<8} | cstel={row[3][:10]:<10} | j={row[4][:10]} | {row[5]}')
    P()

(W / 'data' / 'h11h14_evidence2.txt').write_text('\n'.join(OUT))
print(f'written {len(OUT)} lines -> data/h11h14_evidence2.txt')
