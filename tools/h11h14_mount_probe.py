#!/usr/bin/env python3
"""挂载点取证: 走真实导出 walk, 找 检测结果表 挂在哪个节 + 物理表有没有"""
import sys
from pathlib import Path
ROOT = Path('/Users/abaaba/Projects/ohs-report')
sys.path.insert(0, str(ROOT))
import os
os.chdir(ROOT)

from knowledge.oel import connect
from knowledge.project_assess import assess_project
from web.projects_db import get_project
from web.report_struct import SUBS, SUBS3, SUBS4
from web import word_export as WE
from web.section_filler import fill_section

conn = connect()
p = get_project('c8c7ff0a4d')
data = p['data']
dets = data.get('detections', [])
print(f'项目: {p["name"]}  detections={len(dets)}')

# 本地项目的 detections 形态
from collections import Counter
tc = Counter()
for d in dets[:400]:
    tc[d.get('table_type') or '?'] += 1
print('table_type 分布:', dict(tc))
phys = [d for d in dets if any(k in str(d.get('factor')) for k in ('噪声', '照度', '工频', '高温', '紫外'))]
print(f'物理因素行: {len(phys)}')
for d in phys[:10]:
    print('   ', {k: d.get(k) for k in ('factor', 'ctwa', 'cstel', 'results', 'judgement', 'table_type', '_page') if d.get(k) not in (None, '', [])})
cst = [d for d in dets if str(d.get('cstel') or '').strip() not in ('', '—', '-')]
print(f'cstel 有值行: {len(cst)}')
for d in cst[:6]:
    print('   ', {k: d.get(k) for k in ('factor', 'ctwa', 'cstel', 'judgement', 'table_type') if d.get(k) not in (None, '', [])})

assess = assess_project(conn, {'name': p['name'], 'industry': data.get('industry', ''),
                               'equipment': data.get('equipment', []), 'detections': dets,
                               'process_text': data.get('process_text', '')})

print()
print('==== 导出 walk: 每个节实际挂到的表 (名称含 检测/物理/判定/限值) ====')
WE._USED_TABLES.clear()
# 模拟 word_export 的 walk 顺序: 每章 → SUBS(二级) → SUBS3(三级) → SUBS4
def show(sn, tables):
    hits = [t for t in tables if any(k in t['name'] for k in ('检测', '物理', '判定', '限值'))]
    if hits:
        for t in hits:
            print(f'  {sn}: {t["name"]}  {len(t["rows"])}行 cols={t["cols"]}')

for sec in sorted(SUBS.keys(), key=lambda s: (len(s), s)):
    for sn, st in SUBS.get(sec, []):
        try:
            ts = WE._tables_for_sub(conn, sec, sn, assess)
        except Exception as e:
            print(f'  {sn}: ERR {e}')
            continue
        show(sn, ts)
        subs3 = [(k, v) for k, v in SUBS3.items() if v[0] == sn]
        for sn3, (par, t3) in sorted(subs3):
            try:
                ts3 = WE._tables_for_sub(conn, sec, sn3, assess)
            except Exception as e:
                print(f'  {sn3}: ERR {e}')
                continue
            show(sn3, ts3)
            for sn4, (p4, t4) in sorted(SUBS4.get(sn3, [])):
                try:
                    ts4 = WE._tables_for_sub(conn, sec, sn4, assess)
                except Exception as e:
                    continue
                show(sn4, ts4)
conn.close()
