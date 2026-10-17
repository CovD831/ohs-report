#!/usr/bin/env python3
"""S1a′/S1c′ 守卫: 路径、裸连与写侧工厂的收口 (2026-10-15)

管四件事, 全部是**静态源码检查** —— 不读 data/ohs.db, 不导报告:
  A. `sqlite3.connect(` 在 web/ 与 knowledge/ 里只许出现在
     ① 工厂 (knowledge/oel.py) ② `if __name__ == "__main__":` 开发块 ③ 显式豁免
  B. 库路径字面量 (任何含 "ohs.db" 的字符串, 含 `parent/"data"/"ohs.db"` 这种拼法)
     只许出现在 knowledge/paths.py
     (loader 文件头注释里的 "输出: data/ohs.db → 表名" 不算 —— 那是说明不是代码)
  C. 官方表标准号列的读点收口在 tools/test_std_currentize.py (W4 改造后的 AST 版)
  D. (S1c′ D1) 生产函数里不得调用**写侧工厂** `knowledge.oel.connect()` ——
     它会 mkdir + executescript(DDL), 所以它是"一次写"而不是"一个连接";
     只读取数一律 connect_ro()。写侧入口必须逐个登记, 且登记项过期要被抓 (T7)。

⚠ A/B 两条**必须按 AST 判**, 不能按行文本匹配: 原实现按字面 "sqlite3.connect" 找,
   `import sqlite3 as _sq` + `_sq.connect(str(db))` 整个绕过 —— web/word_export.py:290-297
   就是这样自己拼路径、自己开读写连接跑了 8 个月 (S1b′ 收口时才发现并补掉)。

⚠ 本文件自身的谓词也要被测 (v1 计划踩过): "是否在 __main__ 块内" 用 AST 独立实现,
   并有 T1 组用例证明它判得对 —— 按文件豁免会整批放过生产函数里的裸连
   (web/table_skeleton.py:369 就是活例子: 生产函数里自开连接还被 except 静默吞掉)。

用法: .venv/bin/python tools/test_dataset_door.py    (退出码 0 = 全绿)
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

FACTORIES = {"knowledge/oel.py", "knowledge/paths.py"}
# 允许开**可写**连接的文件 (各自一行理由)。判据不是"哪个目录", 而是"这个连接要不要写":
# 只读连接走 knowledge/oel.connect_ro(), 写连接必须是这里的登记项 —— 新增项必须在评审里说明,
# 空口"先放着"就是 v1 计划被推翻的那种按文件豁免。
WRITE_REGISTERED = {
    "web/auth.py": "users/audit_log 运行时表 (与官方层无关)",
    "web/tasks.py": "task_job 任务队列",
    "web/projects_db.py": "project 表写侧 (取数已改走 connect_ro, S1c′ D1)",
    "web/migrate_states.py": "一次性章节状态迁移脚本",
    "web/external_tables.py": "外部表采集写入",
    "knowledge/fix_mapping.py": "一次性映射修复脚本",
    "knowledge/seed_aux_unit.py": "官方层 seed 写入侧",
    "knowledge/standard_db_loader.py": "官方层 loader 写入侧",
}
# S1c′ D1: **写侧工厂** `knowledge.oel.connect()` 的登记项 (判据同上, 按"要不要写"判)。
# oel.connect() 每次调用都 mkdir + executescript(DDL), 所以它不是"一个连接", 而是"一次写";
# 读路径一旦用它, "只读验收链"就是空话 —— 本轮把生产读点全部换成 connect_ro() 后,
# 生产函数里剩下的可写工厂调用必须逐个有名有姓。`__main__` 开发块 (loader/CLI 的写侧入口)
# 不在此列, 由 tools/db_writers.py 单独报数。
WRITE_FACTORY_REGISTERED = {
    "web/app.py": "保存项目数据 / 删项目 (UPDATE·DELETE project; 读点已走 connect_ro)",
}
# 注: loader / 解析器的写侧全部住在 `def main()` (只有跑脚本才执行) 里, 由 T7 的
# CLI 闭包判据放行, **不需要**登记 —— 登记它们等于写一条没有依据的豁免。
# 这条不是一次手调: 本轮先登记了 knowledge/ppe_matrix_loader.py 与
# knowledge/protection_rule_loader.py, 判据的"过期登记项"检查当场上报这两项无依据,
# 于是摘掉。豁免列表只增不减才是真正要防的腐烂。
SCAN_DIRS = ("web", "knowledge")
n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


def main_block_ranges(tree: ast.AST) -> list[tuple[int, int]]:
    """返回所有 `if __name__ == "__main__":` 的行区间。
    必须认全 Compare(Name('__name__'), Eq, Constant('__main__')), 不能靠字符串匹配 ——
    也不能像我第一版那样把 `isinstance(n, ast.If) and test=='__main__' or Compare(...)`
    写混优先级, 那会得到"生产 138 / __main__ 0"的假结论。"""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.If) or not isinstance(n.test, ast.Compare):
            continue
        cmp_ = n.test
        left, ops, comps = cmp_.left, cmp_.ops, cmp_.comparators
        if not (isinstance(left, ast.Name) and left.id == "__name__"
                and len(ops) == 1 and isinstance(ops[0], ast.Eq)
                and len(comps) == 1 and isinstance(comps[0], ast.Constant)
                and comps[0].value == "__main__"):
            continue
        end = max((getattr(x, "end_lineno", 0) or getattr(x, "lineno", 0)) for x in ast.walk(n))
        out.append((n.lineno, max(end, n.lineno)))
    return out


def in_ranges(lineno: int, ranges: list[tuple[int, int]]) -> bool:
    return any(a <= lineno <= b for a, b in ranges)


def docstring_lines(tree: ast.AST) -> set[int]:
    """模块/函数 docstring 占的行 —— 里面的 "data/ohs.db" 是说明, 不该算代码"""
    lines: set[int] = set()
    nodes = [tree] + [n for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    for node in nodes:
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def sqlite_aliases(tree: ast.AST) -> tuple[set, set]:
    """返回 (sqlite3 模块别名集, 直连 connect 的导入名集) —— 认别名是这版的关键。"""
    mods, direct = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "sqlite3" or a.name.startswith("sqlite3."):
                    mods.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and (n.module or "") == "sqlite3":
            for a in n.names:
                if a.name == "connect":
                    direct.add(a.asname or a.name)
    return mods, direct


def sqlite_connect_calls(tree: ast.AST) -> list[tuple[int, int]]:
    """所有 sqlite3 连接的调用行区间 (sqlite3.connect / _sq.connect / 直连 connect)。"""
    mods, direct = sqlite_aliases(tree)
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        hit = ((isinstance(f, ast.Attribute) and f.attr == "connect"
                and isinstance(f.value, ast.Name) and f.value.id in mods)
               or (isinstance(f, ast.Name) and f.id in direct))
        if hit:
            out.append((n.lineno, n.end_lineno or n.lineno))
    return out


def db_path_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """含 "ohs.db" 的字符串字面量 (拆段拼写的 `parent / "data" / "ohs.db"` 也算)。"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "ohs.db" in n.value:
            out.append((n.lineno, n.value))
    return out


def oel_aliases(tree: ast.AST) -> tuple[set, set]:
    """(oel 模块别名集, `from knowledge.oel import connect` 的导入名集)。
    写侧工厂的两种写法都要认: `oel.connect()` 与 `connect()` / `oc()` 别名。"""
    mods, direct = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "knowledge.oel" or a.name.endswith(".oel"):
                    mods.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and (n.module or "").endswith(".oel"):
            for a in n.names:
                if a.name == "connect":
                    direct.add(a.asname or a.name)
    return mods, direct


def write_factory_calls(tree: ast.AST) -> list[int]:
    """`knowledge.oel.connect()` (写侧工厂, 每次调用都 mkdir + executescript(DDL)) 的调用行。
    只认 oel 的 connect, 不认 sqlite3.connect —— 后者由 scan() 管。"""
    mods, direct = oel_aliases(tree)
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        hit = ((isinstance(f, ast.Attribute) and f.attr == "connect"
                and isinstance(f.value, ast.Name) and f.value.id in mods)
               or (isinstance(f, ast.Name) and f.id in direct))
        if hit:
            out.append(n.lineno)
    return out


def dev_closure(tree: ast.AST) -> set[str]:
    """本模块里**只有跑脚本时才会执行**的顶层函数集合 = `__main__` 块直接调用的函数
    在本模块内的传递闭包。
    为什么要闭包而不是"直接调用的那几个名字": loader 的真实写法是
    `main() → fix_checkpoints() → connect()`, 只豁免 main 会把 fix_checkpoints 里
    的写算成"生产写侧", 逼出一条没意义的登记项 (本轮就被这条抓到过一次)。
    """
    rs = main_block_ranges(tree)
    tops = {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    seeds = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and any(a <= n.lineno <= b for a, b in rs):
            seeds.add(n.func.id)
    out, stack = set(), list(seeds)
    while stack:
        f = stack.pop()
        if f in out or f not in tops:
            continue
        out.add(f)
        for c in ast.walk(tops[f]):
            if isinstance(c, ast.Call):
                fn = c.func
                nm = fn.id if isinstance(fn, ast.Name) else (
                    fn.attr if isinstance(fn, ast.Attribute) else None)
                if nm in tops and nm not in out:
                    stack.append(nm)
    return out


def callee_map(tree: ast.AST) -> dict[str, set[str]]:
    """顶层函数 → 它调用的顶层函数名 (只算模块内的边, 跨模块调用由 imported 表负责)。"""
    tops = {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    out = {}
    for name, node in tops.items():
        called = set()
        for c in ast.walk(node):
            if isinstance(c, ast.Call):
                fn = c.func
                nm = fn.id if isinstance(fn, ast.Name) else (
                    fn.attr if isinstance(fn, ast.Attribute) else None)
                if nm in tops and nm != name:
                    called.add(nm)
        out[name] = called
    return out


def cli_only_funcs(tree: ast.AST, imported_names: set[str]) -> set[str]:
    """**只有跑脚本时才会执行**的顶层函数: 在 `__main__` 的传递闭包里, 且
      · 本模块里没有"闭包外的函数"调用它 (否则它同时是生产路径),
      · 也没有被别的模块 import (否则同理)。
    为什么逐函数判而不是整模块判: `project_assess.py` 的 main() 会调 assess_project(),
    后者被 web/ 到处 import —— 按模块作废会把 main() 也算成生产写侧 (本轮实测错过一次)。
    真正的问题只是"含 connect() 的那个函数会不会在生产请求路径里被执行"。
    """
    closure = dev_closure(tree)
    edges = callee_map(tree)
    out = set()
    for f in closure:
        if f in imported_names:
            continue
        if any(f in callees for g, callees in edges.items() if g not in closure):
            continue
        out.add(f)
    return out


def enclosing_top_func(tree: ast.AST, lineno: int):
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and n.lineno <= lineno <= (n.end_lineno or n.lineno):
            return n.name
    return None


def scan_write_factory(base: Path = ROOT) -> tuple[list[str], list[str], list[str], int]:
    """返回 (未登记的写侧调用, 被前提推翻的 CLI 调用, 过期登记项, 扫描文件数)。

    CLI 闭包的豁免**带前提核验**: 全仓 (web/knowledge/tools) 里只要有人
    `from <该模块> import <闭包里的函数名>`, 整个豁免就作废 —— 否则"按函数名豁免"
    会变成新的按文件豁免 (v1 计划被推翻的那种), 而 route 里调 CLI 函数完全可能发生。
    """
    py_files = [f for d in ("web", "knowledge", "tools") for f in sorted((base / d).rglob("*.py"))]
    imported: dict[str, set[str]] = {}       # 模块末段 -> 被别处 import 进来的名字
    for f in py_files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                imported.setdefault(n.module.split(".")[-1], set()).update(
                    a.name for a in n.names)
    bad, dev_break, live, n_files = [], [], set(), 0
    for d in SCAN_DIRS:
        for f in sorted((base / d).rglob("*.py")):
            rel = str(f.relative_to(base))
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            n_files += 1
            if rel == "knowledge/oel.py":        # 工厂自身
                continue
            ranges = main_block_ranges(tree)
            stem = f.stem
            imported_names = imported.get(stem, set())
            cli_only = cli_only_funcs(tree, imported_names)
            leaked = sorted(dev_closure(tree) & imported_names)
            for lo in write_factory_calls(tree):
                if in_ranges(lo, ranges):        # 直接写在 __main__ 块里
                    continue
                fn = enclosing_top_func(tree, lo)
                if fn in cli_only:               # 只有跑脚本时才执行 → CLI 写侧入口
                    continue
                if leaked:
                    dev_break.append(f"{rel}:{lo} CLI 闭包里的 {leaked} 被别处 import → 豁免作废")
                if rel in WRITE_FACTORY_REGISTERED:
                    live.add(rel)
                    continue
                bad.append(f"{rel}:{lo}")
    stale = [rel for rel in WRITE_FACTORY_REGISTERED
             if rel not in live and (base / rel).exists()]
    return bad, dev_break, stale, n_files


def scan(base: Path = ROOT) -> tuple[list[str], list[str], list[str], int]:
    bad_conn, bad_path, stale_reg, n_files = [], [], [], 0
    live_conn_files = set()
    for d in SCAN_DIRS:
        for f in sorted((base / d).rglob("*.py")):
            rel = str(f.relative_to(base))
            try:
                text = f.read_text(encoding="utf-8")
                tree = ast.parse(text)
            except (SyntaxError, UnicodeDecodeError):
                continue
            n_files += 1
            src = text.splitlines()
            ranges = main_block_ranges(tree)
            docs = docstring_lines(tree)
            for lo, _hi in sqlite_connect_calls(tree):
                if in_ranges(lo, ranges):
                    continue
                if rel in FACTORIES or rel in WRITE_REGISTERED:
                    live_conn_files.add(rel)
                    continue
                bad_conn.append(f"{rel}:{lo}  {(src[lo - 1] if lo <= len(src) else '').strip()[:70]}")
            if rel == "knowledge/paths.py":
                continue
            for lo, val in db_path_literals(tree):
                if lo in docs:
                    continue
                bad_path.append(f"{rel}:{lo}  {val[:60]}")
    # 豁免会腐烂: 登记了但已经没有裸连的文件必须摘掉, 否则列表只会越来越长
    for rel in WRITE_REGISTERED:
        if rel not in live_conn_files and (base / rel).exists():
            stale_reg.append(rel)
    return bad_conn, bad_path, stale_reg, n_files


print("=== T1. 谓词本身: 认别名、认直连、认 __main__ 豁免 ===")
PROBE = '''
import sqlite3
import sqlite3 as _sq
from sqlite3 import connect as cc
def production():
    c = sqlite3.connect("data/ohs.db")     # 1) 常规裸连 → 必须被抓
    d = _sq.connect("x.db")                # 2) 别名写法 (word_export 原样) → 必须被抓
    e = cc("y.db")                         # 3) 直连导入 → 必须被抓
    f = "ohs.db" in p.name                 # 4) 路径字面量 → 必须被抓
if __name__ == "__main__":
    g = _sq.connect("data/ohs.db")         # 5) 开发块 → 豁免
'''
tree1 = ast.parse(PROBE)
rs = main_block_ranges(tree1)
conns = [lo for lo, _ in sqlite_connect_calls(tree1) if not in_ranges(lo, rs)]
check("抓到 3 处块外连接 (常规/别名/直连), __main__ 内那处不误报", conns, [6, 7, 8])
check("__main__ 区间被正确识别", bool(rs) and rs[0][0] == 10, True)
check("路径字面量拆着写也抓到 (且 __main__ 不豁免: 路径唯一产地不分场合)",
      [lo for lo, _v in db_path_literals(tree1) if lo not in docstring_lines(tree1)],
      [6, 9, 11])
# 反例: 名字像但不是 __main__ 的 if 不该当豁免区
t2 = ast.parse('if __name__ == "other":\n    sqlite3.connect("x")\n')
check("非 __main__ 的 if 不构成豁免区间", main_block_ranges(t2), [])
# 反例: 别的模块的 .connect( 不该算 (网络库同名)
t3x = ast.parse('import socket\nsocket.connect("h")\n')
check("非 sqlite3 的 .connect 不算裸连", sqlite_connect_calls(t3x), [])


print("\n=== T2. 真实代码: 连接与路径已收口 ===")
bad_conn, bad_path, stale_reg, n_files = scan()
check(f"未登记的裸 sqlite3.connect = 0 (扫 {n_files} 文件)", bad_conn, [])
check("data/ohs.db 字面量只在 knowledge/paths.py", bad_path, [])
check("写侧豁免登记项都还在用 (无过期条目)", stale_reg, [])


print("\n=== T3. 只读工厂真的只读 ===")
from knowledge.oel import connect_ro  # noqa: E402
c = connect_ro()
try:
    c.execute("CREATE TABLE IF NOT EXISTS __door_probe (x)")
    t3 = "写成功了(不该)"
except Exception as e:
    t3 = "拒绝写" if "readonly" in str(e).lower() else f"其他错:{type(e).__name__}:{e}"
finally:
    c.close()
check("connect_ro() 上执行 DDL 必须失败", t3, "拒绝写")
c2 = connect_ro()
n_oel = None
try:
    n_oel = c2.execute("SELECT COUNT(*) FROM oel_limit").fetchone()[0]
    t3b = "查得到"
except Exception as e:
    t3b = f"查不到: {type(e).__name__}: {e}"
finally:
    c2.close()
# 只断言"schema 可见 + 查询成立", 不断言行数 —— 守卫进容器跑的是生产挂载库,
# 拿行数当判据会把"数据差异"误报成"部署坏了" (GUARD_TESTS 的选取口径)
check("connect_ro() 能读官方表 (schema 可见即可)", t3b, "查得到")
print(f"     ⓘ 当前 oel_limit 行数 = {n_oel} (仅记录, 不参与判定)")


print("\n=== T4. 变异验证: 守卫必须能红 ===")
import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    fake = Path(td) / "web"
    fake.mkdir()
    (fake / "sneaky.py").write_text(
        'import sqlite3\ndef f():\n    return sqlite3.connect("data/ohs.db")\n', encoding="utf-8")
    rs2 = main_block_ranges(ast.parse((fake / "sneaky.py").read_text(encoding="utf-8")))
    caught = any("sqlite3.connect" in ln and not in_ranges(i, rs2)
                 for i, ln in enumerate((fake / "sneaky.py").read_text(encoding="utf-8").splitlines(), 1))
    check("往生产函数塞一行裸连 → 谓词必须抓 (未落进豁免)", caught, True)
    (fake / "mainblock.py").write_text(
        'import sqlite3\nif __name__ == "__main__":\n    sqlite3.connect("data/ohs.db")\n', encoding="utf-8")
    rs3 = main_block_ranges(ast.parse((fake / "mainblock.py").read_text(encoding="utf-8")))
    caught3 = any("sqlite3.connect" in ln and not in_ranges(i, rs3)
                  for i, ln in enumerate((fake / "mainblock.py").read_text(encoding="utf-8").splitlines(), 1))
    check("同样一行写在 __main__ 内 → 不误报 (否则守卫会逼人把裸连挪进生产函数)", caught3, False)


print("\n=== T5. 整条守卫的变异验证 (临时副本, 不碰工作区) ===")
import shutil  # noqa: E402
with tempfile.TemporaryDirectory() as td2:
    tmp = Path(td2)
    for d in SCAN_DIRS:
        shutil.copytree(ROOT / d, tmp / d)
    bc0, bp0, sr0, _ = scan(tmp)
    check("副本初始状态与真实树一致 (裸连/路径/过期三项同为空)", (bc0, bp0, sr0), ([], [], []))
    v = tmp / "web" / "injected.py"
    v.write_text('import sqlite3\ndef f():\n    return sqlite3.connect("data/ohs.db")\n', encoding="utf-8")
    bc1, bp1, sr1, _ = scan(tmp)
    check("注入生产函数裸连 → 守卫必须红", bool(bc1) and bc1[0].startswith("web/injected.py:3"), True)
    check("注入同时也被路径规则抓到", bool(bp1), True)
    v.unlink()
    # 别名绕过 (原守卫的真实漏网点): import sqlite3 as _sq + 拆段拼路径
    v2 = tmp / "web" / "aliased.py"
    v2.write_text('import sqlite3 as _sq\nfrom pathlib import Path\n'
                  'def f():\n    db = Path("x").resolve().parent / "data" / "ohs.db"\n'
                  '    return _sq.connect(str(db))\n', encoding="utf-8")
    bc3, bp3, _sr3, _ = scan(tmp)
    check("别名写法 _sq.connect → 守卫必须红 (这行原来正躲在字面匹配外面)",
          any(s.startswith("web/aliased.py:5") for s in bc3), True)
    check("拆段拼的 ohs.db 路径 → 路径规则必须红",
          any(s.startswith("web/aliased.py:4") for s in bp3), True)
    v2.unlink()
    # 把 auth.py 的裸连全删 → 该豁免项应变"过期"并被要求摘掉 (防列表腐烂)
    a = tmp / "web" / "auth.py"
    a.write_text(a.read_text(encoding="utf-8").replace("sqlite3.connect(", "sqlite3.Nope("), encoding="utf-8")
    bc2, bp2, sr2, _ = scan(tmp)
    check("登记项失去依据时必须被点名 (豁免不许腐烂)", "web/auth.py" in sr2, True)
    check("此时没有别的漏网裸连", bc2, [])

print("\n=== T6. 空 volume 场景: 门报具名错误, bootstrap 能把库建起来 ===")
import knowledge.oel as _oel           # noqa: E402
import knowledge.paths as _paths       # noqa: E402
with tempfile.TemporaryDirectory() as td3:
    ghost = Path(td3) / "sub" / "ohs.db"
    saved_p, saved_o = _paths.DB_PATH, _oel.DB_PATH
    _paths.DB_PATH = _oel.DB_PATH = ghost
    try:
        try:
            _oel.connect_ro()
            t6 = "打开了(不该)"
        except _oel.OfficialSchemaMissing:
            t6 = "具名错误"
        check("库文件不存在 → connect_ro() 抛 OfficialSchemaMissing (不是 no such table)", t6, "具名错误")
        _oel.ensure_official_schema()      # 启动期 bootstrap
        check("bootstrap 后库与官方表就位", ghost.exists(), True)
        c3 = _oel.connect_ro()
        check("bootstrap 后只读连接可查 oel_limit", c3.execute("SELECT COUNT(*) FROM oel_limit").fetchone()[0] >= 0, True)
        c3.close()
    finally:
        _paths.DB_PATH, _oel.DB_PATH = saved_p, saved_o

print("\n=== T7. 写侧工厂收口: 生产函数里不得有 oel.connect() (S1c′ D1) ===")
bad_wf, brk_wf, stale_wf, n_wf = scan_write_factory()
check(f"生产函数里的 oel.connect() 全部登记在案 (扫 {n_wf} 文件)", bad_wf, [])
check("CLI 入口豁免的前提仍成立 (没有别的模块 import 这些 main)", brk_wf, [])
check("写侧工厂登记项无过期条目", stale_wf, [])
print("     ⓘ 登记项: " + "; ".join(f"{k} → {v}" for k, v in WRITE_FACTORY_REGISTERED.items()))
with tempfile.TemporaryDirectory() as td4:
    tmp4 = Path(td4)
    for d in SCAN_DIRS:
        shutil.copytree(ROOT / d, tmp4 / d)
    b0, k0, s0, _ = scan_write_factory(tmp4)
    check("副本基线与真实树一致 (未登记/前提/过期三项同为空)", (b0, k0, s0), ([], [], []))
    inj = tmp4 / "web" / "sneaky_read.py"
    inj.write_text('from knowledge.oel import connect\ndef rows(sql):\n'
                   '    c = connect()\n    return c.execute(sql).fetchall()\n', encoding="utf-8")
    b1, _, _, _ = scan_write_factory(tmp4)
    check("在生产函数里用写侧工厂 (即使只是查询) → 必须红",
          any(x.startswith("web/sneaky_read.py:3") for x in b1), True)
    inj.write_text('from knowledge.oel import connect as oc\ndef rows(sql):\n'
                   '    c = oc()\n    return c.execute(sql).fetchall()\n', encoding="utf-8")
    b2, _, _, _ = scan_write_factory(tmp4)
    check("换别名绕过 → 仍然必须红", any(x.startswith("web/sneaky_read.py:3") for x in b2), True)
    # CLI 入口: 写在 __main__ 块里 / 写在被 __main__ 调用的 main() 里 → 都算开发块, 不误报
    inj.write_text('from knowledge.oel import connect\ndef main():\n'
                   '    connect().executescript("CREATE TABLE IF NOT EXISTS x(y)")\n'
                   'if __name__ == "__main__":\n    main()\n', encoding="utf-8")
    b3, k3, _, _ = scan_write_factory(tmp4)
    check("main() 是 CLI 入口时不误报 (否则 loader 的写侧全被误伤)", (b3, k3), ([], []))
    # 前提一旦被推翻 (别的模块 import 了这个 main), 豁免必须作废
    leak = tmp4 / "web" / "leak.py"
    leak.write_text('from web.sneaky_read import main\ndef route():\n    return main()\n',
                    encoding="utf-8")
    b4, k4, _, _ = scan_write_factory(tmp4)
    check("入口函数被别处 import → 豁免作废并且必须红 (这条才是豁免的真正前提)",
          (any("sneaky_read.py:3" in x for x in b4), any("豁免作废" in x for x in k4)), (True, True))
    leak.unlink()
    inj.unlink()
    # 腐烂检查: 把 app.py 的写侧工厂全换成 connect_ro → 它的登记项必须被点名
    a4 = tmp4 / "web" / "app.py"
    a4.write_text(a4.read_text(encoding="utf-8").replace("conn = connect()", "conn = connect_ro()"),
                  encoding="utf-8")
    b5, _, s5, _ = scan_write_factory(tmp4)
    check("登记项失去依据时必须被点名 (不许只增不减)", "web/app.py" in s5, True)
    check("此时没有别的未登记写侧调用", b5, [])

print("\n=== T8. 守卫套自己也不许给验收库开可写连接 (S1c″ D4′) ===")
# 上一轮的"验收链只读"只做到了**导出链** (CHAIN_READS 那 9 个函数)。但部署第 4 步是
# 用新镜像在容器里跑 GUARD_TESTS, 而容器里 `ROOT/data` 就是挂进去的生产库 ——
# 实测这一条上线当天就抓到 3 处 `connect()` + 3 处 `sqlite3.connect(ROOT/"data"/"ohs.db")`,
# 其中一个还套着 `if db.exists() ... else: print("跳过")` (缺库时绿而没测)。
# 判据范围后来从"部署名单"扩到"部署名单 ∪ 全部 tools/test_*.py": 扩到 26 个文件后当场又抓到
# 2 处 (test_fresh_import 的 connect() 与 test_table_mounts 的 `db = ROOT/"data"/"ohs.db"`),
# 这两个文件都**不在**部署名单里 —— 本地验收天天在给真实库开可写连接, 只查容器那一圈是不够的。
from tools.chain_audit import (GUARD_EXEMPT, deploy_suite_names, guard_write_opens,  # noqa: E402
                               suite_and_local_tests)

names = deploy_suite_names()
scope = suite_and_local_tests()
check("前提对表: 名单是从 quick_deploy.sh 读出来的, 不是空表 (空表会让这条判据永远绿)",
      (len(names) >= 19, "test_import_purity" in names, "test_dataset_door" in names,
       "test_std_ref_parity" in names),
      (True, True, True, True))
# 解析器的两类失效都会把"少一个守卫"变成静默事故: ①`NAME=(` 的位移差一位 → 第一个 token 粘上
# `(`; ②数组行注释没剥干净 → 中文进了名单。两条都在这里钉死 (判据不做"像脚本名才收"的过滤)。
check("名单 token 形状全对 (没有括号/中文/斜杠混进来 = 解析器没把注释当条目)",
      [n for n in names if not re.fullmatch(r"[a-z0-9_]+", n)], [])
check("前提对表: 范围 = 部署名单 ∪ tools/test_*.py, 且比部署名单大 (只查部署名单=漏掉本地守卫)",
      (len(scope) > len(names), "test_table_mounts" in scope, "test_fresh_import" in scope,
       len(scope) == len(set(scope))), (True, True, True, True))
check(f"守卫套 {len(scope)} 个脚本里没有「用可写模式打开验收库」的点", guard_write_opens(), [])

# 变异 (临时树, 只放范围内的文件): 注入一处硬编码可写连 → 必须点名
with tempfile.TemporaryDirectory() as t8:
    tmp8 = Path(t8)
    (tmp8 / "tools").mkdir()
    shutil.copy(ROOT / "quick_deploy.sh", tmp8 / "quick_deploy.sh")

    def seed(break_exempt: str = ""):
        """break_exempt: "" = 原样 | "purity" = 动 factory-proof 那条登记 | "fresh" = 动 write-runtime 那条"""
        for n in scope:
            src = ROOT / "tools" / f"{n}.py"
            txt = src.read_text(encoding="utf-8") if src.exists() else ""
            if break_exempt == "purity" and n == "test_import_purity":
                txt = txt.replace("b = fn(pdb._conn())", "b = fn(pdb._ro())")
            if break_exempt == "fresh" and n == "test_fresh_import":
                txt = txt.replace("c = _pc()", "c = _ro()")
            (tmp8 / "tools" / f"{n}.py").write_text(txt, encoding="utf-8")

    def inject(code: str) -> list[str]:
        """往 test_std_guard.py 末尾拼一行 (它在这棵树里只被静态扫, 不真跑), 看判据认不认"""
        seed()
        (tmp8 / "tools" / "test_std_guard.py").write_text(v8 + code, encoding="utf-8")
        return guard_write_opens(tmp8)

    seed()
    v8 = (tmp8 / "tools" / "test_std_guard.py").read_text(encoding="utf-8")

    got8 = inject('\nconn2 = sqlite3.connect(ROOT / "data" / "ohs.db")\n')
    check("变异: 守卫里注入一行硬编码可写连 → 必须点名 (D4′ 判据能红)",
          (len(got8), "test_std_guard:1" in got8[0] or "sqlite3.connect" in got8[0]), (1, True))
    # 本轮新抓到的两种"看起来不像违规"的形状 —— 上一版判据两种都漏:
    got8b = inject('\ndb2 = ROOT / "data" / "ohs.db"\nconn2 = sqlite3.connect(str(db2))\n')
    check("变异: 路径先存进变量再喂 connect (第一版只看直接实参 → 漏) → 必须点名",
          (len(got8b), "sqlite3.connect(str(db2))" in got8b[0]), (1, True))
    got8c = inject('\nfrom knowledge.oel import connect as zzx\nassess = zzx()\n')
    check("变异: 写侧工厂换别名 (第一版只认字面 connect() → 漏) → 必须点名",
          (len(got8c), "zzx()" in got8c[0]), (1, True))
    got8d = inject('db3: Path = DB_PATH\nconn3 = sqlite3.connect(str(db3))\n')
    check("变异: 带类型标注的赋值 (AnnAssign 没有 .targets, 第一版传播链在这里断) → 必须点名",
          (len(got8d), "sqlite3.connect(str(db3))" in got8d[0]), (1, True))
    # 三条合法写法都必须**不报** (否则判据会逼人放弃检查):
    for label, legal in (("mode=ro 只读 URI",
                          '\nconn2 = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)\n'),
                         ("connect_ro() 只读工厂", "\nfrom knowledge.oel import connect_ro\n"
                          "conn2 = connect_ro()\n"),
                         ("db_uri(read_only=True)",
                          "\nconn2 = sqlite3.connect(db_uri(read_only=True), uri=True)\n")):
        check(f"变异: 注入合法只读取数 ({label}) → 必须不报", inject(legal), [])
    # 反向: 豁免登记腐烂 —— 两条登记各要能红一次
    seed(break_exempt="purity")
    got9 = guard_write_opens(tmp8)
    check("反向对表: factory-proof 登记的调用点被删 → 必须点名「登记腐烂」 (僵尸豁免=永久放行)",
          (len(got9), "豁免登记腐烂" in got9[0], "pdb._conn()" in got9[0]), (1, True, True))
    seed(break_exempt="fresh")
    got9b = guard_write_opens(tmp8)
    check("反向对表: write-runtime 登记的调用点被删 → 同样必须点名",
          (len(got9b), "test_fresh_import.py:_pc()" in got9b[0]), (1, True))
    seed()
    check("对照: 恢复原样后必须回到 0 处 (上面几条红不是永久红)", guard_write_opens(tmp8), [])
    check("登记项都是字符串里带理由的 (豁免要能审计, 不许只是文件名)",
          all(len(v) > 20 for v in GUARD_EXEMPT.values()), True)
    check("登记项都带 kind 前缀 (S1c″ D3′ 的门禁表要按 kind 管两种不同的失效)",
          all(v.startswith(("factory-proof: ", "write-runtime: "))
              for v in GUARD_EXEMPT.values()), True)


print("\n=== T9. 交付链三方对表: 部署名单 ↔ MD5_FILES ↔ 镜像 (S1c″ D4′ 收尾) ===")
# 上面 T8 的范围是从 quick_deploy.sh 读的 —— 而当时镜像里**没有**这个文件 (Dockerfile 只 COPY
# web/tools/knowledge/acquire + requirements.txt/data_stamp.json)。在"镜像形状"的树里跑本文件会直接
# FileNotFoundError (本轮实测复现), 部署第 4 步整条守卫套崩。同一轮量到: 部署名单 21 个守卫
# 有 12 个不在 MD5_FILES → 容器里跑旧守卫时第 7 步完全无声。
from tools.chain_audit import delivery_chain_gaps, image_paths, md5_files   # noqa: E402

md5n, (idirs, ifiles) = len(md5_files()), image_paths()
check("前提对表: MD5_FILES 非空且镜像 COPY 集非空 (空表会让这条判据永远绿)",
      (md5n >= 20, len(idirs) + len(ifiles) >= 4), (True, True))
check("MD5_FILES token 形状全对 (路径里没有括号/中文/等号 = 解析器没把注释当条目)",
      [f for f in md5_files() if not re.fullmatch(r"[A-Za-z0-9_./-]+", f)], [])
check("镜像 COPY 集读得到 (Dockerfile 里 COPY 行数>0 且含 tools 与 web)",
      (idirs.count("tools"), idirs.count("web"), "quick_deploy.sh" in ifiles), (1, 1, True))
check(f"交付链缺口 = 0 (MD5_FILES {md5n} 项全部在源码树+镜像里, 部署名单全部登记)",
      delivery_chain_gaps(), [])
with tempfile.TemporaryDirectory() as t9:
    tmp9 = Path(t9)
    # 变异树 = 真树的薄壳: 源码目录/根配置用软链 (判据要查"MD5_FILES 的项在源码树里存在"),
    # 只有被改的两个配置文件是真副本。
    # ⚠ 这个"薄壳要覆盖哪些路径"的名单**从交付链自己取**, 不在这里抄第二份 (2026-10-16 踩过:
    #   原写法硬编码 ("web","tools","knowledge","acquire","data_stamp.json") →
    #   std_render_hold.json/std_code_exempt.json 进 MD5_FILES 之后立刻少两项,
    #   三条红, 其中两条是"期望恰好 1 个缺口"被撑成 3 个。抄来的名单会腐烂, 真源只有一份。)
    mutated = {"quick_deploy.sh", "Dockerfile"}
    shell_dirs, shell_files = image_paths()
    need = set(shell_files) | {f for f in md5_files() if "/" not in f}
    check("前提对表: 薄壳名单非空且覆盖镜像 COPY 的全部根文件 (空名单=这条对表等于没做)",
          (len(need) >= 4, all(f in need for f in shell_files)), (True, True))
    for d in shell_dirs:
        (tmp9 / d).symlink_to(ROOT / d)
    for f in sorted(need):
        if f in mutated:
            continue
        if not (ROOT / f).exists():
            check(f"薄壳: 交付链要求的 {f} 在真树里不存在", False, "先修 MD5_FILES/COPY 再谈薄壳")
            continue
        (tmp9 / f).symlink_to(ROOT / f)
    dep0 = (ROOT / "quick_deploy.sh").read_text(encoding="utf-8")
    dock0 = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    (tmp9 / "quick_deploy.sh").write_text(dep0, encoding="utf-8")
    (tmp9 / "Dockerfile").write_text(dock0, encoding="utf-8")

    check("对照: 薄壳树 (软链源码 + 副本两个配置) 上缺口也必须是 0 —— 上面那条不是空判",
          delivery_chain_gaps(tmp9), [])
    # 事故形状本身: 镜像里没有 quick_deploy.sh, 而它现在在 MD5_FILES 里 → 必须报
    (tmp9 / "Dockerfile").write_text(
        re.sub(r"^COPY quick_deploy\.sh.*\n", "", dock0, flags=re.M), encoding="utf-8")
    g9 = delivery_chain_gaps(tmp9)
    check("变异: Dockerfile 删掉 COPY quick_deploy.sh → 必须点名 (本轮事故的形状)",
          (len(g9), any("quick_deploy.sh" in x for x in g9)), (1, True))
    (tmp9 / "Dockerfile").write_text(dock0, encoding="utf-8")
    # 腐烂: MD5_FILES 指向一个不存在的文件
    (tmp9 / "quick_deploy.sh").write_text(
        dep0.replace("tools/chain_audit.py", "tools/chain_audit_typo.py"), encoding="utf-8")
    g9b = delivery_chain_gaps(tmp9)
    check("变异: MD5_FILES 写一个不存在的文件 → 必须点名 (清单腐烂)",
          (len(g9b), any("chain_audit_typo" in x for x in g9b)), (1, True))
    # 漏登记: 守卫名单里的一个守卫从 MD5_FILES 改掉 (上一条 typo 会一起报出来, 所以只认点名)
    (tmp9 / "quick_deploy.sh").write_text(
        dep0.replace("tools/test_std_guard.py", "tools/_not_a_guard.py"), encoding="utf-8")
    g9c = delivery_chain_gaps(tmp9)
    check("变异: 守卫在部署名单里但 MD5_FILES 被改掉 → 必须点名 (容器跑旧守卫无人知晓)",
          (any("test_std_guard" in x for x in g9c), any("_not_a_guard" in x for x in g9c)),
          (True, True))
    (tmp9 / "quick_deploy.sh").write_text(dep0, encoding="utf-8")
    check("对照: 恢复原样后缺口必须回到 0 (上面几条红不是永久红)", delivery_chain_gaps(tmp9), [])


# ============ T10. 证据链工具不许自己拼 data/ 路径 (2026-10-16 data_stamp 读错树事故) ============
print("\n=== T10. 证据链工具里『从仓库根拼 data/ 路径』必须为 0 (判据读错源头=绿而没测) ===")
from tools.chain_audit import PATH_EXEMPT, evidence_tools, hardcoded_data_paths   # noqa: E402

hp_in, hp_out = hardcoded_data_paths()
check(f"前提对表: 证据链范围非空 (守卫 ∪ 主门三件套 = {len(evidence_tools())} 支; 空表=永远绿)",
      len(evidence_tools()) >= 25, True)
check("证据链内未登记的硬拼路径 = 0", hp_in, [])
check("登记项都带 kind 前缀 + 理由 (real-dir-forensic / cleanup-target)",
      [k for k, v in PATH_EXEMPT.items()
       if not v.startswith(("real-dir-forensic: ", "cleanup-target: "))], [])
with tempfile.TemporaryDirectory() as t10:
    tmp10 = Path(t10)
    (tmp10 / "tools").mkdir()
    for f in sorted((ROOT / "tools").glob("*.py")):
        (tmp10 / "tools" / f.name).symlink_to(f)
    for f in ("quick_deploy.sh", "Dockerfile", "data_stamp.json"):
        (tmp10 / f).symlink_to(ROOT / f)
    src0 = (ROOT / "tools" / "data_stamp.py").read_text(encoding="utf-8")
    # 变异 A: 把事故原样注回去 (硬拼 ROOT/"data"/"ohs.db") → 必须点名 data_stamp.py
    (tmp10 / "tools" / "data_stamp.py").unlink()
    (tmp10 / "tools" / "data_stamp.py").write_text(
        src0.replace("from knowledge.paths import DB_PATH as DB  # noqa: E402",
                     'DB = ROOT / "data" / "ohs.db"'), encoding="utf-8")
    a = [x for x in hardcoded_data_paths(tmp10)[0] if "data_stamp.py" in x]
    check("变异 A: 给主门工具注回硬拼路径 → 必须点名 (读错树=绿而没测, 这条尺子要能红)",
          len(a) >= 1, True)
    # 变异 B: 临时树里把某条登记的表达式改掉 → 必须报"登记腐烂" (僵尸登记=永久放行)
    (tmp10 / "tools" / "data_stamp.py").unlink()
    (tmp10 / "tools" / "data_stamp.py").symlink_to(ROOT / "tools" / "data_stamp.py")
    ar0 = (ROOT / "tools" / "test_acceptance_readonly.py").read_text(encoding="utf-8")
    (tmp10 / "tools" / "test_acceptance_readonly.py").unlink()
    (tmp10 / "tools" / "test_acceptance_readonly.py").write_text(
        ar0.replace('DATA = ROOT / "data"', 'DATA = Path("/tmp/nowhere")'), encoding="utf-8")
    b = [x for x in hardcoded_data_paths(tmp10)[0] if "登记腐烂" in x]
    check("变异 B: 被登记的调用点搬走 → 必须报『登记腐烂』", len(b) >= 1, True)
    (tmp10 / "tools" / "test_acceptance_readonly.py").unlink()
    (tmp10 / "tools" / "test_acceptance_readonly.py").symlink_to(
        ROOT / "tools" / "test_acceptance_readonly.py")
    check("对照: 恢复原样后证据链内必须回到 0 处 (上面两条红不是永久红)",
          hardcoded_data_paths(tmp10)[0], [])
print(f"     ⓘ 链外存量 {len(hp_out)} 处 = S1c″ D3′ 的账 (只报数不判红; 判红会逼人开豁免, 而豁免正是这条要防的)")

print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 ===")
sys.exit(1 if n_fail else 0)
