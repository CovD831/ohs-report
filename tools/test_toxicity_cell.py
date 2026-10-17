#!/usr/bin/env python3
"""危害程度格子口径回归 (2026-10-14 C 方案定稿)

口径: 权威来源(真实报告/GHS名录已给四级之一) → 原样单值;
      我们算的(THI) 或 库里存的不是等级 → 按注6 出等级 + 实测项数 + 待复核;
      完全无源 → 待补充。

主口径唯一 (2026-10-16 拍板"统一到注6"): rate_substance 的 thi/level/grade **恒等于**
  *_note6; thi_zero/level_zero 只是随结果返回的复核参照。官方层里 source 含 thi 的行,
  存值必须等于现算注6 —— 否则库里就留着上一轮"下界为主"的第二套口径 (不变式5/6 + 阳性对照)。
  重算工具: `python tools/backfill_hazard_toxicity.py --refresh-thi --apply`。

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
# 取数走**只读工厂** (S1c″ D4′)。上一版是 `DB = os.path.join(ROOT, "data", "ohs.db")` +
# `sqlite3.connect(DB)`, 两个问题一起犯: ① 可写模式打开 —— 本文件在 GUARD_TESTS 里,
# 容器里 `ROOT/data` 就是挂进去的生产库, 守卫套每跑一次就给生产库开一次可写连接
# (WAL 辅助文件的生灭/回写); ② 路径硬编码 —— OHS_DATA_DIR 指到临时卷时它照读真实库,
# 于是"演练/临时卷"根本测不到这一段。
# 库或表不在 → 打印原因并 **exit 1**: 静默跳过 (旧写法 `except: pass` / `if exists() else 跳过`)
# 会让"绿"变成"没测"。
from knowledge.oel import OfficialSchemaMissing, connect_ro      # noqa: E402
from knowledge.paths import DB_PATH                              # noqa: E402

print(f"\n### B. 真库不变式 (DB={DB_PATH})")
try:
    conn = connect_ro()
except (sqlite3.Error, OfficialSchemaMissing) as e:
    print(f"  ✗ 官方数据层不可读, 而 B 段必须跑在它上面 ({type(e).__name__}: {e})")
    sys.exit(1)

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


def thi_row_staleness(name, level, grade):
    """一条 hazard_toxicity 的「我们算的」行是否与现算注6 脱节 → 脱节描述 / None。

    抽成函数的唯一目的: 下面的**阳性对照**要喂它一个假存值, 让它跑**同一段代码** ——
    判据自己另写一遍比对逻辑, 测的就不是那条判据能不能红了。
    """
    r = rate_substance(conn, name, sf._cas_from_oel(conn, name), min_have=0)
    if r is None:
        return None
    if (level, grade) != (r["level"], r["grade"]):
        return f"{name}: 存值 {level}/{grade} != 现算注6 {r['level']}/{r['grade']} (THI {r['thi']})"
    return None


gate_viol, main_viol, tested = [], [], 0
for name in cands:
    cas = sf._cas_from_oel(conn, name)
    strict = rate_substance(conn, name, cas)
    loose = rate_substance(conn, name, cas, min_have=0)
    if loose is None:
        continue
    tested += 1
    # 不变式5: **主口径唯一** —— 主输出必须就是注6 值 (2026-10-16 拍板"统一到注6")。
    #   下界 (thi_zero/level_zero) 只是随结果返回的复核参照; 谁把它接回主字段,
    #   `hazard_toxicity` 存的、报告印的、脚本算的就又变成三套口径 (本轮实测的病灶)。
    if (loose["thi"], loose["level"], loose["grade"]) != \
            (loose["thi_note6"], loose["level_note6"], loose["grade_note6"]):
        main_viol.append((name, "主输出 != 注6",
                          (loose["thi"], loose["level"]),
                          (loose["thi_note6"], loose["level_note6"])))
    have = loose.get("have", 0)
    want_strict_none = have < MIN_HAVE
    if (strict is None) != want_strict_none:
        gate_viol.append((name, have, strict is not None))
    elif not want_strict_none and strict.get("thi_note6") != loose.get("thi_note6"):
        gate_viol.append((name, "两法结果不一致", strict.get("thi_note6"), loose.get("thi_note6")))
if tested:
    check(f"min_have 语义 ({tested} 项实测): 严格门槛=None ⟺ have<{MIN_HAVE}", gate_viol, [])
    check(f"主口径唯一 ({tested} 项实测): thi == thi_note6 且 level == level_note6", main_viol, [])
else:
    skip("min_have 语义", "库中无可算出注6结果的行")
    skip("主口径唯一", "库中无可算出注6结果的行")

# 不变式6: 库里 source 含 thi 的行, 存值必须 == 现算注6 (官方层里不许留第二个口径的化石)
thi_rows = [(n, l, g) for n, l, g, s in conn.execute(
    "select factor_name, level, grade, source from hazard_toxicity").fetchall()
    if "thi" in (s or "").lower()]
stale = [m for m in (thi_row_staleness(n, l, g) for n, l, g in thi_rows) if m]
if thi_rows:
    check(f"存值==现算注6 ({len(thi_rows)} 行: {', '.join(n for n, _l, _g in thi_rows)})", stale, [])
else:
    skip("存值==现算注6", "本环境 hazard_toxicity 没有 source 含 thi 的行 "
                         "(生产库确实可能没有) —— 该口径由上面「主口径唯一」那条兜住")
# 阳性对照: 判据必须先证明**能红**。取本环境里算得出来的第一个物质, 喂一个必然不等于
#   现算值的假存级 (四级里挑一个不同的) 给同一个比较函数 —— 它不点名, 就得红。
probe = None
for n in cands:
    rr = rate_substance(conn, n, sf._cas_from_oel(conn, n), min_have=0)
    if rr is not None:
        probe = (n, rr)
        break
if probe is None:
    skip("阳性对照", "本环境取样面里没有任何算得出来的物质 —— 不变式5/6 都是空转, 必须查数据层")
else:
    pname, pr = probe
    fake_grade = next(g for g in GRADES if g != pr["level"])
    check(f"阳性对照: 把 {pname} 的存值说成 {fake_grade} 必须被点名",
          bool(thi_row_staleness(pname, fake_grade, pr["grade"])), True)

conn.close()

print(f"\n通过 {n_pass} / 失败 {n_fail} / 跳过 {n_skip}")
sys.exit(1 if n_fail else 0)
