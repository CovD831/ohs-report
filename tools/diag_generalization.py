"""跨项目泛化性诊断: 新泰 vs 长兴 检测表格式差异

结论: 长兴检测报告是"一因素一行"(表型A/D), 新泰是"一岗位多因素"(表型B),
      我的提取器/过滤器只完整支持前者 → 新泰丢 27/47 条。

本脚本量化差异, 供决策: 泛化支持表型B(拆多因素) 还是 维持现状。
"""
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
c.row_factory = sqlite3.Row


def profile(pid, label):
    d = json.loads(c.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()["data"])
    dets = d.get("detections") or []
    print(f"=== {label} ({pid}) ===")
    print(f"  落库 detections: {len(dets)}")
    multi = [x for x in dets if "、" in str(x.get("factor") or "")]
    print(f"  factor 含顿号: {len(multi)}")
    fields = Counter()
    for x in dets:
        for k in ("ctwa", "cstel", "results", "sio2", "pass_ratio", "judgement"):
            if x.get(k):
                fields[k] += 1
    print(f"  字段填充: {dict(fields)}")
    print()


print("########## 落库数据对比 ##########")
profile("c8c7ff0a4d", "长兴 (合成树脂/有机)")
profile("06ea028767", "新泰 (氯化钙氢氟酸/无机)")
print()

print("########## 视觉提取原始结果对比 ##########")
for f, label in (("data/vision_cache/32e58e26097c8e58.json", "长兴 125页"),
                 ("data/vision_cache/a66517be268181e6.json", "新泰 94页")):
    p = Path(f)
    if not p.exists():
        print(f"  {label}: 缓存不存在")
        continue
    d = json.loads(p.read_text())
    dets = d.get("detections") or []
    tt = Counter(x.get("table_type") for x in dets)
    multi = sum(1 for x in dets if "、" in str(x.get("factor") or ""))
    withr = sum(1 for x in dets if x.get("results"))
    withp = sum(1 for x in dets if x.get("pass_ratio"))
    print(f"  {label}: {len(dets)} 条 | 表型 {dict(tt)}")
    print(f"    多因素(顿号) {multi} | 有results {withr} | 有pass_ratio {withp}")
