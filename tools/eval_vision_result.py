"""评估视觉提取结果质量: 覆盖率 / 表型分布 / 与已有数据交叉校验

用法: python tools/eval_vision_result.py /tmp/changxing_detect.json
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main():
    p = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/changxing_detect.json")
    if not p.exists():
        print(f"结果文件不存在: {p}")
        return 1
    d = json.loads(p.read_text(encoding="utf-8"))
    dets = d.get("detections") or []
    cost = d.get("cost") or {}
    idx = d.get("index") or {}

    print("=" * 78)
    print("【成本与规模】")
    print(f"  索引页数 {cost.get('index_pages')} | 精读页数 {cost.get('read_pages')}")
    print(f"  耗时 {cost.get('elapsed', 0):.0f}s | credit {cost.get('credit', 0):.2f}")
    print(f"  提取记录 {len(dets)} 条")

    print()
    print("=" * 78)
    print("【表型分布】")
    tt = Counter(x.get("table_type") or "?" for x in dets)
    for k, v in tt.most_common():
        name = {"A": "逐样结果表(浓度)", "B": "岗位汇总表", "D": "游离二氧化硅表",
                "C": "无数据页", "?": "未标"}.get(k, k)
        print(f"  表型{k} {name:16} {v:4} 条")

    print()
    print("=" * 78)
    print("【字段填充率】(衡量提取完整度)")
    fields = ["factor", "sampling_point", "exposure_hours", "ctwa", "cstel",
              "pass_ratio", "judgement", "sio2_percent", "dust_type"]
    n = len(dets) or 1
    for f in fields:
        c = sum(1 for x in dets if str(x.get(f) or "").strip())
        bar = "#" * int(c * 30 / n)
        print(f"  {f:16} {c:4}/{n}  {c*100//n:3}%  {bar}")

    print()
    print("=" * 78)
    print("【危害因素覆盖】(去重)")
    fac = Counter()
    for x in dets:
        f = str(x.get("factor") or "").strip()
        if f:
            fac[f] += 1
    print(f"  共 {len(fac)} 种因素")
    for f, c in fac.most_common(30):
        print(f"    {f:26} {c}")

    print()
    print("=" * 78)
    print("【游离二氧化硅 (作业分级关键字段)】")
    sio = [x for x in dets if str(x.get("sio2_percent") or "").strip()]
    if sio:
        for x in sio:
            print(f"  {x.get('sio2_percent'):>8}  {x.get('dust_type','')} "
                  f"@ {str(x.get('sampling_point'))[:46]} (p{x.get('_page')})")
    else:
        print("  ✗ 未提取到")

    print()
    print("=" * 78)
    print("【可能的问题样本】(疑似识别异常)")
    bad = []
    for x in dets:
        ct = str(x.get("ctwa") or "")
        # 浓度值里出现中文/字母混排 → 可疑
        if ct and not re.match(r"^[<≥≤]?\s*[\d.,]+$", ct.strip()) and "mg" not in ct:
            bad.append(("ctwa异常", ct, x))
        rs = x.get("results") or []
        for r in rs:
            s = str(r)
            if re.search(r"[\u4e00-\u9fff]", s) and not re.match(r"^[\d.,<>≤≥×\s/]+$", s):
                bad.append(("results含中文", s, x))
                break
    if bad:
        for kind, v, x in bad[:12]:
            print(f"  [{kind}] {v!r} | {str(x.get('factor'))[:16]} p{x.get('_page')}")
    else:
        print("  ✓ 无明显异常")

    print()
    print("=" * 78)
    print("【索引遍命中 SiO2 的页 vs 实际提取到的】")
    idx_sio = [x["page"] for x in (idx.get("pages") or []) if x.get("has_sio2")]
    det_sio = sorted({x.get("_page") for x in sio})
    print(f"  索引判定: {idx_sio}")
    print(f"  实提取到: {det_sio}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
