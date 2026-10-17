#!/usr/bin/env python3
"""度量: 全库**写类 SQL** 触点在哪一层、动哪张表 (S1c″ D5 的取数脚本, 只读, 不参与守卫)

口径 (写死在这里, 只留这一把尺子 —— 计划里曾有 166 与 174 两个数, 差别就在下面第 3 条):
  1. "写类 SQL" = 字符串常量 (含 f-string 静态片段) 里出现
     INSERT / UPDATE / DELETE / REPLACE / CREATE / ALTER / DROP / TRUNCATE 动词 (词首匹配,
     不误伤 "created_at" 这种)。`executescript(...)` 调用**另报一行** (它必然跑 DDL/DML,
     但脚本内容常是变量, 按字面量算会漏, 按调用点算才是它的真实规模)。
  2. 分层按**路径**判, 不按名字猜: web/ knowledge/ = 生产层 (服务进程会执行),
     tools/ = 脚本层, 其余 = 其他。生产层里再分"模块顶层/函数内/__main__ 开发块"。
  3. 两个计数都报, 因为它们是两件事:
        occurrences = 含写类 SQL 的**字符串常量条数** (同一行写两条 SQL 就是 2)
        sites       = 按 (文件, 行) 去重后的触点数
     引数时必须写清用的哪一个 —— 计划里"166 vs 174"两个数就是这么对不上的。
  4. 目标表取 `INTO <表>` / `UPDATE <表>` / `FROM <表>` / `TABLE [IF NOT EXISTS] <表>` 的
     第一个标识符; 取不到记 `(?)` (拼在变量里的 SQL 就是取不到, 不许假装取到了)。

用法: .venv/bin/python tools/write_sites.py [--json] [--layer web|knowledge|tools|all]
"""
import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "TRUNCATE")
VERB_RE = re.compile(r"\b(" + "|".join(VERBS) + r")\b", re.I)
TABLE_RE = re.compile(
    r"\b(?:INTO|UPDATE|FROM|TABLE)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?[`\"']?([A-Za-z_][A-Za-z0-9_]*)",
    re.I)


def lit_strings(node: ast.AST) -> list[tuple[int, str, bool, int]]:
    """(行号, 文本, 是否含 f-string 插值)。第三项很关键:
    `f"UPDATE {tbl} SET ..."` 的表名是变量, 硬去猜一个名字就是在编数据 —— 标成 (动态)。"""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append((n.lineno, n.value, False, id(n)))
        elif isinstance(n, ast.JoinedStr):
            parts = [v.value for v in n.values
                     if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            dyn = any(isinstance(v, ast.FormattedValue) for v in n.values)
            if parts:
                out.append((n.lineno, " ".join(parts), dyn, id(n)))
    return out


def prose_lines(src: str) -> set[int]:
    """注释与 docstring 的行: 里面出现的 "UPDATE xxx" 是在**讲** SQL, 不是在跑 SQL。"""
    import io
    import tokenize
    out: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.update(range(tok.start[0], tok.end[0] + 1))
    except Exception:
        pass
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    nodes = [tree] + [n for n in ast.walk(tree) if isinstance(
        n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    for node in nodes:
        b = getattr(node, "body", [])
        if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                and isinstance(b[0].value.value, str):
            out.update(range(b[0].lineno, (b[0].end_lineno or b[0].lineno) + 1))
    return out


def classify_layer(rel: str) -> str:
    return "web" if rel.startswith("web/") else \
        "knowledge" if rel.startswith("knowledge/") else \
        "tools" if rel.startswith("tools/") else "其他"


def vocab_ids(tree: ast.AST) -> set[int]:
    """"动词表"里的字符串常量 id 集合: 元组/集合/列表字面量, 且**每个元素都是短字符串**。
    为什么必须排掉: `chain_audit.WRITE_SQL`、`db_writers.WRITE_SQL`、本脚本自己的 `VERBS`
    都是 ("insert ", "update ", ...) 这种词表, 它们不是 SQL 语句, 却能被 SQL 判据命中 ——
    实测 (2026-10-16) `(?)` 桶 53 条里绝大多数就是度量脚本自己的词表, 也就是
    **"尺子在量尺子"**。排掉并单独报数, 不静默丢。"""
    out: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Tuple, ast.Set, ast.List)):
            vals = [e.value for e in n.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if len(vals) == len(n.elts) and vals and all(len(v) <= 20 for v in vals):
                out.update(id(e) for e in n.elts)
    return out


def main_blocks(tree: ast.AST) -> list[tuple[int, int]]:
    return [(n.lineno, n.end_lineno or n.lineno)
            for n in ast.walk(tree) if isinstance(n, ast.If)
            and ast.unparse(n.test) == '__name__ == "__main__"']


def in_blocks(lineno: int, blocks: list[tuple[int, int]]) -> bool:
    return any(a <= lineno <= b for a, b in blocks)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--layer", default="all", choices=["web", "knowledge", "tools", "all"])
    args = ap.parse_args()

    occurrences = 0
    sites: set[tuple[str, int]] = set()
    by_layer: Counter = Counter()
    by_verb: Counter = Counter()
    by_table: dict[str, Counter] = defaultdict(Counter)
    prod_rows: list[str] = []
    exec_scripts = 0
    vocab_skipped = 0
    files = sorted(p for p in ROOT.rglob("*.py")
                   if ".venv" not in p.parts and "__pycache__" not in p.parts
                   and "node_modules" not in p.parts)
    for p in files:
        rel = str(p.relative_to(ROOT))
        layer = classify_layer(rel)
        if args.layer != "all" and layer != args.layer:
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        docs = prose_lines(src)
        mb = main_blocks(tree)
        voc = vocab_ids(tree)
        for lineno, val, dyn, nid in lit_strings(tree):
            if lineno in docs:
                continue
            up = val.upper().replace("\n", " ")
            verbs = {v.upper() for v in VERB_RE.findall(up)}
            if not verbs:
                continue
            # 词表常量 ("insert ", "update " 这类度量脚本自己的动词表) 只有在它**同时命中动词**
            # 时才要排掉, 且必须在算完动词之后排 —— 放在前面会把几千条无关字符串一起吞掉,
            # 那个"已排除 6443"的数本身就说明口径失控了。
            if nid in voc:
                vocab_skipped += 1
                continue
            occurrences += 1
            sites.add((rel, lineno))
            by_layer[layer] += 1
            for v in verbs:
                by_verb[v] += 1
            m = TABLE_RE.search(val)          # 用原文取表名: 大写化会把 "SET" 之类当表名
            tbl = "(动态)" if dyn else (m.group(1) if m else "(?)")
            by_table[tbl][layer] += 1
            if layer in ("web", "knowledge") and not in_blocks(lineno, mb):
                prod_rows.append(f"{rel}:{lineno} {'/'.join(sorted(verbs))} {tbl} "
                                 f"| {val[:56]}")
        exec_scripts += sum(1 for n in ast.walk(tree)
                            if isinstance(n, ast.Call)
                            and getattr(n.func, "attr", "") == "executescript")

    data = {"occurrences": occurrences, "sites": len(sites),
            "by_layer": dict(by_layer), "by_verb": dict(by_verb),
            "by_table": {k: dict(v) for k, v in sorted(by_table.items())},
            "生产层非开发块触点": len(prod_rows),
            "词表常量已排除": vocab_skipped}
    if args.json:
        print(json.dumps({**data, "prod": prod_rows}, ensure_ascii=False, indent=1))
        return 0
    print(f"写类 SQL: **occurrences(字面量出现次数) = {occurrences}** / "
          f"**sites(按文件+行去重) = {len(sites)}**  ← 引数时必须写清用哪个")
    print(f"  `executescript(...)` 调用点 = {exec_scripts} (它必然跑 DDL/DML, 单独记, 不混进上面两个数)")
    print(f"  已排除的词表常量 (度量脚本自己的动词表这类) = {vocab_skipped}")
    print("  分层:", dict(by_layer))
    print("  动词:", dict(by_verb.most_common()))
    print(f"\n  生产层 (web/knowledge) 且不在 __main__ 开发块里: {len(prod_rows)} 处")
    for r in prod_rows[:40]:
        print("   ", r)
    print("\n  被写类 SQL 点到的表 (前 20, 按触点总数):")
    tot = sorted(((sum(v.values()), k, v) for k, v in by_table.items()), reverse=True)
    for n, k, v in tot[:20]:
        print(f"     {k:<24}{n:>4}  {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
