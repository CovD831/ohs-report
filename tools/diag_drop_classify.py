"""定性: 新泰被丢的 27 条记录, 到底该进哪条路径?

假设: 它们本质是**识别类**信息 (岗位×多因素), 不是**检测结果**类 (因素×浓度)。
      我的管道:
        识别表 ← hazard_grid (从设备/工序规则推导)  ← 不看 detections
        检测结果表 ← detections (需 ctwa)            ← 新泰的多因素记录被塞进这里
      → 所以"丢弃"可能不是损失, 而是**归类错误**。

验证: 那些被丢的因素, 我的 hazard_grid 是否已独立识别出来?
      若是 → 没有信息损失, 只是不该走 detections。
"""
import json
import sqlite3
import sys

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

PID = "06ea028767"
c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
c.row_factory = sqlite3.Row

# ⚠ 新泰测试项目已清理 → 直接从**视觉提取缓存**分析 (那是权威输出)
import pathlib  # noqa: E402

CACHE = pathlib.Path("/Users/abaaba/Projects/ohs-report/data/vision_cache/"
                     "a66517be268181e6.json")
_vd = json.loads(CACHE.read_text())
all_d = _vd.get("detections") or []
print(f"=== 新泰 视觉提取: {len(all_d)} 条 ===")
multi = [x for x in all_d if "、" in str(x.get("factor") or "")]
print(f"  多因素(顿号) {len(multi)} 条")
withv = [x for x in all_d if x.get("ctwa") or x.get("results") or x.get("sio2")]
print(f"  有浓度/结果 {len(withv)} 条")
only_pass = [x for x in all_d if not (x.get("ctwa") or x.get("results") or x.get("sio2"))]
print(f"  仅有合格率/判定 {len(only_pass)} 条")
print()

drop_factors = set()
for x in only_pass:
    for f in str(x.get("factor") or "").split("、"):
        if f.strip():
            drop_factors.add(f.strip())
print(f"  这些记录涉及因素 {len(drop_factors)} 个:")
print(f"    {sorted(drop_factors)}")
print()

# 对照: 长兴 (有完整数据)
d2 = json.loads(c.execute("SELECT data FROM project WHERE id='c8c7ff0a4d'").fetchone()["data"])
grid2 = d2.get("hazard_grid") or []
gf2 = set()
for g in grid2:
    for f in (g.get("factors") or []):
        gf2.add(str(f).strip())
print(f"=== 对照 长兴 hazard_grid: {len(grid2)} 条 / {len(gf2)} 因素 ===")
print(f"  样本: {sorted(gf2)[:10]}")
print()
print("=== 判定 ===")
print("  长兴: hazard_grid 由 设备/工序 规则推导 → 识别表有数据 (42 行)")
print("  新泰: 若 hazard_grid 也为空 → 多因素记录是**唯一的识别信息来源**,")
print("        丢弃它们 = 真的丢信息 (不只是归类问题)")

