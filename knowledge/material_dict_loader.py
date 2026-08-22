"""物料危害词典构建 — 设备知识库第3层

原理: 物料词 (来自设备物料桥) → OEL 表物质名 (CAS锚点) → 危害因素
匹配策略 (保守降级):
  1. 精确匹配: 物料词 == OEL factor_name
  2. 包含匹配: OEL 名包含物料词 或 物料词包含OEL名 (标记 fuzzy=1 供人工)
  3. 均不配: 入 unmapped 清单 (人工补词典)

输出: data/ohs.db → material_dictionary
      data/ohs.db → material_dictionary (fuzzy=0 精确 / fuzzy=1 模糊)
      knowledge/material_unmapped.jsonl (未映射物料, 人工补全)
用法: python3 -m knowledge.material_dict_loader
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS material_dictionary (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  material     TEXT NOT NULL,      -- 物料词 (丙烯酸/环己烷...)
  oel_factor   TEXT,               -- OEL 物质名 (精确/模糊匹配到的)
  cas          TEXT,               -- 该物质的 CAS
  fuzzy        INTEGER DEFAULT 0,  -- 0=精确匹配 1=模糊(子串) 待人工
  source       TEXT NOT NULL,
  verified     INTEGER DEFAULT 0
);
"""

UNMAPPED = Path(__file__).resolve().parent / "material_unmapped.jsonl"


def split_materials(material_field: str) -> list[str]:
    """物料字段 → 物料词列表 (去前缀/换行/长度过滤)"""
    if not material_field:
        return []
    # 先清理换行 (find_tables 里多行单元格)
    text = material_field.replace("\n", " ").replace("\r", " ")
    parts = re.split(r"[、,，/;\s]+", text)
    out = []
    for p in parts:
        p = re.sub(r"^(物料|内部物料|介质|罐内)[：:]?", "", p).strip()
        # 去设备部位前缀 (外半管/内蛇管/壳程/管程/槽内/筒体 等)
        p = re.sub(r"^(外半管|内蛇管|外盘管|内盘管|壳程|管程|槽内|槽体|筒体|主体|内换热列管|内换热管)[：:]?", "", p).strip()
        p = re.sub(r"^[（(].*?[）)]$", "", p).strip()
        if 1 < len(p) <= 15 and not p.isdigit():
            out.append(p)
    return out


def main():
    conn = connect()
    conn.executescript(DDL)
    # OEL 物质锚点 (因子类型 = 化学/粉尘/生物)
    oels = [(r[0], r[1]) for r in conn.execute(
        "SELECT DISTINCT factor_name, cas FROM oel_limit "
        "WHERE factor_type IN ('化学有害因素','粉尘','生物因素') AND factor_name IS NOT NULL")]
    # 物料词
    mats = set()
    for (m,) in conn.execute("SELECT DISTINCT material FROM equipment_material"):
        mats.update(split_materials(m))
    # 匹配
    rows, unmapped = [], []
    for mat in sorted(mats):
        exact = [o for o, c in oels if o == mat]
        if exact:
            for o in exact:
                rows.append({"material": mat, "oel_factor": o,
                             "cas": dict((o, c) for o, c in oels)[o], "fuzzy": 0})
            continue
        # 包含匹配 (取最长命中)
        cands = [(o, c) for o, c in oels if (o in mat or mat in o) and len(o) > 1]
        if cands:
            best = max(cands, key=lambda x: len(x[0]))
            rows.append({"material": mat, "oel_factor": best[0], "cas": best[1], "fuzzy": 1})
        else:
            unmapped.append(mat)
    conn.execute("DELETE FROM material_dictionary")
    conn.executemany("""
        INSERT OR REPLACE INTO material_dictionary
        (material, oel_factor, cas, fuzzy, source, verified)
        VALUES (:material, :oel_factor, :cas, :fuzzy, :source, 0)
    """, [{**r, "source": "设备物料桥×OEL自动匹配"} for r in rows])
    conn.commit()
    with open(UNMAPPED, "w", encoding="utf-8") as f:
        for u in unmapped:
            f.write(json.dumps({"material": u}, ensure_ascii=False) + "\n")
    fuzzy_n = sum(1 for r in rows if r["fuzzy"])
    print(f"物料词: {len(mats)} | 入库: {len(rows)} (精确 {len(rows)-fuzzy_n} / 模糊 {fuzzy_n})")
    print(f"未映射: {len(unmapped)} → {UNMAPPED.name}")
    # 展示
    for r in conn.execute(
            "SELECT material, oel_factor, cas, fuzzy FROM material_dictionary "
            "WHERE fuzzy=0 LIMIT 12"):
        print(f"  ✓ {r[0]} → {r[1]} (CAS {r[2]})")
    conn.close()


if __name__ == "__main__":
    main()
