#!/usr/bin/env python3
"""度量: 谁开了**可写**连接, 那个函数到底写不写 (S1c′ D1 的取数脚本, 只读不改任何文件)

口径 (写死在这里, 只留这一把尺子):
  · "开可写连接" = 调用 `knowledge.oel.connect()` 及其别名 (`from knowledge.oel import
    connect as oc`)。`connect_ro()` / `sqlite3.connect()` 裸连 / `projects_db._conn()`
    分开统计, 不混为一谈 —— 计划里"连接调用点 91"与"工厂调用 103"是两个不同的数,
    混过一次就别再混。
  · "这个函数写不写" = 函数体 (含其调用的本模块函数一层) 里出现写类 SQL 字面量:
    INSERT / UPDATE / DELETE / REPLACE / CREATE / ALTER / DROP / TRUNCATE / executescript。
    判据来自 SQL 动词表, 不是按目录或文件名猜。
  · READ 桶 = 开了可写连接但函数体没有任何写类 SQL → 这些就是 D1 要改成 connect_ro() 的点。

用法: .venv/bin/python tools/db_writers.py [--json] [目录...]   默认 web knowledge
"""
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRITE_SQL = ("insert ", "update ", "delete ", "replace ", "create ", "alter ",
             "drop ", "truncate ", "executescript")
WRITE_FACTORY = "connect"          # knowledge.oel.connect (会 mkdir + executescript(DDL))
READ_FACTORIES = ("connect_ro", "_conn_ro")


def sql_strings(node: ast.AST) -> list[str]:
    """函数体里出现的 SQL 文本 (字符串常量 + f-string 的静态片段)"""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value.lower())
        elif isinstance(n, ast.JoinedStr):
            for v in n.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append(v.value.lower())
    return out


def has_write(node: ast.AST) -> bool:
    return any(any(w in s for w in WRITE_SQL) for s in sql_strings(node))


def enclosing(tree: ast.AST, lineno: int):
    best = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.lineno <= lineno <= (n.end_lineno or n.lineno):
                if best is None or n.lineno > best.lineno:
                    best = n
    return best


def main_blocks(tree: ast.AST) -> list[tuple[int, int]]:
    """`if __name__ == "__main__":` 的行区间 (谓词与 tools/test_dataset_door.py 同款)。
    必须单独标出来: 开发块里的 connect() 不是生产读路径, 混在一起统计会把
    "生产函数里该改 connect_ro 的点" 和 "脚本自己开的可写连接" 算成同一个数。"""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.If) or not isinstance(n.test, ast.Compare):
            continue
        c = n.test
        if not (isinstance(c.left, ast.Name) and c.left.id == "__name__"
                and len(c.ops) == 1 and isinstance(c.ops[0], ast.Eq)
                and len(c.comparators) == 1 and isinstance(c.comparators[0], ast.Constant)
                and c.comparators[0].value == "__main__"):
            continue
        end = max((getattr(x, "end_lineno", 0) or getattr(x, "lineno", 0)) for x in ast.walk(n))
        out.append((n.lineno, max(end, n.lineno)))
    return out


def in_blocks(lineno: int, blocks: list[tuple[int, int]]) -> bool:
    return any(a <= lineno <= b for a, b in blocks)


def aliases_of(tree: ast.AST) -> set[str]:
    """本文件里 `connect` 的别名集合 (含 `import knowledge.oel as X` 的 X.connect 另判)"""
    a = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.endswith("oel"):
            for al in n.names:
                if al.name == WRITE_FACTORY:
                    a.add(al.asname or al.name)
                if al.name == "connect_ro":
                    a.add("RO:" + (al.asname or al.name))
    return a


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv[1:]
    dirs = [Path(a) for a in args] or [ROOT / "web", ROOT / "knowledge"]
    rows = []
    for d in dirs:
        base = d if d.is_absolute() else ROOT / d
        for f in sorted(base.rglob("*.py")):
            rel = str(f.relative_to(ROOT))
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            als = aliases_of(tree)
            names = {a for a in als if not a.startswith("RO:")}
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                fn = n.func
                name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
                mod_is_oel = isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) \
                    and fn.value.id in ("oel", "knowledge") and name in (WRITE_FACTORY, "connect_ro")
                if name in READ_FACTORIES or name.startswith("RO:"):
                    continue
                is_ro = (name in {"connect_ro"} or (mod_is_oel and fn.attr == "connect_ro"))
                if is_ro:
                    continue
                is_write_factory = (name in names) or (mod_is_oel and fn.attr == WRITE_FACTORY
                                                       and isinstance(fn.value, ast.Name) and fn.value.id == "oel")
                if not is_write_factory:
                    continue
                encl = enclosing(tree, n.lineno)
                in_dev = in_blocks(n.lineno, main_blocks(tree)) or (encl is not None
                                                                    and encl.name in ("main",))
                scope = encl.name if encl else ("<__main__/脚本>" if in_dev else "<模块顶层>")
                scope_node = encl if encl else tree
                # 类/模块级调用点看整棵子树
                rows.append({"file": rel, "line": n.lineno, "fn": scope,
                             "dev_block": in_dev,
                             "writes": has_write(scope_node)})
    read_sites = [r for r in rows if not r["writes"]]
    write_sites = [r for r in rows if r["writes"]]
    prod_read = [r for r in read_sites if not r["dev_block"]]
    cli_read = [r for r in read_sites if r["dev_block"]]
    if as_json:
        print(json.dumps({"total": len(rows), "prod_read": prod_read,
                          "cli_read": cli_read, "write": write_sites},
                         ensure_ascii=False, indent=1))
        return 0

    def dump(title: str, sites: list[dict]) -> None:
        by = defaultdict(list)
        for r in sites:
            by[r["file"]].append(f"{r['line']}@{r['fn']}")
        print(f"\n--- {title} ({len(sites)} 处 / {len(by)} 文件) ---")
        for f, lst in sorted(by.items()):
            print(f"  {f:44s} {' '.join(sorted(lst, key=lambda x: int(x.split('@')[0])))}")

    print(f"可写工厂 oel.connect() 调用点 = {len(rows)}   "
          f"写类函数里 = {len(write_sites)}   纯读 = {len(read_sites)} "
          f"(生产路径 {len(prod_read)} / CLI·开发块 {len(cli_read)})")
    dump("生产路径纯读却开了可写连接 → D1 要改 connect_ro 的点", prod_read)
    dump("CLI / __main__ 里的纯读 (按需改, 不影响验收链)", cli_read)
    dump("确有写类 SQL (写侧, 保持 connect 但要登记)", write_sites)
    return 0


if __name__ == "__main__":
    sys.exit(main())
