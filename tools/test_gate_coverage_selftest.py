#!/usr/bin/env python3
"""S1.0 分级器自证 (数据无关, 可进容器守卫)

gate_coverage 用 AST 猜"这个读点会不会印到纸上"。猜错的方向很关键:
把 branch 站点评成 printed = 给"零 diff 即安全"发假证明; 反过来只是多要一份取证。
本测试用**合成源码片段** (不读库、不导报告) 钉住判据, 并断言失误只朝保守方向。

用法: .venv/bin/python tools/test_gate_coverage_selftest.py   (退出码 0 = 全绿)
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import tools.gate_coverage as gc  # noqa: E402

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


def classify_snippet(src: str) -> tuple[str, str]:
    """对片段里**第一个**带官方表名的 execute/rows_of 调用做分级 (与被测工具同一套函数)"""
    tree = ast.parse(src)
    pmap, ftab, cctx = gc._parent_map(tree), gc._func_table(tree), gc._func_ctx(tree)
    fmap = gc._enclosing_func(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or gc._fn_name(node) not in gc.EXEC_FNS:
            continue
        sql = "".join(gc._lit(a) for a in node.args) or gc._lit(node.args[0] if node.args else None)
        if not sql or not gc.TBL_RE.search(sql):
            continue
        fname = fmap.get(getattr(node, "lineno", 0), "")
        bis, fis = cctx.get(fname, (set(), set()))
        return gc.classify(sql, fname, node, pmap, gc.find_assigned_to(node, pmap), bis, fis, ftab.get(fname))
    return ("no-site", "")


print("=== 1. 判断型 → 必须 branch (绝不能是 printed) ===")
check("COUNT 聚合 + if",
      classify_snippet('def f(conn):\n    if conn.execute("SELECT COUNT(*) FROM bio_limit WHERE x=?", ()).fetchone()[0]:\n        return 1\n')[0],
      "branch")
check("LIMIT 1 结果只用于 if",
      classify_snippet('def f(conn):\n    r = conn.execute("SELECT v FROM oel_limit LIMIT 1").fetchone()\n    if r:\n        return 1\n')[0],
      "branch")
check("布尔表达式内 (三元 test)",
      classify_snippet('def f(conn):\n    ok = conn.execute("SELECT 1 FROM oel_limit LIMIT 1").fetchone() is not None\n    return ok\n')[0],
      "branch")

print("\n=== 2. 进正文型 → printed ===")
check("fetchall 直接 return",
      classify_snippet('def f(conn):\n    rows = conn.execute("SELECT a,b FROM oel_limit").fetchall()\n    return rows\n')[0],
      "printed")
check("写进 out.append 再 return",
      classify_snippet('def f(conn):\n    out = []\n    row = conn.execute("SELECT v FROM oel_limit LIMIT 1").fetchone()\n    out.append(row[0])\n    return out\n')[0],
      "printed")
check("for 逐行消费",
      classify_snippet('def fill_section(conn):\n    for r in conn.execute("SELECT clause, rule FROM gbz1_rule WHERE theme=?"):\n        print(r)\n')[0],
      "printed")
check("进 f-string",
      classify_snippet('def f(conn):\n    v = conn.execute("SELECT value FROM oel_limit LIMIT 1").fetchone()[0]\n    return f"限值为 {v}"\n')[0],
      "printed")

print("\n=== 3. prompt 侧 ===")
check("落到 *_build_prompt 函数",
      classify_snippet('def _build_prompt(conn):\n    rows = conn.execute("SELECT rule FROM gbz1_rule").fetchall()\n    return str(rows)\n')[0],
      "prompt")

print("\n=== 4. 拿不准 → unknown (不许硬猜成 printed) ===")
check("赋值后整个函数没用到",
      classify_snippet('def f(conn):\n    z = conn.execute("SELECT x FROM oel_limit").fetchone()\n    return 1\n')[0],
      "unknown")
check("裸调用不接值",
      classify_snippet('def f(conn):\n    conn.execute("SELECT x FROM oel_limit")\n    return 1\n')[0],
      "unknown")

print("\n=== 5. 失误方向纪律: 判断型站点绝不能被算成受保护 ===")
risky = ['def f(conn):\n    n = conn.execute("SELECT COUNT(*) FROM ghs_class").fetchone()[0]\n    return n > 0\n',
         'def f(conn):\n    hit = conn.execute("SELECT 1 FROM standard_db WHERE code=?", (c,)).fetchone()\n    if hit:\n        log()\n']
for i, src in enumerate(risky):
    check(f"危险样本#{i} 不是 printed", classify_snippet(src)[0] != "printed", True)

print("\n=== 6. 表名正则含数字 (gbz1_rule 曾被 [a-z_]+ 截成 gbz) ===")
check("gbz1_rule 能整名匹配", gc.TBL_RE.findall("SELECT a FROM gbz1_rule WHERE x"), ["gbz1_rule"])
check("ppe_work_matrix 能匹配", gc.TBL_RE.findall("SELECT a FROM ppe_work_matrix"), ["ppe_work_matrix"])


# ================= 7-10. 身份键 (S1c″-D5′ 前半段) =================
# 判据必须先证明能红: 三条变异 + 阳性对照 + 兼容路径, 每条都拿合成源码在内存里跑,
# 不读库、不导报告、不碰 tools/golden 的写路径。
import json
from collections import Counter

TBLS = {"oel_limit", "management_rule", "gbz1_rule", "standard_db"}


def keyed(src: str) -> list[dict]:
    """合成源码 → 站点表 (走被测工具同一套抽取 + 键推导)"""
    return gc.assign_keys(gc._scan_tree(ast.parse(src), "m/fx.py", TBLS))


BASE_SRC = (
    'def fill_a(conn):\n'
    '    rows = conn.execute("SELECT cas FROM oel_limit").fetchall()\n'
    '    out = [rows]\n'
    '    return out\n'
    'def fill_b(conn):\n'
    '    for r in conn.execute("SELECT clause FROM gbz1_rule WHERE x=?"):\n'
    '        print(r)\n'
    '    return []\n')

print("\n=== 7. 变异①: 某站点整体上移/下移 5 行 → 站点键集合必须**不变** (排版免疫=本次改造目的) ===")
by_fn = lambda sites, fn: next(s for s in sites if s["fn"] == fn)
b0 = keyed(BASE_SRC)
moved_src = BASE_SRC.replace("def fill_b", "# pad1\n# pad2\n# pad3\n# pad4\n# pad5\ndef fill_b")
b1 = keyed(moved_src)
check("变异先证明落了: fill_b 站点行号确实 +5",
      (by_fn(b1, "fill_b")["line"] - by_fn(b0, "fill_b")["line"], by_fn(b0, "fill_b")["line"]), (5, 6))
check("变异①: 行号漂了但键集合一条不差", sorted(s["key"] for s in b1), sorted(s["key"] for s in b0))
d1 = gc.diff_keys(b0, b1)
check("变异①: diff_keys 对该漂移报 0 消失 0 新增 (旧键 (file,line) 会报 1 条假变化)",
      (sum(d1["gone"].values()), sum(d1["added"].values())), (0, 0))
check("变异①: 但排版移动被如实登记为 line_moves (键同、行号集不同), 恰好 1 个键",
      len(d1["line_moves"]), 1)

print("\n=== 8. 变异②: 给同一张表真加一个读点 → 必须点名 (同形重数 dup 不许把它吞掉) ===")
# ⚠ 合成 SQL 一律经**变量**传给 replace(): 若把含 SELECT 的字面量直接写进调用实参,
#   static_sites 的准入判据 ("实参里有静态 SELECT") 会把本测试文件自己的样本串
#   扫成真站点 (2026-10-17 --iddiff 实测 +3 污染 240 计数 —— 判据太有效也算能红的证据)。
TWIN_OLD = "    out = [rows]"
TWIN_NEW = ('    more = conn.execute("SELECT cas FROM oel_limit").fetchall()\n'
            "    out = [rows, more]")
twin_src = BASE_SRC.replace(TWIN_OLD, TWIN_NEW)
b2 = keyed(twin_src)
d2 = gc.diff_keys(b0, b2)
check("变异②: 恰好新增 1 条键、0 条消失", (sum(d2["added"].values()), sum(d2["gone"].values())), (1, 0))
_ak = sorted(d2["added"])[0].split("|")
check("变异②: 新增键点名到位 = fill_a/oel_limit/cas 的同形第 2 个 (dup=1)",
      (_ak[1], _ak[2], _ak[3], _ak[5]), ("fill_a", "oel_limit", "cas", "1"))
check("变异②: 与既有键只差 dup 分量 (语句指纹/列集原样 → 键只看内容也能认出新第 N 个)",
      _ak[:5], by_fn(b0, "fill_a")["key"].split("|")[:5])

print("\n=== 9. 变异③: 站点表名/列名改掉 → 必须点名, 不许被任何 ±1 容差吞 ===")
SEL_OLD = "SELECT cas FROM oel_limit"
SEL_COL = "SELECT value FROM oel_limit"
SEL_TBL = "SELECT cas FROM standard_db"
col_src = BASE_SRC.replace(SEL_OLD, SEL_COL)
tbl_src = BASE_SRC.replace(SEL_OLD, SEL_TBL)
for label, src, want_field in (("列名 cas→value", col_src, "cols"), ("表名 oel_limit→standard_db", tbl_src, "tables")):
    d3 = gc.diff_keys(b0, keyed(src))
    check(f"变异③({label}): gone=1 + added=1 (键整体换, 没有容差可言)",
          (sum(d3["gone"].values()), sum(d3["added"].values())), (1, 1))
    check(f"变异③({label}): 配对成'改造站点'且 changed 分量点名 {want_field}",
          d3["modified"] and want_field in d3["modified"][0]["changed"], True)
    check(f"变异③({label}): 点名到的正是 fill_a 那一条",
          bool(d3["modified"]) and d3["modified"][0]["old"]["fn"] == "fill_a", True)

print("\n=== 10. 兼容路径 + 阳性对照: 旧基线 240 站点能推导出新键, 四项计数与旧键一致 ===")
gold = ROOT / "tools" / "golden" / "gate_coverage.json"
check("旧基线记录存在 (兼容对账对象)", gold.exists(), True)
if gold.exists():
    data = json.loads(gold.read_text(encoding="utf-8"))
    legacy = [dict(r) for r in data["sites"]]
    ks = [s["key"] for s in gc.assign_keys(legacy)]
    check("旧键形状记录 (无 cols/shape/key 字段) 全部可推导新键", all("|" in k for k in ks), True)
    check("推导无碰撞: 240 站点 → 240 个互异键 (同质站点靠 dup 区分开)", len(ks), len(set(ks)))
    census = gc.four_counts(legacy)
    st = data["stats"]
    want = {"total_sites": st["total_sites"], "printed": st["by_kind"]["printed"],
            "gate_visible": st["gate_visible"], "protected_now": st["protected_now"]}
    for k, v in want.items():
        check(f"阳性对照: 新键口径 {k} = 基线 stats ({v})", census[k], v)
    again = [s["key"] for s in gc.assign_keys([dict(r) for r in legacy])]
    check("键推导可重入 (已物化 cols/shape 再跑一遍不变)", again, ks)
    dup_max = max(s["dup"] for s in legacy)
    groups = Counter("|".join(s["key"].split("|")[:5]) for s in legacy)
    n_dup_groups = sum(1 for g, c in groups.items() if c > 1)
    print(f"  (基线里同形重组: {n_dup_groups} 组共 {sum(groups.values()) - len(groups)} 条额外 dup, 最大 dup={dup_max})")

print("\n=== 11. overrides 重对表: 僵尸必须点名, 旧形/新形两种键都命中 ===")
import tempfile
tmp = Path(tempfile.mkdtemp())


def apply_ov(mapping: dict, sites: list[dict]):
    p = tmp / "ov.json"
    p.write_text(json.dumps(mapping), encoding="utf-8")
    return gc.apply_overrides([dict(s) for s in sites], p)


site_x = keyed(BASE_SRC)
fa, fb = by_fn(site_x, "fill_a"), by_fn(site_x, "fill_b")
out_s, zomb = apply_ov({"m/fx.py:9999": {"kind": "branch", "why": "僵尸样本"}}, site_x)
check("命中不到站点的登记 → 点名僵尸 override (不许静默)", zomb, ["m/fx.py:9999"])
out_s2, zomb2 = apply_ov({f"{fa['file']}:{fa['line']}": {"kind": "branch", "why": "旧形兼容"}}, site_x)
hit2 = next(s for s in out_s2 if s.get("override_ref"))
check("旧形 file:line 键仍命中 (迁移期兼容)", (hit2["kind"], zomb2), ("branch", []))
check("旧形命中时顺带报出现在的新键 (给迁移对表用)", hit2.get("override_new_key"), fa["key"])
out_s3, zomb3 = apply_ov({fb["key"]: {"kind": "prompt", "why": "新形键"}}, site_x)
hit3 = next(s for s in out_s3 if s.get("override_ref"))
check("新形键直接命中且按内容不随行号掉 (键里没有行号)", (hit3["kind"], zomb3), ("prompt", []))
# 僵尸判据必须能红: 把站点搬走 (行号变了) 而登记还是旧形 file:line → 必须报僵尸
moved_a_src = BASE_SRC.replace("def fill_a(conn):", "def fill_a(conn):\n    # pad\n    # pad\n    # pad")
check("旧形登记因代码搬家失去依据 → 点名 (同 test_dataset_door 的登记腐烂纪律)",
      apply_ov({f"{fa['file']}:{fa['line']}": {"kind": "branch", "why": "该腐烂的"}}, keyed(moved_a_src))[1],
      [f"{fa['file']}:{fa['line']}"])
check("但新形登记同一场景不腐烂 (行号搬家键不变)",
      apply_ov({fa["key"]: {"kind": "branch", "why": "稳"}}, keyed(moved_a_src))[1], [])

print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 ===")
sys.exit(1 if n_fail else 0)
