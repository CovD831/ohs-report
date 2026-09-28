"""端到端验证: 扫描件检测报告 → import_materials_from_dir → detections(标 vision)

验证点:
  ① 扫描件被识别并走视觉提取 (而非静默产出 0 行)
  ② 提取到的条目带 source="vision" (不伪装成确定提取)
  ③ 体检报告仍被排除 (历史 bug 不复发)
  ④ 文本层来源的数据不被 vision 覆盖
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

PACK = Path.home() / "Desktop/长兴合成树脂/材料包_生成报告用"


def main():
    from web.app import import_materials_from_dir
    r = import_materials_from_dir(str(PACK))
    dets = r.get("detections") or []
    print("=" * 78)
    print(f"detections 共 {len(dets)} 条")
    nv = [d for d in dets if d.get("source") == "vision"]
    nt = [d for d in dets if d.get("source") != "vision"]
    print(f"  vision 来源: {len(nv)} 条")
    print(f"  文本层来源: {len(nt)} 条")
    print()
    print("=== 文本层来源 (不应被覆盖) ===")
    for d in nt:
        print(f"  {str(d.get('factor')):24} ctwa={d.get('ctwa')!r}")
    print()
    print("=== vision 来源 (前 12 条) ===")
    for d in nv[:12]:
        print(f"  {str(d.get('factor')):24} ctwa={d.get('ctwa')!r:10} "
              f"results={d.get('results')} p{d.get('_page')}")
    print()
    sio = [d for d in dets if d.get("sio2")]
    print(f"=== 含游离二氧化硅: {len(sio)} 条 ===")
    for d in sio:
        print(f"  {d.get('factor')} sio2={d.get('sio2')} @ {d.get('_file')} p{d.get('_page')}")
    print()
    # 体检报告必须被排除
    bad = [d for d in dets if "体检" in str(d.get("_file", "")) or "职业健康" in str(d.get("_file", ""))]
    print(f"体检文件混入: {len(bad)} 条 {'✓' if not bad else '✗ 有问题!'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
