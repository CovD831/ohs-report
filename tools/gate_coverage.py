#!/usr/bin/env python3
"""S1.0 · 官方层读点分级 (2026-10-15)

回答两个问题, 它们决定"重构时什么算被保护":

  A 门可见性: 这个读点的 SQL, 4 项目导出路径**实际执行过**吗?
  B 消费者类型: 读回来的值, 是**印到纸上**(printed) / 只参与判断(branch)
                / 只进草稿或落在"已存正文回放"路径(prompt) / 判不准(unknown)?

为什么 B 才是关键 (对抗审查 G1, 已复验):
  导出正文大量来自 `web/word_export.py:1044  sub_text = sub_meta.get("text","")`
  —— 叙述是**回放已存正文**, 不是重算。所以 branch/prompt 类站点即便读错,
  内容指纹 (--check) 也纹丝不动。**只有 printed 类站点可以援引"零 diff"当证据。**

⚠ printed 是**函数内**证据 (值在本函数里进了 f-string / 容器 / return), 跨函数去向没追 ——
  所以 printed 是"可能印到纸上"的上界, 不是保证; 真正裁决仍是逐表跑 --check 指纹。
  分类器的失误方向被刻意压保守: 拿不准就落 branch/unknown (= "需要额外取证"),
  绝不把无证据的站点算成受保护。抽查例: entity_link 的 `row[0] if row else ""`
  被判成 branch (值其实经 cas 流向下游), 低估而非高估。

⚠ 本工具是**本地验收命令**, 禁止进 quick_deploy 的 GUARD_TESTS:
  它要跑 4 次导出 (~4.3s/次) 且依赖挂载库里存在那 4 个 pid —— 生产与本地数据
  本就按设计有差异 (hazard_toxicity 104 vs 103 事故), 放进部署守卫等于让部署
  成败取决于生产数据状态。

用法:
  .venv/bin/python tools/gate_coverage.py --write     # 跑导出+分级, 落 JSON
  .venv/bin/python tools/gate_coverage.py --offline    # 复用缓存 trace, 只重算分级
  .venv/bin/python tools/gate_coverage.py --audit      # 打印 unknown 与未处置的 branch/prompt
  .venv/bin/python tools/gate_coverage.py --iddiff     # 只读对表 (不落盘): 新身份键 vs 旧基线逐条差异清单

身份键 (S1c″-D5′ 前半段, 2026-10-17):
  站点身份 = (file, fn, 表名集合, SELECT 列多重集, 语句指纹, 同形重数 dup)。
  行号降级为**参考属性**, 不再进键 —— 2026-10-16 实测: 活代码加几行注释让 17 个站点行号 +1,
  基线整批"漂移", 而 total_sites/printed/gate_visible/protected_now 四个计数一个都没动:
  纯排版噪声却要求验收的人逐条人肉确认。新键全部由**内容/结构**派生, 代码上下移动时不变;
  同文件同质的多个读点靠 (语句指纹 + 同形重数) 区分, 给同一张表新增加一个读点会被点名。
  兼容路径: 旧基线 (tools/golden/gate_coverage.json, 无 key 字段) 的记录可由其
  (file, fn, tables, sql_full) 内容字段**推导**出新键, --iddiff 即按此对表, 不崩不整判红。
"""
import argparse
import ast
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# 库路径走单一产地 knowledge.paths.DB_PATH (2026-10-16): 原来这里三处硬写 ROOT/"data"/"ohs.db",
#   于是 OHS_DATA_DIR 搬家 (部署演练/变异对照) 时看板读的还是仓库真库 —— 与 tools/data_stamp.py
#   同一类失效, 由 chain_audit.hardcoded_data_paths() 收口。
from knowledge.paths import DB_PATH  # noqa: E402
sys.stdout.reconfigure(encoding="utf-8")

OUT = ROOT / "tools" / "golden" / "gate_coverage.json"
TRACE = ROOT / "tools" / "golden" / "gate_trace.json"
BASE_DIRS = ("web", "knowledge", "tools")
# 表名正则**必须含数字**: 曾用 [a-z_]+ 把 gbz1_rule 截成 gbz, 得出"门看不见 gbz1_rule"的假结论
TBL_RE = re.compile(r"FROM\s+([a-z_][a-z_0-9]*)", re.I)
EXEC_FNS = {"execute", "executescript", "rows_of", "_rows_of", "table", "one", "rows", "scalar"}
# ⚠ 准入判据不能只看被调名 (S1b′ 实测): 站点迁到 Dataset.rows()/one() 与 _ds_rows/_ds_one
#   这类本地 shortcut 之后, 按名字白名单收会把**还在读的读点看成消失** —— 196→189 的差额里
#   有 6 处就是这么来的。名字只当提示, 真正的判据是"调用实参里有没有一条静态 SELECT"。

# —— 消费者类型的判据词 (启发式, 判不准一律 unknown, 不许硬猜成 printed) ——
COUNT_RE = re.compile(r"\bSELECT\s+COUNT\(|\bEXISTS\s*\(", re.I)
SEL1_RE = re.compile(r"LIMIT\s+1\b", re.I)
ORDER_RE = re.compile(r"ORDER\s+BY", re.I)
PRINT_FN = re.compile(r"^(fill_|build_|table_|_?rows|matrix|ppe_|hazard_grid|section_)")
PROMPT_FN = re.compile(r"(prompt|llm|draft|system_msg|user_msg|narrat)", re.I)
PROMPT_VAR = re.compile(r"(prompt|sys_txt|user_txt|draft|narrat)", re.I)
TABLE_SINK = re.compile(r"(rows|cells|cols|table|body|out|res|items)", re.I)


def official_tables() -> set[str]:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")} - {
            "project", "users", "audit_log", "task_job", "equipment_material"}
    finally:
        con.close()


def _lit(node) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
                       for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _lit(node.left) + _lit(node.right)
    return ""


def _fn_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _enclosing_func(tree: ast.AST) -> dict[int, str]:
    """lineno → 所属函数名"""
    m: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in ast.walk(fn):
                m.setdefault(getattr(stmt, "lineno", 0), fn.name)
    return m


def _parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    p: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            p[id(ch)] = node
    return p


def bool_contexts(fn_node: ast.FunctionDef | None) -> set[int]:
    """函数体内所有"处在判断位置"的节点 id —— 落在这里的读点不可能影响打印内容"""
    ids: set[int] = set()
    if fn_node is None:
        return ids
    tests: list[ast.AST] = []
    for n in ast.walk(fn_node):
        if isinstance(n, (ast.If, ast.While)):
            tests.append(n.test)
        elif isinstance(n, ast.IfExp):
            tests.append(n.test)
        elif isinstance(n, (ast.Compare, ast.BoolOp, ast.Assert)):
            tests.append(n)
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            tests.append(n)
    for t in tests:
        for sub in ast.walk(t):
            ids.add(id(sub))
    return ids


def decisive_ancestor(node, parents: dict[int, ast.AST], up_to: int = 6):
    """向上找第一个能说明去向的祖先 (Assign/Return/For/Call)"""
    n = node
    for _ in range(up_to):
        n = parents.get(id(n))
        if n is None:
            return None
        if isinstance(n, (ast.Assign, ast.Return, ast.For, ast.AugAssign)):
            return n
        if isinstance(n, ast.Call) and _fn_name(n) in ("append", "extend", "update", "setdefault", "format", "join"):
            return n
    return None


def find_assigned_to(node, parents: dict, up_to: int = 4) -> str:
    """`x = conn.execute(...).fetchall()` 的直接父节点是 .fetchall() 而非 Assign;
    只看一层会把赋值目标整个漏掉 (实测 36 个 LIMIT 1 站点因此掉进 unknown)。"""
    n = node
    for _ in range(up_to):
        n = parents.get(id(n))
        if n is None:
            return ""
        if isinstance(n, (ast.Assign, ast.AugAssign)):
            tgt = n.targets[0] if isinstance(n, ast.Assign) else n.target
            if isinstance(tgt, ast.Name):
                return tgt.id
            if isinstance(tgt, ast.Tuple) and tgt.elts and isinstance(tgt.elts[0], ast.Name):
                return tgt.elts[0].id
            return ""
        if isinstance(n, (ast.Return, ast.For, ast.If, ast.While)):
            return ""
    return ""


def var_uses(fn_node, name: str) -> set[str]:
    """变量在整个函数里的去向 (数据流粗算, 比"看变量名"可靠):
      text   → 进 f-string / .format / % / join / 字符串拼接   ⇒ 值会印到纸上
      return → 被 return 出去 (装配层消费)                      ⇒ 同上
      forit  → 被 for 逐行消费                                 ⇒ 同上
      bool   → 只出现在判断位置                                ⇒ 不参与打印
    """
    if fn_node is None or not name:
        return set()
    flags: set[str] = set()
    text_ids: set[int] = set()
    for n in ast.walk(fn_node):
        holders: list[ast.AST] = []
        if isinstance(n, ast.JoinedStr):
            holders.extend(n.values)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("format", "join"):
            holders.append(n)
        elif isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Mod)):
            holders.append(n)
        for h in holders:
            text_ids.update(id(sub) for sub in ast.walk(h))
    bids = bool_contexts(fn_node)
    for n in ast.walk(fn_node):
        if isinstance(n, ast.Name) and n.id == name:
            if id(n) in text_ids:
                flags.add("text")
            if id(n) in bids:
                flags.add("bool")
        elif isinstance(n, ast.Return) and any(isinstance(x, ast.Name) and x.id == name for x in ast.walk(n)):
            flags.add("return")
        elif isinstance(n, ast.For) and n.iter is not None \
                and any(isinstance(x, ast.Name) and x.id == name for x in ast.walk(n.iter)):
            flags.add("forit")
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr in ("append", "extend", "update", "setdefault") \
                and any(isinstance(x, ast.Name) and x.id == name for a in n.args for x in ast.walk(a)):
            flags.add("sink")     # 写进列表/字典 → 由装配层带走
    return flags


def classify(sql: str, funcname: str, node, parents: dict, assigned_to: str,
             bool_ids: set[int], for_ids: set[int], fn_node) -> tuple[str, str]:
    """返回 (类型, 依据)。判不准一律 unknown —— 诚实优先于覆盖率。"""
    why: list[str] = []
    if COUNT_RE.search(sql):
        why.append("COUNT/EXISTS 聚合")
    if SEL1_RE.search(sql) and not ORDER_RE.search(sql):
        why.append("LIMIT 1 无 ORDER BY")

    if id(node) in bool_ids:
        return "branch", "; ".join(why + ["结果直接落在判断位置"])
    if COUNT_RE.search(sql):
        return "branch", "; ".join(why)

    uses = var_uses(fn_node, assigned_to)
    # prompt 判据**先于** reach: 值被 _build_prompt() return 出去时它进的是草稿而不是纸面,
    # 按 reach 会误判成 printed —— 那正是"把无证据站点算成受保护"的方向, 绝不允许。
    tgt = assigned_to or ""
    if PROMPT_VAR.search(tgt) or PROMPT_FN.search(funcname or ""):
        return "prompt", f"落到 {tgt or funcname} (草稿/prompt 侧)"
    reach = uses - {"bool"}
    if reach:
        return "printed", f"{assigned_to} 走向 {'/'.join(sorted(reach))} (进正文/装配层)"
    if uses == {"bool"}:
        return "branch", f"{assigned_to} 只用于判断"

    anc = decisive_ancestor(node, parents)
    if id(node) in for_ids or (isinstance(anc, ast.For) and anc.iter is not None):
        return "printed", "被 for 逐行消费 (进装配层)"
    if isinstance(anc, ast.Return):
        return ("printed", f"由 {funcname}() 返回给装配层") if PRINT_FN.match(funcname or "") \
            else ("unknown", f"{funcname}() 返回, 去向未追")
    if isinstance(anc, ast.Call):
        outer = _fn_name(anc)
        if outer in ("append", "extend", "update", "setdefault"):
            return "printed", f"写入 {outer}() 目标"
        if outer in ("format", "join"):
            return "prompt", f"被 {outer}() 拼进文字"
    if isinstance(anc, (ast.Assign, ast.AugAssign)) and TABLE_SINK.search(tgt):
        return "printed", f"落到容器变量 {tgt}"
    return "unknown", "; ".join(why + ["未定位到明确去向"]) if why else "无信号"


def _func_table(tree: ast.AST) -> dict:
    return {fn.name: fn for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _func_ctx(tree: ast.AST) -> dict[str, tuple[set[int], set[int]]]:
    """每个函数的 (处在判断位置的节点 id 集, For 迭代源的节点 id 集)"""
    ctx: dict[str, tuple[set[int], set[int]]] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fis: set[int] = set()
            for n in ast.walk(fn):
                if isinstance(n, ast.For) and n.iter is not None:
                    fis.update(id(sub) for sub in ast.walk(n.iter))
            ctx[fn.name] = (bool_contexts(fn), fis)
    return ctx


def _scan_tree(tree: ast.AST, relpath: str, tables: set[str]) -> list[dict]:
    """单个 AST 的官方层读点抽取 (S1c″-D5′: 从 static_sites 拆出, 让变异测试能喂合成源码)"""
    out = []
    fmap, pmap = _enclosing_func(tree), _parent_map(tree)
    cctx, ftab = _func_ctx(tree), _func_table(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        sql = ""
        for a in node.args:
            if isinstance(a, ast.List):
                sql = " ".join(_lit(e) for e in a.elts)
                if sql:
                    break
            s = _lit(a)
            if s and re.search(r"\bselect\b", s, re.I):
                sql = s
                break
        if not sql or not re.search(r"\bSELECT\b", sql, re.I):
            continue
        hit = sorted({t for t in TBL_RE.findall(sql) if t in tables})
        if not hit:
            continue
        fn = fmap.get(node.lineno, "")
        bis, fis = cctx.get(fn, (set(), set()))
        assigned_to = find_assigned_to(node, pmap)
        kind, why = classify(sql, fn, node, pmap, assigned_to, bis, fis, ftab.get(fn))
        out.append({"file": relpath, "line": node.lineno,
                    "tables": hit, "fn": fn,
                    "sql_full": re.sub(r"\s+", " ", sql).strip(),
                    "sql": re.sub(r"\s+", " ", sql).strip()[:180],
                    "kind": kind, "why": why})
    return out


def static_sites(tables: set[str]) -> list[dict]:
    out = []
    for d in BASE_DIRS:
        for f in sorted((ROOT / d).rglob("*.py")):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            out.extend(_scan_tree(tree, str(f.relative_to(ROOT)), tables))
    return out


def run_trace() -> list[str]:
    """跑 4 项目导出, 抓 trace 里执行过的 SQL (表名含数字的正则口径同上)"""
    seen: list[str] = []
    orig = sqlite3.connect

    def traced(*a, **k):
        c = orig(*a, **k)
        c.set_trace_callback(lambda s: seen.append(s))
        return c
    sqlite3.connect = traced
    Path("/tmp/gate_cov_docx").mkdir(parents=True, exist_ok=True)
    try:
        from tools.export_fingerprint import PIDS, export
        Path("/tmp/gate_cov_docx").mkdir(exist_ok=True)
        for pid in PIDS:
            export(pid, Path("/tmp/gate_cov_docx"))
    finally:
        sqlite3.connect = orig
    return [re.sub(r"\s+", " ", s).strip() for s in seen]


def sig_pattern(sql: str) -> re.Pattern:
    """整句匹配, 不是前缀包含 —— 前缀匹配会把 hazard_factor_alias.py:47 的
    `SELECT DISTINCT factor_name FROM oel_limit ...` 误判成"门执行到过",
    其实那两句来自 process_material_extractor。宁可漏判成不可见 (保守), 不可误判成受保护。"""
    s = re.sub(r"\s+", " ", sql).strip().upper()
    s = re.escape(s)
    s = s.replace("\\?", ".+?")                                   # 绑定参数 → 实际字面量
    s = re.sub(r"\\\{[A-Z_a-z0-9]+\\\}", ".{0,60}? ", s)          # f-string 插值位
    return re.compile("^" + s + ";?$")


def grade(sites: list[dict], trace: list[str]) -> list[dict]:
    up = [t.upper() for t in trace]
    by_table: dict[str, list[str]] = {}
    for s in up:
        # 键必须小写: 这里索引的是大写后的语句, 而 site["tables"] 来自小写源码 ——
        # 两边不同案会导致 cands 恒空, 表现为"门可见 = 0"的假结论 (2026-10-15 踩过)
        for t in {x.lower() for x in TBL_RE.findall(s)}:
            by_table.setdefault(t, []).append(s)
    for site in sites:
        cands = None
        for t in site["tables"]:
            lst = by_table.get(t, [])
            cands = lst if cands is None else [s for s in lst if s in cands]
        pat = sig_pattern(site.get("sql_full") or site["sql"])
        site["gate_visible"] = any(pat.fullmatch(c) for c in (cands or []))
    return sites


def probe_tables() -> dict:
    """表级"牙齿"实测: 在**副本库**上给每张表的一个文本列打标记, 重新导出, 看指纹变不变。

    为什么需要它: classify() 靠 AST 猜"值会不会印到纸上", 猜错的方向是
    把无证据站点算成受保护 —— 那是最危险的失误。这里改成直接量:
    数据没上纸, 打标记就不可能改变指纹, 于是"这张表的改动门看不见"成为事实而非推测。

    安全: 全程把 sqlite3.connect 的目标重定向到 /tmp 副本, **真实库一个字节都不写**。
    """
    import hashlib
    import shutil
    import sqlite3 as _s3
    from tools.export_fingerprint import PIDS, digest, export

    real = DB_PATH
    copy = Path("/tmp/gate_probe_ohs.db")
    orig = _s3.connect

    def redirect(*a, **k):
        args = list(a)
        if args and isinstance(args[0], (str, Path)):
            s = str(args[0])
            if "ohs.db" in s:
                args[0] = str(copy) if "mode=" not in s else f"file:{copy}?mode=ro"
        return orig(*args, **k)

    def run_digest(pid: str) -> str:
        g = digest(export(pid, Path("/tmp/gate_probe_docx")))
        return hashlib.sha1(json.dumps({"p": g["para_hashes"], "c": g["cell_hashes"],
                                        "s": g["std_codes"]}, sort_keys=True).encode()).hexdigest()

    shutil.copyfile(real, copy)
    _s3.connect = redirect
    sqlite3.connect = redirect
    Path("/tmp/gate_probe_docx").mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    try:
        base = {pid: run_digest(pid) for pid in PIDS}
        print("  副本基线指纹: " + ", ".join(f"{p}:{h[:8]}" for p, h in base.items()))
        tables = official_tables()
        for t in sorted(tables):
            c = orig(str(copy))
            try:
                cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % t) if r[2] == "TEXT"]
                if not cols:
                    out[t] = {"seen": None, "why": "无文本列"}
                    continue
                marked = 0
                for col in cols:      # ⚠ 全部文本列一起打标记: 只挑 cols[0] 会选到
                    #    category/code/industry_code 这类标识符列, 得出"看不见"的假阴性
                    marked += c.execute(f"UPDATE {t} SET {col}=COALESCE({col},'')||'|QOZ'").rowcount
                c.commit()
                # 4 项目并集: 某张表只在一个项目的正文里出现, 单项目探测会判成"看不见"
                changed = [pid for pid in PIDS if run_digest(pid) != base[pid]]
                out[t] = {"seen": bool(changed), "text_cols": len(cols), "rows_marked": marked,
                          "changed_in_projects": changed,
                          "note": "" if changed else "全部文本列都打了标记而 4 项目指纹均未变 → 该表数据没上纸"}
                for col in cols:
                    c.execute(f"UPDATE {t} SET {col}=REPLACE({col},'|QOZ','')")
                c.commit()
            finally:
                c.close()
            print(f"  {'✓ 看得见' if out[t]['seen'] else '✗ 看不见'} {t:22s}"
                  f"{'  项目 ' + ','.join(x[:4] for x in out[t]['changed_in_projects']) if out[t]['seen'] else ''}")
    finally:
        _s3.connect = orig
        sqlite3.connect = orig
    return out


def apply_overrides(sites: list[dict],
                    ov_path: Path | None = None) -> tuple[list[dict], list[str]]:
    """人工定性落盘 —— 给"行号确实需要人判"的例外用。迁移期接受两种键 (S1c″-D5′):
      · 旧形 "file:line": 按当前代码行号命中站点 (行号本就是这类例外的判据的一部分);
      · 新形 "file|fn|tables|cols|shape|dup" (含 "|"): 直接对 site["key"], 不随行号漂。
    **命中不到任何站点的登记必须点名 (僵尸 override)**, 不许静默留着 —— 与
    chain_audit 的"豁免登记腐烂"、test_dataset_door 的"登记腐烂"同一纪律:
    被登记的调用点搬走了要报, 否则例外会变成"永久放行"。
    返回 (sites, 僵尸键列表)。"""
    ov = ov_path or (ROOT / "tools" / "golden" / "gate_coverage_overrides.json")
    if not ov.exists():
        return sites, []
    data = json.loads(ov.read_text(encoding="utf-8"))
    by_line: dict[tuple, tuple[str, dict]] = {}
    by_key: dict[str, tuple[str, dict]] = {}
    for k, v in data.items():
        m = re.match(r"^(.+):(\d+)$", k) if "|" not in k else None
        if m:
            by_line[(m.group(1), int(m.group(2)))] = (k, v)
        else:
            by_key[k] = (k, v)
    hit: set[str] = set()
    for s in sites:
        got = by_key.get(s.get("key", "")) or by_line.get((s["file"], s["line"]))
        if got:
            ok, v = got
            s["kind"], s["why"] = v["kind"], "人工定性: " + v["why"]
            s["override_ref"] = ok
            if "|" not in ok and "key" in s:      # 旧形命中 → 顺带报出它现在对应的新键, 便于迁移对表
                s["override_new_key"] = s["key"]
            hit.add(ok)
    return sites, sorted(set(data) - hit)


def probe_columns() -> dict:
    """列级牙齿图: 表级探针会说谎 (2026-10-15 实测) —— oel_limit 整体有牙齿,
    但 `cas` / `source_standard` 两列打标记后 4 项目指纹纹丝不动; 而 `source_standard`
    正是这道门要改写的列。所以"这张表有牙齿"绝不能当成"这个站点被护住"。

    只对表级已见 (seen) 的 11 张表逐列探 (70 列 × 4 导出 ≈ 21 分钟)。
    标记会让数值列解析失败 —— 那证明"这列被消费但不以文字上纸", 单独记 used_unprintable。
    """
    import hashlib
    import shutil
    from tools.export_fingerprint import PIDS, digest, export

    real = DB_PATH
    copy = Path("/tmp/gate_cols_ohs.db")
    orig = sqlite3.connect
    saved = dict(sqlite3_connect=sqlite3.connect)

    def redirect(*a, **k):
        args = list(a)
        if args and isinstance(args[0], (str, Path)) and "ohs.db" in str(args[0]):
            args[0] = str(copy) if "mode=" not in str(args[0]) else f"file:{copy}?mode=ro"
        return orig(*args, **k)

    sqlite3.connect = redirect

    def run_digest(pid: str) -> str:
        g = digest(export(pid, Path("/tmp/gate_cols_docx")))
        return hashlib.sha1(json.dumps({"p": g["para_hashes"], "c": g["cell_hashes"],
                                        "s": g["std_codes"]}, sort_keys=True).encode()).hexdigest()

    probe = json.loads((ROOT / "tools" / "golden" / "gate_probe.json").read_text(encoding="utf-8"))
    targets = sorted(t for t, v in probe.items() if v.get("seen"))
    shutil.copyfile(real, copy)
    Path("/tmp/gate_probe_docx").mkdir(parents=True, exist_ok=True)
    Path("/tmp/gate_cols_docx").mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    try:
        base = {pid: run_digest(pid) for pid in PIDS}
        for t in targets:
            c = orig(str(copy))
            cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % t) if r[2] == "TEXT"]
            c.close()
            out[t] = {}
            for col in cols:
                c = orig(str(copy))
                try:
                    n = c.execute(f"UPDATE {t} SET {col}=COALESCE({col},'')||'|QZ'").rowcount
                    c.commit()
                finally:
                    c.close()
                rec = {"rows_marked": n}
                try:
                    rec["changed_in"] = [pid for pid in PIDS if run_digest(pid) != base[pid]]
                    rec["teeth"] = bool(rec["changed_in"])
                except Exception as e:                      # 标记破坏解析 = 被消费但不以文字上纸
                    rec["teeth"] = False
                    rec["used_unprintable"] = f"{type(e).__name__}: {str(e)[:60]}"
                    rec["changed_in"] = []
                finally:
                    c = orig(str(copy))
                    c.execute(f"UPDATE {t} SET {col}=REPLACE({col},'|QZ','')")
                    c.commit()
                    c.close()
                out[t][col] = rec
                print(f"  {t}.{col:18s} {'✓' if rec['teeth'] else '·'}"
                      f"{'  ' + rec.get('used_unprintable', '') if rec.get('used_unprintable') else ''}",
                      flush=True)
    finally:
        sqlite3.connect = saved["sqlite3_connect"]
        c = orig(str(copy))
        residue = sum(
            c.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} LIKE '%|QZ%'").fetchone()[0]
            for t in targets
            for col in [r[1] for r in c.execute("PRAGMA table_info(%s)" % t) if r[2] == "TEXT"])
        c.close()
        print(f"  副本残留标记: {residue} (必须 0)")
    return out


_SEL_RE = re.compile(r"SELECT\s+(.*?)\s+FROM", re.I | re.S)


def _select_cols(sql: str) -> list[str]:
    """SELECT 与 FROM 之间的列名 (去 DISTINCT/表前缀; 函数表达式取原词)"""
    m = _SEL_RE.search(sql)
    if not m:
        return []
    out = []
    for c in m.group(1).split(","):
        c = re.sub(r"^(DISTINCT\s+|\*\s*)", "", c.strip(), flags=re.I)
        c = c.split(".")[-1].strip('"').strip()
        if c and c != "*":
            out.append(c)
    return out


# ================= 身份键 (S1c″-D5′ 前半段) =================
# 键形状: file | fn | tables(升序) | cols(升序多重集) | 语句指纹(归一 SQL 的 sha1[:12]) | dup
#   · 为什么免疫排版: 六个分量全部由**内容与结构**派生 —— 行号、注释、空行一个都不参与;
#     加一行注释让后面所有站点行号 +1 时, 键集体不变 (2026-10-16 那 17 条假漂移会塌缩成 0)。
#   · 为什么不吞新读点: 同一文件同一函数再写一条一模一样的 SELECT, 语句指纹相同,
#     靠同形重数 dup (组内按源码行序 0..n-1) 让"第 N 个同形站点"各有其键;
#     新增一条 → 键多重集多一个元素, diff 能点名。组内交换/中间插删不改多重集, 不造假差异。
#   · 兼容路径: 旧基线记录没有这些字段, key_parts 会现场从 (file, fn, tables, sql_full) 推导,
#     所以 --iddiff 能读旧基线对表, 不崩、不整判红。
KEY_FIELDS = ("file", "fn", "tables", "cols", "shape", "dup")


def _shape_hash(sql: str) -> str:
    s = re.sub(r"\s+", " ", sql).strip()
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def key_parts(rec: dict) -> tuple:
    """(file, fn, tables, cols, shape) —— cols/shape 缺失时从 sql_full 现场推导 (旧基线兼容)"""
    cols = rec.get("cols")
    if cols is None:
        cols = _select_cols(rec.get("sql_full") or rec.get("sql", ""))
    return (rec["file"], rec.get("fn", ""),
            ",".join(sorted(rec.get("tables") or [])),
            ",".join(sorted(cols)),
            rec.get("shape") or _shape_hash(rec.get("sql_full") or rec.get("sql", "")))


def assign_keys(sites: list[dict]) -> list[dict]:
    """给每个站点补 cols/shape/dup/key (行号只留作人用参考, 不进键)。可重入: 对已有键的记录
    再跑一遍结果不变 (cols/shape 已物化, dup 按 (file,line) 源码序重排, 基线内行序单调)。"""
    groups: dict[tuple, int] = {}
    for s in sorted(sites, key=lambda x: (x["file"], x["line"])):
        p = key_parts(s)
        dup = groups.get(p, 0)
        groups[p] = dup + 1
        s["cols"] = sorted(p[3].split(",")) if p[3] else []
        s["shape"], s["dup"] = p[4], dup
        s["key"] = "|".join((s["file"], s["fn"], p[2], p[3], p[4], str(dup)))
    return sites


def diff_keys(old_sites: list[dict], new_sites: list[dict]) -> dict:
    """按键多重集对比两份**已 assign_keys** 的站点表。
    返回: gone/added (Counter, 含重数), modified (gone↔added 配对: 同 file+fn, 内容分量变化),
    gone_only/added_only (配不上对的), line_moves (键相同但行号集不同 = 纯排版漂移, 新键免疫)。"""
    oc = Counter(s["key"] for s in old_sites)
    nc = Counter(s["key"] for s in new_sites)
    gone, added = oc - nc, nc - oc
    olines: dict[str, list[int]] = defaultdict(list)
    nlines: dict[str, list[int]] = defaultdict(list)
    orec: dict[str, dict] = {}
    nrec: dict[str, dict] = {}
    for s in old_sites:
        olines[s["key"]].append(s["line"])
        orec.setdefault(s["key"], s)
    for s in new_sites:
        nlines[s["key"]].append(s["line"])
        nrec.setdefault(s["key"], s)
    line_moves = [{"key": k, "old": sorted(olines[k]), "new": sorted(nlines[k])}
                  for k in sorted(oc & nc) if sorted(olines[k]) != sorted(nlines[k])]

    modified, used = [], set()
    for gk in sorted(gone):
        gp = gk.split("|")
        cands = [ak for ak in sorted(added)
                 if ak not in used and gp[0] == ak.split("|")[0] and gp[1] == ak.split("|")[1]]
        if not cands:
            continue
        ak = min(cands, key=lambda a: sum(1 for x, y in zip(a.split("|"), gp) if x != y))
        changed = [f for f, x, y in zip(KEY_FIELDS, ak.split("|"), gp) if x != y]
        if 1 <= len(changed) <= 3:          # 同 file+fn 且最多三个内容分量变 → 认定为"改造过的站点"
            used.add(ak)
            modified.append({"gone_key": gk, "added_key": ak, "changed": changed,
                             "old": orec[gk], "new": nrec[ak]})
    gone_only = [k for k in sorted(gone) if k not in {m["gone_key"] for m in modified}]
    added_only = [k for k in sorted(added) if k not in used]
    return {"gone": gone, "added": added, "modified": modified,
            "gone_only": gone_only, "added_only": added_only, "line_moves": line_moves}


def four_counts(sites: list[dict]) -> dict:
    """四项计数 (阳性对照口径): total_sites / printed / gate_visible / protected_now"""
    return {"total_sites": len(sites),
            "printed": sum(1 for s in sites if s["kind"] == "printed"),
            "gate_visible": sum(1 for s in sites if s.get("gate_visible")),
            "protected_now": sum(1 for s in sites if s.get("gate_visible") and s["kind"] == "printed")}


def migration_list() -> dict:
    """S1b′ 迁移白名单: printed × 门可见, 再按**列级**牙齿分档。

    为什么必须再分列级: 表级"门可见"会骗人 —— 列级探针实测 `oel_limit.source_standard`
    (正是这道门要改写的列) 打标记后 4 项目指纹一字不动。column_visible=False 的站点
    不能用 "--check 零差异" 当保护证据, 只能由 tools/test_door_currentize_unit.py 那种
    合成库单测负责。
    """
    cov = json.loads(OUT.read_text(encoding="utf-8"))
    colpath = ROOT / "tools" / "golden" / "gate_columns.json"
    cols = json.loads(colpath.read_text(encoding="utf-8")) if colpath.exists() else {}
    sites = []
    for s in cov["sites"]:
        if s["kind"] != "printed" or not s.get("gate_visible"):
            continue
        read = _select_cols(s["sql_full"])
        hit, miss = [], []
        for tb in s["tables"]:
            for c in read:
                info = cols.get(tb, {}).get(c)
                if info is None:
                    continue
                (hit if info["teeth"] else miss).append(f"{tb}.{c}")
        sites.append({"file": s["file"], "line": s["line"], "tables": s["tables"],
                      "read_cols": read, "column_visible": bool(hit),
                      "visible_cols": sorted(set(hit)), "blind_cols": sorted(set(miss)),
                      "note": ("读到的上纸列: " + ",".join(x.split(".")[1] for x in sorted(set(hit)))
                               if hit else
                               "所读列全不上纸: " + ",".join(x.split(".")[1] for x in sorted(set(miss)))
                               or "列不在列级探针表内"),
                      "sql": s["sql_full"]})
    return {"summary": {"total": len(sites),
                        "column_visible": sum(1 for x in sites if x["column_visible"]),
                        "needs_unit_test": sum(1 for x in sites if not x["column_visible"]),
                        "note": "column_visible=True 才能用 export_fingerprint --check 交账; "
                                "False 的站点由 tools/test_door_currentize_unit.py 负责"},
            "sites": sorted(sites, key=lambda x: (x["file"], x["line"]))}


def iddiff_report(old_path: Path, new_sites: list[dict], zombies: list[str]) -> dict:
    """只读对表 (不落盘, 不重打基线): 旧基线记录按内容推导新键, 与当前扫描逐键对账, 打印
    "如果按新键重打基线会差多少条、每条差在哪"。返回统计 dict 供守卫复用。"""
    old = json.loads(old_path.read_text(encoding="utf-8"))
    old_sites = assign_keys(old["sites"])
    cn_old, cn_new = four_counts(old_sites), four_counts(new_sites)
    print("=== ① 四项计数对账 (旧基线 vs 当前重算; 语义结论必须一致) ===")
    for k, v in cn_new.items():
        same = cn_old[k] == v
        print(f"  {k:14s} 基线={cn_old[k]:4d}  现在={v:4d}  {'✓ 一致' if same else '✗ 不一致'}")
    d = diff_keys(old_sites, new_sites)
    n_gone, n_added = sum(d["gone"].values()), sum(d["added"].values())
    print(f"\n=== ② 按新键重打基线: 差 {n_gone + n_added} 条记录 "
          f"(消失 {n_gone} + 新增 {n_added}; 其中改造配对 {len(d['modified'])} 对 = {2 * len(d['modified'])} 条) ===")
    for m in d["modified"]:
        ch = []
        for f in m["changed"]:
            if f == "dup":
                continue
            a = dict(zip(KEY_FIELDS, m["gone_key"].split("|")))[f]
            b = dict(zip(KEY_FIELDS, m["added_key"].split("|")))[f]
            ch.append(f"{f}: {a or '∅'}→{b or '∅'}")
        print(f"  [改造 {len(ch)}处内容分量] {m['old']['file']}:{m['old']['line']}"
              f"→{m['new']['file']}:{m['new']['line']}  fn={m['old']['fn']}")
        for c in ch:
            print(f"      · {c}")
        print(f"      sql(旧): {(m['old'].get('sql_full') or '')[:88]}")
        print(f"      sql(新): {(m['new'].get('sql_full') or '')[:88]}")
        print(f"      性质: 内容真变化 (表/列/语句指纹变), 不是排版 —— 旧键下这类必须逐条人判, 新键直接点名")
    for k in d["gone_only"]:
        s = next(x for x in old_sites if x["key"] == k)
        print(f"  [消失] {s['file']}:{s['line']} fn={s['fn']} tables={k.split('|')[2]}")
        print(f"      sql: {(s.get('sql_full') or '')[:88]}")
        print(f"      性质: 站点被删/迁到别的函数 (fn 变) —— 需人判去向")
    for k in d["added_only"]:
        s = next(x for x in new_sites if x["key"] == k)
        print(f"  [新增] {s['file']}:{s['line']} fn={s['fn']} tables={k.split('|')[2]} dup={s['dup']}")
        print(f"      sql: {(s.get('sql_full') or '')[:88]}")
        print(f"      性质: 新读点 (同形重数键 dup>0 时=给同一张表又加了一个, 不会被吞)")
    print(f"\n=== ③ 排版免疫实测 (这次改造的目的本身) ===")
    print(f"  键两侧都在但行号集移动: {len(d['line_moves'])} 个键")
    moved_sites = sum(len(x['new']) for x in d['line_moves'])
    print(f"  —— 按旧键 (file,line) 这些会报成 {moved_sites} 条'变化' (全是假漂移); 按新键 {len(d['line_moves'])} 条真差异都不用报")
    for lm in d["line_moves"]:
        print(f"    · {lm['key'].split('|')[0]} fn={lm['key'].split('|')[1]} dup#{lm['key'].split('|')[5]}"
              f" 行号 {lm['old']} → {lm['new']}")
    print(f"\n=== ④ overrides 对表 (僵尸必须点名) ===")
    ovf = ROOT / "tools" / "golden" / "gate_coverage_overrides.json"
    if ovf.exists():
        data = json.loads(ovf.read_text(encoding="utf-8"))
        byref = {s.get("override_ref"): s for s in new_sites if s.get("override_ref")}
        for k in sorted(data):
            s = byref.get(k)
            if s:
                print(f"  ✓ 命中 {k}  → 现在对应新键 {s['key']}"
                      f" (行号参考 {s['file']}:{s['line']})")
        for z in zombies:   # 判据=登记键在当前站点表里命中不到 (僵尸 override, 同 chain_audit 豁免腐烂口径)
            print(f"  ✗ 僵尸 override: {z} —— 当前代码里命中不到任何站点, 要么删登记, 要么调用点不该被删")
        if not zombies:
            print(f"  僵尸 0 条 ({len(data)} 条登记全部命中)")
    return {"counts_old": cn_old, "counts_new": cn_new, "diff": d, "zombies": zombies}


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true", help="跑导出+分级, 落 JSON")
    g.add_argument("--offline", action="store_true", help=f"复用 {TRACE.name} 里的缓存 trace")
    g.add_argument("--audit", action="store_true", help="打印 unknown / 未处置的 branch,prompt")
    g.add_argument("--probe", action="store_true", help="表级牙齿实测 (副本库, 不写真库)")
    g.add_argument("--probe-columns", action="store_true",
                   help="列级牙齿图: 对表级已见的表逐列探 (~21 分钟, 只写 /tmp 副本)")
    g.add_argument("--migration-list", action="store_true",
                   help="按当前代码重算迁移白名单 (printed×可见×列级牙齿), 需先有 gate_coverage.json")
    g.add_argument("--iddiff", action="store_true",
                   help="只读对表 (不落盘): 旧基线按内容推导新身份键, 与当前重算逐键 diff + 僵尸 override")
    args = ap.parse_args()
    tables = official_tables()

    if args.iddiff:
        if not OUT.exists():
            print(f"无基线 {OUT.name}, --iddiff 是拿它做对账对象的, 先由主代理 --write")
            return 1
        if not TRACE.exists():
            print(f"无缓存 trace ({TRACE.name}), --iddiff 不跑导出, 先 --write")
            return 1
        sites = grade(static_sites(tables), json.loads(TRACE.read_text(encoding="utf-8")))
        sites = assign_keys(sites)
        sites, zombies = apply_overrides(sites)
        iddiff_report(OUT, sites, zombies)
        return 0

    if getattr(args, "migration_list"):
        if not OUT.exists():
            print(f"先跑 --write/--offline 生成 {OUT.name}")
            return 1
        ml = migration_list()
        path = ROOT / "tools" / "golden" / "gate_migration_list.json"
        path.write_text(json.dumps(ml, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"迁移白名单: {ml['summary']['total']} 处 = "
              f"{ml['summary']['column_visible']} 处可用 --check 交账 + "
              f"{ml['summary']['needs_unit_test']} 处必须合成库单测 → {path.name}")
        return 0

    if getattr(args, "probe_columns"):
        res = probe_columns()
        path = ROOT / "tools" / "golden" / "gate_columns.json"
        path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        ncol = sum(len(v) for v in res.values())
        teeth = sum(1 for v in res.values() for x in v.values() if x["teeth"])
        print(f"\n列级牙齿: {teeth}/{ncol} 列在 4 项目导出上真的上纸 → {path.relative_to(ROOT)}")
        for t, v in res.items():
            zero = [c for c, x in v.items() if not x["teeth"]]
            if zero:
                print(f"  {t}: 零牙齿列 {zero}")
        return 0

    if args.probe:
        print("=== 表级实测: 打标记后指纹会不会变 ===")
        res = probe_tables()
        seen = [t for t, v in res.items() if v.get("seen")]
        blind = [t for t, v in res.items() if v.get("seen") is False]
        path = ROOT / "tools" / "golden" / "gate_probe.json"
        path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n门看得见 {len(seen)}/{len(res)} 张: {', '.join(sorted(seen))}")
        print(f"门看不见 {len(blind)} 张 (迁这些表时 --check 零差异**不构成证据**): {', '.join(sorted(blind))}")
        print(f"→ {path.relative_to(ROOT)}")
        return 0

    if args.audit:
        if not OUT.exists():
            print("无分级结果, 先 --write")
            return 1
        data = json.loads(OUT.read_text(encoding="utf-8"))
        unk = [s for s in data["sites"] if s["kind"] == "unknown"]
        print(f"unknown {len(unk)} 处 (需人工定, 不许默认当 printed):")
        for s in unk[:40]:
            print(f"  {s['file']}:{s['line']} [{','.join(s['tables'])}] {s['sql'][:72]}")
        bp = [s for s in data["sites"] if s["kind"] in ("branch", "prompt")]
        print(f"\nbranch/prompt {len(bp)} 处 —— 这些站点迁移时 --check 零差异不构成证据:")
        print("  处置状态:", dict(Counter(s.get("disposition", "unhandled") for s in bp)))
        return 0

    sites = static_sites(tables)
    if args.write:
        tr = run_trace()
        TRACE.write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
    elif TRACE.exists():
        tr = json.loads(TRACE.read_text(encoding="utf-8"))
    else:
        print(f"无缓存 trace ({TRACE.name}), 先 --write")
        return 1
    sites = grade(sites, tr)
    sites = assign_keys(sites)          # 新身份键随 --write/--offline 落盘 (行号仍保留为参考属性)
    sites, zombies = apply_overrides(sites)
    for z in zombies:
        print(f"⚠ 僵尸 override: {z} —— 登记了但当前代码命中不到任何站点 (要么删登记, 要么调用点不该被删)")

    stats = {
        "total_sites": len(sites),
        "by_kind": dict(Counter(s["kind"] for s in sites)),
        "by_file": dict(Counter(s["file"] for s in sites).most_common(20)),
        "gate_visible": sum(1 for s in sites if s["gate_visible"]),
        "trace_sql": len(tr),
        "protected_now": sum(1 for s in sites if s["gate_visible"] and s["kind"] == "printed"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"stats": stats, "sites": sites}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("=== 读点分级结果 ===")
    print(f"  官方层 SELECT 读点: {stats['total_sites']}")
    print(f"  消费者类型: {stats['by_kind']}")
    print(f"  门可见: {stats['gate_visible']}")
    print(f"  ★ 同时是 printed 且门可见 (唯一可用'零 diff'当证据的一批): {stats['protected_now']}")
    print(f"  → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
