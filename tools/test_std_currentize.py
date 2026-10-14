"""守卫: 官方数据层读出的标准号必须"读出即纠版" (rows_of 单点兜底)

背景 (2026-10-15): 版本戳首跑抓到生产 emergency_rule/protection_rule 仍挂废止标准号
  (GBZ 2.2—2007 / GB/T 38144.1—2019), 本地 oel_limit.source_standard 也存着 GBZ 2.2—2007。
  部署刻意排除 data/ → 不能假设两份库都被清洗过, 所以兜底放在**读出点**:
  web.standard_version.rows_of 是唯一入口, 表格里印的依据标准号先过一遍现行版替换。

本文件钉三层:
  A. 合成库判定表: 规则行存废止号 → 读出来必须换成现行版 (不依赖任何本地/生产数据)
  B. 真库不变式: 受管表经 rows_of 读出后, 不再含任何"库内判为废止"的标准号
  C. 接线检查: web/ 里读这些规则表的点必须走 rows_of; knowledge/project_assess 的
     ppe_item 读取是**有意的例外** (knowledge 不反向依赖 web), 故改为监控其数据 ——
     一旦出现废止号就让它红, 而不是留一个没人看的注释。
用法: python tools/test_std_currentize.py
"""
import inspect
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import knowledge.project_assess                                    # noqa: E402
import web.advice_gen                                              # noqa: E402
import web.llm_draft                                               # noqa: E402
import web.section_filler                                          # noqa: E402
import web.standard_version                                        # noqa: E402
import web.structure_data                                          # noqa: E402
import web.table_skeleton                                          # noqa: E402
from knowledge.oel import connect                                  # noqa: E402
from web.standard_version import rows_of                           # noqa: E402

RULE_TABLES = ("emergency_rule", "protection_rule", "management_rule",
               "surveillance_rule", "standard_ref", "oel_limit")
STD_COLS = ("std_code", "source_standard", "code")

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
c.execute("CREATE TABLE emergency_rule (scenario TEXT, item TEXT, require TEXT, std_code TEXT, clause TEXT)")
c.executemany("INSERT INTO emergency_rule VALUES (?,?,?,?,?)", [
    ("高温中暑应急", "防暑降温", "高温作业场所应有防暑降温措施", "GBZ 2.2—2007", "表8"),
    ("一般场景", "卫生设计", "按 GBZ 1-2010 执行", "GBZ 1-2010", "5.2"),
])
rows = rows_of(c, "SELECT scenario, item, require, std_code, clause FROM emergency_rule")
check("废止号 GBZ 2.2—2007 被换成现行版", "2007" not in rows[0][3], f"实得 {rows[0][3]!r}")
check("替换后是 GBZ 2.2—2019", rows[0][3] == "GBZ 2.2—2019", f"实得 {rows[0][3]!r}")
check("非废止号原样保留", rows[1][3] == "GBZ 1-2010", f"实得 {rows[1][3]!r}")
check("同行文本列里嵌的废止号也被纠 (require 列)",
      "GBZ 1-2010" in rows[1][2], f"实得 {rows[1][2]!r}")
check("非字符串列不被碰 (数字/None)",
      rows_of(c, "SELECT NULL, 3.14 FROM emergency_rule")[0], [None, 3.14])
c.close()

print("\n### B. 真库不变式: 受管表读出后不含废止号")
conn = connect()
dead = {(r[0] or "") for r in conn.execute("SELECT code FROM standard_db WHERE state IN ('废止','已废止')")}
norm_dead = {re.sub(r"[\s　]", "", web.standard_version.norm_code(x)) for x in dead}
bad = []
cells = 0
for t in RULE_TABLES:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    stdcols = [cn for cn in cols if cn in STD_COLS]
    if not stdcols:
        continue
    for r in rows_of(conn, f"SELECT {','.join(stdcols)} FROM {t}"):
        for v in r:
            if not isinstance(v, str) or not v.strip():
                continue
            cells += 1
            # 逐个标准号片段判: 库里判为废止的, 不得从 rows_of 里活着出来
            for piece in re.split(r"[,;；，、]", v):
                p = re.sub(r"[\s　]", "", web.standard_version.norm_code(piece))
                if p and p in norm_dead:
                    bad.append((t, piece))
check(f"{cells} 个标准号单元格无废止残留", not bad, bad[:5])

# 反向对照: 真库里确实**存在**废止号 (否则 B 段是空转)
raw_dead = []
for t in RULE_TABLES:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    for cn in [x for x in cols if x in STD_COLS]:
        for (v,) in conn.execute(f"SELECT DISTINCT {cn} FROM {t} WHERE {cn} IS NOT NULL"):
            p = re.sub(r"[\s　]", "", web.standard_version.norm_code(v or ""))
            if p in norm_dead:
                raw_dead.append((t, cn, v))
check("真库里确有废止号残留 (B 段不是空转)", len(raw_dead) >= 1, raw_dead[:3])
print("    受管表里原样存着废止号的列:", sorted({f"{t}.{cn}" for t, cn, _ in raw_dead}) or "无")
conn.close()

print("\n### C. 接线: 读规则表的**标准号列**必须走 rows_of")
# 只盯"会印进报告的标准号列": 同表其他列 (category / value / cas) 与兜底无关,
# 按整表一刀切会把正确代码也报成违规 (上一版就这么误判了 9 行)。
_STD_COL = re.compile(r"\b(std_code|source_standard)\b")
_RULE_TBL = re.compile(r"FROM\s+(emergency_rule|protection_rule|management_rule|standard_ref|oel_limit)\b")


def violations(src: str) -> list[str]:
    out = []
    lines = src.splitlines()
    for i, ln in enumerate(lines):
        if ".execute(" not in ln:
            continue
        stmt = " ".join(lines[i:i + 3])
        if not _RULE_TBL.search(stmt):
            continue
        need = bool(_STD_COL.search(stmt)) or ("standard_ref" in stmt and re.search(r"\bcode\b", stmt))
        if need:
            out.append(ln.strip()[:78])
    return out


for mod in (web.section_filler, web.advice_gen, web.llm_draft,
            web.structure_data, web.table_skeleton):
    check(f"{mod.__name__} 规则表标准号列读取经 rows_of",
          not violations(inspect.getsource(mod)), violations(inspect.getsource(mod)))

# knowledge/project_assess 的 ppe_item 读取是有意例外 (layering), 改为监控其数据
conn2 = connect()
ppe_dead = []
for (v,) in conn2.execute("SELECT DISTINCT std_code FROM ppe_item WHERE std_code IS NOT NULL"):
    p = re.sub(r"[\s　]", "", web.standard_version.norm_code(v or ""))
    if p in norm_dead:
        ppe_dead.append(v)
src_pa = inspect.getsource(knowledge.project_assess)
if ppe_dead:
    check("ppe_item.std_code 无废止号 (否则须给 knowledge 侧加兜底)", [], ppe_dead[:3])
else:
    check("ppe_item.std_code 当前无废止号 → knowledge 侧例外成立", "rows_of" not in src_pa, True)
    print("    注: knowledge/project_assess.py 直读 ppe_item.std_code; 一旦该列出现废止号, "
          "本检查会红, 届时须给它加同款兜底 (不让 knowledge 反向依赖 web 是刻意的)")
conn2.close()

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
