#!/usr/bin/env python3
"""危害程度格子口径回归 (2026-10-14 C 方案定稿)

口径: 权威来源(真实报告/GHS名录已给四级之一) → 原样单值;
      我们算的(THI) 或 库里存的不是等级 → 按注6 出等级 + 实测项数 + 待复核;
      完全无源 → 待补充。

⚠⚠ 教训 (2026-10-14): 本文件上一版直接硬读 data/ohs.db 的「苯醌」「丙酮」等具体行,
  部署到生产容器后 line 36 `row[0]` 直接 TypeError —— 生产 hazard_toxicity 比本地少一行
  (本地 104 / 生产 103)。**对本地数据过拟合的守卫 = 没有守卫**。
  现在拆两段:
    A. 合成库跑「判定表」: 口径分支是真值表, 不需要任何官方数据;
    B. 真库只跑**不变式**: 对库里实际存在的行断言普适性质, 不点名任何物质。
用法: python tools/test_toxicity_cell.py   (本地 .venv 与生产容器内均可)
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import web.section_filler as sf                        # noqa: E402
from web.section_filler import lookup_toxicity         # noqa: E402
from web.thi_rating import rate_substance, MIN_HAVE    # noqa: E402

GRADES = ("轻度危害", "中度危害", "高度危害", "极度危害")
NOTE6 = "极度危害（按注6；实测4/8项，待复核）"
n_pass = n_fail = n_skip = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got!r}\n      期望: {want!r}")


def skip(label, why):
    global n_skip
    n_skip += 1
    print(f"  – {label} 跳过 ({why})")


# ============ A. 判定表: 合成库, 口径分支不依赖任何官方数据 ============
# _thi_grade_cell 打桩成固定字符串: 只验 lookup_toxicity 的"取哪一支", 不重算 THI
_real_thi = sf._thi_grade_cell
sf._thi_grade_cell = lambda conn, fac: NOTE6


def synth(rows):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE hazard_toxicity(factor_name TEXT, aliases TEXT, level TEXT, source TEXT)")
    c.executemany("INSERT INTO hazard_toxicity VALUES(?,?,?,?)", rows)
    return c


try:
    print("### A1 库里存的等级是 THI 算出来的 (source 含 thi) → 必须走注6, 不印下界")
    c = synth([("苯醌", "", "轻度危害", "gbz_t_230_2025_thi + ghs_class(CAS精确)")])
    check("下界(轻度危害)不被印出", lookup_toxicity(c, "苯醌"), NOTE6)
    c.close()

    print("\n### A2 权威定级 (真实报告/GHS 名录) → 原样单值, 不加标注")
    c = synth([("苯乙烯", "", "中度危害", "真实报告表5.2-1 取证"),
               ("某GHS物质", "", "高度危害", "gbz_2019_ghs_list")])
    check("苯乙烯 原样", lookup_toxicity(c, "苯乙烯"), "中度危害")
    check("GHS 名录行 原样 (不当成我们算的)", lookup_toxicity(c, "某GHS物质"), "高度危害")
    c.close()

    print("\n### A3 存的不是等级 (致癌/剧毒/待补充 标签) → 禁止进「危害程度」列")
    for tag in ("致癌", "剧毒", "待补充", ""):
        c = synth([("某物质", "", tag, "ghs_class")])
        check(f"标签 {tag!r} 不印出, 下探注6", lookup_toxicity(c, "某物质"), NOTE6)
        c.close()

    print("\n### A4 source 含 thi 但注6 也算不出 → 退回存值 (不空转)")
    sf._thi_grade_cell = lambda conn, fac: None
    c = synth([("半截物质", "", "中度危害", "gbz_t_230_2025_thi")])
    check("退回库内值", lookup_toxicity(c, "半截物质"), "中度危害")
    c.close()

    print("\n### A5 无行且算不出 → 待补充 (不编造); 空因子名不炸")
    sf._thi_grade_cell = lambda conn, fac: None
    c = synth([("别的物质", "", "中度危害", "x")])
    check("未收录 → 待补充", lookup_toxicity(c, "查无此物"), "待补充")
    check("空因子名 → 待补充", lookup_toxicity(c, ""), "待补充")
    c.close()
    sf._thi_grade_cell = _real_thi

    print("\n### A6 表不存在 (未初始化官方数据层) 也不炸")
    c = sqlite3.connect(":memory:")
    check("hazard_toxicity 缺表 → 待补充", lookup_toxicity(c, "苯醌"), "待补充")
    c.close()
    sf._thi_grade_cell = _real_thi
except Exception as e:                                    # noqa: BLE001
    sf._thi_grade_cell = _real_thi
    check("A 段不抛异常", f"抛出 {type(e).__name__}: {e}", "无异常")

# ============ B. 真库不变式: 只点名"库里确实存在的行", 不硬编码物质 ============
DB = os.path.join(ROOT, "data", "ohs.db")
conn = sqlite3.connect(DB)
print(f"\n### B. 真库不变式 (DB={DB})")

rows = conn.execute("select factor_name, level, source from hazard_toxicity").fetchall()
check("官方数据层可用 (有行)", len(rows) > 0, True)

bad_out = []
for name, level, source in rows:
    out = lookup_toxicity(conn, name)
    # 不变式1: 输出只能是 四级(可带注6标注) 或 待补充
    if not (out == "待补充" or out.startswith(GRADES)):
        bad_out.append((name, out))
    # 不变式2: 库里存的是标签时, 标签不得原样进列
    #   例外: '待补充' 本身就是本列的合法缺值写法, 存它印它结果没错, 不算"标签泄漏"
    if level and level not in GRADES and level != "待补充" and out == level:
        bad_out.append((name, f"标签 {level!r} 被印出"))
    # 不变式3: 权威定级行 (非 thi 来源) 必须原样保留
    if level in GRADES and "thi" not in (source or "").lower() and out != level:
        bad_out.append((name, f"权威 {level!r} 被改成 {out!r}"))
check(f"{len(rows)} 行全量满足三条不变式", bad_out[:5], [])

# 不变式4: min_have 只放宽门槛, 不改算法
#   取样面 = hazard_toxicity 里 thi 来源行 ∪ ghs_class 名录前 40 名 (按名排序, 可复现);
#   只用 hazard_toxicity 会让"生产库里没这批行"时整段空转, 等于没测。
cands = [n for n, _l, s in rows if "thi" in (s or "").lower()]
try:
    cands += [r[0] for r in conn.execute(
        "select distinct name from ghs_class order by name limit 40")]
except sqlite3.OperationalError:
    pass
seen: set[str] = set()
cands = [c for c in cands if c and not (c in seen or seen.add(c))]
gate_viol, tested = [], 0
for name in cands:
    cas = sf._cas_from_oel(conn, name)
    strict = rate_substance(conn, name, cas)
    loose = rate_substance(conn, name, cas, min_have=0)
    if loose is None:
        continue
    tested += 1
    have = loose.get("have", 0)
    want_strict_none = have < MIN_HAVE
    if (strict is None) != want_strict_none:
        gate_viol.append((name, have, strict is not None))
    elif not want_strict_none and strict.get("thi_note6") != loose.get("thi_note6"):
        gate_viol.append((name, "两法结果不一致", strict.get("thi_note6"), loose.get("thi_note6")))
if tested:
    check(f"min_have 语义 ({tested} 项实测): 严格门槛=None ⟺ have<{MIN_HAVE}", gate_viol, [])
else:
    skip("min_have 语义", "库中无可算出注6结果的行")

conn.close()

print(f"\n通过 {n_pass} / 失败 {n_fail} / 跳过 {n_skip}")
sys.exit(1 if n_fail else 0)
