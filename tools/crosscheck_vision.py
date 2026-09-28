"""交叉校验: 视觉提取结果 vs 已有多来源数据

目的: 不只看"提取了多少条", 而是看**提得对不对** —— 
      与已有检测数据(来自有文本层的 .docx/.xlsx) 比对同一岗位同一因素的浓度。
      对不上 → 该条 vision 数值可疑。
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")


def num(s):
    """从字符串抽第一个数 (容忍 mg/m³, <1.7, 3.39 等)"""
    if s is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(s))
    return float(m.group(1)) if m else None


def main():
    vis_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/changxing_detect.json")
    pid = sys.argv[2] if len(sys.argv) > 2 else "c8c7ff0a4d"
    if not vis_path.exists():
        print(f"视觉结果不存在: {vis_path}")
        return 1
    v = json.loads(vis_path.read_text(encoding="utf-8"))
    dets = v.get("detections") or []

    c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
    base = (json.loads(row["data"]).get("detections") or []) if row else []

    print("=" * 80)
    print(f"视觉提取 {len(dets)} 条  vs  已有基准 {len(base)} 条")
    print("=" * 80)

    # 建索引: 因素名(归一) → 已有 ctwa
    def norm(s):
        return re.sub(r"[\s（）()【】\[\]]", "", str(s or ""))

    base_map = {}
    for b in base:
        base_map.setdefault(norm(b.get("factor")), []).append(b)

    print("\n【基准因素 vs 视觉提取到的同名因素】")
    hit = miss = 0
    for f, items in base_map.items():
        found = [d for d in dets if norm(d.get("factor")) == f
                 or f in norm(d.get("factor")) or norm(d.get("factor")) in f]
        if found:
            hit += 1
            b = items[0]
            print(f"\n  ✓ {b.get('factor')}  (基准 ctwa={b.get('ctwa')}, {b.get('factory')})")
            for d in found[:4]:
                print(f"      视觉: ctwa={d.get('ctwa') or '-'} "
                      f"results={d.get('results')} 判定={d.get('judgement') or '-'} "
                      f"表型{d.get('table_type')} p{d.get('_page')}")
                bn, vn = num(b.get("ctwa")), num(d.get("ctwa"))
                if bn is not None and vn is not None:
                    ok = "一致" if abs(bn - vn) < 0.05 else f"⚠ 不一致(基{bn}/视{vn})"
                    print(f"      → 交叉校验: {ok}")
        else:
            miss += 1
            print(f"\n  ✗ {items[0].get('factor')} — 视觉提取里没找到同名")
    print(f"\n基准因素命中 {hit} / 未命中 {miss}")

    print("\n" + "=" * 80)
    print("【视觉独有因素】(基准里没有 → 可能是提取层之前丢掉的新数据)")
    base_names = set(base_map.keys())
    extra = {}
    for d in dets:
        n = norm(d.get("factor"))
        if n and not any(n == bn or n in bn or bn in n for bn in base_names):
            extra[n] = extra.get(n, 0) + 1
    for n, cnt in sorted(extra.items(), key=lambda x: -x[1])[:40]:
        print(f"  + {n:28} {cnt} 条")
    print(f"\n共 {len(extra)} 种视觉独有因素")
    return 0


if __name__ == "__main__":
    sys.exit(main())
