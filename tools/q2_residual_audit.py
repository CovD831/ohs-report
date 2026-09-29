"""修复后残余缺口终局核查

三个悬而未决的问题:
  A. 长兴 p50 顶部延续行(工频电场 0.924/0.405/0.911/0.186) 是否真丢?
  B. 新泰 p46 (12条 in verify run, 0条 in rebuild) 的数据是否在别处有?
  C. 残余缺口总量: 扫描区里 0 记录 但"应有数据"的页 = ?

判据(避免复现"按名配对就误报"的坑): 按**值**找, 不按字段名配对。
"""
import json
from pathlib import Path

XT = json.loads(Path('data/vision_cache/a66517be268181e6.json').read_text())
CX = json.loads(Path('data/vision_cache/32e58e26097c8e58.json').read_text())

def all_text(x):
    return ' '.join(str(v) for v in [
        x.get('factor'), x.get('ctwa'), x.get('cstel'), x.get('judgement'),
        x.get('sampling_point'), x.get('job'), ' '.join(x.get('results') or [])
    ])

def find_val(vals, tag, cache):
    print(f'--- {tag}: 找值 {vals} ---')
    for v in vals:
        hits = [x for x in cache['detections'] if v in all_text(x)]
        if hits:
            for h in hits:
                print(f'  {v!r} 命中 p{h.get("_page")} | {str(h.get("factor"))[:20]:22} | {all_text(h)[:70]}')
        else:
            print(f'  {v!r} ✗ 全缓存未命中')

print('='*76)
print('A. 长兴 p50 延续行的值是否在缓存里')
print('='*76)
find_val(['0.924', '0.405', '0.911', '0.186'], '长兴', CX)

print()
print('='*76)
print('B. 新泰 p46/p44 页记录现状')
print('='*76)
for pg in (44, 45, 46):
    recs = [x for x in XT['detections'] if x.get('_page') == pg]
    print(f'  新泰 p{pg}: {len(recs)} 条')
    for x in recs[:4]:
        print(f'      {str(x.get("factor"))[:24]:26} {str(x.get("results"))[:30]}')
    if len(recs) > 4:
        print(f'      ... 共 {len(recs)} 条')

print()
print('='*76)
print('C. 残余缺口: 索引判"有因素" + 扫描区 + 最终 0 记录')
print('='*76)
for tag, cache, scan_end in (('新泰', XT, 49), ('长兴', CX, 56)):
    idx = {p['page']: p for p in cache['index']['pages']}
    got = {}
    for x in cache['detections']:
        got[x.get('_page')] = got.get(x.get('_page'), 0) + 1
    bad = []
    for pg, info in sorted(idx.items()):
        if pg > scan_end:
            continue
        if not str(info.get('factor') or '').strip():
            continue
        if got.get(pg, 0) == 0:
            bad.append((pg, info.get('factor'), info.get('raw')))
    print(f'  {tag}: 扫描区({scan_end}页内) 0记录但有因素 = {len(bad)} 页')
    for pg, f, raw in bad:
        print(f'      p{pg}: factor={f!r} raw={raw!r}')
    print(f'      缓存的 retried={cache.get("retried")}')
    print(f'      缓存的 empty_after_retry={cache.get("empty_after_retry")}')
    print(f'      缓存的 unresolved={cache.get("unresolved")}')
