"""验证 sio2 过滤修复: 导入后游离二氧化硅是否保留"""
import sys

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

from web.app import import_materials_from_dir  # noqa: E402

r = import_materials_from_dir("a3f512eca6")
dets = r.get("detections") or []
sio = [x for x in dets if x.get("sio2")]
print(f"导入后 detections: {len(dets)}")
print(f"含 sio2 的: {[(x.get('factor'), x.get('sio2')) for x in sio]}")
# 同时看修复前的过滤会丢多少
old = [d for d in dets if d.get("ctwa") is not None]
print(f"旧过滤(只留 ctwa) 会剩: {len(old)} → 丢 {len(dets) - len(old)} 条")
