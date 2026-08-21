"""GBZ 2.2—2007 物理因素限值入库 — 判定引擎基准数据源

数据采集方式: 扫描件(~/Desktop/law/data/gbz_pdfs/GBZ 2.2-2007.pdf) 300dpi 渲染
→ vision 模型逐表直读 → knowledge/data/gbz22_physical.json (人工可读可改)

条目类型: 条件规则型 (同一因素多条规则, conditions 承载适用条件), 区别于化学因素表。
修改单: GBZ 2.2—2007 无修改单; 新版本 GBZ 2.2 未发布 (catalog 核实 2026-08 在线仍为2007版)。

输出: data/ohs.db → oel_limit (verified=0)
用法: python3 -m knowledge.gbz22_loader
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect, insert  # noqa: E402

DATA = Path(__file__).resolve().parent / "data" / "gbz22_physical.json"
SRC = "GBZ 2.2—2007"


def main():
    d = json.loads(DATA.read_text(encoding="utf-8"))
    rows = []
    for r in d["rules"]:
        rows.append({
            "factor_name": r["factor_name"],
            "factor_type": r.get("factor_type", "物理因素"),
            "cas": r.get("cas"),
            "english_name": r.get("english_name"),
            "oel_type": r["oel_type"],
            "value": r["value"],
            "unit": r.get("unit", ""),
            "conditions": r.get("conditions"),
            "note": r.get("note"),
            "source_standard": SRC,
            "verified": 0,
        })
    conn = connect()
    conn.execute("DELETE FROM oel_limit WHERE source_standard=?", (SRC,))  # 幂等重建
    insert(conn, rows)
    cur = conn.execute(
        "SELECT COUNT(DISTINCT factor_name), COUNT(*) FROM oel_limit WHERE source_standard=?", (SRC,))
    n_f, n_r = cur.fetchone()
    print(f"入库 GBZ 2.2—2007: {n_f} 因素 / {n_r} 规则行 (verified=0)")
    # 抽查
    for name in ("噪声", "高温", "手传振动", "工频电场", "微波辐射", "中波紫外线"):
        for r in conn.execute(
                "SELECT factor_name, oel_type, value, unit, COALESCE(conditions,'') "
                "FROM oel_limit WHERE source_standard=? AND factor_name=? ORDER BY oel_type LIMIT 2",
                (SRC, name)):
            print("  抽查:", " | ".join(str(x) for x in r))
    conn.close()


if __name__ == "__main__":
    main()
