#!/usr/bin/env python3
"""probe2: 备案稿全表头 + 缓存物理/无CTWA行全量 + 标准库查表 → 落盘"""
import json, sqlite3, re
from pathlib import Path
from collections import Counter, defaultdict

REPO = Path('/Users/abaaba/Projects/ohs-report')
OUT = []

def P(*a):
    OUT.append(' '.join(str(x) for x in a))

PHYS = ('噪声', '照度', '工频电场', '高温', 'WBGT', '紫外辐射', '手传振动', '全身振动',
        '微波辐射', '高频电磁场', '激光辐射', '电离辐射', '振动')

# ---------- 1. 两备案稿 全表头 ----------
from docx import Document
for label, path in (('长兴', '/tmp/bench_orig/长兴--预评（备案稿7-31）.docx'),
                    ('新泰', '/tmp/xt_probe/6-1新泰--预评（备案稿）.docx')):
    doc = Document(path)
    P(f'########## {label}备案稿 全表头 ({len(doc.tables)} 张) ##########')
    for ti, t in enumerate(doc.tables):
        h = ' | '.join(c.text.replace('\n', '/').replace('\r', '')[:16] for c in t.rows[0].cells)
        h2 = ''
        if len(t.rows) > 1:
            h2 = ' // '.join(c.text.replace('\n', '/')[:16] for c in t.rows[1].cells)
        P(f'T{ti:02d} {len(t.rows)}x{len(t.columns)}  {h[:150]}')
        if h2 and h2 != h:
            P(f'      2nd: {h2[:150]}')

# ---------- 2. 缓存: 物理行 + ctwa空results非空行 全量 ----------
for tag, rel in (('新泰', 'data/vision_cache/a66517be268181e6.json'),
                 ('长兴', 'data/vision_cache/32e58e26097c8e58.json')):
    d = json.loads((REPO / rel).read_text())['detections']
    P(f'########## {tag} 缓存 {len(d)} 条 ##########')
    kc = Counter()
    for e in d:
        kc.update(e.keys())
    P('key分布:', dict(kc))
    # 全judgement分布
    P('judgement分布:', dict(Counter(re.sub(r'\s+', '', str(e.get('judgement') or ''))[:16] for e in d)))
    PLACE = ('', '—', '-', '--', '/', '／', '无', '未检出', 'n/a', 'N/A')
    def realvals(e):
        return [str(x).strip() for x in (e.get('results') or []) if str(x).strip() not in PLACE]
    # 物理行
    ph = [e for e in d if str(e.get('factor') or '').strip() in PHYS]
    phv = [e for e in ph if realvals(e)]
    P(f'-- 纯物理factor行 {len(ph)}; results有值 {len(phv)}')
    byp = defaultdict(list)
    for e in phv:
        byp[e.get('_page')].append(e)
    for pg in sorted(byp, key=lambda x: (x is None, x)):
        es = byp[pg]
        P(f'  p{pg} n={len(es)}')
        for e in es:
            P('    P|%s res=%s ctwa=%s cstel=%s j=%s tt=%s sp=%s src=%s' % (
                e.get('factor'), e.get('results'), e.get('ctwa'), e.get('cstel'),
                e.get('judgement'), e.get('table_type'), e.get('sampling_point'), e.get('source')))
    # ctwa空+results非空 (非纯物理)
    rows = [e for e in d if str(e.get('ctwa') or '').strip() in PLACE and realvals(e)
            and str(e.get('factor') or '').strip() not in PHYS]
    P(f'-- ctwa空+results非空+非纯物理 {len(rows)} 行')
    byp2 = defaultdict(list)
    for e in rows:
        byp2[e.get('_page')].append(e)
    for pg in sorted(byp2, key=lambda x: (x is None, x)):
        es = byp2[pg]
        P(f'  Q p{pg} n={len(es)}')
        for e in es[:12]:
            P('    Q|%s res=%s cstel=%s j=%s tt=%s sp=%s' % (
                e.get('factor'), e.get('results'), e.get('cstel'),
                e.get('judgement'), e.get('table_type'), e.get('sampling_point')))
    # cstel非空行
    cst = [e for e in d if str(e.get('cstel') or '').strip()]
    P(f'-- cstel非空 {len(cst)} 行 (factor/page/tt 分布)')
    for e in cst:
        P('    S|%s ctwa=%s cstel=%s j=%s tt=%s p=%s' % (
            e.get('factor'), e.get('ctwa'), e.get('cstel'), e.get('judgement'),
            e.get('table_type'), e.get('_page')))

# ---------- 3. 标准库: hazard_factor / oel_limit 物理 ----------
conn = sqlite3.connect(str(REPO / 'data/ohs.db'))
conn.row_factory = sqlite3.Row
P('########## 库表 ##########')
tabs = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
P('tables:', tabs)
if 'hazard_factor' in tabs:
    cols = [c[1] for c in conn.execute('PRAGMA table_info(hazard_factor)')]
    P('hazard_factor cols:', cols)
    for kw in ('噪声', '照度', '工频', '高温', '紫外', '粉尘'):
        rr = conn.execute("SELECT * FROM hazard_factor WHERE name LIKE ? LIMIT 4", (f'%{kw}%',)).fetchall()
        for r in rr:
            P('  HF', dict(r))
if 'oel_limit' in tabs:
    cols = [c[1] for c in conn.execute('PRAGMA table_info(oel_limit)')]
    P('oel_limit cols:', cols)
    for kw in ('噪声', '工频', '照度', '高温'):
        rr = conn.execute("SELECT * FROM oel_limit WHERE factor_name LIKE ? LIMIT 5", (f'%{kw}%',)).fetchall()
        for r in rr:
            P('  OEL', dict(r))
if 'illumination_std' in tabs:
    rr = conn.execute("SELECT * FROM illumination_std LIMIT 8").fetchall()
    for r in rr:
        P('  ILL', dict(r))

(REPO / 'tools' / '_probe2_out.txt').write_text('\n'.join(OUT))
print('WROTE', len(OUT), 'lines ->', REPO / 'tools' / '_probe2_out.txt')
