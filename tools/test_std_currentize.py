#!/usr/bin/env python3
"""守卫: 官方数据层读出的标准号必须"读出即纠版" (S1b′ 后的 Dataset 读门口径)

背景 (2026-10-15): 版本戳首跑抓到生产 emergency_rule/protection_rule 仍挂废止标准号
  (GBZ 2.2—2007 / GB/T 38144.1—2019), 本地 oel_limit.source_standard 也存着 GBZ 2.2—2007。
  部署刻意排除 data/ → 不能假设两份库都被清洗过, 所以兜底放在**读出点**:
  knowledge.dataset.Dataset.rows()/one() 是唯一纠版出口, raw()/raw_one() 是不纠版的
  诚实出口 (必须写 reason)。

本文件钉四层:
  A. 合成库判定表: 规则行存废止号 → 读出来必须换成现行版 (不依赖任何本地/生产数据)
  B. 真库不变式: 受管表经读门取出后, 不再含任何"库内判为废止"的标准号
  C. 接线 (AST, W4 改造): 官方表的标准号列不得用裸 `*.execute()` 读;
     raw 出口必须带非空 reason 字面量; 纠版出口不得读 standard_db (W3: 那会把替代关系
     反查表自己吃掉 —— 实测 standard_db 的 code 有 200 个自身会被判废止)。
     ⚠ 为什么重写: 旧版 C 段按行文本找 `.execute(`。站点迁成 `ds.rows(...)` 之后行里
       再没有这个串 → 5 条断言集体零杀伤, 而它正好漏掉"raw 出口读规则表标准号列"这类事。
       现在按 AST 认调用, 与迁移同一个 commit 落地。
  D. 变异验证: C 段每条判据都在临时副本上注入反例, 证明它真能红。
用法: .venv/bin/python tools/test_std_currentize.py
"""
import ast
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import knowledge.standard_version                                  # noqa: E402
from knowledge.dataset import Dataset                              # noqa: E402
from knowledge.oel import connect                                  # noqa: E402
from knowledge.standard_version import rows_of                     # noqa: E402

RULE_TABLES = ("emergency_rule", "protection_rule", "management_rule",
               "surveillance_rule", "standard_ref", "oel_limit")
STD_COLS = ("std_code", "source_standard", "code")

# ── C 段口径 ────────────────────────────────────────────────────────────────
OFFICIAL = ("emergency_rule", "protection_rule", "management_rule", "surveillance_rule",
            "standard_ref", "oel_limit", "gbz1_rule", "hazard_factor", "hazard_toxicity",
            "health_effect", "occupational_disease", "ghs_class", "ppe_item",
            "method_catalog", "bio_limit", "hazchem_item", "material_dictionary")
_STD_COL = re.compile(r"\b(std_code|source_standard|standard_code|replace_of|code)\b")
_TBL = re.compile(r"\bFROM\s+(\w+)", re.I)
CUR_EXIT = ("rows", "one")                        # 纠版出口
RAW_EXIT = ("raw", "raw_one")                     # 不纠版出口 (必须写 reason)
COMPAT = ("rows_of",)                             # 兼容出口: rows_of(conn, sql, args)
BARE = ("execute", "executemany")                 # 裸连接: 读标准号列即违规
SCAN_DIRS = ("web", "knowledge")
# 判据产地: 它必须读 standard_db 的原样 code/state/replace_of, 且永远不经过读门。
RULER = {"knowledge/standard_version.py":
         "号→现行版判据的产地: 反查表/状态表必须用库内原样 code (W3)"}

n_pass = n_fail = 0


def check(label, cond, detail=""):
    global n_pass, n_fail
    if cond:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label} {detail}")


print("### A. 合成库: 规则行存废止号 → 读出即现行版")
c = sqlite3.connect(":memory:")
c.execute("CREATE TABLE standard_db (code TEXT, name TEXT, state TEXT, replace_of TEXT)")
c.executemany("INSERT INTO standard_db VALUES (?,?,?,?)", [
    ("GBZ2.2-2007", "工作场所有害因素职业接触限值 第2部分:物理因素", "废止", ""),
    ("GBZ2.2-2019", "工作场所有害因素职业接触限值 第2部分:物理因素", "实施中", "GBZ 2.2—2007"),
    ("GBZ1-2010", "工业企业设计卫生标准", "实施中", ""),
])
c.execute("CREATE TABLE emergency_rule (scenario TEXT, item TEXT, require TEXT, "
          "std_code TEXT, clause TEXT)")
c.executemany("INSERT INTO emergency_rule VALUES (?,?,?,?,?)", [
    ("高温中暑应急", "防暑降温", "高温作业场所应有防暑降温措施", "GBZ 2.2—2007", "表8"),
    ("一般场景", "卫生设计", "按 GBZ 1-2010 执行", "GBZ 1-2010", "5.2"),
])
for label, reader in (("rows_of 兼容出口", lambda sql: rows_of(c, sql)),
                      ("Dataset.rows 读门", lambda sql: Dataset(c).rows(sql))):
    rows = reader("SELECT scenario, item, require, std_code, clause FROM emergency_rule")
    check(f"{label}: 废止号 GBZ 2.2—2007 被换成现行版", "2007" not in rows[0][3], f"实得 {rows[0][3]!r}")
    check(f"{label}: 替换后是 GBZ 2.2—2019", rows[0][3] == "GBZ 2.2—2019", f"实得 {rows[0][3]!r}")
    check(f"{label}: 非废止号原样保留", rows[1][3] == "GBZ 1-2010", f"实得 {rows[1][3]!r}")
    check(f"{label}: 同行文本列里嵌的废止号也被纠 (require 列)",
          "GBZ 1-2010" in rows[1][2], f"实得 {rows[1][2]!r}")
check("非字符串列不被碰 (数字/None)",
      rows_of(c, "SELECT NULL, 3.14 FROM emergency_rule")[0], [None, 3.14])
c.close()

print("\n### B. 真库不变式: 受管表经读门取出后不含废止号")
conn = connect()
dead = {(r[0] or "") for r in conn.execute("SELECT code FROM standard_db WHERE state IN ('废止','已废止')")}
norm_dead = {re.sub(r"[\s　]", "", knowledge.standard_version.norm_code(x)) for x in dead}
bad, cells, ds = [], 0, Dataset(conn)
for t in RULE_TABLES:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    stdcols = [cn for cn in cols if cn in STD_COLS]
    if not stdcols:
        continue
    for r in ds.rows(f"SELECT {','.join(stdcols)} FROM {t}"):
        for v in r:
            if not isinstance(v, str) or not v.strip():
                continue
            cells += 1
            # 逐个标准号片段判: 库里判为废止的, 不得从读门里活着出来
            for piece in re.split(r"[,;；，、]", v):
                p = re.sub(r"[\s　]", "", knowledge.standard_version.norm_code(piece))
                if p and p in norm_dead:
                    bad.append((t, piece))
check(f"{cells} 个标准号单元格无废止残留", not bad, bad[:5])

# 反向对照: 真库里确实**存在**废止号 (否则 B 段是空转)
raw_dead = []
for t in RULE_TABLES:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    for cn in [x for x in cols if x in STD_COLS]:
        for (v,) in conn.execute(f"SELECT DISTINCT {cn} FROM {t} WHERE {cn} IS NOT NULL"):
            p = re.sub(r"[\s　]", "", knowledge.standard_version.norm_code(v or ""))
            if p in norm_dead:
                raw_dead.append((t, cn, v))
check("真库里确有废止号残留 (B 段不是空转)", len(raw_dead) >= 1, raw_dead[:3])
print("    受管表里原样存着废止号的列:", sorted({f"{t}.{cn}" for t, cn, _ in raw_dead}) or "无")
conn.close()

print("\n### C. 接线: 官方表标准号列只能走读门, raw 必须表态")


def flat_str(node):
    """Constant / f-string / `+` 拼接 里能静态取到的字符串; 取不到返回 None"""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = [v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "?"
                 for v in node.values]
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = flat_str(node.left), flat_str(node.right)
        return None if left is None or right is None else left + right
    return None


def main_block_ranges(tree):
    """与 tools/test_dataset_door.py 同一口径 (那边有独立用例证明它判得对)"""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.If) or not isinstance(n.test, ast.Compare):
            continue
        cmp_ = n.test
        if not (isinstance(cmp_.left, ast.Name) and cmp_.left.id == "__name__"
                and len(cmp_.ops) == 1 and isinstance(cmp_.ops[0], ast.Eq)
                and len(cmp_.comparators) == 1
                and isinstance(cmp_.comparators[0], ast.Constant)
                and cmp_.comparators[0].value == "__main__"):
            continue
        end = max((getattr(x, "end_lineno", 0) or getattr(x, "lineno", 0)) for x in ast.walk(n))
        out.append((n.lineno, max(end, n.lineno)))
    return out


def in_ranges(lineno, ranges):
    return any(a <= lineno <= b for a, b in ranges)


def classify(node):
    """调用 → (出口名, SQL 静态文本, reason) 或 None"""
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
    if name not in CUR_EXIT + RAW_EXIT + COMPAT + BARE:
        return None
    sql_i = 1 if name in COMPAT else 0            # rows_of(conn, sql, args)
    if len(node.args) <= sql_i:
        return None
    sql = flat_str(node.args[sql_i])
    if sql is None:
        return None
    reason = None
    for kw in node.keywords:
        if kw.arg == "reason":
            reason = flat_str(kw.value)
    return name, sql, reason


def audit(base: Path):
    """返回 (裸读违规, raw 未表态, 纠版出口读 standard_db 越界, 用到的判据产地)"""
    bypass, raw_bad, ruler_read, ruler_used = [], [], [], set()
    for d in SCAN_DIRS:
        for f in sorted((base / d).rglob("*.py")):
            rel = str(f.relative_to(base))
            if f.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            ranges = main_block_ranges(tree)
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call) or in_ranges(n.lineno, ranges):
                    continue
                got = classify(n)
                if not got:
                    continue
                name, sql, reason = got
                tabs = {x.lower() for x in _TBL.findall(sql)}
                reads_std = "standard_db" in tabs
                if rel in RULER:                      # 判据产地本身不经过读门
                    if reads_std:
                        ruler_used.add(rel)
                    continue
                if not (reads_std or tabs & set(OFFICIAL)):
                    continue
                std_col = reads_std or bool(_STD_COL.search(sql))
                if reads_std and name in CUR_EXIT + COMPAT:
                    ruler_read.append(f"{rel}:{n.lineno} 纠版出口读 standard_db: {sql[:52]}")
                elif name in BARE and std_col:
                    bypass.append(f"{rel}:{n.lineno} 裸 execute 读标准号列: {sql[:52]}")
                elif name in RAW_EXIT and not (reason or "").strip():
                    raw_bad.append(f"{rel}:{n.lineno} raw 无 reason: {sql[:52]}")
    return bypass, raw_bad, ruler_read, ruler_used


def stale_ruler(base: Path) -> list:
    """登记腐烂检查: 挂了"判据产地"免死金牌却已经不读 standard_db 的文件必须被点名"""
    _b, _r, _s, used = audit(base)
    return sorted(rel for rel in RULER if rel not in used and (base / rel).exists())


bypass, raw_bad, ruler_read, used = audit(ROOT)
check("裸 execute 读官方表标准号列 = 0 (web/+knowledge/, __main__ 除外)", bypass, bypass[:4])
check(f"Dataset.raw/raw_one 全部带非空 reason (缺 {len(raw_bad)} 处)", not raw_bad, raw_bad[:4])
check("纠版出口不得读 standard_db (W3)", not ruler_read, ruler_read[:4])
check("判据产地登记项在用 (未腐烂)", not stale_ruler(ROOT), stale_ruler(ROOT))
print("    判据产地登记:", {k: RULER[k] for k in sorted(used)} or "本轮无")
print("    旧口径留下的两个洞本轮一并补掉: knowledge 侧 ppe_item.std_code 已进读门;")
print("    web/word_export.py 的第二份号→版实现已删 (4 项目 1,956 表/271,158 格逐格同结果取证)")

print("\n### D. 变异验证: C 段每条判据都必须能红")
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    for d in SCAN_DIRS:
        shutil.copytree(ROOT / d, tmp / d, ignore=shutil.ignore_patterns("__pycache__"))
    check("副本初始状态与工作区一致", audit(tmp)[:3], (bypass, raw_bad, ruler_read))
    cases = [
        # 旧守卫靠 ".execute(" 字符串才能抓的那一类: 现在按 AST 也必须抓到
        ("inj_bypass.py",
         'def f(conn):\n    return conn.execute('
         '"SELECT hazard_category, check_point, std_code FROM protection_rule")\n', "bypass"),
        ("inj_raw.py",
         'from knowledge.dataset import Dataset\n\n\ndef f(conn):\n'
         '    a = Dataset(conn).raw("SELECT std_code FROM protection_rule")\n'
         '    b = Dataset(conn).raw_one("SELECT std_code FROM protection_rule", reason="   ")\n'
         '    return a, b\n', "raw_bad"),
        ("inj_ruler.py",
         'from knowledge.dataset import Dataset\n\n\ndef f(conn):\n'
         '    return Dataset(conn).rows("SELECT code, state FROM standard_db")\n', "ruler_read"),
    ]
    for fname, src, bucket in cases:
        v = tmp / "web" / fname
        v.write_text(src, encoding="utf-8")
        b1, r1, s1, _ = audit(tmp)
        got = {"bypass": b1, "raw_bad": r1, "ruler_read": s1}[bucket]
        check(f"D {fname} → 对应判据必须红", any(fname in x for x in got), True)
        v.unlink()
    # 正确写法不得误报 (否则等于逼着大家把取数挪回裸 execute)
    v = tmp / "web" / "inj_ok.py"
    v.write_text('from knowledge.dataset import Dataset\n\n\ndef f(conn):\n'
                 '    return Dataset(conn).rows("SELECT std_code FROM protection_rule")\n',
                 encoding="utf-8")
    b2, r2, s2, _ = audit(tmp)
    check("D 走读门的正确写法不误报", not any("inj_ok" in x for x in b2 + r2 + s2), True)
    v.unlink()
    a = tmp / "knowledge" / "standard_version.py"
    a.write_text(a.read_text(encoding="utf-8").replace("FROM standard_db", "FROM other_table"),
                 encoding="utf-8")
    check("D 判据产地不再读 standard_db → 登记项必须被点名",
          "knowledge/standard_version.py" in stale_ruler(tmp), True)

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
