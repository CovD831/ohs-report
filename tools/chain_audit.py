"""验收链"只读取数"的接线判据 (单一真源, 被多条守卫共用)

为什么单独成模块: 判据写在两处 (tools/test_acceptance_readonly.py 与
tools/test_gate_sensitivity.py 第 7 节) 就会漂移 —— 本轮 D2 正是要把后者那条
**字符串级假断言** (`"UPDATE " not in src`) 换成这里的行为/AST 判据。
本模块**不执行任何检查**, 只提供四把尺子:
  · `chain_write_sites()`    —— 验收链的 9 个取数函数里不许有写动作 (D2)
  · `guard_write_opens()`    —— 守卫脚本自己 (部署名单 ∪ 全部 tools/test_*.py) 不许给验收库开
                                可写连接 (S1c″ D4′; 容器里 ROOT/data 就是挂载进去的生产库)
  · `delivery_chain_gaps()`  —— 部署名单 ↔ MD5_FILES ↔ Dockerfile(镜像) 三方对表
                                (D4′ 收尾实测: 镜像里没有 quick_deploy.sh 时, 上一把尺子
                                连"范围是哪些脚本"都读不到 → 守卫套在容器里整条崩)
  · `hardcoded_data_paths()` —— 全仓(tools/web/knowledge)自己拼数据目录路径的点:
                                链内链外**都点名判红**, 正当的必须登记 (PATH_EXEMPT /
                                LEGACY_PATH_EXEMPT), 登记腐烂也要点名 (S1c″ D4″ 起,
                                之前"链外只报数"没人对账, 账会漂)

口径 (与 tools/db_writers.py 的读写分家同一条线):
  · 名单 CHAIN_READS 只列"取数函数" —— 判据管的是"读的地方不许写",
    不是"这几个文件一行都别写" (projects_db 的 create_project/_conn 本来就该写)。
  · 违规 = 函数体里出现 ①写侧工厂 `knowledge.oel.connect()` (含别名/`oel.connect`)
    ②可写工厂 `_conn()` ③写类 SQL 字面量 (INSERT/UPDATE/DELETE/CREATE/ALTER/DROP)。
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHAIN_READS = [
    ("tools/export_fingerprint.py", "export"),          # 主验收门: 取数+导出
    ("tools/export_e2e.py", "build_project"),           # 与 app 同源的取数组装
    ("tools/export_e2e.py", "main"),
    ("web/word_export.py", "export_docx"),
    ("knowledge/project_assess.py", "assess_project"),
    ("web/projects_db.py", "get_project"),
    ("web/projects_db.py", "get_project_data"),
    ("web/projects_db.py", "list_reports"),
    ("web/projects_db.py", "list_all_reports"),
]
WRITE_SQL_WORDS = ("insert into", "update ", "delete from", "create table",
                   "alter table", "drop table")


def _oel_names(tree: ast.AST) -> tuple[set, set]:
    """(oel 模块别名, `from knowledge.oel import connect` 的导入名) —— 别名也要认"""
    mods, direct = {"oel"}, set()
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


def chain_write_sites(base: Path = ROOT) -> list[str]:
    """返回验收链取数函数里的写动作清单 (空 = 链是只读的)"""
    out: list[str] = []
    for rel, fname in CHAIN_READS:
        p = Path(base) / rel
        if not p.exists():
            out.append(f"{rel}: 文件不存在")
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        mods, direct = _oel_names(tree)
        target = next((n for n in tree.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and n.name == fname), None)
        if target is None:
            out.append(f"{rel}: 找不到函数 {fname}() (链改名了, 判据必须跟着改)")
            continue
        for n in ast.walk(target):
            if isinstance(n, ast.Call):
                f = n.func
                name = f.attr if isinstance(f, ast.Attribute) else (
                    f.id if isinstance(f, ast.Name) else "")
                owner = f.value.id if (isinstance(f, ast.Attribute)
                                       and isinstance(f.value, ast.Name)) else None
                if name == "connect" and (owner in mods or name in direct):
                    out.append(f"{rel}:{n.lineno} {fname}() 里调用写侧工厂 connect()")
                elif name == "_conn":
                    out.append(f"{rel}:{n.lineno} {fname}() 里调用可写工厂 _conn()")
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                low = " ".join(n.value.lower().split())
                for w in WRITE_SQL_WORDS:
                    if w in low:
                        out.append(f"{rel}:{n.lineno} {fname}() 里有写类 SQL: {w}")
                        break
    return out


# 指向"本进程数据目录里的库"的写法。名字面只覆盖**写在实参里**的形状;
# `db = ROOT/"data"/"ohs.db"` 那种先把路径存进变量、再把变量喂给 connect 的写法,
# 由 _db_names() 的赋值链传播负责 (第一版只看直接实参, 漏掉了 test_table_mounts.py:104,
# 见 guard_write_opens 的 docstring)。
DBISH = re.compile(r"ohs\.db|DB_PATH|DATA_DIR|\bDB\b|ROOT\s*/\s*[\"']data|[\"']data[\"']")

# 无实参写侧工厂的**登记式豁免**: key = "文件:调用表达式", value 以 `kind: ` 开头 + 理由。
# 为什么留口子而不是直接放行: 豁免必须是**可腐烂的** —— 登记的调用点一旦不在文件里了,
# 判据点名 (见 guard_write_opens 末尾的僵尸检查)。这正是 S1c″ D3′ 要推广到全库的模式。
# kind 只有两种, 各自管一种失效:
#   · factory-proof —— 调它就是为了证明写侧工厂指向哪儿, 调用点全在临时树作用域内;
#   · write-runtime —— 这个脚本按设计要写 (全链回归), 且不在部署名单里, 容器永不执行。
GUARD_EXEMPT = {
    "test_import_purity.py:pdb._conn()":
        "factory-proof: points_at() 用 `_conn()` 就是为了证明「写侧工厂在 monkeypatch 后仍指"
        "临时树」(PRAGMA database_list 实证); 调用点全在 use_tree(tmp) 之内, C 段还带对表前提",
    "test_fresh_import.py:_pc()":
        "write-runtime: 全链回归按设计要写 (create_project/save_upload/update_project 走生产写"
        "路径), 这里删掉的是它自己建的测试项目; 本文件不在 GUARD_TESTS/LOCAL_TESTS → 容器里"
        "永不执行, 碰不到挂载进去的生产库 (残留改为点名, 不再 except: pass)",
}


def guard_tests(base: Path = ROOT) -> list[str]:
    """部署第 4 步在**容器里**跑的那份名单 (GUARD_TESTS)。演练要按这个真形状跑,
    不能拿"部署名单并集"凑 —— LOCAL_TESTS 那两支是在本机跑的, 不进镜像。"""
    return _shell_array(_deploy_text(base), "GUARD_TESTS")


def local_tests(base: Path = ROOT) -> list[str]:
    """部署第 0 步在本机跑的名单 (LOCAL_TESTS)。"""
    return _shell_array(_deploy_text(base), "LOCAL_TESTS")


def deploy_suite_names(base: Path = ROOT) -> list[str]:
    """quick_deploy.sh 里 GUARD_TESTS + LOCAL_TESTS 的脚本名 (去重、保序)。
    判据范围**从部署链读出来**, 不抄第二份名单 —— 抄了就会腐烂 (19+2 那份是唯一的真源)。
    文件不在就抛点名错误, 不静默返回空表: 空表会让整条判据"绿而没测" (S1c″ D4′ 反例)。
    容器里踩到这条 = 镜像没 COPY quick_deploy.sh (已在 Dockerfile 修好, 见其注释)。"""
    p = Path(base) / "quick_deploy.sh"
    if not p.exists():
        raise FileNotFoundError(
            f"{p} 不存在 —— 判据范围只能从部署链取 (第二份名单会腐烂)。"
            "若在容器里遇到: 镜像需要 COPY quick_deploy.sh。")
    out: list[str] = []
    for tok in guard_tests(base) + local_tests(base):
        # 不做"看起来像脚本名才收"的过滤: 过滤器会把解析事故 (括号粘进第一个 token、注释没剥净)
        # 变成**静默少一个守卫**。原样交出去, 脏 token 会在 guard_write_opens 里报"脚本不存在",
        # 在 test_dataset_door T8 里报"token 形状不对" —— 两个都是红, 不是少测。
        if tok not in out:
            out.append(tok)
    return out


def _assign_targets(node) -> list[str]:
    """Assign / AnnAssign 的落点名。
    AnnAssign (`db: Path = ROOT/"data"/"ohs.db"`) 只有 `.target` 没有 `.targets`,
    上一版只读 `.targets` → 带类型标注的赋值把传播链打断。"""
    if isinstance(node, ast.AnnAssign):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    return [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]


def _propagate(tree: ast.AST, seed) -> set[str]:
    """赋值链不动点: 表达式命中 seed → 落点名进集合, 集合再回头参与命中。"""
    names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for n in ast.walk(tree):
            if not isinstance(n, (ast.Assign, ast.AnnAssign)):
                continue
            v = getattr(n, "value", None)
            if v is None or not seed(ast.unparse(v), names):
                continue
            for t in _assign_targets(n):
                if t not in names:
                    names.add(t)
                    changed = True
    return names


def _temp_names(tree: ast.AST) -> set[str]:
    """由 tempfile / TemporaryDirectory / mkdtemp 派生出来的变量名闭包。
    守卫**必须**能拿临时库做变异对照, 所以"实参里出现临时名"要放行;
    放行按数据派生关系判, 不按变量名清单硬编码, 也不按目录判 (tmp/ 也会被硬编码路径骗)。"""
    seeds: set[str] = set()
    for n in ast.walk(tree):                    # with tempfile.TemporaryDirectory() as td
        if isinstance(n, ast.With):
            for it in n.items:
                if re.search(r"tempfile|mkdtemp|TemporaryDirectory", ast.unparse(it.context_expr)):
                    if isinstance(it.optional_vars, ast.Name):
                        seeds.add(it.optional_vars.id)
    return seeds | _propagate(tree, lambda v, acc: bool(
        re.search(r"tempfile|mkdtemp|TemporaryDirectory|TemporaryFile", v)
        or any(re.search(rf"\b{re.escape(m)}\b", v) for m in acc | seeds)))


def _db_names(tree: ast.AST) -> set[str]:
    """指向"本进程数据目录里那个库"的名字闭包 (含赋值链传播)。
    为什么要有: 第一版只看调用的**直接实参**, 于是

        db = ROOT / "data" / "ohs.db"
        conn = sqlite3.connect(str(db))

    整个漏过去 —— 实测 `tools/test_table_mounts.py:104` 就是这个形状 (而且是本轮唯一
    还在**硬编码路径**打开验收库的守卫: 它既不认 OHS_DATA_DIR, 临时卷演练也就测不到它)。"""
    return _propagate(tree, lambda v, acc: bool(
        DBISH.search(v) or any(re.search(rf"\b{re.escape(m)}\b", v) for m in acc)))


def _local_defs(tree: ast.AST) -> set[str]:
    """本文件自己定义的名字 (函数/类/lambda)。
    用途: 无实参的 `connect()` / `_conn()` 若是**本地定义**的同名包装, 不算"指向 DB_PATH 的
    写侧工厂" —— 判据改去查它的函数体 (整棵树本来就在walk, 不会漏)。"""
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Lambda):
            out.update(_assign_targets(n))
    return out


def suite_and_local_tests(base: Path = ROOT) -> list[str]:
    """判据范围 = 部署链名单 ∪ 全部 tools/test_*.py。
    只查部署名单会漏: `test_table_mounts`/`test_fresh_import` 不在数组里 (本地才跑),
    但它们每次本地验收都在**给真实库开可写连接** —— 那正是 D0/D1 想根治的东西。"""
    out = list(deploy_suite_names(base))
    for p in sorted((Path(base) / "tools").glob("test_*.py")):
        if p.stem not in out:
            out.append(p.stem)
    return out


def _write_factory_names(tree: ast.AST) -> tuple[set, set]:
    """本文件里"可写工厂"的所有名字 (leaf 名集合, 完整属性路径集合)。
    别名必须认 —— 第一版只认字面 `connect()`,
    于是 `from knowledge.oel import connect as oc` + `assess_project(oc(), …)` 直接漏过去
    (实测 test_table_mounts.py:140 就是这个形状)。"""
    mods, direct = _oel_names(tree)
    names = {"connect", "_conn"}
    names |= direct
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (n.module or "") in ("web.projects_db", "projects_db"):
            for a in n.names:
                if a.name == "_conn":
                    names.add(a.asname or a.name)
    return names, {f"{m}.connect" for m in mods}


def _shell_array(text: str, name: str) -> list[str]:
    """取 quick_deploy.sh 里一个数组的 token, 返回去重前的原序。
    两个坑 (都是本轮实测踩到的):
      · 数组里可以有 `#` 行注释 —— bash 认, 判据也必须认, 否则中文注释会被当成条目;
      · 注释里可以出现 `)` (比如 "(第 7 步查不到它们)") —— 所以不能用 `=\\((.*?)\\)` 一把正则,
        那会在注释的括号处提前截断 (实测 MD5_FILES 被截成 79 项, 新加的 12 条整块看不见)。"""
    i = text.find(name + "=(")
    if i < 0:
        return []
    i += len(name) + 2          # 跳过 `NAME` 与紧跟的 `(` (差一位会把 `(` 粘进第一个 token)
    body: list[str] = []
    in_comment = False
    while i < len(text):
        ch = text[i]
        if in_comment:
            if ch == "\n":
                in_comment = False
                body.append(ch)
        elif ch == "#":
            in_comment = True
        elif ch == ")":
            break
        else:
            body.append(ch)
        i += 1
    return [t.strip("\"'") for t in "".join(body).split() if t.strip("\"'")]


def _deploy_text(base: Path = ROOT) -> str:
    p = Path(base) / "quick_deploy.sh"
    if not p.exists():
        raise FileNotFoundError(f"{p} 不存在 —— 部署链名单只有这一份真源")
    return p.read_text(encoding="utf-8", errors="replace")


def md5_files(base: Path = ROOT) -> list[str]:
    """MD5_FILES: 第 1 步"必须在包里" 与第 7 步"容器里必须 md5 得到" 共用的那份清单。"""
    return _shell_array(_deploy_text(base), "MD5_FILES")


def image_paths(base: Path = ROOT) -> tuple[list[str], list[str]]:
    """Dockerfile 里 COPY 的**源** (目录, 文件)。镜像文件清单的唯一口径就写在 Dockerfile 上。"""
    dirs: list[str] = []
    files: list[str] = []
    df = Path(base) / "Dockerfile"
    if not df.exists():
        return dirs, files
    for line in df.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*COPY\s+(.+)$", line)
        if not m:
            continue
        toks = [t for t in m.group(1).split() if not t.startswith("--")]
        for src in toks[:-1]:                     # 最后一个 token 是镜像里的目标路径
            if src.endswith("/"):
                if src.rstrip("/") not in dirs:
                    dirs.append(src.rstrip("/"))
            elif src not in files:
                files.append(src)
    return dirs, files


def delivery_chain_gaps(base: Path = ROOT) -> list[str]:
    """交付链三方对表: 部署名单 ↔ MD5_FILES ↔ Dockerfile(镜像)。

    为什么要有这一条 (2026-10-16 实测事故): D4′ 的判据范围是 `deploy_suite_names()` 从
    quick_deploy.sh 读的, 而镜像里**没有** quick_deploy.sh (Dockerfile 只 COPY 了
    web/tools/knowledge/acquire + 两个 json) —— 在"镜像形状"的树里跑 test_dataset_door 直接
    FileNotFoundError, 部署第 4 步整条守卫套崩。本地全绿、容器里崩, 正是本项目最怕的那类失效。
    同一轮还量到: 部署名单 21 个守卫里有 12 个不在 MD5_FILES → 容器里跑的是旧守卫时第 7 步无声。

    判据 (全部从文件派生, 不抄第二份名单):
      · MD5_FILES 每一项必须能在**当前源码树里存在**, 且被 Dockerfile 的 COPY 集覆盖
        (第 7 步是对 /app 里的它做 md5sum, 镜像没这个文件就等于没校);
      · GUARD_TESTS ∪ LOCAL_TESTS 的每个守卫都必须在 MD5_FILES 里。
    """
    out: list[str] = []
    if not (Path(base) / "Dockerfile").exists():
        # 一条点名, 而不是 93 条"镜像里没有 X": 读不到源头时**任何**逐条比对都是废话。
        return [f"Dockerfile 不在 {base} —— 交付链判据读不到镜像形状 "
                f"(容器里踩到这条 = 镜像需要 COPY Dockerfile, 见 Dockerfile 第 14 行注释)"]
    dirs, files = image_paths(base)
    md5 = md5_files(base)
    md5_set = set(md5)
    for f in md5:
        if not (Path(base) / f).exists():
            out.append(f"MD5_FILES 腐烂: {f} 在本地源码树里不存在 (第 7 步只会报'读不到')")
            continue
        covered = f in files or any(f.startswith(d + "/") for d in dirs)
        if not covered:
            out.append(f"镜像里没有 {f}: Dockerfile 的 COPY 集不覆盖它, 但它在 MD5_FILES 里 "
                       f"(第 7 步要在容器里 md5sum 它) —— 要么加 COPY, 要么从清单删掉")
    for g in deploy_suite_names(base):
        if f"tools/{g}.py" not in md5_set:
            out.append(f"守卫 {g} 在部署名单里但不在 MD5_FILES —— 容器里跑的是旧版时无人知晓")
    return out


def guard_write_opens(base: Path = ROOT) -> list[str]:
    """守卫套里"给验收库开**可写**连接"的点。返回空 = 验收链在容器里也只读。

    判据范围 = 部署链名单 ∪ 全部 tools/test_*.py (见 suite_and_local_tests)。

    为什么单独立一条判据 (2026-10-16 对抗审查复验成立): 部署第 4 步用新镜像在容器里跑
    GUARD_TESTS, 而容器里 `ROOT/data` **就是挂载进去的生产库**。上一轮把"验收链只读"
    只做到了导出链 (CHAIN_READS 那 9 个函数), 守卫脚本自身还有 3 处
    `sqlite3.connect(ROOT/"data"/"ohs.db")` —— 可写模式 + 硬编码路径 (连 OHS_DATA_DIR
    都不认, 于是临时卷演练也测不到它们)。

    判据 (纯 AST, 不执行), 以及它踩过的三个坑:
      · 被检调用: 叶子名 ∈ {connect, _conn, 本文件里的 oel/projects_db 写侧工厂别名}
        或完整路径 ∈ {`<oel 模块别名>.connect`, `<projects_db 模块别名>._conn`}。
        —— 第一版只认字面 `connect()`, 于是 `from knowledge.oel import connect as oc`
        + `assess_project(oc(), …)` 漏过去 (实测 test_table_mounts.py:140)。
      · 实参**先看名字闭包**再看字面 (`sqlite3.connect(str(db))`, db 由 ROOT/"data" 赋值而来)
        —— 第一版只看直接实参的字面, 同样漏 (实测 test_table_mounts.py:104)。
      · 只读标记: `mode=ro` / `read_only=True` / `:memory:`; 临时名派生的实参放行
        (守卫要能拿临时库做变异对照, 否则判据会逼人放弃检查)。
      · 无实参的写侧工厂天然指向 DB_PATH → 违规, 除非 ①本文件自己定义的同名包装 (判它函数体)
        ②登记在 GUARD_EXEMPT 且带理由。
      · 字符串常量里的 "connect(" 不算 (守卫的变异夹具全是字符串), 判据只看真调用。
    """
    out: list[str] = []
    seen_exempt: set[str] = set()
    for name in suite_and_local_tests(base):
        p = Path(base) / "tools" / f"{name}.py"
        if not p.exists():
            out.append(f"{name}: 名单里的脚本不存在 (腐烂)")
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            out.append(f"{name}: 解析失败 ({e}) —— 判据无法覆盖, 必须红")
            continue
        tnames = _temp_names(tree)
        dbns = _db_names(tree)
        local = _local_defs(tree)
        leaf_names, dotted = _write_factory_names(tree)
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = ast.unparse(n.func)
            leaf = fn.split(".")[-1]
            if leaf not in leaf_names and fn not in dotted:
                continue
            if not n.args and not n.keywords:
                if "." not in fn and fn in local:
                    continue          # 本文件自己定义的同名包装: 函数体另有一次真调用, 判它
                key = f"{p.name}:{fn}()"
                if key in GUARD_EXEMPT:
                    seen_exempt.add(key)
                    continue
                out.append(f"{name}:{n.lineno} {fn}() 无实参 = 直接指向 DB_PATH 的可写工厂")
                continue
            arg = ast.unparse(n.args[0]) if n.args else \
                next((ast.unparse(k.value) for k in n.keywords if k.arg == "database"), "")
            if re.search(r"mode=ro\b|read_only=True|:memory:", arg):
                continue
            if any(re.search(rf"\b{re.escape(t)}\b", arg) for t in tnames):
                continue
            if DBISH.search(arg) or any(re.search(rf"\b{re.escape(d)}\b", arg) for d in dbns):
                out.append(f"{name}:{n.lineno} {fn}({arg[:56]}) 用可写模式打开验收库")
    # 反向对表: 登记过的豁免必须**仍然命中一个真调用点** ——
    # 否则文件改名/调用点搬走之后, 豁免条目会变成"永久放行"的僵尸 (S1c″ D3′ 的核心教训)。
    for key in sorted(set(GUARD_EXEMPT) - seen_exempt):
        out.append(f"豁免登记腐烂: {key} 现在命中不到任何调用点 (要么删登记, 要么它不该被删)")
    return out


# 主门三件套: 它们的输出被直接当"数据层没变/成品没变"的证据, 所以读数必须走 DB_PATH。
GATE_TOOLS = ("data_stamp", "export_fingerprint", "gate_coverage")


def evidence_tools(base: Path = ROOT) -> list[str]:
    """产出"被当证据的读数"的脚本 = 守卫套范围 (部署名单 ∪ tools/test_*.py) + 主门三件套。"""
    out = [f"tools/{n}.py" for n in suite_and_local_tests(base)]
    out += [f"tools/{t}.py" for t in GATE_TOOLS]
    return sorted(set(out))


def _split_path_expr(node: ast.AST) -> tuple[set, list]:
    """把 `ROOT / "data" / "ohs.db"` 拆成 (出现的名字, 出现的字符串常量)。"""
    names: set = set()
    strs: list = []

    def rec(n):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            rec(n.left)
            rec(n.right)
        elif isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(ast.unparse(n))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            strs.append(n.value)
    rec(node)
    return names, strs


# 证据链里"确实要看真实 data/"的**登记式豁免** (与 GUARD_EXEMPT 同一套规矩: value 必须以
# kind 前缀开头 + 理由, 命中不到任何表达式就报"登记腐烂")。kind 两种:
#   · real-dir-forensic —— 这条判据的**被测对象**就是真实 data/ (证它没被写), 当然要按真路径读;
#   · cleanup-target    —— 本地守卫清理自己种进真实树的缓存 (不在部署名单, 容器永不执行)。
# ⚠ kind 白名单被 tools/test_dataset_door.py T10 冻死 (只认这两种前缀), 链外的新 kind 走
#   LEGACY_PATH_EXEMPT, 不要往这里塞第三种。
PATH_EXEMPT = {
    # key 的形状 = "<文件名>:<ast.unparse 出来的表达式子串>" —— 必须按 unparse 的形状写
    #   (单引号), 源码里的双引号形态匹配不到: 第一版就因此报了三条"登记腐烂"的假红。
    "test_import_purity.py:ROOT / 'data'":
        "real-dir-forensic: 该守卫的结论就是『import web.app 对真实 data/ 全树零改动』, 取证对象"
        "必须是真路径; 临时树那处是 Path(td2)/'data', 不含 ROOT 味名字 → 本来就不归这条管",
    "test_acceptance_readonly.py:ROOT / 'data'":
        "real-dir-forensic: 本文件的行为尺子是『对真实 data/ 快照做前后 diff』, 改成 DB_PATH 等于"
        "把取证对象搬到临时树上, 真实目录反而测不到",
    "test_fresh_import.py:ROOT / 'data' / 'vision_cache' / '32e58e26097c8e58.json'":
        "cleanup-target: 删的是本守卫自己种下的视觉缓存 (同段点名), 本文件不在部署名单 → 容器里"
        "永不执行; 它对库的读已经走 connect_ro/DB_PATH",
}


# ———— S1c″ D4″ (任务 #21): 链外硬拼路径从"只报数"收成"点名 + 可登记" ————
# 扫描范围: 三个代码目录 (tools/web/knowledge)。paths.py 是唯一产地, chain_audit /
# test_dataset_door 自己的源码里全是"反例字符串", 按形状匹配会自咬, 都排除。
DATA_SCAN_DIRS = ("tools", "web", "knowledge")
DATA_SCAN_SKIP = {"tools/chain_audit.py", "tools/test_dataset_door.py", "knowledge/paths.py"}
# "data/ohs.db" 出现在**字符串常量**里 (绝对路径 sqlite3.connect("/Users/.../data/ohs.db")、
# argparse default="data/ohs.db"、REPO / 'data/ohs.db' 单段拼法) —— 上一版只看除法链,
# 实测 2026-10-17 重数时漏掉了 22 处 tools/ 绝对路径字符串 (diag_* / audit_* / e2e_verify 等)。
DB_PATH_STR = re.compile(r"data[/\\]ohs\.db")
# 独立常量必须**整串就是路径** (以 data/ohs.db 结尾) 才算拼路径;
# "前提对表: …不是真实库 data/ohs.db)" 这类**句子** (check 标签/help 文案) 不是读库动作
# —— 2026-10-17 实测: 用无锚 regex 会把 test_det_rows 的两句标签误判成硬拼。
DB_PATH_IS = re.compile(r"data[/\\]ohs\.db$")


def _docstring_lines(tree: ast.AST) -> set:
    """docstring 占的行 —— 里面的 "data/ohs.db" 是**说明**不是代码 (口径同 test_dataset_door B)。"""
    lines: set = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                d = b[0].value
                lines.update(range(d.lineno, (d.end_lineno or d.lineno) + 1))
    return lines


def _data_path_sites(base: Path = ROOT) -> list[tuple]:
    """返回 (rel, lineno, expr) —— 源码里所有"自己拼数据目录路径"的代码位点。

    三种形状, 每一种都是重数 45 那批时实测漂出来的:
      · 除法链含 'data' 段且基数含 ROOT 味名字 (上一版唯一的判法, 会误伤临时树, 规则保留);
      · 任一表达式 (含单段 `'data/ohs.db'`) 里出现 data/ohs.db 字面 —— 绝对路径与 f-string 都在这;
      · `os.path.join(<repo根>, "data", "ohs.db")` 拆段拼 (test_std_supersede 旧形状)。
    同一行只报一次 (嵌套 BinOp / 行内常量会重叠)。
    """
    out: list[tuple] = []
    seen_lines: set = set()
    for d in DATA_SCAN_DIRS:
        dirp = Path(base) / d
        if not dirp.is_dir():
            continue          # 薄壳树 (变异夹具) 只挂 tools/ 也要能跑
        for p in sorted(dirp.glob("*.py")):
            rel = f"{d}/{p.name}"
            if rel in DATA_SCAN_SKIP:
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue      # 解析失败由 guard_write_opens 那条点名
            dsl = _docstring_lines(tree)
            # Pass 1: 表达式形状 (除法链 / os.path.join)
            for n in ast.walk(tree):
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
                    names, strs = _split_path_expr(n)
                    bad = (("data" in strs and any("ROOT" in x or "__file__" in x for x in names))
                           or any(DB_PATH_STR.search(s) for s in strs))
                    if not bad or (rel, n.lineno) in seen_lines:
                        continue
                    seen_lines.add((rel, n.lineno))
                    out.append((rel, n.lineno, ast.unparse(n)))
                elif isinstance(n, ast.Call) and ast.unparse(n.func) == "os.path.join":
                    strs = [a.value for a in n.args
                            if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                    if "data" in strs and "ohs.db" in strs and (rel, n.lineno) not in seen_lines:
                        seen_lines.add((rel, n.lineno))
                        out.append((rel, n.lineno, ast.unparse(n)))
            # Pass 2: 独立的字符串常量 (connect("…") 实参、argparse default= 等) ——
            # 只认"整串就是路径"的 (见 DB_PATH_IS 注释), 句子/标签不算。
            for n in ast.walk(tree):
                if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and DB_PATH_IS.search(n.value)
                        and n.lineno not in dsl and (rel, n.lineno) not in seen_lines):
                    seen_lines.add((rel, n.lineno))
                    out.append((rel, n.lineno, n.value))
    return out


# 链外的**登记式豁免** (S1c″ D4″)。为什么不并进 PATH_EXEMPT: PATH_EXEMPT 的 kind 白名单被
# test_dataset_door.py T10 冻在 "real-dir-forensic / cleanup-target" 两种上 (属主代理独占,
# 本轮不许碰), 而链外的正当形状还有"随模块打包的兄弟目录"与"属主代理在途文件"两种,
# 硬塞旧 kind 等于说谎。本表沿用同一套纪律: 每条必须 `kind: 理由` 开头, 且登记项命中不到
# 任何表达式时按"登记腐烂"点名 —— 搬走必须销账, 不许当永久放行。
LEGACY_PATH_EXEMPT = {
    "local_deploy_drill.py:ROOT / 'data'":
        "real-dir-forensic: 部署演练的**被测对象**就是真实 data/ (把它的副本挂进容器跑全套守卫, "
        "再对表真实树字节没变); 改成 DATA_DIR 会让演练跟着外层 env 搬到副本上, 自证失效",
    "gbz22_loader.py:Path(__file__).resolve().parent / 'data'":
        "bundled-data: 读的是 knowledge/data/gbz22_physical.json —— **随代码走**的 OCR 原始数据 "
        "(与 knowledge/gbz188_pages 等同层), 不是运行期数据, 不该认 OHS_DATA_DIR; "
        "D0 的 materials 错配事故管的是 data/ 子树, 这里根本不在那棵树上",
    "section_filler.py:Path(__file__).resolve().parent.parent / 'data' / 'ohy_invest.json'":
        "in-flight-owner: 属主代理独占本文件 (任务 #21 所有权清单), 路径行由**它**收口; "
        "收口后这条登记命中不到表达式 → '链外登记腐烂'会点名, 届时删登记 (登记=限时债据, 不是免死牌)",
    "section_filler.py:Path(__file__).resolve().parent.parent / 'data' / 'tech_econ.json'":
        "in-flight-owner: 同上, 属主代理在途 (本条与 :85 各自独立销账)",
    "section_filler.py:Path(__file__).resolve().parent.parent / 'data' / 'product_plan.json'":
        "in-flight-owner: 同上, 属主代理在途",
}


def hardcoded_data_paths(base: Path = ROOT) -> tuple[list[str], list[str]]:
    """从**仓库根**自己拼数据目录路径的地方 —— 返回 (证据链里未登记的, 证据链外未登记的)。

    为什么单独立这一条 (2026-10-16 实测): `tools/data_stamp.py` 里写着
    `DB = ROOT / "data" / "ohs.db"`, 于是 `OHS_DATA_DIR` 搬家时它读的**还是仓库里那份真库** ——
    给临时副本的 oel_limit 改一格再跑 --check, 它照样报"一致"。这条尺子一度被我误判成
    "看不见内容变化", 真相是它根本没看那棵树。判据读错源头 = 绿而没测 (见记忆: 先证明能红)。

    口径 (每条都是被反例逼出来的):
      · 形状: 见 _data_path_sites (除法链 / data/ohs.db 字面 / os.path.join 三段);
      · 只看代码: docstring 里的 "输出: data/ohs.db → 表" 是说明不是读库 (口径同 door 的 B);
      · 'data' 单段要基数含 ROOT 味 (临时树 tmp/'data' 不是"绕过单一产地");
      · 不查 knowledge/paths.py 本身 (它就是唯一允许的产地)。
    范围 (S1c″ D4″ 起): 链内 (守卫套 ∪ 主门三件套) 与**链外**都点名 ——
      两边都不许有未登记项; 正当硬拼走登记 (链内 PATH_EXEMPT, 链外 LEGACY_PATH_EXEMPT),
      登记项腐烂必须点名。上一版"链外只报数"是给人记账, 账没人对就永远漂。
    """
    inside: list[str] = []
    outside: list[str] = []
    scope = set(evidence_tools(base))
    seen_exempt: set = set()
    seen_legacy: set = set()
    for rel, lineno, expr in _data_path_sites(base):
        fname = rel.split("/", 1)[1]
        hit = [k for k in PATH_EXEMPT if fname in k and k.split(":", 1)[1] in expr]
        if hit:
            seen_exempt.update(hit)
            continue
        hitl = [k for k in LEGACY_PATH_EXEMPT if fname in k and k.split(":", 1)[1] in expr]
        if hitl:
            seen_legacy.update(hitl)
            continue
        where = f"{rel}:{lineno} {expr[:60]}"
        (inside if rel in scope else outside).append(where)
    for key in sorted(set(PATH_EXEMPT) - seen_exempt):
        inside.append(f"路径豁免登记腐烂: {key} 现在命中不到任何表达式 (要么删登记, 要么它不该被删)")
    for key in sorted(set(LEGACY_PATH_EXEMPT) - seen_legacy):
        outside.append(f"链外登记腐烂: {key} 现在命中不到任何表达式 (调用点已搬走 → 删这条登记)")
    return inside, outside


if __name__ == "__main__":
    v = chain_write_sites()
    print(f"验收链取数函数 {len(CHAIN_READS)} 个 → 写动作 {len(v)} 处")
    for x in v:
        print("   ", x)
    g = guard_write_opens()
    scope = suite_and_local_tests()
    print(f"\n守卫套 {len(scope)} 个脚本 (部署名单 {len(deploy_suite_names())} ∪ 全部 "
          f"tools/test_*.py) → 给验收库开可写连接的 {len(g)} 处")
    for x in g:
        print("   ", x)
    d = delivery_chain_gaps()
    dirs, files = image_paths()
    print(f"\n交付链对表: MD5_FILES {len(md5_files())} 项 / 镜像 COPY {len(dirs)} 目录 + "
          f"{len(files)} 文件 → 缺口 {len(d)} 处")
    for x in d:
        print("   ", x)
    hp, hp_out = hardcoded_data_paths()
    print(f"\n硬拼数据目录路径 (S1c″ D4″: 链内+链外**都点名判红**, 正当的走登记): "
          f"链内 {len(hp)} 处 / 链外 {len(hp_out)} 处")
    for x in hp + hp_out:
        print("   ", x)
    # 退出码: 有违规就非 0 —— 这把尺子既能当度量 (print), 也能当门 (rc)。
    # 上一版无论如何都 rc=0, 于是"报告了 3 处"和"一处没有"在部署脚本眼里一模一样。
    sys.exit(1 if (v or g or d or hp or hp_out) else 0)
