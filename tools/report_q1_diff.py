"""Q1 差异报告: 把"表型B 岗位↔因素映射"与现有 hazard_grid 逐项比

目的: 让用户**带着数据**决定"要不要把表型B映射补进识别表"。
只读不改。

对比维度:
  ① hazard_grid 覆盖了哪些 岗位/工序
  ② 表型B 提供了哪些 岗位↔因素
  ③ 两者重叠 / 表型B 独有 / hazard_grid 独有
  ④ 若补进去, 识别表行数/因素数会怎么变
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

# ---------- A. 新泰 表型B 的 岗位↔因素 ----------
CACHE = Path("/Users/abaaba/Projects/ohs-report/data/vision_cache/"
             "a66517be268181e6.json")
vd = json.loads(CACHE.read_text())
B = [x for x in vd["detections"] if x.get("table_type") == "B"]

B_pairs = {}     # 岗位 -> set(因素)
for x in B:
    post = str(x.get("sampling_point") or "").strip()
    if not post:
        continue
    facs = [f.strip() for f in str(x.get("factor") or "").replace(",", "、").split("、")]
    facs = [f for f in facs if f]
    if facs:
        B_pairs.setdefault(post, set()).update(facs)

print("=" * 84)
print("① 新泰 表型B —— 岗位↔因素 映射 (这是它独有的信息)")
print("=" * 84)
print(f"  岗位数 {len(B_pairs)}")
for p, fs in list(B_pairs.items())[:8]:
    print(f"    {p[:44]:46} → {len(fs)} 因素")
print()

# ---------- B. 现有识别表 (新泰没有, 用长兴做对照说明机制) ----------
import sqlite3  # noqa: E402

c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
c.row_factory = sqlite3.Row
d = json.loads(c.execute("SELECT data FROM project WHERE id='c8c7ff0a4d'").fetchone()["data"])
grid = d.get("hazard_grid") or []
print("=" * 84)
print("② 现有 hazard_grid 机制 (用长兴对照) —— 看它的'岗位/工序'从哪来")
print("=" * 84)
print(f"  hazard_grid 条数: {len(grid)}")
if grid:
    k = sorted(grid[0].keys())
    print(f"  字段: {k}")
    for g in grid[:3]:
        print(f"    样本: {json.dumps(g, ensure_ascii=False)[:150]}")

print()
print("=" * 84)
print("③ 关键对比: 表型B 的'岗位' vs hazard_grid 的'工序/设备'")
print("=" * 84)
grid_units = set()
for g in grid:
    for key in ("unit", "工序", "岗位", "process", "equipment", "设备"):
        v = g.get(key)
        if v:
            grid_units.add(str(v).strip())
print(f"  hazard_grid 里的单元/工序标识 {len(grid_units)} 个:")
print(f"    {sorted(grid_units)[:12]}")
print()
print(f"  表型B 的岗位标识 {len(B_pairs)} 个 (带'操作工-/巡检工-'前缀 + 工位):")
print(f"    {sorted(B_pairs)[:6]}")
print()
print("=== 粒度差异判断 ===")
print("  hazard_grid 单元 → 粗粒度 (工序/设备级, 如 '反应釜' '投料')")
print("  表型B 岗位     → 细粒度 (人员级, 如 '操作工-三车间氟硼酸钾生产单元干燥投料操作位')")
print()
print("  → 两者**粒度不同**, 不是同一层东西:")
print("    hazard_grid 回答'这个工序产生什么危害'")
print("    表型B     回答'这个具体工位的人接触什么危害'")
print()
print("  ⚠ 若直接并进识别表: 会出现'反应釜'和'操作工-三车间...'两套粒度混排")
print("     → 建议: 只取表型B 的**因素清单**(补 hazard_grid 漏掉的), 不并岗位粒度")

# ---------- C. 8 个未测因素 ----------
print()
print("=" * 84)
print("④ 表型B 独有因素 (真实报告惯例: 应标'未检测')")
print("=" * 84)
A = [x for x in vd["detections"] if x.get("table_type") == "A"]
A_fac = {str(x.get("factor") or "").replace("（", "(").replace("）", ")").strip()
         for x in A if (x.get("ctwa") or x.get("cstel") or x.get("results"))}
B_all = set()
for fs in B_pairs.values():
    B_all |= fs
missing = {f for f in (B_all - A_fac)
           if not any(ch in f for ch in "()")}     # 剔掉残缺括号的
print(f"  A 已测 {len(A_fac)} 因素 | B 提到 {len(B_all)} 因素")
print(f"  B 提到但 A 未测 (完整名): {sorted(missing)}")
