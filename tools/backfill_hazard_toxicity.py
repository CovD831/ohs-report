"""按 GBZ/T 230—2025 补齐 hazard_toxicity (危害程度) — 预演/应用双模式

2026-10-14 背景:
  「健康影响表·危害程度」列 10 处待补充, 三向核验结论:
    - 项目材料不提供 (材料缺口);
    - 真稿有值但**真稿是同公司另一产线** (丙烯酸酯) 的健康影响表, 不是本项目
      (不饱和聚酯树脂) 的直接对标 → 不能照抄;
    - 官方数据层可算: GBZ/T 230—2025 PDF 在本地 + ghs_class 2832 条。

纪律:
  - CAS 精确匹配 (禁止无约束子串; skill 教训: 丙酮曾误配丙酮氰醇→剧毒)
  - 只补 hazard_toxicity 里**没有**的物质 (不覆盖 verified=1 行, 不覆盖真稿取证行)
  - 每条带 basis (算法+实测项数+缺项清单) + source (gbz_t_230_2025_thi)
  - 预演默认 (--apply 才写)

用法:
  .venv/bin/python tools/backfill_hazard_toxicity.py            # 预演
  .venv/bin/python tools/backfill_hazard_toxicity.py --apply    # 写入
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.thi_rating import rate_substance  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "ohs.db")

# 待补物质 (factor_name, CAS) — 来自最终版报告 t33 健康影响表 10 处 + 相邻物质
# CAS 全部来自 oel_limit 表 (GBZ 2.1—2019) 的权威登记, 不手填
TARGETS = [
    ("N,N-二甲基苯胺", "121-69-7"),
    ("丙酮", "67-64-1"),
    ("乙二醇", "107-21-1"),
    ("乙酸乙酯", "141-78-6"),
    ("二聚环戊二烯", "77-73-6"),
    ("双酚A", "80-05-7"),
    ("对苯二甲酸", "100-21-0"),
    ("苯醌", "106-51-4"),
    ("邻苯二甲酸酐", "85-44-9"),
    ("马来酸酐", "108-31-6"),
    ("二甲苯", "1330-20-7"),
]


def main() -> int:
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    have_rows = {r["factor_name"] for r in
                 conn.execute("SELECT factor_name FROM hazard_toxicity")}
    print(f"=== hazard_toxicity 现有 {len(have_rows)} 条 ===")

    n_new = n_skip = n_none = 0
    plan = []
    for name, cas in TARGETS:
        if name in have_rows:
            r = conn.execute("SELECT level, grade, source FROM hazard_toxicity "
                             "WHERE factor_name=?", (name,)).fetchone()
            print(f"[跳过] {name:<14} 已收录 level={r['level']} src={r['source']}")
            n_skip += 1
            continue
        res = rate_substance(conn, name, cas)
        if not res:
            print(f"[缺数] {name:<14} GHS/理化数据不足 → 不写 (维持待补充)")
            n_none += 1
            continue
        plan.append((name, cas, res))
        print(f"[待写] {name:<14} THI={res['thi']:<5} {res['level']} "
              f"(实测{res['have']}/8, 注6值{res['thi_note6']}→{res['level_note6']}) "
              f"match={res['matched']}/{res['matched_name']}")
        n_new += 1

    print(f"\n=== 汇总: 待写 {n_new} / 已收录跳过 {n_skip} / 数据不足 {n_none} ===")
    if not apply:
        print("(预演模式 — 未写库; 加 --apply 才写入)")
        conn.close()
        return 0

    for name, cas, res in plan:
        conn.execute(
            "INSERT INTO hazard_toxicity (factor_name, aliases, form, level, grade, "
            "basis, source, verified) VALUES (?,?,?,?,?,?,?,?)",
            (name, "", "", res["level"], res["grade"], res["basis"], res["source"], 0))
    conn.commit()
    print(f"\n✅ 已写入 {len(plan)} 条")
    for r in conn.execute("SELECT factor_name, level, grade, source FROM hazard_toxicity "
                          "WHERE source LIKE 'gbz_t_230%' ORDER BY id"):
        print("   ", dict(r))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
