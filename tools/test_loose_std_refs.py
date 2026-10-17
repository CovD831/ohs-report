#!/usr/bin/env python3
"""tools/loose_std_refs.py 自己的判据 (#44)

规矩 (与全仓一致): 每条被当证据的检查, 都配一条"必然能红、且每次都红"的变异或阳性对照。

为什么要单独一支: #44 的枚举器数的是"尺子看不见的形状"。这类尺子最容易**静默数出 0** ——
上一轮调研里 `grep -o "2\\.2[-—]2019"` 把全角破折号放进方括号字符类, 数出 0, 差点被当成
"成品里没有这种写法"。所以本文件的重点不是"成品里有多少条" (那是取景, 换环境会变),
而是这五件事:
  A. 夹具必须**能红**: 合成一份含各类形态的输入, 逐类断言捕获数不为 0、且逐类断言真掉在网外;
  B. 规范形不变式: 只改空白 → 归因/网内外/规范形全不变; 只改大小写 → 规范形不变,
     原因集**只**允许多出 '大小写' 一项, 而净可见性必须翻转 (这才是 #44 的盲区);
  C. 全角破折号专项回归: `—` 与 `-` 必须分开记账, 并给出"折成 ASCII 字符类就静默吞掉全角"的变异;
  D. 味道检查: 判据不许长成"已知写法清单" (自实现一份 whitelist_smells, 口径照
     tools/test_det_rows.py), 并用一段白名单式假代码当阳性对照 —— 证明这把尺子自己也量得出缺陷;
  E. 只读与路径: 不给验收库开可写连接、不硬拼 data/ 路径 (复用 tools/chain_audit 的位点尺子);
  F. 真实取景: 内部一致性 (网内+网外=总数、主因相加=总数) + 每一类都有分母可查 +
     收尾对表真实 data/ 的文件清单与库文件字节。

用法: .venv/bin/python tools/test_loose_std_refs.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import tools.chain_audit as CA                                      # noqa: E402  位点尺子 (纯 AST, 不执行)
import tools.loose_std_refs as L                                    # noqa: E402  被测对象
from knowledge.oel import connect_ro                                # noqa: E402  只读 (本文件在 tools/test_*.py 扫描范围内)
from knowledge.paths import DATA_DIR, DB_PATH                        # noqa: E402
from knowledge.standard_version import STD_SEPS, std_key            # noqa: E402

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


def db_stat(p: Path = DB_PATH):
    s = p.stat()
    return (s.st_size, s.st_mtime_ns)


DATA_BEFORE = {p.name for p in Path(DATA_DIR).iterdir()}
DB_BEFORE = db_stat()

# ─────────────────────────────────────────────────────────────────────────────
print("### A. 夹具必须能红: 合成输入, 逐类断言捕获数不为 0")
conn = connect_ro()
BASES, GENERAL, N_ROWS = L.official_backing(conn)
conn.close()
check("A0 前提对表: standard_db 读到行 (官方层结构是 P4 总称判据的分母)", N_ROWS > 0, True)
check("A0 前提对表: 存在'库里只有分部版的族根'≥1 个 (否则总称那一支是空测)", len(GENERAL) > 0, True)

PH = L.placeholder_witness()        # 探针字符由判据派生, 不在这里写死
OFF = L.off_class_conn_witness()    # 候选连接符类里 STD_SEPS 没收的那个 (实测 U+007E '~')
check("A0 前提对表: 占位符判据有见证字符", bool(PH), True)
check("A0 前提对表: off-class 连接符有见证字符, 且它确实不在 STD_SEPS 里",
      (bool(OFF), OFF in L.SEP_SET), (True, False))

ROOT_TOKEN, _gen_root = None, sorted(GENERAL)[0]
for _f in sorted(L.STD_COLLECT_FAMS, key=len, reverse=True):
    if _gen_root.startswith(_f):
        ROOT_TOKEN = f"{_f} {_gen_root[len(_f):]}"
        break
check(f"A0 前提对表: 从库里取到一个族根形态 ({ROOT_TOKEN!r})", bool(ROOT_TOKEN), True)

FIXTURE = [
    "本项目的现场检测按 GBZ 9.9 的要求执行。",                    # 无年份
    "检测结果按 gbz 9.9—2019 判定。",                             # 大小写 (带年份仍看不见)
    f"限值依据 GBZ 9.9.{PH} 规定。",                              # 占位分部
    "GBZ 9.1—9.2 两部分合并执行。",                                # 区间写法 (尾部还是分部号)
    "设备按 GBZ 9.1/9.2—2019 选型。",                             # 一个 token 并列多分部
    f"{ROOT_TOKEN} 的现行版另行确定。",                             # 库里只有分部版而正文写族根
    f"噪声限值按 GBZ 9.9{OFF}2019 复核。",                         # off-class 连接符 (波浪号)
    "高温限值按 GBZ 9.9～2007 复核。",                              # 全角波浪号当连接符
    "照度按 GBZ 9.9 2019 执行。",                                  # 号与年之间只有空白
    "照明按 GBZ\n9.9—2019 执行。",                                 # 词内断字 (号内换行)
    "通风按 GBZ 9.9—94 执行。",                                    # 两位年 (_YEAR_TAIL 只认 19xx/20xx)
    "防寒按 GBZ 9.9—9999 执行。",                                  # 四位但不在 19xx/20xx
    "正常写法 GBZ 9.9—2019、GBZ9.9-2019 与 GBZ 9.9 - 2019 三行。",  # 在册对照 (含空格变体)
]
fx = L.scan([("fixture", FIXTURE)], BASES, GENERAL)
blind = ("case", "series", "sep", "no_year", "other")
PRIMARY = [c for c in L.CATS if c != "break"]        # 断字不入主因桶 (它是块边界上的性质)


def hits_of(stats, cat):
    return [h for h in stats["hits"] if h["primary"] == cat]


for cat in blind + ("in_grammar",):
    check(f"A1 {L.LABEL[cat]}: 夹具里这一类被捕获 (计数>0)", fx["primary"].get(cat, 0) > 0, True)
for cat in blind:
    off_net = [h for h in hits_of(fx, cat) if not (h["collect"] or h["ref"])]
    check(f"A2 {L.LABEL[cat]}: 至少一条确实掉在两把尺子之外 (网外, 不是'看得见但没数')",
          len(off_net) > 0, True)
# A2b 断字: 三种断点形状都要响, 而且**只有劈进数字内部的**才掉在网外 (旧尺子的 \s* 跨得过另两类)
brk_fx = L.scan([("f-num", ["按 GBZ 9.9.\n9—1999 执行。"]),          # 劈在数字内部
                 ("f-sep", ["按 GBZ 9.9—", "1999 执行。"]),          # 号与年之间
                 ("f-pre", ["按 GBZ", " 9.9—1999 执行。"])],         # 族前缀与数字之间
                BASES, GENERAL)
check("A2b 断字: 三种断点各抓到一条 (计数=3)", brk_fx["breaks"]["total"], 3)
check("A2b 断字: 三种子形状都出现 (不是一类重复数三次)", len(brk_fx["breaks"]["sub"]), 3)
check("A2b 断字: 只有'数字/年份内部'那一支掉在网外 —— 另两支两把尺子的 \\s* 跨得过 (实测, 不是猜)",
      (brk_fx["breaks"]["out_net"], brk_fx["breaks"]["in_net"]), (1, 2))
check("A3 主因桶相加 = 号形命中数 (断字不入这一账)", sum(fx["primary"].get(c, 0) for c in PRIMARY),
      fx["trigger"].get("head", 0))
check("A4 原因计数 ≥ 主因计数 (一条命中可有多因, 例如 'gbz 9.9' 同时是小写+无年份)",
      all(fx["flags"].get(c, 0) >= fx["primary"].get(c, 0) for c in L.CATS), True)
check("A5 在册形态被现有尺子看见 (网内>0)", fx["cat_net"]["in_grammar"][0] > 0, True)
check("A6 夹具分母非零 (号形命中与字符都数到了)",
      (fx["trigger"].get("head", 0) > 0, fx["denom"].get("chars", 0) > 0), (True, True))
print(f"    (夹具分母: 号形命中 {fx['trigger'].get('head', 0)} 条 / 字符 {fx['denom'].get('chars', 0)} · "
      f"网外 {fx['net_out']} 条 / 网内 {fx['net_in']} 条 · 主因 " +
      " ".join(f"{L.LABEL[c]}={fx['primary'].get(c, 0)}" for c in L.CATS) + ")")


def one(text):
    """单条文句 → 第一条命中的完整判据结果 (供 B/C 节复用同一把尺子)。"""
    return L.scan([("t", [text])], BASES, GENERAL)["hits"][0]


# ─────────────────────────────────────────────────────────────────────────────
print("\n### B. 规范形不变式: 只改空白 / 只改大小写")
for a, b in [("GBZ9.9—2019", "GBZ 9.9 — 2019"), ("GBZ9.9-2019", "GBZ 9.9 - 2019"),
             ("GBZ9.1—9.2", "GBZ 9.1 — 9.2"), ("gbz9.9～2019", "gbz 9.9 ～ 2019")]:
    ha, hb = one(a), one(b)
    check(f"B1 只改空白 [{a!r} ↔ {b!r}]: 主因桶/原因集/净可见性 三项都不变",
          (ha["primary"], set(ha["reasons"]), ha["collect"] or ha["ref"]),
          (hb["primary"], set(hb["reasons"]), hb["collect"] or hb["ref"]))
    check(f"B1 只改空白: 规范形 std_key 也必须是同一个号 (现有尺子的口径)", std_key(a), std_key(b))
for a, b in [("GBZ 9.9—2019", "gbz 9.9—2019"), ("GBZ/T 9.9-2019", "gbz/t 9.9-2019")]:
    ha, hb = one(a), one(b)
    check(f"B2 只改大小写 [{a!r} ↔ {b!r}]: 规范形不变 (并成一个号)", std_key(a), std_key(b))
    blind_a, blind_b = set(ha["reasons"]) - {"in_grammar"}, set(hb["reasons"]) - {"in_grammar"}
    check(f"B2 只改大小写: 原因集差异恰好只有 'case' 一项 (放宽采集, 不放宽判定)",
          (blind_b - blind_a, blind_a - blind_b), ({"case"}, set()))
    check(f"B2 只改大小写: 净可见性必须翻转 (大写在册 / 小写掉网外) —— 这就是 #44 的盲区",
          (bool(ha["collect"] or ha["ref"]), bool(hb["collect"] or hb["ref"])), (True, False))
check("B3 '空白当连接符' 与 '波浪号当连接符' 是两种不同子形状 (都数得出来, 没被合并)",
      sorted(s for s in fx["sub"] if "空白" in s or "连接符" in s) != [], True)
check("B4 四位但非 19xx/20xx 的年份: 采集尺看得见 (它放宽到 \\d{4}) 而改写尺看不见 —— 两问分开答",
      [(h["collect"], h["ref"]) for h in fx["hits"] if h["tail"] == "9999"], [(True, False)])
check("B5 两位年则是**两把尺子都看不见** (采集尺也要求四位) —— 与 B4 是不同的一支",
      [(h["collect"], h["ref"]) for h in fx["hits"] if h["tail"] == "94"], [(False, False)])

# ─────────────────────────────────────────────────────────────────────────────
print("\n### C. 全角破折号专项回归 (上轮事故: 字符类把 —— 静默吞成 0)")
FULL, HALF = "—", "-"
check("C0 前提对表: 全角 em dash 与半角 hyphen 都在 STD_SEPS 里 (所以它们**不是**盲区)",
      (FULL in L.SEP_SET, HALF in L.SEP_SET), (True, True))
sep_st = L.scan([("only-full", [f"依据 GBZ 9.9{FULL}2019 与 GBZ 9.1{FULL}2007 执行。"]),
                 ("only-half", [f"依据 GBZ 9.9{HALF}2019 与 GBZ 9.1{HALF}2007 执行。"])],
                BASES, GENERAL)
check("C1 全角/半角**分开记账**: 两个键、各 2 次 (合并成一个键就看不见用了哪种破折号)",
      sorted(sep_st["conns"].items()), sorted([(HALF, 2), (FULL, 2)]))
check("C2 键数 = 2 而不是 1", len(sep_st["conns"]), 2)
check("C3 合计连接符数 = 号↔年槽位数 (没有一个被字符类吃掉)", sum(sep_st["conns"].values()), 4)
ascii_fold = set(STD_SEPS.encode("ascii", "ignore").decode("ascii"))
check("变异C4 STD_SEPS 折成 ASCII 字符类: 半角还在 (折叠看起来'能用')", HALF in ascii_fold, True)
check("变异C4 同一折叠下全角 em dash 消失 → 全角那一类**静默数出 0**, 正是上轮踩的坑",
      FULL in ascii_fold, False)
check("变异C4 对照: 生产判据 is_std_sep(全角) 仍为真 —— 判定走集合成员测试, 没被字符类污染",
      L.is_std_sep(FULL), True)
merged = {std_key("GBZ 9.9" + ch + "2019") for ch in (FULL, HALF)}
check("变异C5 若按规范形给连接符记账, 两种破折号塌成 1 个键 → 本工具刻意不这么做", len(merged), 1)

# ─────────────────────────────────────────────────────────────────────────────
print("\n### D. 味道检查: 判据不许长成'已知写法清单' (口径照 tools/test_det_rows.py 自实现一份)")
DECISION_FUNCS = ("classify", "tail_slot", "primary_of", "_one_hit", "scan", "self_probe",
                  "break_at", "formed_codes", "probe_cases")
SELF_FUNCS = ("one", "hits_of", "docstring_lines", "emitted_constants", "whitelist_smells", "check")
MOD_SRC = (ROOT / "tools" / "loose_std_refs.py").read_text(encoding="utf-8")
SELF_SRC = (ROOT / "tools" / "test_loose_std_refs.py").read_text(encoding="utf-8")


def docstring_lines(tree):
    """docstring 占的行 —— 里面的举例是**取证记录**, 不是发射点 (同 test_det_rows 的口径)。"""
    out = set()
    nodes = [tree] + [n for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    for node in nodes:
        b = getattr(node, "body", [])
        if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                and isinstance(b[0].value.value, str):
            d = b[0].value
            out.update(range(d.lineno, (d.end_lineno or d.lineno) + 1))
    return out


def emitted_constants(src):
    tree = ast.parse(src)
    docs = docstring_lines(tree)
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.lineno not in docs]


TEXT_NAMES = ("raw", "num", "fam_part", "tail", "sep", "base", "text", "block", "joined")


def constants_of(nodes, docs):
    """给定 AST 节点集里的非 docstring 字符串常量 (行号, 值)。"""
    out = []
    for node in nodes:
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                    and sub.lineno not in docs):
                out.append((sub.lineno, sub.value))
    return out


def whitelist_smells(src, funcs=DECISION_FUNCS, code_literals="module"):
    """三种"已知写法清单"形状 (比较**方向**是判据的一部分, 别把形状测试当成清单):
    ① 字面量**本身就是一枚标准号** —— 尺子就用被测模块自己的 HEAD: 它认得出的东西正是某种具体
       写法, 把它写进判定代码就是把清单当判据。`code_literals`: 'module' 查全模块 (生产代码用这把),
       'funcs' 只查判定函数体 (判据文件要拿夹具喂尺子, 字面量是**输入**不是判据);
    ② 判定函数里 ≥2 个汉字串的 list/set/tuple 字面量 (词表当判据);
    ③ 判定函数里把**被扫文本**与字面量做相等比较 (`num == <一枚号>`), 或把它 `in` 进一个**由字面量
       绑来的清单** (`raw in known`, 且 known 在本函数里 = [...]) —— 两个方向都要判:
       `"/" in num` 是形状测试 (字面量 in 文本), `base in general` 是数据派生成员测试 (general 来自
       standard_db), 都不是清单; 把 `x == "某个具体号"` 写进判据才是。"""
    smells = []
    tree = ast.parse(src)
    docs = docstring_lines(tree)
    func_nodes = [n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name in funcs]
    if code_literals == "module":
        pool = emitted_constants(src)
    else:
        pool = constants_of(func_nodes, docs)
    for lineno, val in pool:
        if L.HEAD.search(val):
            smells.append(f"标准号形状的字面量 (行 {lineno}): {val[:40]!r}")
    for node in func_nodes:
        # 本函数里"由字面量集合绑来的名字" —— `x in known` (known=[...]) 才是清单, `x in general`
        # (从库里派生的集合) 不是; 不做这个区分就会把数据派生的成员测试误判成白名单。
        containers = {t.id for a in ast.walk(node) if isinstance(a, (ast.Assign, ast.AnnAssign))
                      for t in ([a.target] if isinstance(a, ast.AnnAssign) else a.targets)
                      if isinstance(t, ast.Name)
                      and isinstance(getattr(a, "value", None), (ast.List, ast.Set, ast.Tuple))}
        for sub in ast.walk(node):
            if isinstance(sub, (ast.List, ast.Set, ast.Tuple)):
                strs = [e.value for e in sub.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if len([s for s in strs if any("\u4e00" <= c <= "\u9fff" for c in s)]) >= 2:
                    smells.append(f"{node.name}: 汉字串集合字面量 (词表当判据) → {strs[:3]}")
            if isinstance(sub, ast.Compare) and len(sub.ops) == 1:
                lit = any(isinstance(x, ast.Constant) and isinstance(x.value, str)
                          for x in [sub.left, *sub.comparators])
                txt_l = isinstance(sub.left, ast.Name) and sub.left.id in TEXT_NAMES
                txt_r = any(isinstance(x, ast.Name) and x.id in TEXT_NAMES for x in sub.comparators)
                if isinstance(sub.ops[0], (ast.Eq, ast.NotEq)) and lit and (txt_l or txt_r):
                    smells.append(f"{node.name}: 字面量与被扫文本相等比较 → {ast.unparse(sub)[:60]}")
                elif (isinstance(sub.ops[0], (ast.In, ast.NotIn)) and txt_l and not lit
                      and (isinstance(sub.comparators[0], (ast.List, ast.Set, ast.Tuple))
                           or (isinstance(sub.comparators[0], ast.Name)
                               and sub.comparators[0].id in containers))):
                    smells.append(f"{node.name}: 被扫文本被 in 进一个清单 → {ast.unparse(sub)[:60]}")
    return smells


check("D1 生产模块 tools/loose_std_refs.py: 三种白名单形状全部为 0", whitelist_smells(MOD_SRC), [])
check("D2 本判据文件的判定函数同样干净 (夹具字面量是**输入**, 不许流进判据函数)",
      whitelist_smells(SELF_SRC, funcs=SELF_FUNCS, code_literals="funcs"), [])
FAKE = (
    'def classify(fam_part, num, sep, tail, gap, base, general):\n'
    '    if num == "GBZ 2.1":\n'
    '        return ["no_year"], []\n'
    '    known = ["gbz/t 192.1-2007", "GBZ1\uff5e2013"]\n'
    '    words = ["系列标准", "等各部分", "见附录"]\n'
    '    if raw in known:\n'
    '        return ["sep"], []\n'
    '    return [], []\n')
fake = whitelist_smells(FAKE)
check("D3 阳性对照: 白名单式修法喂给同一把尺子 → 号形状字面量 / 词表清单 / 相等比较 / in 清单 全点名",
      (len(fake) >= 4, any("标准号形状" in f for f in fake), any("词表当判据" in f for f in fake),
       any("相等比较" in f for f in fake), any("in 进一个清单" in f for f in fake)),
      (True, True, True, True, True))
DOC0 = (ast.parse(MOD_SRC).body[0].value.value or "")
check("D4 尺子有牙: 生产模块的 docstring 里举的例**确实**被认成号形状 (所以 D1 的放行是按形状判的, "
      "不是没匹配到东西)", bool(L.HEAD.search(DOC0)), True)
check("D5 而发射点 (非 docstring 常量) 里一枚都没有",
      [v for _ln, v in emitted_constants(MOD_SRC) if L.HEAD.search(v)], [])

# ─────────────────────────────────────────────────────────────────────────────
print("\n### E. 只读与路径: 不给验收库开可写连接, 不硬拼 data/ 路径")
mine = ("tools/loose_std_refs.py", "tools/test_loose_std_refs.py")
check("E1 chain_audit 的硬拼路径位点尺子: 本次交付的两个文件 0 处",
      [f"{rel}:{ln} {expr[:40]}" for rel, ln, expr in CA._data_path_sites(ROOT) if rel in mine], [])
for rel in mine:
    src = (ROOT / rel).read_text(encoding="utf-8")
    bad = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            fn = ast.unparse(node.func)
            leaf = fn.split(".")[-1]
            if leaf in ("connect", "_conn") or (leaf == "connect_ro" and node.args):
                bad.append(f"{rel}:{node.lineno} {fn}")
    check(f"E2 {rel} 没有可写工厂调用 (connect/_conn), connect_ro 也没被塞别的路径", bad, [])
    check(f"E3 {rel} 从 knowledge.paths 取路径 (单一产地, 认 OHS_DATA_DIR)",
          "from knowledge.paths import" in src, True)
check("E4 被测模块不 import sqlite3 (读数一律走 knowledge.oel.connect_ro)",
      "import sqlite3" in MOD_SRC, False)

# ─────────────────────────────────────────────────────────────────────────────
print("\n### F. 真实取景 (只读本地成品 + project.data; 数字换环境会变, 当分母不当结论)")
sources = L.docx_sources(DATA_DIR)
n_docx = len(sources)
check("F1 前提对表: 成品份数 = DATA_DIR 下 report_*.docx 的实际个数 (计划说 68, 自己复算)",
      n_docx, len(list(Path(DATA_DIR).glob("report_*.docx"))))
conn = connect_ro()
try:
    sources += L.project_sources(conn)
    bases, general, n_rows = L.official_backing(conn)
    guard_codes, guard_hits = set(), 0
    from web.std_guard import scan_superseded
    for name, blocks in sources:
        if name.startswith("report_"):
            for it in scan_superseded("\n".join(blocks), conn):
                guard_codes.add(it["norm"])
                guard_hits += 1
finally:
    conn.close()
stats = L.scan(sources, bases, general)
probes = L.self_probe(bases, general)
check("F2 尺子自证: 六类盲区探针全部能红 (任一探针红 = 尺子断了, 那批计数不可信)",
      sorted(k for k, v in probes.items() if v), sorted(probes))
check("F3 内部一致性: 网内 + 网外 = 命中总数", stats["net_in"] + stats["net_out"], len(stats["hits"]))
check("F4 内部一致性: 主因桶相加 = 号形命中数 (断字不入这一账)",
      sum(stats["primary"].get(c, 0) for c in PRIMARY), stats["trigger"].get("head", 0))
check("F4b 内部一致性: 断字的 网内+网外 = 断字总数",
      stats["breaks"]["in_net"] + stats["breaks"]["out_net"], stats["breaks"]["total"])
check("F5 在册形态的网内数 ≥ 在册主因数 (在册却没被尺子看见的形态要单独追)",
      stats["cat_net"]["in_grammar"][0] >= stats["primary"].get("in_grammar", 0), True)
check("F6 每类计数都有分母可查 (号形命中>0 且字符>0) → 本批的 0 不是'没扫到'",
      (stats["trigger"].get("head", 0) > 0, stats["denom"].get("chars", 0) > 0), (True, True))
print(f"    真实份数 {n_docx} 份 docx · project.data {len(sources) - n_docx} 行 · "
      f"块 {stats['denom'].get('blocks', 0):,} · 字符 {stats['denom'].get('chars', 0):,} · "
      f"号形命中 {stats['trigger'].get('head', 0):,} 条 · standard_db {n_rows:,} 行")
print("    主因桶: " + "  ".join(f"{L.LABEL[c]}={stats['primary'].get(c, 0):,}" for c in PRIMARY)
      + f"  |  {L.LABEL['break']}(单独记账)={stats['breaks']['total']:,} "
        f"(网外 {stats['breaks']['out_net']:,} / 网内 {stats['breaks']['in_net']:,})")
print(f"    网外 = {stats['net_out']:,} 条 / 网内 = {stats['net_in']:,} 条 "
      f"(采集尺 {stats['net_in_collect']:,} · 废止尺 {stats['net_in_ref']:,} · "
      f"std_guard 真点名 {len(guard_codes)} 个号 / {guard_hits} 处)")
print("    连接符实测: " + "  ".join(f"U+{ord(c):04X}×{n}" for c, n in sorted(
    stats["conns"].items(), key=lambda x: -x[1])))
for line in L.zero_notes(stats):
    print("    " + line.strip())

DATA_AFTER = {p.name for p in Path(DATA_DIR).iterdir()}
DB_AFTER = db_stat()
check("F7 收尾: 真实 data/ 没有多出任何文件 (没留 -wal/-shm, 没落缓存)",
      sorted(DATA_AFTER - DATA_BEFORE), [])
if DB_AFTER != DB_BEFORE:
    print(f"      ⚠ ohs.db 的 (size, mtime_ns) 在本轮内变了 {DB_BEFORE} → {DB_AFTER}; "
          f"本进程按 E1/E2 静态可证没开任何可写连接 → 差值来自**别的进程并发写库**, 不是本工具。")
check("F8 收尾: 库文件 ohs.db 的 (size, mtime_ns) 与开跑前一致 (本工具一行都没写)",
      DB_AFTER, DB_BEFORE)

print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 ===")
sys.exit(1 if n_fail else 0)
