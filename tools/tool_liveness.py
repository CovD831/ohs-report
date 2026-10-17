#!/usr/bin/env python3
"""度量: `tools/` 里每个脚本**现在还有没有人指向它** (S1c″ D5 的取数脚本, 只读, 不删任何东西)

为什么要有它: `tools/` 已经 158 个脚本。计划里"120 个无锚点文件"这种数是从"没人指"推的,
而这类"死代码"判据有两个假阳来源, 这版把它们都堵上:
  1. 脚本互相调用 (`gate_coverage.py` 只被 `test_gate_coverage_selftest.py` 指向) ——
     只看"部署链/代码/docs"三条就会把显然活着的脚本判成死的。所以加了 **T** 桶。
  2. **注释里提一句 ≠ 有人在调它** —— 反过来 (把注释当引用) 会让"没人用"的名单变短,
     而把注释和真引用混成一桶, 报出来的数字就说不清是哪一种。所以 web/knowledge 侧
     拆成 **B(代码级)** 与 **P(散文级)**, 按 tokenize 的 COMMENT 与 docstring 行区间分开算。

口径 (写死在这里, 只留这一把尺子):
  A deploy → quick_deploy.sh / Dockerfile / docker-compose.yml 里出现 `tools/<name>.py`;
             或 MD5_FILES / GUARD_TESTS / LOCAL_TESTS 数组里的**裸名** (容器命令是
             `tools/$_t.py`, 裸名就是点名 —— 漏了这条会把守卫脚本判成死代码)。
  B code   → web/ 与 knowledge/ 的 .py 里, **非注释非 docstring** 处出现
             `tools/<name>.py` / `tools.<name>` / `import <name>` / 裸 `<name>.py`。
  P prose  → 同上但落在注释或 docstring 里, 或出现在仓库 .md 里。
  T tool   → **其他** tools/*.py 里非注释处的引用 (脚本互相调用、守卫自测)。
  D none   → A/B/P/T 全空 = 归档候选。删不删由用户逐批判, 本脚本只列名单。

用法: .venv/bin/python tools/tool_liveness.py [--json] [--dead-only] [--brief]
"""
import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY_FILES = ("quick_deploy.sh", "Dockerfile", "docker-compose.yml")
ARRAYS = ("MD5_FILES", "GUARD_TESTS", "LOCAL_TESTS")


def py_files(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def prose_lines(src: str) -> set[int]:
    """注释 + docstring 占的行号 —— 这些行里的"引用"只是有人提了一嘴, 不是调用点。"""
    out: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, IndentationError, SyntaxError):
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


class Src:
    """一个源文件的"代码行 / 散文行"两套文本"""
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.raw = path.read_text(encoding="utf-8", errors="replace")
        pl = prose_lines(self.raw)
        lines = self.raw.splitlines()
        self.code = "\n".join(l for i, l in enumerate(lines, 1) if i not in pl)
        self.prose = "\n".join(l for i, l in enumerate(lines, 1) if i in pl)


def patterns(stem: str) -> list[re.Pattern]:
    """指向某个 tools 脚本的写法: 带路径的、当模块 import 的、只写文件名的都算,
    但**只匹配这个词**, 不做子串猜 (否则 test_std_guard 会匹配到 test_std_guard_x)。"""
    return [re.compile(p) for p in (
        rf"tools/{re.escape(stem)}\.py",
        rf"tools\.{re.escape(stem)}\b",
        rf"\bimport\s+{re.escape(stem)}\b",
        rf"\bfrom\s+{re.escape(stem)}\b",
        rf"(?<![\w./]){re.escape(stem)}\.py",
    )]


def deploy_names() -> dict[str, str]:
    hit: dict[str, str] = {}
    for rel in DEPLOY_FILES:
        p = ROOT / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"tools/([a-zA-Z0-9_]+)\.py", text):
            hit.setdefault(m.group(1), rel)
        for arr in ARRAYS:
            m = re.search(arr + r"=\((.*?)\)", text, re.S)
            if m:
                for tok in m.group(1).split():
                    tok = tok.strip('"\'')
                    if re.fullmatch(r"[a-zA-Z0-9_]+", tok):
                        hit.setdefault(tok, f"{rel}:{arr}")
    return hit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dead-only", action="store_true")
    ap.add_argument("--brief", action="store_true")
    args = ap.parse_args()

    tools = py_files(ROOT / "tools")
    dep = deploy_names()
    web_kn = [Src(p) for p in py_files(ROOT / "web") + py_files(ROOT / "knowledge")]
    tool_src = {p.stem: Src(p) for p in tools}
    docs = [(p.name, p.read_text(encoding="utf-8", errors="replace"))
            for p in sorted(ROOT.rglob("*.md")) if ".venv" not in p.parts]

    rows = []
    for p in tools:
        stem = p.stem
        pats = patterns(stem)
        hit_any = lambda text: any(pt.search(text) for pt in pats)      # noqa: E731
        b = [s.name for s in web_kn if hit_any(s.code)]
        pr = [s.name for s in web_kn if hit_any(s.prose)]
        pr += [f"{n}(md)" for n, text in docs if hit_any(text)]
        t = [q for q, s in tool_src.items() if q != stem and hit_any(s.code)]
        rows.append({"name": stem, "A_deploy": stem in dep,
                     "deploy_where": dep.get(stem, ""), "B_code": b,
                     "P_prose": pr, "T_tools": t,
                     "D_none": not (b or pr or t or stem in dep)})

    stats = {"总数": len(rows),
             "A 部署链点名": len([r for r in rows if r["A_deploy"]]),
             "B web/knowledge 代码级引用": len([r for r in rows if r["B_code"]]),
             "P 仅散文级 (注释/doc/md) 引用": len([r for r in rows
                                                   if r["P_prose"] and not r["B_code"]
                                                   and not r["T_tools"] and not r["A_deploy"]]),
             "T 其他 tools 代码级引用": len([r for r in rows if r["T_tools"]]),
             "D 四者全无 = 归档候选": len([r for r in rows if r["D_none"]]),
             "A∩B": len([r for r in rows if r["A_deploy"] and r["B_code"]])}

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if args.dead_only:
        for r in rows:
            if r["D_none"]:
                print(r["name"])
        return 0
    print(f"tools/ 脚本总数 {stats['总数']}  (各桶有交集, **不加总**)")
    for k, v in stats.items():
        print(f"  {k:<34}{v}")
    if not args.brief:
        print("\n  A 部署链点到的:")
        print("     " + "  ".join(r["name"] for r in rows if r["A_deploy"]))
        print("\n  B 被 web/knowledge 代码级指向的:")
        for r in rows:
            if r["B_code"]:
                print(f"     {r['name']:<32} ← {','.join(r['B_code'])}")
        print("\n  T 被其他 tools 代码级指向的:")
        for r in rows:
            if r["T_tools"]:
                print(f"     {r['name']:<32} ← {','.join(r['T_tools'])}")
        print("\n  P 只有散文级引用 (注释/doc 提了一嘴, 没人真调) 的:")
        for r in rows:
            if r["P_prose"] and not r["B_code"] and not r["T_tools"] and not r["A_deploy"]:
                print(f"     {r['name']:<32} ← {','.join(r['P_prose'])[:78]}")
    print("\n  D 归档候选 (四者全无; 只列名单, 不删任何东西):")
    dead = [r["name"] for r in rows if r["D_none"]]
    for i in range(0, len(dead), 6):
        print("     " + "  ".join(dead[i:i + 6]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
