"""内容抽查(补充): 检查"报告正文/表格里的数字" 是否真能回溯到源头

重点抽查高风险项 (LLM 最容易编的地方):
  A. 接触限值的**出处标注** (报告说来自 GBZ 2.1, 实际值是否一致)
  B. 报告正文里的浓度数值 vs 检测数据源
  C. 危害因素清单 vs 检测/物料源 (有没有多出材料里没有的)
  D. 关键控制点/风险判定 vs 评估结果
"""
import json
import re
import sqlite3
import sys

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

PID = "c8c7ff0a4d"


def main():
    c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
    c.row_factory = sqlite3.Row
    d = json.loads(c.execute("SELECT data FROM project WHERE id=?", (PID,)).fetchone()["data"])
    ss = d.get("section_states") or {}
    bt = d.get("built_tables") or {}

    print("=" * 88)
    print("C. 危害因素清单: 报告列的 vs 材料实际有的 (查有没有多出的/编造的)")
    print("=" * 88)
    det_factors = {str(x.get("factor")).strip() for x in (d.get("detections") or []) if x.get("factor")}
    haz = {str(h.get("factor")).strip() for h in (d.get("hazards") or []) if h.get("factor")}
    # 报告接触限值表列的因素
    lim = bt.get("接触限值表") or {}
    lim_f = {str(r[0]).strip() for r in (lim.get("rows") or [])}
    print(f"  检测数据源因素 {len(det_factors)} 个")
    print(f"  assess.hazards  {len(haz)} 个")
    print(f"  报告限值表      {len(lim_f)} 个")
    print()
    only_report = lim_f - det_factors - haz
    if only_report:
        print(f"  ⚠ 只在报告里出现、材料中无来源的: {sorted(only_report)}")
    else:
        print("  ✓ 报告因素均可回溯到检测/物料来源")

    print()
    print("=" * 88)
    print("B. 正文里的浓度数值 vs detections 源值")
    print("=" * 88)
    # 收集正文里 "低于X mg/m3" 这类值
    prose = " ".join((v or {}).get("text") or "" for v in ss.values())
    prose_vals = re.findall(r"(\d+(?:\.\d+)?)\s*mg/m", prose)
    src_vals = {str(x.get("ctwa")).lstrip("<").strip()
                for x in (d.get("detections") or []) if x.get("ctwa")}
    src_nums = {re.sub(r"[^\d.]", "", v) for v in src_vals if re.sub(r"[^\d.]", "", v)}
    print(f"  正文出现的浓度值 {len(set(prose_vals))} 种: {sorted(set(prose_vals))[:20]}")
    print(f"  检测源 ctwa 值  {len(src_nums)} 种: {sorted(src_nums)[:20]}")
    # 正文值里有多少能在源里找到(或作为限值)
    lim_vals = set()
    for r in (lim.get("rows") or []):
        for x in r[1:5]:
            v = str(x).strip()
            if v not in ("—", ""):
                lim_vals.add(v)
    unknown = {v for v in set(prose_vals) if v not in src_nums and v not in lim_vals}
    print()
    if unknown:
        print(f"  ⚠ 正文中既非检出值也非限值的数: {sorted(unknown)[:25]}")
    else:
        print("  ✓ 正文浓度值均能对上(检出值或限值)")

    print()
    print("=" * 88)
    print("D. 风险判定 vs 评估结果")
    print("=" * 88)
    from knowledge.project_assess import assess_project
    from knowledge.oel import connect as oc
    a = assess_project(oc(), d)
    risk = a.get("industry_risk") or {}
    print(f"  assess.industry_risk = {risk.get('level')} ({risk.get('name')})")
    # 正文里说的风险类别
    m = re.findall(r"职业病危害风险类别[为是]?\s*[\"']?([严重较重一般]{2})", prose)
    print(f"  正文声明的风险类别: {set(m)}")

    print()
    print("=" * 88)
    print("A. 限值出处抽查 (报告是否声称 GBZ 2.1 且值一致)")
    print("=" * 88)
    for name in ("苯乙烯", "甲醇", "苯"):
        db = c.execute("SELECT oel_type, value, source_standard FROM oel_limit "
                       "WHERE factor_name=? AND oel_type LIKE '%PC-TWA%'", (name,)).fetchall()
        for x in db:
            print(f"  {name:10} {x['oel_type']:16} = {x['value']:>8}  出处: {x['source_standard']}")


if __name__ == "__main__":
    main()
