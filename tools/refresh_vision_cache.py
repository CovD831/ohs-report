"""用修复后的 extract_pages 重建缓存 (找回静默丢失的页)

⚠ 不改动旧缓存, 新缓存写入同路径 (键 = path+size+mtime+model)。
   旧缓存先备份到 data/vision_cache/_backup_v58/。

用法:
    python tools/refresh_vision_cache.py 新泰
    python tools/refresh_vision_cache.py 长兴
"""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
sys.path.insert(0, "/Users/abaaba/Projects/ohs-report/web")

import vision_extract as V  # noqa: E402
from factor_clean import clean_factor_name  # noqa: E402

TARGETS = {
    "新泰": (Path("/Users/abaaba/Desktop/新泰预评价/材料包_生成报告用"
                  "/05_类比检测_2025职业病危害因素检测报告.pdf"),
             Path("/Users/abaaba/Projects/ohs-report/data/vision_cache"
                  "/a66517be268181e6.json")),
    "长兴": (Path("/Users/abaaba/Desktop/长兴合成树脂/材料包_生成报告用"
                  "/05_类比检测_职业卫生检测2025.pdf"),
             Path("/Users/abaaba/Projects/ohs-report/data/vision_cache"
                  "/32e58e26097c8e58.json")),
}

which = sys.argv[1] if len(sys.argv) > 1 else "新泰"
if which not in TARGETS:
    raise SystemExit(f"用法: {sys.argv[0]} {'|'.join(TARGETS)}")
pdf, cache = TARGETS[which]

print(f"=== 重建 {which} 视觉缓存 ===")
print(f"PDF  : {pdf}")
print(f"缓存 : {cache}")

old = json.loads(cache.read_text())
print(f"\n旧缓存: {len(old.get('detections') or [])} 条 | "
      f"标称 read_pages={old['cost'].get('read_pages')} | "
      f"实际产出页={len({d.get('_page') for d in old['detections']})}")

index = old["index"]                      # 复用索引遍 (不需重跑)
key_pages = sorted({
    p["page"] for p in index["pages"]
    if (p["factor"] and p["factor"] not in ("无", "")) or p.get("has_sio2")
})
print(f"索引判有因素页: {len(key_pages)}")

# 备份
bak = cache.parent / "_backup_v58"
bak.mkdir(exist_ok=True)
shutil.copy2(cache, bak / cache.name)
print(f"旧缓存已备份 → {bak / cache.name}")

print("\n精读 (含重试修复)…")
t0 = time.time()
res = V.extract_pages(pdf, key_pages, dpi=180, verbose=True)

dets, sio2 = [], {}
for it in res["items"]:
    f = str(it.get("factor") or "").strip()
    if not f:
        continue
    f2, _flags = clean_factor_name(f)
    rec = {
        "factor": f2,
        "sampling_point": str(it.get("sampling_point") or "").strip(),
        "ctwa": str(it.get("ctwa") or "").strip(),
        "cstel": str(it.get("cstel") or "").strip(),
        "results": it.get("results") or [],
        "pass_ratio": str(it.get("pass_ratio") or "").strip(),
        "dust_type": str(it.get("dust_type") or "").strip(),
        "judgement": str(it.get("judgement") or "").strip(),
        "exposure_hours": str(it.get("exposure_hours") or "").strip(),
        "table_type": it.get("_table_type") or "",
        "source": "vision",
        "_page": it.get("_page"),
        "_file": pdf.name,
    }
    sp = str(it.get("sio2_percent") or "").strip()
    if sp:
        rec["sio2_percent"] = sp
        sio2[rec["sampling_point"] or f2] = sp
    dets.append(rec)

out = {
    "detections": dets,
    "sio2": sio2,
    "cost": {"credit": index["credit"] + res["credit"],
             "elapsed": index["elapsed"] + res["elapsed"],
             "index_pages": index["scanned_pages"],
             "read_pages": len(key_pages)},
    "index": index,
    "retried": res.get("retried"),
    "empty_after_retry": res.get("empty_after_retry"),
    "unresolved": res.get("unresolved"),
    "cached": False,
}
cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

print()
print("=" * 74)
print("重建完成")
print("=" * 74)
print(f"  旧: {len(old.get('detections') or [])} 条 / "
      f"{len({d.get('_page') for d in old['detections']})} 页产出")
print(f"  新: {len(dets)} 条 / {len({d.get('_page') for d in dets})} 页产出")
print(f"  → 找回 {len(dets) - len(old.get('detections') or [])} 条")
print(f"  重试过: {res.get('retried')}")
print(f"  重试后仍空: {res.get('empty_after_retry')}")
print(f"  始终失败: {res.get('unresolved')}")
print(f"  耗时 {time.time()-t0:.0f}s | credit {res['credit']:.2f}")
print(f"  写入 → {cache}")
