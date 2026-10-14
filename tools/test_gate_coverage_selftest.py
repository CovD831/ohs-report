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
check("gbz1_rule 能整名匹配", gc.TBL_RE.findall("SELECT a FROM gbz1_rule WHERE x") , ["gbz1_rule"])
check("ppe_work_matrix 能匹配", gc.TBL_RE.findall("SELECT a FROM ppe_work_matrix"), ["ppe_work_matrix"])

print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 ===")
sys.exit(1 if n_fail else 0)
