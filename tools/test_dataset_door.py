#!/usr/bin/env python3
"""S1a′ 守卫: 路径与连接的收口 (2026-10-15)

管三件事, 全部是**静态源码检查** —— 不读 data/ohs.db, 不导报告:
  A. `sqlite3.connect(` 在 web/ 与 knowledge/ 里只许出现在
     ① 工厂 (knowledge/oel.py) ② `if __name__ == "__main__":` 开发块 ③ 显式豁免
  B. 库路径字面量 (任何含 "ohs.db" 的字符串, 含 `parent/"data"/"ohs.db"` 这种拼法)
     只许出现在 knowledge/paths.py
     (loader 文件头注释里的 "输出: data/ohs.db → 表名" 不算 —— 那是说明不是代码)
  C. 官方表标准号列的读点收口在 tools/test_std_currentize.py (W4 改造后的 AST 版)

⚠ A/B 两条**必须按 AST 判**, 不能按行文本匹配: 原实现按字面 "sqlite3.connect" 找,
   `import sqlite3 as _sq` + `_sq.connect(str(db))` 整个绕过 —— web/word_export.py:290-297
   就是这样自己拼路径、自己开读写连接跑了 8 个月 (S1b′ 收口时才发现并补掉)。

⚠ 本文件自身的谓词也要被测 (v1 计划踩过): "是否在 __main__ 块内" 用 AST 独立实现,
   并有 T1 组用例证明它判得对 —— 按文件豁免会整批放过生产函数里的裸连
   (web/table_skeleton.py:369 就是活例子: 生产函数里自开连接还被 except 静默吞掉)。

用法: .venv/bin/python tools/test_dataset_door.py    (退出码 0 = 全绿)
"""
import ast
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
    "web/projects_db.py": "project 表读写",
    "web/migrate_states.py": "一次性章节状态迁移脚本",
    "web/external_tables.py": "外部表采集写入",
    "knowledge/fix_mapping.py": "一次性映射修复脚本",
    "knowledge/seed_aux_unit.py": "官方层 seed 写入侧",
    "knowledge/standard_db_loader.py": "官方层 loader 写入侧",
}
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

print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 ===")
sys.exit(1 if n_fail else 0)
