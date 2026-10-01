"""守卫: lookup_toxicity 物质匹配必须**宁缺勿错** — 禁止张冠李戴

背景(2026-10 实测踩坑):
  原实现用无约束子串包含 `name in fac or fac in name`:
    「丙酮」(低毒溶剂) ⊂ 「丙酮氰醇」(剧毒) → 丙酮被判「剧毒」❌
    「乙酸」⊂ 「乙酸乙烯酯」→ 串物质
  物质身份必须**确定**(用户红线), 故改为: 归一化等值 + 同义名组精确拆分。

断言:
  1. 严禁跨物质子串匹配(丙酮/丙酮氰醇、乙酸/乙酸乙烯酯、苯/乙苯)
  2. 同义名组仍能命中(氯化氢及盐酸 ↔ 盐酸)
  3. 未收录 → 「待补充」(不编造)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge.oel import connect              # noqa: E402
from web.section_filler import _same_substance, lookup_toxicity  # noqa: E402

fails = []

# --- 1. 单元: 跨物质必须 False ---
CROSS = [
    ("丙酮", "丙酮氰醇"),
    ("乙酸", "乙酸乙烯酯"),
    ("苯", "乙苯"),
    ("甲苯", "甲苯二异氰酸酯"),
    ("甲醇", "甲醇钠"),
]
for a, b in CROSS:
    if _same_substance(a, b):
        fails.append(f"跨物质误判为同一: {a!r} ~ {b!r}")
print(f"  ✓ 跨物质判定 {len(CROSS)} 例均 False" if not any("跨物质" in f for f in fails) else "")

# --- 2. 单元: 同义名组必须 True ---
SAME = [
    ("氯化氢及盐酸", "盐酸"),
    ("甲苯", "甲苯"),
    ("三氯甲烷（氯仿）", "三氯甲烷"),
]
for a, b in SAME:
    if not _same_substance(a, b):
        fails.append(f"同义名组漏判: {a!r} ~ {b!r}")
print(f"  ✓ 同义名组 {len(SAME)} 例均 True" if not any("同义名组" in f for f in fails) else "")

# --- 3. 集成: 真实查表 ---
conn = connect()
r_acetone = lookup_toxicity(conn, "丙酮")
if r_acetone == "剧毒":
    fails.append("丙酮 仍被判「剧毒」(串到丙酮氰醇) — 严重")
elif r_acetone != "待补充":
    fails.append(f"丙酮 期望「待补充」, 实得 {r_acetone!r}")
else:
    print("  ✓ 丙酮 → 待补充 (未串到丙酮氰醇)")

r_hcl = lookup_toxicity(conn, "氯化氢及盐酸")
if r_hcl == "待补充":
    fails.append("氯化氢及盐酸 未能经同义组命中盐酸")
else:
    print(f"  ✓ 氯化氢及盐酸 → {r_hcl} (同义组命中)")

r_cyan = lookup_toxicity(conn, "丙酮氰醇（按CN 计）")
if r_cyan != "剧毒":
    fails.append(f"丙酮氰醇 期望「剧毒」, 实得 {r_cyan!r}")
else:
    print("  ✓ 丙酮氰醇 → 剧毒 (自身命中)")

print()
if fails:
    for f in fails:
        print(f"  ✗ {f}")
    sys.exit(1)
print("通过: 3/3")
