#!/usr/bin/env python3
"""骨架解冻 场景4 (单元格级单向升级) 回归

判据边界全部固化在此: 升级只认「骨架缺 → 现算有」, 其余方向一律保留骨架。
用法: .venv/bin/python tools/test_unfreeze_cells.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from web.word_export import _merge_pending_cells, _unfreeze_truncated_rows  # noqa: E402

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


COLS = ["名称", "形态", "危害程度"]


def sk(rows, prov=None):
    return {"cols": COLS, "rows": rows, "status": "filled",
            "prov": prov if prov is not None else {"source": "rule",
                                                   "evidence": {"file": "骨架先行(导入时建表)"}}}


print("### 升级方向 (唯一允许的方向)")
r1 = _merge_pending_cells(sk([["苯醌", "—", "待补充"]]), {"cols": COLS, "rows": [["苯醌", "—", "轻度危害"]]})
check("骨架待补充 → 现算真值: 升级", r1 and r1["rows"], [["苯醌", "—", "轻度危害"]])
check("升级格写入格级 prov", "0_2" in (r1 or {}).get("prov", {}), True)
check("表级 prov 降为 _default 保留",
      (r1 or {})["prov"].get("_default", {}).get("source"), "rule")
check("升级计数标记", (r1 or {}).get("_pending_upgraded_cells"), 1)

r2 = _merge_pending_cells(sk([["甲醇", "液体", "待补充"], ["苯醌", "—", "待补充"]]),
                          {"cols": COLS, "rows": [["甲醇", "液体", "中度危害"], ["苯醌", "—", "轻度危害"]]})
check("多格升级全部落地", (r2 or {})["rows"], [["甲醇", "液体", "中度危害"], ["苯醌", "—", "轻度危害"]])
check("两格都记 prov", sorted((r2 or {})["prov"].keys()), ["0_2", "1_2", "_default"])

print("\n### 反向与冲突必须保留骨架 (返回 None)")
check("现算反而空 → 不倒退",
      _merge_pending_cells(sk([["苯醌", "—", "轻度危害"]]), {"cols": COLS, "rows": [["苯醌", "", ""]]}), None)
r_dash = _merge_pending_cells(sk([["苯醌", "—", "待补充"]]), {"cols": COLS, "rows": [["苯醌", "", "轻度危害"]]})
check("骨架「—」体裁标记不被现算空覆盖, 同格仍升级危害程度",
      r_dash and r_dash["rows"], [["苯醌", "—", "轻度危害"]])
check("倒退格不写 prov (只记真升级)", sorted((r_dash or {}).get("prov", {}).keys()), ["0_2", "_default"])
check("两侧都有值且不同 → 整表 veto",
      _merge_pending_cells(sk([["苯醌", "—", "中度危害"]]), {"cols": COLS, "rows": [["苯醌", "—", "轻度危害"]]}), None)
check("行标识不同位 → veto",
      _merge_pending_cells(sk([["甲苯", "液体", "待补充"]]), {"cols": COLS, "rows": [["二甲苯", "液体", "轻度危害"]]}), None)
check("cols 不一致 → veto",
      _merge_pending_cells(sk([["苯醌", "—", "待补充"]]), {"cols": ["名称", "形态"], "rows": [["苯醌", "—"]]}), None)
check("行数不同 → 场景4 不适用",
      _merge_pending_cells(sk([["苯醌", "—", "待补充"]]), {"cols": COLS, "rows": []}), None)
check("逐格全等 → 无需改动 (None)",
      _merge_pending_cells(sk([["苯醌", "—", "轻度危害"]]), {"cols": COLS, "rows": [["苯醌", "—", "轻度危害"]]}), None)

print("\n### 与原有整表场景共存 (回归)")
mk = _merge_pending_cells(sk([["苯醌", "—", "待补充"], ["甲醇", "液体", "中度危害"]]),
                          {"cols": COLS, "rows": [["苯醌", "—", "轻度危害"], ["甲醇", "液体", "中度危害"]]})
check("混有可升级格与相同格 → 只升级该格", mk["rows"],
      [["苯醌", "—", "轻度危害"], ["甲醇", "液体", "中度危害"]])
# 场景1 (前缀截断) 行为不变
s1 = _unfreeze_truncated_rows(sk([["苯醌", "防尘", "中度"]]), {"cols": COLS, "rows": [["苯醌", "防尘", "中度危害"]]})
check("前缀截断仍整表用现算 (场景1 未回退)", s1["rows"], [["苯醌", "防尘", "中度危害"]])
check("整表替换时不带 _pending_upgraded_cells 标记", "_pending_upgraded_cells" in s1, False)
# 场景1 走不到时的出口
s4 = _unfreeze_truncated_rows(sk([["苯醌", "—", "待补充"]]), {"cols": COLS, "rows": [["苯醌", "—", "轻度危害"]]})
check("_unfreeze 入口能触发场景4", s4["rows"], [["苯醌", "—", "轻度危害"]])
sV = _unfreeze_truncated_rows(sk([["苯醌", "—", "中度危害"]]), {"cols": COLS, "rows": [["苯醌", "—", "轻度危害"]]})
check("冲突时 _unfreeze 仍返回骨架本体", sV["rows"], [["苯醌", "—", "中度危害"]])

print("\n### 空/缺数据的诚实边界")
check("现算也是待补充 → 不假装升级",
      _merge_pending_cells(sk([["苯醌", "—", "待补充"]]), {"cols": COLS, "rows": [["苯醌", "—", "待补充（需企业）"]]}), None)
check("空串骨架也可被真值取代",
      _merge_pending_cells(sk([["苯醌", "—", ""]]), {"cols": COLS, "rows": [["苯醌", "—", "轻度危害"]]})["rows"],
      [["苯醌", "—", "轻度危害"]])

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
