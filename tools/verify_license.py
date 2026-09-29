"""B2 验证: 营业执照扫描件 → 企业信息落库"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

from web.app import import_materials_from_dir  # noqa: E402

PACK = str(Path.home() / "Desktop/长兴合成树脂/材料包_生成报告用")
r = import_materials_from_dir(PACK)
print("=== 导入结果 ===")
for k in ("company", "founded", "registered_capital", "legal_rep", "location",
          "detections", "equipment"):
    v = r.get(k)
    if isinstance(v, list):
        print(f"  {k:22} = {len(v)} 条")
    else:
        print(f"  {k:22} = {v!r}")
print()
fp = r.get("field_provenance") or {}
print(f"=== 营业执照字段溯源 ({len(fp)} 项) ===")
for k, v in fp.items():
    print(f"  {k:20} src={v.get('source')} needs_review={v.get('needs_review')} "
          f"file={v.get('_file')} p{v.get('_page')}")
    print(f"      value = {v.get('value')!r}")
