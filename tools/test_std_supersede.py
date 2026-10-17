#!/usr/bin/env python3
"""标准"被代替"双判据回归 (2026-10-14)

事故: GBZ230-2010 在 standard_db 里 state='实施中', 而 GBZ/T230-2025.replace_of
      明写「代替GBZ/T 230—2010」。旧实现两级护栏都只信 state=='废止' →
      引用作废的 2010 版时 不替换 + 不报警 (同时漏网)。
用法: .venv/bin/python tools/test_std_supersede.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.oel import connect_ro  # noqa: E402  (只读工厂: 库路径走 knowledge.paths 单一产地, 认 OHS_DATA_DIR)
conn = connect_ro()

from knowledge.standard_version import (fix_standard_refs, is_superseded,  # noqa: E402
                                  supersession_index)
from web.validators import _check_standards  # noqa: E402

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


print("### 判据层")
check("state 漏标的 2010 版被 replace_of 反查认出",
      bool(is_superseded(conn, "GBZ 230—2010")), True)
check("GBZ/T 230—2010 写法同样认出",
      (is_superseded(conn, "GBZ/T 230—2010") or {}).get("code"), "GBZ/T230-2025")
check("现行未被代替的号 → None (不误伤)",
      is_superseded(conn, "GBZ 2.1—2019"), None)
check("库里不存在的号 → None", is_superseded(conn, "GB 99999—1900"), None)
check("索引可复用 (同连接二次调用命中缓存)",
      supersession_index(conn)["ident"] == supersession_index(conn)["ident"], True)

print("\n### 替换层 (fix_standard_refs)")
t = "依据GBZ 230—2010 对毒物危害程度分级。"
out, ch = fix_standard_refs(t, conn)
check("漏网句被改写", ch != [], True)
check("改写目标为 2025 现行版", "2025" in out and "2010" not in out, True)
check("未涉及的句子原样返回",
      fix_standard_refs("按 GBZ 2.1—2019 的 PC-TWA 判定。", conn)[1], [])

print("\n### 校验层 (V-05)")
v = _check_standards(t)
hit = [i for i in v if i["rule_id"] == "V-05" and "230" in str(i.get("standard", ""))]
check("V-05 现在报 error", [i["level"] for i in hit], ["error"])
check("给出的现行版正确", hit[0]["current"] if hit else None, "GBZ/T230-2025")
check("报警语里说明了判据来源 (state 漏标)",
      "replace_of" in (hit[0]["note"] if hit else ""), True)

print("\n### 回归: 原本就标了废止的仍照常处理")
t2 = "按 GBZ/T 196—2007 执行。"
o2, c2 = fix_standard_refs(t2, conn)
check("state=废止 的替换未回退", c2 != [], True)
check("V-05 对废止版仍报", bool([i for i in _check_standards(t2) if i["level"] == "error"]), True)

print("\n### 全库普查口径 (与修复前对照)")
rows = conn.execute("SELECT code, state, replace_of FROM standard_db").fetchone()
bad_state = 0
for code, state, rep in conn.execute("SELECT code, state, replace_of FROM standard_db"):
    if rep and state in ("实施中", "实施"):
        if is_superseded(conn, code):
            bad_state += 1
check("被 state 漏标但已被双判据覆盖的旧版数 >= 1", bad_state >= 1, True)
print(f"      本轮双判据新覆盖: {bad_state} 条 (修复前这些一律漏网)")

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
