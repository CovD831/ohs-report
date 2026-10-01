"""迁移: built_tables 里旧的 PPE √/※ 横排矩阵壳 → 清空(交现算兜底)

背景:
  真稿 PPE 表 = 竖排 6 列 (生产单元|岗位|用品|单位|数量|更换周期), 43 表零矩阵。
  旧版导入时用 ppe_work_matrix 建了「PPE配备表」= 16列×15行 √/※ 矩阵并沉淀进
  project.data.built_tables。导出按「骨架优先」(word_export._tables_for_sub:126)
  取骨架行 → 矩阵覆盖了 fill_section 现算的竖排表 → 产物永远是矩阵。

修法:
  判定 built_tables 里哪张表是「PPE 横排矩阵」= 首列含「安全装备项目」或
  大量 √/※ 单元格; 命中则删除该骨架条目 → 导出回退现算(竖排 6 列)。
  ⚠ 只删矩阵壳, 不动其他 37 张骨架表。

用法:
  python tools/migrate_ppe_matrix.py            # dry-run 列出将改项目
  python tools/migrate_ppe_matrix.py --apply    # 执行
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ohs.db"


def is_ppe_matrix(t: dict) -> bool:
    """判定是否为 PPE √/※ 横排矩阵 (而非竖排 6 列表)"""
    cols = [str(c) for c in (t.get("cols") or t.get("header") or [])]
    rows = t.get("rows") or []
    if not cols or not rows:
        return False
    # 特征1: 首列标题含「安全装备项目」/「工作性质」
    if any(("安全装备" in c) or ("工作性质" in c) for c in cols[:1]):
        return True
    # 特征2: 表头是装备名罗列(≥10列) 且 数据区大量 √/※
    if len(cols) >= 10:
        marks = sum(1 for r in rows for c in r
                    if str(c).strip() in ("√", "※", "√ ", "※ "))
        if marks >= len(rows):
            return True
    return False


def main(argv):
    apply = "--apply" in argv
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT id, name, data FROM project").fetchall()
    touched = []
    for pid, name, data in rows:
        try:
            d = json.loads(data or "{}")
        except Exception:
            continue
        bt = d.get("built_tables") or {}
        if not bt:
            continue
        hit = [k for k, v in bt.items()
               if isinstance(v, dict) and ("PPE" in k or "防护" in k) and is_ppe_matrix(v)]
        if not hit:
            continue
        touched.append((pid, name, hit))
        print(f"{'[APPLY]' if apply else '[DRY]'} {pid} {name[:28]!r}")
        for k in hit:
            v = bt[k]
            print(f"     - {k}: {len(v.get('cols') or [])}列 × {len(v.get('rows') or [])}行 矩阵 → 删除骨架")
        if apply:
            for k in hit:
                del bt[k]
            d["built_tables"] = bt
            conn.execute("UPDATE project SET data=? WHERE id=?",
                         (json.dumps(d, ensure_ascii=False), pid))
    if apply:
        conn.commit()
    print()
    print(f"{'已修改' if apply else '将修改'} {len(touched)} 个项目")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
