"""修正 hazard_toxicity.basis 措辞 — 如实标注取值方法

背景(2026-10 探路结论):
  hazard_toxicity 27 条 level 值 = 真实报告 5.2-1 直接取证, **不是** THI 计算结果。
  原 basis 写作「GBZ/T 230-2025 (THI分级)」容易被读成「按 THI 公式算出来的」→ 误导。

  实测: 用 mem.gov.cn GHS 分类数据按 GBZ/T 230 公式算氨的 THI=82(极度危害),
        而真稿为「高度危害」→ 因多数物质 8 项毒理参数缺失, 按标准注6兜底会系统性虚高。
        故**不采用计算**, 维持真稿取证, 但措辞必须如实说明。

新措辞分两层:
  basis  = 分级体系出处 + 取值方法(如实: 取证, 非计算)
  source = 具体取证来源
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "ohs.db"

NEW_BASIS = (
    "GBZ/T 230—2025《工作场所毒物危害程度分级标准》(分级体系依据); "
    "取值方法=真实报告取证, 未经THI计算复核"
)


def main() -> int:
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB)
    n = conn.execute("SELECT COUNT(*) FROM hazard_toxicity").fetchone()[0]
    print(f"hazard_toxicity 共 {n} 条")
    print(f"\n旧 basis: {conn.execute('SELECT basis FROM hazard_toxicity LIMIT 1').fetchone()[0]!r}")
    print(f"新 basis: {NEW_BASIS!r}")

    if not apply:
        print("\n[预演] 加 --apply 写入。")
        return 0

    cur = conn.execute("UPDATE hazard_toxicity SET basis=?", (NEW_BASIS,))
    conn.commit()
    print(f"\n✓ 已更新 {cur.rowcount} 条")
    for r in conn.execute("SELECT DISTINCT basis FROM hazard_toxicity"):
        print("  现 basis:", repr(r[0]))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
