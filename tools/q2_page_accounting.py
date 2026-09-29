"""致命核查: 索引找到的页 vs 精读实际读的页 — 是否漏读?

索引遍已标出含因素的页: 26-34, 44,45,46, 54,55, 66-69, 72, 81, 87
若精读遍只读了其中一部分 → **漏读** = 我自己的 bug (第三类: 索引→精读选择)
"""
import collections
import json
from pathlib import Path

CACHE = Path("/Users/abaaba/Projects/ohs-report/data/vision_cache/a66517be268181e6.json")
vd = json.loads(CACHE.read_text())

pages = vd["index"]["pages"]
dets = vd["detections"]

# ① 索引说有因素的页
idx_with_factor = sorted(
    p["page"] for p in pages
    if str(p.get("factor") or "").strip() not in ("", "无")
)
idx_sio2 = sorted(p["page"] for p in pages if p.get("has_sio2"))

# ② 精读实际产出的页
read_pages = sorted({d.get("_page") for d in dets if d.get("_page")})

print("=" * 80)
print("索引 vs 精读  页面对账")
print("=" * 80)
print(f"索引总页数        : {vd['index']['total_pages']}")
print(f"扫描件页数        : {vd['index']['scanned_pages']}")
print(f"索引判'有因素'页数: {len(idx_with_factor)}")
print(f"索引判'有SiO2'页数: {len(idx_sio2)}")
print(f"精读实际覆盖页数  : {len(read_pages)}")

print()
print("索引说有因素、但精读**没读**的页:")
missing = [p for p in idx_with_factor if p not in read_pages]
print(f"  {missing}")
print(f"  ({len(missing)} 页)")

print()
print("精读读了、但索引没说有因素的页:")
extra = [p for p in read_pages if p not in idx_with_factor]
print(f"  {extra}")

print()
print("=" * 80)
print("漏读页上, 索引说有什么因素")
print("=" * 80)
for p in missing:
    rec = next((x for x in pages if x["page"] == p), None)
    if rec:
        print(f"  p{p}: {str(rec.get('factor'))[:170]}")

print()
print("=" * 80)
print("成本记录")
print("=" * 80)
print("  index:", vd["index"].get("credit"), vd["index"].get("elapsed"))
print("  read :", vd["cost"].get("credit"), vd["cost"].get("elapsed"))

print()
print("=" * 80)
print("关键: 索引里的 '照度/工频电场/氧化钙' 页是否在漏读集合里")
print("=" * 80)
for tgt in ["照度", "工频电场", "氧化钙"]:
    tp = sorted(p["page"] for p in pages if tgt in str(p.get("factor") or ""))
    read_tp = [p for p in tp if p in read_pages]
    print(f"  {tgt}: 索引命中页 {tp}")
    print(f"       其中精读覆盖: {read_tp}")
    print(f"       → 漏读 {len(tp) - len(read_tp)} 页")
