"""hazard_factor 别名映射 + OEL↔目录 对齐分析

背景: OEL(GBZ 2.1, 359物质) 与 分类目录(2015-92号, 375化学条目) 名称体系不同:
  - 目录用聚合条目: 氯化氢及盐酸 / 磷及其化合物(磷化氢、磷化锌...) 
  - OEL用细分物质: 氯化氢 / 磷化氢
  - 部分OEL物质目录无(丙烯酸只有酯类) — 真实差异
用途: 识别引擎输出每个危害因素时:
  1. OEL链精确查 (判定) 
  2. 目录链: 精确→包含→别名 三级查 (分类标签)
输出: data/ohs.db → hazard_factor_alias (oel_name → catalog_name)
用法: python3 -m knowledge.hazard_factor_alias
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

SRC = "auto-suggest-人工核对"

DDL = """
CREATE TABLE IF NOT EXISTS hazard_factor_alias (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  oel_name     TEXT NOT NULL,     -- OEL 物质名 (GBZ 2.1)
  catalog_name TEXT NOT NULL,     -- 分类目录条目名 (2015-92号)
  match_type   TEXT NOT NULL,     -- exact | contain | alias
  source       TEXT NOT NULL,
  verified     INTEGER DEFAULT 0
);
"""


def names_core(s: str) -> str:
    """名称核心词: 去空格/括号备注/结构前缀(1,2- / o- / N,N- / 全部异构体等)"""
    import re
    s = re.sub(r"[（(].*?[）)]", "", s)                    # 去括号备注
    s = re.sub(r"^([A-Za-z0-9.,\-oN/`]+[ -])+", "", s)     # 去前缀 (1,2- 二氯乙烯)
    s = s.replace("全部异构体", "").replace("异构体", "").strip()
    s = re.sub(r"[\s,，]+", "", s)                          # 去空白
    return s


def main():
    conn = connect()
    conn.execute(DDL)
    conn.execute("DELETE FROM hazard_factor_alias")
    oel = [r[0] for r in conn.execute(
        "SELECT DISTINCT factor_name FROM oel_limit WHERE factor_type='化学有害因素'")]
    cats = [r[0] for r in conn.execute(
        "SELECT name FROM hazard_factor WHERE category='化学因素'")]
    rows = []
    unmatched = []
    for o in oel:
        if o in cats:
            rows.append((o, o, "exact"))
            continue
        oc = names_core(o)
        # 核心词匹配: oc 在目录名核心词中 或 相等
        hits = []
        for d in cats:
            dc = names_core(d)
            if dc == oc:
                hits.append((d, "core_eq"))
            elif oc and (oc in dc or dc in oc) and len(oc) >= 2:
                hits.append((d, "core_contain"))
        if hits:
            # 取最短目录名(核心词最接近)
            best = min(hits, key=lambda x: (len(names_core(x[0])), len(x[0])))
            rows.append((o, best[0], best[1]))
        else:
            unmatched.append(o)
    conn.executemany("""
        INSERT OR REPLACE INTO hazard_factor_alias
        (oel_name, catalog_name, match_type, source, verified)
        VALUES (?, ?, ?, ?, 0)
    """, [(o, c, t, SRC) for o, c, t in rows])
    conn.commit()
    from collections import Counter
    ct = Counter(t for _, _, t in rows)
    print(f"映射: {len(rows)} 条 (exact={ct.get('exact',0)} core_eq={ct.get('core_eq',0)} "
          f"core_contain={ct.get('core_contain',0)})")
    print(f"未对齐: {len(unmatched)} 个 (目录真实无/聚合条目)")
    print()
    print("=== 人工核对清单 (core_contain 全部待人工) ===")
    print("自动可信: exact + core_eq (识别引擎直接使用)")
    with open(Path(__file__).resolve().parent / "alias_review.jsonl", "w", encoding="utf-8") as f:
        for o, c, t in rows:
            if t == "core_contain":
                f.write(f'{{"oel_name": "{o}", "catalog_name": "{c}"}}\n')
    conn.close()


if __name__ == "__main__":
    main()
