#!/usr/bin/env python3
"""门自身的正确性: Dataset 两个出口语义不同 + 记忆化 + 原地改写可见 (S1b′, 合成库)

为什么必须有这个文件 (2026-10-15 W1 取证): 内容级指纹 `export_fingerprint --check`
证明不了这道门在干活 —— 列级牙齿探针实测 `oel_limit.source_standard` (正是这道门要
改写的列) 打标记后 4 个项目指纹**一字不动**。所以"门到底纠不纠版"只能在合成库上验,
而且每条判据都要先证明**它能红** (§5 变异段)。

口径: 全程 /tmp 临时库, 不读 data/ohs.db → 容器里也能跑 (GUARD_TESTS)。
用法: .venv/bin/python tools/test_door_currentize_unit.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import knowledge.dataset as ds_mod                      # noqa: E402
import knowledge.standard_version as sv                 # noqa: E402
from knowledge.dataset import Dataset                   # noqa: E402

n_pass = n_fail = 0


def check(label, cond, want=None, got=None):
    global n_pass, n_fail
    if cond is True or (want is not None and cond == want):
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got if got is not None else cond}\n      期望: {want}")


def db_file(conn) -> str:
    return conn.execute("PRAGMA database_list").fetchone()[2]


def inplace_update(conn, sql: str) -> None:
    """另开一个连接改库 —— 模拟 tools/fix_official_std_codes.py --apply 那种"事后改判据"。"""
    ext = sqlite3.connect(db_file(conn))
    ext.execute(sql)
    ext.commit()
    ext.close()


# ── 合成库: 照真实 standard_db 的行形态搭 (含 2026-10-14 事故的 state 漏标形态) ──
STD = [
    # code, name, state, replace_of
    ("GBZ2.2-2007", "工作场所有害因素职业接触限值 第2部分:物理因素", "废止", ""),
    ("GBZ2.2-2019", "工作场所有害因素职业接触限值 第2部分:物理因素", "实施中", "GBZ 2.2—2007"),
    ("GBZ188-2014", "职业健康监护技术规范", "实施中", "GBZ 188—2007"),   # ← state 漏标, 只能靠 replace_of 反查
    ("GBZ188-2025", "职业健康监护技术规范", "实施中", "GBZ 188—2014"),
    ("GBZ1-2010", "工业企业设计卫生标准", "实施中", ""),
    # 下面两对用来测"行数不变的原地 UPDATE" (旧 COUNT 口径对它完全失明)
    ("GBZ9-2000", "原地改写用 旧版", "实施中", ""),
    ("GBZ9-2020", "原地改写用 现行", "实施中", ""),
    ("GBZ8-2001", "变异段用 旧版", "实施中", ""),
    ("GBZ8-2021", "变异段用 现行", "实施中", ""),
]
RULES = [
    ("高温中暑应急", "防暑降温", "高温作业场所应有防暑降温措施", "GBZ 2.2—2007", "表8"),
    ("职业健康监护", "监护", "按 GBZ 188-2014 执行监护", "GBZ 1-2010", "5.2"),
    ("一般场景", "卫生设计", "按 GBZ 1-2010 执行", "GBZ 1-2010", "5.2"),
]


def build(tmp: Path) -> None:
    w = sqlite3.connect(str(tmp))
    w.execute("CREATE TABLE standard_db (code TEXT, name TEXT, state TEXT, replace_of TEXT)")
    w.executemany("INSERT INTO standard_db VALUES (?,?,?,?)", STD)
    w.execute("CREATE TABLE emergency_rule (scenario TEXT, item TEXT, require TEXT, "
              "std_code TEXT, clause TEXT)")
    w.executemany("INSERT INTO emergency_rule VALUES (?,?,?,?,?)", RULES)
    w.execute("CREATE TABLE oel_limit (factor_name TEXT, oel_type TEXT, value TEXT, "
              "unit TEXT, source_standard TEXT)")
    w.executemany("INSERT INTO oel_limit VALUES (?,?,?,?,?)", [
        ("苯", "PC-TWA", "6", "mg/m3", "GBZ 2.1—2019"),
        ("噪声", "LEX8h", "85", "dB(A)", "GBZ 2.2—2007"),      # 废止号藏在限值表的依据列
    ])
    w.commit()
    w.close()


def ro(tmp: Path) -> sqlite3.Connection:
    """与 knowledge.oel.connect_ro() 同口径 (file: URI + mode=ro), 但指向临时库。"""
    return sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)


def trace(conn) -> list:
    seen: list[str] = []
    conn.set_trace_callback(lambda s: seen.append(s.strip()))
    return seen


def inplace_visibility(c, probe: str, target_code: str, replace_of: str) -> bool | None:
    """预热判据 → 他连接原地改一行 → **开一个新工作单元**再读, 看见了吗。
    返回 None = 改前就已经会纠 (前提不成立, 样本设计有问题)。"""
    if Dataset(c).rows("SELECT ? AS x", (probe,))[0][0] != probe:
        return None
    inplace_update(c, f"UPDATE standard_db SET replace_of='{replace_of}' WHERE code='{target_code}'")
    return Dataset(c).rows("SELECT ? AS x", (probe,))[0][0] != probe


print("### 1. 两个出口语义真的不同 (纠版 vs 原样)")
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td) / "mini.db"
    build(tmp)
    conn = ro(tmp)
    ds = Dataset(conn)
    got = ds.rows("SELECT std_code, require FROM emergency_rule")
    check("rows(): 依据列 'GBZ 2.2—2007' → 'GBZ 2.2—2019' (破折号形态沿用原文)",
          got[0][0] == "GBZ 2.2—2019", "GBZ 2.2—2019", got[0][0])
    check("rows(): 文本列里**嵌着**的废止号也纠 (require)",
          got[1][1] == "按 GBZ 188-2025 执行监护", "按 GBZ 188-2025 执行监护", got[1][1])
    check("rows(): 现行号原样不动",
          got[2][1] == "按 GBZ 1-2010 执行", "按 GBZ 1-2010 执行", got[2][1])
    raw_got = ds.raw("SELECT std_code, require FROM emergency_rule",
                     reason="合成库: 验证 raw 出口不纠版")
    check("raw(): 同一行原样出来 (证明两出口不等价)",
          raw_got[0][0] == "GBZ 2.2—2007" and raw_got[1][1] == "按 GBZ 188-2014 执行监护",
          "不纠版", raw_got[:2])
    check("one(): 与 rows() 同判据",
          ds.one("SELECT source_standard FROM oel_limit WHERE factor_name='噪声'")[0]
          == "GBZ 2.2—2019", "GBZ 2.2—2019", None)
    check("one(): 无行返回 None (与 fetchone 口径一致)",
          ds.one("SELECT std_code FROM emergency_rule WHERE clause=?", ("不存在",)) is None,
          True, None)
    check("fix(): 叙述文本与表格取数共用同一份判据",
          ds.fix("依据 GBZ 2.2—2007 与 GBZ 188-2014")[0] == "依据 GBZ 2.2—2019 与 GBZ 188-2025",
          "依据 GBZ 2.2—2019 与 GBZ 188-2025", None)
    check("非字符串列不参与 (NULL/数字原样)",
          ds.rows("SELECT NULL, 3.14, 42 FROM emergency_rule LIMIT 1")[0] == (None, 3.14, 42),
          (None, 3.14, 42), None)
    check("rows(): 返回 tuple 行 (迁移不改 fetchall 的形状)",
          all(type(r) is tuple for r in got), "tuple", [type(r).__name__ for r in got])

    print("\n### 2. raw 出口必须写理由 (运行时也拦, 不只靠守卫)")
    for fn, label in ((ds.raw, "raw"), (ds.raw_one, "raw_one")):
        for bad in ("", "   ", None):
            try:
                fn("SELECT 1", reason=bad)
                check(f"{label}(reason={bad!r}) 被拒", False, True, "放过了")
            except ValueError:
                check(f"{label}(reason={bad!r}) 被拒", True)

    print("\n### 3. 记忆化: 同一个号第二次判不再碰 standard_db")
    def memo_live(c: sqlite3.Connection) -> bool:
        sv.invalidate_caches()
        s = trace(c)
        d = Dataset(c)                        # 建实例才校验/建判据
        r1 = d.fix("GBZ 2.2—2007")
        built = [x for x in s if "standard_db" in x.lower()]
        s.clear()
        r2 = d.fix("GBZ 2.2—2007")
        return bool(built) and s == [] and r1 == r2

    sv.invalidate_caches()
    seen = trace(conn)
    ds3 = Dataset(conn)
    first = ds3.fix("GBZ 2.2—2007")
    n1 = [s for s in seen if "standard_db" in s.lower()]
    seen.clear()
    second = ds3.fix("GBZ 2.2—2007")
    check("建实例时建判据要读 standard_db", len(n1) >= 1, ">=1", n1)
    check("两次结果一致", first == second, first, second)
    check("fix() 本身不发任何 SQL (memo 生效)", seen == [], [], seen)

    print("\n### 3b. 校验粒度 = 实例 (生命周期口径: 一个工作单元一个 Dataset)")
    sv.invalidate_caches()
    seen = trace(conn)
    ds2 = Dataset(conn)
    for _ in range(50):
        ds2.rows("SELECT 1")
    n_dv = sum(1 for s in seen if s.upper().startswith("PRAGMA DATA_VERSION"))
    check("同一实例读 50 次只校验 1 次新鲜度 (导出每格都问一遍会把 SQL 抬到十万级)",
          n_dv, 1)
    Dataset(conn).rows("SELECT 1")             # 新工作单元 → 再校验一次
    n_dv2 = sum(1 for s in seen if s.upper().startswith("PRAGMA DATA_VERSION"))
    check("换新实例即重新校验 (判据陈旧不会跨工作单元)", n_dv2, 2)

    print("\n### 4. 他连接**原地 UPDATE (行数不变)** 后必须看到新判定 (W5)")
    check("改前 'GBZ 9—2000' 不纠 (库里既没判废止也没代替关系)",
          ds.rows("SELECT 'GBZ 9—2000' AS x")[0][0] == "GBZ 9—2000", "GBZ 9—2000", None)
    inplace_update(conn, "UPDATE standard_db SET replace_of='GBZ 9—2000' WHERE code='GBZ9-2020'")
    after = Dataset(conn).rows("SELECT 'GBZ 9—2000' AS x")[0][0]
    check("改后同一个号被纠成现行版 (COUNT 口径看不见这件事)",
          after == "GBZ 9—2020", "GBZ 9—2020", after)
    check("同一实例内看不到 (校验只在建实例时做 —— 生命周期是约定, 不是可选注释)",
          ds.rows("SELECT 'GBZ 9—2000' AS x")[0][0] == "GBZ 9—2000", "GBZ 9—2000", None)
    check("行数确实没变 (证明确实不是靠 COUNT 抓到的)",
          conn.execute("SELECT COUNT(*) FROM standard_db").fetchone()[0] == len(STD),
          len(STD), None)

    print("\n### 5. 变异验证: 上面每条判据都必须能红")
    orig_currentizer = ds_mod.currentizer
    orig_check_reason = Dataset._check_reason
    try:
        # 变异 A: 每次新建判据 (memo 不跨调用) → 第 3 组判据必须转红
        ds_mod.currentizer = lambda c: sv.RefFixer(c, 0)
        check("变异A: 桩掉缓存后 记忆化判据转红", memo_live(conn), False, None)
        # 变异 B: 缓存只比连接、忽略 data_version → 第 4 组判据必须转红
        slot: dict[int, object] = {}

        def dv_blind(c):
            k = id(c)
            if k not in slot:
                slot[k] = sv.RefFixer(c, c.execute("PRAGMA data_version").fetchone()[0])
            return slot[k]

        ds_mod.currentizer = dv_blind
        vis = inplace_visibility(conn, "GBZ 8—2001", "GBZ8-2021", "GBZ 8—2001")
        check("变异B: 忽略 data_version 后 原地改写看不见 (判据因此能红)",
              vis, False, None)
        # 变异 C: 放开 reason 校验 → 第 2 组必须拦不住
        Dataset._check_reason = staticmethod(lambda *_a, **_k: None)
        try:
            ds.raw("SELECT 1", reason="")
            check("变异C: 放开校验后空 reason 确实被放过 (证明第 2 组测的是这道校验)",
                  True, True, None)
        except ValueError:
            check("变异C: 放开校验后空 reason 仍被拦", False, True, "校验没被桩掉")
    finally:
        ds_mod.currentizer = orig_currentizer
        Dataset._check_reason = orig_check_reason
        sv.invalidate_caches()

    print("\n### 6. 阳性对照: 变异段留下的那处原地改写, 真门必须看得见")
    check("恢复真门后 'GBZ 8—2001' → 'GBZ 8—2021' (与 §4 同机制, 第二对样本)",
          Dataset(conn).rows("SELECT 'GBZ 8—2001' AS x")[0][0] == "GBZ 8—2021", "GBZ 8—2021", None)

    print("\n### 7. 判据本身仍是那一把尺子")
    f = sv.currentizer(conn)

    def slow(t: str) -> str:
        """不带快路径的同款改写 (逐格跑 STD_REF_PAT.sub) —— 用来证明快路径没跳过任何命中"""
        def _s(m):
            nc = f.replacement(sv.ref_code(m), int(m.group(4)))
            return (f._rewrite(m.group(0), nc) if nc else None) or m.group(0)
        return sv.STD_REF_PAT.sub(_s, t)

    samples = ["苯", "噪声 85 dB(A)", "PC-TWA=6 mg/m3", "2019 年修订", "GBZ 2.2—2007",
               "GBZ 188-2014 版", "2007 年版 GBZ 2.2", "GB 30871—2022 8.4.2a", "WS 1-1996"]
    check("快路径 (不含 4 位年份直接返回) 与逐格 sub 同结果",
          all(f.fix(s)[0] == slow(s) for s in samples),
          [(s, f.fix(s)[0], slow(s)) for s in samples if f.fix(s)[0] != slow(s)][:2])
    check("样本里既有走快路径的也有走 sub 的 (否则上一条是空转)",
          any(not sv._RE_YEAR.search(s) for s in samples) and any(sv._RE_YEAR.search(s) for s in samples),
          True, [bool(sv._RE_YEAR.search(s)) for s in samples])
    check("双判据: state 漏标的 GBZ188-2014 仍解析到 2025 版",
          (f.resolve_current("GBZ188-2014") or {}).get("code") == "GBZ188-2025",
          "GBZ188-2025", None)
    check("库里判废止的 GBZ2.2-2007 不得从纠版出口活着出来",
          f.fix("GBZ 2.2—2007")[0] != "GBZ 2.2—2007", "已改写", None)
    check("现行版号不误改",
          f.fix("GBZ 1-2010")[0] == "GBZ 1-2010", "GBZ 1-2010", None)
    check("standard_db 快照按连接缓存 (行数=建库数)",
          len(f.rows) == len(STD), len(STD), len(f.rows))
    check("supersession_index 仍带 ident (test_std_supersede 依赖)",
          isinstance(sv.supersession_index(conn)["ident"], str)
          and sv.supersession_index(conn)["ident"] == sv.supersession_index(conn)["ident"],
          True, sv.supersession_index(conn))
    conn.close()

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
