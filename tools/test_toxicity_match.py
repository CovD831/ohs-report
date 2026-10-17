"""守卫: lookup_toxicity 物质匹配必须**宁缺勿错** — 禁止张冠李戴

背景(2026-10 实测踩坑):
  原实现用无约束子串包含 `name in fac or fac in name`:
    「丙酮」(低毒溶剂) ⊂ 「丙酮氰醇」(剧毒) → 丙酮被判「剧毒」❌
    「乙酸」⊂ 「乙酸乙烯酯」→ 串物质
  物质身份必须**确定**(用户红线), 故改为: 归一化等值 + 同义名组精确拆分。

断言:
  1. 严禁跨物质子串匹配(丙酮/丙酮氰醇、乙酸/乙酸乙烯酯、苯/乙苯)
  2. 同义名组仍能命中(氯化氢及盐酸 ↔ 盐酸)
  3. 串物质守卫在**合成库**里断言 (见下"为什么不用真库")
  4. 真库只断言 C 口径的取值形态: 非等级标签不得进「危害程度」列

⚠ 2026-10-14 C 方案后, 本文件第 3 块曾直接拿真库字符串比对 (丙酮→待补充、
  丙酮氰醇→剧毒)。改成注6 单值后两物质都落到「极度危害（按注6…）」,
  字符串不再可判别 → 张冠李戴就算复发也测不出来。故跨物质守卫下沉到
  合成库: 两行 level 人为设成不同等级, 串了必然露馅。
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# S1c″ D4′: 真库只读核对 → 只读工厂 (旧版 connect() 在容器里会可写打开生产库)
from knowledge.oel import connect_ro              # noqa: E402
from web.section_filler import _same_substance, lookup_toxicity  # noqa: E402

GRADES = ("轻度危害", "中度危害", "高度危害", "极度危害")
fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ✓ {label}")
    else:
        fails.append(f"{label} {detail}")
        print(f"  ✗ {label} {detail}")


print("### 1. 单元: 跨物质必须判为不同")
CROSS = [
    ("丙酮", "丙酮氰醇"),
    ("乙酸", "乙酸乙烯酯"),
    ("苯", "乙苯"),
    ("甲苯", "甲苯二异氰酸酯"),
    ("甲醇", "甲醇钠"),
]
for a, b in CROSS:
    check(f"{a} ≠ {b}", not _same_substance(a, b), "← 误判为同一物质")

print("\n### 2. 单元: 同义名组必须判为同一")
SAME = [
    ("氯化氢及盐酸", "盐酸"),
    ("甲苯", "甲苯"),
    ("三氯甲烷（氯仿）", "三氯甲烷"),
]
for a, b in SAME:
    check(f"{a} = {b}", _same_substance(a, b), "← 漏判")


def synth(rows):
    """只建 lookup_toxicity 会读到的表; 其余表缺失 → _thi_grade_cell 走 except 返回 None"""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE hazard_toxicity(factor_name TEXT, aliases TEXT, level TEXT, source TEXT)")
    c.executemany("INSERT INTO hazard_toxicity VALUES(?,?,?,?)", rows)
    return c


print("\n### 3. 合成库: 串物质必然露馅 (level 人为设成不同等级)")
# A. 事故原貌: 库里只有"邻名"物质, 查询物质本身未收录 → 必须待补充
c = synth([
    ("丙酮氰醇", "", "剧毒", "真实报告表5.2-1"),
    ("乙酸乙烯酯", "", "中度危害", "真实报告表5.2-1"),
    ("乙苯", "", "高度危害", "真实报告表5.2-1"),
])
for fac, expect_label in (("丙酮", "不吞「丙酮氰醇」"),
                          ("乙酸", "不吞「乙酸乙烯酯」"),
                          ("苯", "不吞「乙苯」")):
    r = lookup_toxicity(c, fac)
    check(f"{fac} 未收录 → 待补充 ({expect_label})", r == "待补充", f"实得 {r!r}")
c.close()

# B. 两行都在: 必须取自身行, 且非等级标签一律不进列
c = synth([
    ("丙酮氰醇", "", "剧毒", "真实报告表5.2-1"),
    ("丙酮", "", "轻度危害", "真实报告表5.2-1"),
])
r = lookup_toxicity(c, "丙酮")
check("丙酮 取自身行(轻度危害), 不是丙酮氰醇的等级", r == "轻度危害", f"实得 {r!r}")
r = lookup_toxicity(c, "丙酮氰醇")
check("丙酮氰醇 的「剧毒」标签不进危害程度列", r == "待补充", f"实得 {r!r}")
c.close()

print("\n### 4. 真库: C 口径取值形态")
conn = connect_ro()
r = lookup_toxicity(conn, "丙酮")
check("丙酮 不含异物质标签「剧毒」", "剧毒" not in r, f"实得 {r!r}")
check("丙酮 为等级(可带注6标注)或待补充",
      r == "待补充" or r.startswith(GRADES), f"实得 {r!r}")

r = lookup_toxicity(conn, "氯化氢及盐酸")
check("氯化氢及盐酸 经同义组命中盐酸的权威定级", r == "中度危害", f"实得 {r!r}")

r = lookup_toxicity(conn, "丙酮氰醇（按CN 计）")
check("丙酮氰醇（按CN 计） 不再印标签「剧毒」", "剧毒" not in r, f"实得 {r!r}")
check("丙酮氰醇（按CN 计） 为等级或待补充",
      r == "待补充" or r.startswith(GRADES), f"实得 {r!r}")
conn.close()

print()
if fails:
    for f in fails:
        print(f"  ✗ {f}")
    sys.exit(1)
print("跨物质守卫 + C 口径: 通过")
