"""剩余缺口终局分析: 扫描区里"索引说有因素"但最终缓存 0 记录的页

口径修正 (上一版分析漏了排除 factor='无'):
  index.entries[].factors 里可能含 '无'/'None' 等值 → 排除后才算"有因素"。

输出: 每份材料 → 扫描区页数 / 有因素页 / 0记录页 / 明细
"""
import json
from pathlib import Path

CASES = [
    ('新泰', 'data/vision_cache/a66517be268181e6.json', '/Users/abaaba/Desktop/新泰预评价/材料包_生成报告用/05_类比检测_2025职业病危害因素检测报告.pdf'),
    ('长兴', 'data/vision_cache/32e58e26097c8e58.json', '/Users/abaaba/Desktop/长兴合成树脂/材料包_生成报告用/05_类比检测_职业卫生检测2025.pdf'),
]

BAD = {'', '无', 'none', 'null', '-', '—', '/', 'nan'}


def has_real_factor(x):
    f = str(x.get('factor') or '').strip()
    return f.lower() not in BAD


for tag, cf, pdf in CASES:
    d = json.loads(Path(cf).read_text())
    # index 是 dict: {pages:[{page,factor,has_sio2,raw}], total_pages, scanned_pages, ...}
    idx = {p['page']: p for p in (d.get('index') or {}).get('pages', [])}
    got = {}
    for r in d['detections']:
        got[r.get('_page')] = got.get(r.get('_page'), 0) + 1

    # 扫描区 = 无文本层的页 (用 pymupdf 判)
    import fitz
    doc = fitz.open(pdf)
    scan_pages = [i + 1 for i in range(doc.page_count)
                  if not (doc[i].get_text() or '').strip()]
    doc.close()

    print('=' * 78)
    print(f'{tag}: 共 {doc.page_count if False else (d.get("index") or {}).get("total_pages","?")} 页 '
          f'| 扫描区 {len(scan_pages)} 页 | detections {len(d["detections"])} 条')
    print(f'  缓存记录: retried={d.get("retried")} empty_after_retry={len(d.get("empty_after_retry") or [])} '
          f'unresolved={d.get("unresolved")}')
    print('=' * 78)

    gap = []
    for p in scan_pages:
        e = idx.get(p) or {}
        fac = str(e.get('factor') or '').strip()
        n = got.get(p, 0)
        if n == 0 and fac.lower() not in BAD:
            gap.append((p, fac, e.get('has_sio2')))

    print(f'扫描区 0 记录但索引有真因素: {len(gap)} 页')
    for p, fac, si in gap:
        print(f'  p{p:<4} factor={fac!r} has_sio2={si}')
    print()

    # 顺便: 扫描区 0 记录且索引无因素(说明页/图页) → 正常
    benign = [p for p in scan_pages
              if got.get(p, 0) == 0
              and str((idx.get(p) or {}).get('factor') or '').strip().lower() in BAD]
    print(f'扫描区 0 记录且索引无因素 (说明/图/签字页, 正常): {len(benign)} 页 {benign[:25]}')
    print()
    print(f'扫描区有记录页: {sorted(p for p in scan_pages if got.get(p,0))}')
    print()
