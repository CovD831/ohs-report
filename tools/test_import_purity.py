#!/usr/bin/env python3
"""S1c′ D0 守卫: import 期零副作用 + 生命周期回收的删除闸 (2026-10-15)

钉三件事, 全部**不碰真实 data/ 的写路径** (只在临时 DATA_DIR 上做删除演练):

  A. 数据目录单一产地: DB / materials / vision_cache / .secret 必须由同一个
     knowledge.paths.DATA_DIR 派生 —— 用 OHS_DATA_DIR 整体搬家时四者一起走。
     为什么要这条: 原来 DB_PATH 有产地, 其余各自 `DB.parent/"materials"`、
     `Path(__file__).parent.parent/"data"/"materials"`、`_data_dir()/"vision_cache"`,
     三把尺子各算各的; 只要有人把库指到别处而材料目录没跟着走, 下面 B 段那个
     rmtree 就会把 193MB 真实企业材料判成"孤儿"删光。

  B. `import web.app` 不得动 data/ 一个字节 (子进程实测, 不是读注释):
     旧状是 import 期跑 init_users / ensure_official_schema / init_tasks /
     seed_demo / **cleanup_expired**。前四个是写库 (建表/WAL), 第五个能删目录。
     变异对照: 手工调 `_bootstrap()` (等价旧的 import 期行为) → 判据必须红。

  C. cleanup_expired 的两把开关**分开**: 默认都不动 / apply_rows 只删行不碰目录 /
     apply_dirs 才删目录 /
     库读不到项目 id 或目录不在 DATA_DIR 子树内 → **拒绝执行** (具名错误)。
     阳性对照: 按旧逻辑 (无闸) 在同一棵树上跑 → 确实会把目录全删,
     否则 C 段是空转 —— 这正是"当证据的检查先证明它能红"的做法。

用法: .venv/bin/python tools/test_import_purity.py    (退出码 0 = 全绿)
"""
import ast
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import knowledge.paths as paths                      # noqa: E402
import web.projects_db as pdb                        # noqa: E402

TTL = pdb.EMPTY_PROJECT_TTL
n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


VOLATILE_ENV = ("OHS_DATA_DIR", "OHS_LIFECYCLE_ROWS", "OHS_LIFECYCLE_DIRS", "SEED_DEMO",
                "OHS_STARTUP_LOCK_TIMEOUT")


def base_env(**kw) -> dict:
    """起子进程用的环境: 先把所有会影响数据位置/回收开关/锁超时的变量清空, 再按 kw 给。
    不这么做的话, 本机或 CI 上残留一个 OHS_DATA_DIR 就会让整套用例测到别的目录。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    for k in VOLATILE_ENV:
        env.pop(k, None)
    env.update(kw)
    return env


def env_for(data_dir: Path, **kw) -> dict:
    return base_env(OHS_DATA_DIR=str(data_dir), **kw)


def run_py(code: str, env_extra: dict | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                          env=base_env(**(env_extra or {})),
                          capture_output=True, text=True, timeout=timeout)


def snapshot(base: Path) -> dict:
    """(类型, 大小, mtime_ns) 每条目; 只对 db 三件套额外记 sha1。
    193MB 材料全哈希会让取证本身成瓶颈, 而"有没有被动过"由 mtime_ns/size 足够判。"""
    out = {}
    if not base.exists():
        return out
    for p in sorted(base.rglob("*")):
        rel = str(p.relative_to(base))
        st = p.stat()
        if p.is_dir():
            out[rel] = f"DIR {st.st_mtime_ns}"
            continue
        rec = f"FILE {st.st_size} {st.st_mtime_ns}"
        if p.name in ("ohs.db", "ohs.db-wal", "ohs.db-shm") and st.st_size:
            import hashlib
            rec += " " + hashlib.sha1(p.read_bytes()).hexdigest()[:12]
        out[rel] = rec
    out["."] = f"ROOTDIR {base.stat().st_mtime_ns}"
    return out


def diff_entries(before: dict, after: dict) -> dict:
    keys = set(before) | set(after)
    return {
        "added": sorted(k for k in keys if k not in before),
        "removed": sorted(k for k in keys if k not in after),
        "changed": sorted(k for k in keys if k in before and k in after and before[k] != after[k]),
    }


def touched(d: dict) -> int:
    return len(d["added"]) + len(d["removed"]) + len(d["changed"])


DUMP_SNIP = (
    "import hashlib, json, sqlite3;"
    "c = sqlite3.connect('file:__DB__?mode=ro', uri=True);"
    "t = [r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")];"
    "rows = {k: [list(map(str, x)) for x in c.execute('SELECT * FROM \\\"' + k + '\\\" ORDER BY rowid')] for k in t};"
    "print(json.dumps({'tables': t, 'n': {k: len(v) for k, v in rows.items()},"
    " 'sha': hashlib.sha1(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:12]}))"
)


def db_dump(base: Path) -> dict:
    """库的**逻辑内容**指纹: 表集合 + 每表全部行 (走 mode=ro, 不碰写侧工厂)。

    为什么需要它而不是文件哈希: 2026-10-15 实测 —— 上一次只读连接留下的 -wal/-shm
    被写侧连接 checkpoint 回写时, `ohs.db` 的**字节**会变 (页头 change counter),
    但表集合与每一行都不动。启动期"开了一次可写连接"在字节尺子上必然留痕,
    拿字节哈希当"没写数据"的证据会把合法启动判成红; 反过来只比文件大小又太松。
    """
    r = run_py(DUMP_SNIP.replace("__DB__", str(base / "ohs.db")), {"OHS_DATA_DIR": str(base)})
    if r.returncode:
        return {"error": r.stderr.strip()[-160:]}
    return json.loads(r.stdout.strip())


# ============ A. 数据目录单一产地 ============
print("=== A. DATA_DIR 是唯一产地: 搬家时库/材料/缓存/密钥一起走 ===")
import tempfile                                        # noqa: E402
with tempfile.TemporaryDirectory() as td:
    other = Path(td) / "moved"
    r = run_py(
        "import json, knowledge.paths as p;"
        "print(json.dumps([str(p.DATA_DIR), str(p.DB_PATH), str(p.MATERIALS_DIR),"
        " str(p.VISION_CACHE_DIR), str(p.SECRET_FILE), p.same_origin(p.MATERIALS_DIR)]))",
        {"OHS_DATA_DIR": str(other)})
    if r.returncode:
        check("子进程读 OHS_DATA_DIR 成功", r.stderr.strip()[-200:], "")
    else:
        got = json.loads(r.stdout.strip())
        # 期望值也走 resolve(): macOS 的 /var 是 /private/var 的软链, 不解析会假红
        o = other.resolve()
        want = [str(o), str(o / "ohs.db"), str(o / "materials"),
                str(o / "vision_cache"), str(o / ".secret"), True]
        check("四个路径全部派生自同一个 DATA_DIR", got, want)
        check("搬家后同源的 materials 仍算 same_origin", got[5], True)
    r2 = run_py("import json, knowledge.paths as p;"
                "print(json.dumps([str(p.DB_PATH), str(p.MATERIALS_DIR), p.same_origin(p.MATERIALS_DIR)]))")
    g2 = json.loads(r2.stdout.strip())
    check("不设 OHS_DATA_DIR 时回到仓库 data/ (生产挂载点不变)",
          g2, [str(paths.DB_PATH), str(paths.MATERIALS_DIR), True])
    check("真实 DATA_DIR 之外的路径判为非同源 (rmtree 闸1 的尺子)",
          run_py("import knowledge.paths as p; print(p.same_origin('/tmp'))").stdout.strip(), "False")

print("\n=== A2. 谁还自己算数据路径 (字面量收口, 与 test_dataset_door 同口径) ===")
OWN_DIR = {"knowledge/paths.py"}
PAT = ("data", "materials")
own = []
for d in ("web", "knowledge"):
    for f in sorted((ROOT / d).rglob("*.py")):
        rel = str(f.relative_to(ROOT))
        if rel in OWN_DIR:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        docs = set()
        for node in [tree] + [n for n in ast.walk(tree) if isinstance(
                n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]:
            b = getattr(node, "body", [])
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                docs.update(range(b[0].lineno, (b[0].end_lineno or b[0].lineno) + 1))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Constant) or not isinstance(n.value, str) or n.lineno in docs:
                continue
            v = n.value.replace("\\", "/")
            segs = [s for s in v.split("/") if s]
            hit_pair = any(segs[i:i + 2] == list(PAT) for i in range(len(segs) - 1))
            hit_vision = "vision_cache" in v or ".secret" in v or "startup.lock" in v
            if (hit_pair or hit_vision) and "data/materials_pf" not in v:
                own.append(f"{rel}:{n.lineno} {v[:52]}")
# 允许: 与 DATA_DIR 无关的写侧脚本 web/extract_pf.py 自己造 data/materials_pf/ (不是 materials)
check("web/ 与 knowledge/ 里 materials/vision_cache/.secret/startup.lock 字面量只在 paths.py",
      own, [])

# ============ B. import 零副作用 ============
print("\n=== B. import web.app 不动 data/ (子进程实测) ===")
with tempfile.TemporaryDirectory() as td2:
    fresh = Path(td2) / "data"
    r = run_py("import web.app", {"OHS_DATA_DIR": str(fresh)})
    check("import web.app 在空 DATA_DIR 上也能成功 (不靠建库兜住)", r.returncode, 0)
    check("import 后空 DATA_DIR 里没被建出任何东西 (库/材料/缓存都不许 import 期创建)",
          sorted(p.name for p in fresh.iterdir()) if fresh.exists() else [], [])

    # 变异: 手工跑启动期准备 (= 旧 import 期那批调用), 判据必须红
    r2 = run_py("import web.app; web.app._bootstrap()", {"OHS_DATA_DIR": str(fresh)})
    if r2.returncode:
        check("_bootstrap 在空 DATA_DIR 上能跑完 (空 volume 首启演练)", r2.stderr.strip()[-200:], "")
    created = sorted(p.name for p in fresh.iterdir()) if fresh.exists() else []
    check("变异对照: _bootstrap() 会建出库与目录 → 上面那条判据不是空转, 它能红",
          bool(created) and (fresh / "ohs.db").exists(), True)
    print(f"     ⓘ _bootstrap 在新目录建出: {created}")

# 真实 data/: 只读取证, 绝不在真实库上跑变异
before = snapshot(ROOT / "data")
r3 = run_py("import web.app")
check(f"import web.app 退出码 0 (cwd={ROOT.name})", r3.returncode, 0)
d3 = diff_entries(before, snapshot(ROOT / "data"))
check(f"真实 data/ 全树零改动 ({len(before)} 条目)", touched(d3), 0)
if touched(d3):
    print("     实际变动:", d3)

# 取证函数本身能不能红 —— 顺便把"启动是否幂等"钉成一个事实
with tempfile.TemporaryDirectory() as td3:
    tdir = Path(td3) / "d"
    BOOT = "import web.app; web.app._bootstrap()"

    # B1: 关掉生命周期回收那条写侧, 剩下的启动步骤必须**完全静默幂等**。
    #     为什么把 ROWS=0 单独测: 判据要能定位责任 —— 混在一起跑, 一旦有变动就分不清
    #     是"建表/建用户不幂等"还是"回收在写库"。
    rb = run_py(BOOT, {"OHS_DATA_DIR": str(tdir), "OHS_LIFECYCLE_ROWS": "0"})
    check("对照前提: _bootstrap 在空目录上跑通 (空 volume 首启演练)", rb.returncode, 0)
    s0 = snapshot(tdir)
    rb2 = run_py(BOOT, {"OHS_DATA_DIR": str(tdir), "OHS_LIFECYCLE_ROWS": "0"})
    check("启动步骤幂等 (ROWS=0): warm 目录上再跑一次, data/ 零改动",
          (rb2.returncode, touched(diff_entries(s0, snapshot(tdir)))), (0, 0))

    # B1b: 生产默认 ROWS=1 → 生命周期回收这条必然开一次可写连接。
    #      尺子是**逻辑内容** (见 db_dump 的实测说明): 允许 data/ 根目录 mtime 与 ohs.db
    #      字节被动 (WAL 副文件的生灭/回写), 但表集合、每表行数、全部单元格都不许动。
    pre = run_py("import web.projects_db as p, json;"
                 "print(json.dumps(p.cleanup_expired(verbose=False)))",
                 {"OHS_DATA_DIR": str(tdir), "OHS_LIFECYCLE_ROWS": "1"})
    po = json.loads(pre.stdout.strip())
    check("前提对表: 首启后的库里没有到期空项目 (下面'内容不变'不是因为压根没活干才碰巧成立)",
          (po["would_drop_rows"], po["would_drop_dirs"], po["refused"]), (0, 0, ""))
    e0 = db_dump(tdir)
    rb3 = run_py(BOOT, {"OHS_DATA_DIR": str(tdir), "OHS_LIFECYCLE_ROWS": "1"})
    dd1 = diff_entries(s0, snapshot(tdir))
    check("启动步骤幂等 (ROWS=1): 不新增/不删除条目, 变动只限根目录 mtime 与 ohs.db 字节",
          (rb3.returncode, dd1["added"], dd1["removed"],
           [k for k in dd1["changed"] if k not in (".", "ohs.db")]), (0, [], [], []))
    check("启动步骤幂等 (ROWS=1): 库的**逻辑内容** (表集合 + 每表全部行) 一字不变",
          db_dump(tdir), e0)
    print(f"     ⓘ 尺子分工: 条目变动={dd1['changed']} / 逻辑内容={e0}")

    # 注入一次真实写入 (模拟"读路径里混进写") → 两把尺子都必须红
    inj = run_py("import web.projects_db as p;"
                 "c = p._conn(); c.execute('CREATE TABLE IF NOT EXISTS __probe(x)'); c.commit(); c.close()",
                 {"OHS_DATA_DIR": str(tdir)})
    dd = diff_entries(s0, snapshot(tdir))
    e2 = db_dump(tdir)
    check("变异对照: 注入一次真实写入后快照必须报红 (所以上面两条零改动不是空转)",
          (inj.returncode, touched(dd) >= 1, "ohs.db" in dd["changed"]), (0, True, True))
    check("变异对照: 同一注入在**逻辑内容**尺子上也必须红 (否则 B1b 那条是空转)",
          (e2.get("error", ""), e2["sha"] != e0["sha"], "__probe" in e2["tables"]),
          ("", True, True))
    print(f"     ⓘ 注入后被报出的条目: {dd['added'] + dd['changed'] + dd['removed']}")

print("\n=== B2. 启动钩子挂着 _bootstrap, 且整段被启动锁包住 (不靠注释) ===")
src = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
tree = ast.parse(src)


def fn(name):
    return next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)


def calls_in(node):
    out = set()
    for m in (ast.walk(node) if node else []):
        if isinstance(m, ast.Call):
            out.add(m.func.attr if isinstance(m.func, ast.Attribute)
                    else m.func.id if isinstance(m.func, ast.Name) else "")
    return out


boot, steps = fn("_bootstrap"), fn("_bootstrap_steps")
check("FastAPI 构造带 lifespan (启动期才会跑)", "lifespan=_lifespan" in src, True)
check("启动步骤齐 (官方层/运行时表/用户/任务/生命周期)",
      bool(steps) and {"init_users", "ensure_official_schema", "ensure_runtime_schema",
                       "init_tasks", "cleanup_expired"} <= calls_in(steps), True)

# D4: 串行化必须是"整段"的, 不是某个步骤自带的 —— 所以判据看 with 的形状, 不看注释。
locked = [m for m in (ast.walk(boot) if boot else [])
          if isinstance(m, ast.With)
          and any("startup_lock" in ast.unparse(it.context_expr) for it in m.items)
          and "_bootstrap_steps" in calls_in(m)]
check("_bootstrap_steps() 只在 `with startup_lock():` 里被调用", len(locked) >= 1, True)
callers = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
           and isinstance(n.func, ast.Name) and n.func.id == "_bootstrap_steps"]
check("_bootstrap_steps 只有一个调用点 (留第二个就等于留一条绕过锁的路)", len(callers), 1)
mkdir_ln = min([m.lineno for m in ast.walk(boot) if isinstance(m, ast.Call)
                and getattr(m.func, "attr", "") == "mkdir"], default=10 ** 9)
check("mkdir(DATA_DIR) 在拿锁之前 (锁文件本身要落在 DATA_DIR 里)",
      bool(locked) and mkdir_ln < min(m.lineno for m in locked), True)
top_calls = [ast.unparse(n.value)[:50] for n in tree.body
             if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
check("web/app.py 模块顶层没有裸调用 (import 期不执行任何函数)", top_calls, [])

# ============ B3/B4. 多 worker 首启的真并发形状 (演练实测到的 bug) ============
print("\n=== B3. 4 个进程同时跑 _bootstrap (--workers 2 的形状, 拉紧到 4) ===")
BOOTC = "import web.app; web.app._bootstrap()"
for rnd in (1, 2):
    # 每轮都用**冷卷**: check-then-act 的竞争窗口只在"表里还什么都没有"时存在,
    # 第二轮复用热卷的话测的是"大家都看到已有 admin"这条无竞争分支, 等于白跑。
    with tempfile.TemporaryDirectory() as tdc:
        cdir = Path(tdc) / "d"
        ps = [subprocess.Popen([sys.executable, "-c", BOOTC], cwd=str(ROOT),
                               env=env_for(cdir), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True) for _ in range(4)]
        outs = []
        for p in ps:
            out, _ = p.communicate(timeout=240)
            outs.append((p.returncode, (out or "").strip()))
        bad = [(rc, t[-140:]) for rc, t in outs if rc]
        c = sqlite3.connect(str(cdir / "ohs.db"))
        n_all = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        n_admin = c.execute("SELECT COUNT(*) FROM users WHERE username='admin'").fetchone()[0]
        n_proj = c.execute("SELECT COUNT(*) FROM project").fetchone()[0]
        c.close()
        check(f"第 {rnd} 轮 (冷卷): {len(ps)} 个并发首启退出码全 0 "
              f"(改前这里会撞 UNIQUE users.username → 静默少一个 worker)", bad, [])
        check(f"第 {rnd} 轮: 库里只有 1 个 admin, project 表建好且没被并发写坏 "
              f"(users={n_all} admin={n_admin} project={n_proj})",
              (n_all, n_admin, n_proj >= 0), (1, 1, True))

print("\n=== B4. 启动锁确实在锁: 有人持锁时第二家必须**响**, 不能裸跑 ===")
LOCKER = ("import time, knowledge.paths as p;"
          "cm = p.startup_lock(); cm.__enter__(); time.sleep(25)")
with tempfile.TemporaryDirectory() as tdd:
    ddir = Path(tdd) / "d"
    lk = subprocess.Popen([sys.executable, "-c", LOCKER], cwd=str(ROOT),
                          env=env_for(ddir), stdout=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 30
        while time.time() < deadline and not (ddir / ".startup.lock").exists():
            time.sleep(0.1)
        check("前提对表: 持锁进程已经建出锁文件 (否则下面测的是空气)",
              (ddir / ".startup.lock").exists(), True)
        r = run_py(BOOTC, {"OHS_DATA_DIR": str(ddir), "OHS_STARTUP_LOCK_TIMEOUT": "2"})
        txt = r.stdout + r.stderr
        check("别人持锁时 _bootstrap 必须失败退出, 而不是没拿到锁也照样写库",
              r.returncode != 0, True)
        check("响必须是**具名**错误 StartupLockHeld (运维要能分辨「有人在准备数据」和「代码坏了」)",
              ("StartupLockHeld" in txt, "启动锁" in txt), (True, True))
    finally:
        lk.terminate()
        lk.wait(timeout=15)
    r2 = run_py(BOOTC, {"OHS_DATA_DIR": str(ddir)})
    check("阳性对照: 持锁进程退出后同一条命令立刻跑通 (上面那条红不是永久红)",
          (r2.returncode, (ddir / "ohs.db").exists()), (0, True))

# ============ C. cleanup_expired 的删除闸 ============
print("\n=== C. 生命周期回收: 默认不删 / 显式才删 / 错配拒绝 ===")
P = lambda pid, name, data, age: (                        # noqa: E731
    pid, name, json.dumps(data), "ready", "", time.time() - age, time.time() - age)
KEEP = P("p_keep", "示例 kept 项目", {"equipment": ["锅炉"]}, 10)
STALE = P("p_stale", "未命名报告", {}, 40 * 86400)


def build(tmp: Path, rows: list[tuple], dirs: list[str], keep_ids_table: bool = True):
    """临时 DATA_DIR: 建 ohs.db(project/task_job) + materials/<dir>"""
    (tmp / "materials").mkdir(parents=True, exist_ok=True)
    for d in dirs:
        (tmp / "materials" / d).mkdir(parents=True, exist_ok=True)
    if keep_ids_table:
        c = sqlite3.connect(str(tmp / "ohs.db"))
        c.execute("CREATE TABLE project (id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL,"
                  " status TEXT DEFAULT 'draft', owner_id TEXT DEFAULT '', created REAL, updated REAL)")
        c.execute("CREATE TABLE task_job (id INTEGER PRIMARY KEY AUTOINCREMENT, pid TEXT, kind TEXT)")
        c.executemany("INSERT INTO project VALUES (?,?,?,?,?,?,?)", rows)
        c.commit()
        c.close()
    return tmp / "materials"


def use_tree(tmp: Path, materials_dir: Path | None = None):
    """把 projects_db / paths / oel 的模块全局指到临时树 (测完必须还原)。

    ⚠ 必须连 `oel.DB_PATH` 一起改: `connect_ro()` 读的是**它自己模块里的** DB_PATH,
      只改 `projects_db.DB` 会让"写路径读临时库、读路径读真实项目表"混在一起 ——
      本轮 D1 把 cleanup_expired 的 dry-run 改成走 `_ro()` 时就是这样假绿了一次
      (真实库里 55 个项目 id 被当成临时库的 id 用)。所以还原后下面还有对表检查。
    """
    import knowledge.oel as oel
    saved = (pdb.DB, pdb.MATERIALS_DIR, pdb.DATA_DIR, paths.DATA_DIR,
             paths.DB_PATH, paths.db_uri, oel.DB_PATH)
    pdb.DB = tmp / "ohs.db"
    pdb.MATERIALS_DIR = materials_dir or (tmp / "materials")
    pdb.DATA_DIR = tmp
    paths.DATA_DIR = tmp
    paths.DB_PATH = tmp / "ohs.db"
    paths.db_uri = lambda read_only=False: (f"file:{tmp / 'ohs.db'}?mode=ro" if read_only
                                            else str(tmp / "ohs.db"))
    oel.DB_PATH = tmp / "ohs.db"
    return saved


def restore(saved):
    import knowledge.oel as oel
    (pdb.DB, pdb.MATERIALS_DIR, pdb.DATA_DIR, paths.DATA_DIR,
     paths.DB_PATH, paths.db_uri, oel.DB_PATH) = saved


def points_at(tmp: Path) -> tuple[str, str]:
    """返回 (只读连接看到的库文件, 可写连接看到的库文件) —— 用 PRAGMA database_list 实证"""
    def fn(conn):
        r = conn.execute("PRAGMA database_list").fetchone()
        return str(Path(r[2]).resolve())
    a = fn(pdb._ro())
    b = fn(pdb._conn())
    return a, b


def live(md: Path) -> list[str]:
    return sorted(p.name for p in md.iterdir()) if md.exists() else []


with tempfile.TemporaryDirectory() as td4:
    tmp = Path(td4) / "d"
    md = build(tmp, [KEEP, STALE], ["p_keep", "orphan_a", "orphan_b"])
    saved = use_tree(tmp)
    try:
        want = str((tmp / "ohs.db").resolve())
        check("读/写两个工厂都指到临时库 (否则 C 段测的是真实项目表, 结论无效)",
              points_at(tmp), (want, want))
        out = pdb.cleanup_expired(verbose=False)
        check("默认两个开关都关: 目录与行都不动", live(md), ["orphan_a", "orphan_b", "p_keep"])
        check("默认 dry-run 把两类要删的都报出来 (可见性: 行 1 / 目录 2, 一个都没真删)",
              (out["dropped_rows"], out["would_drop_rows"],
               out["dropped_dirs"], out["would_drop_dirs"],
               out["apply_rows"], out["apply_dirs"]),
              (0, 1, 0, 2, False, False))

        # 分家的核心判据: 删行 ≠ 删目录。生产默认走这一条 (ROWS=1 / DIRS=0)。
        o_r = pdb.cleanup_expired(apply_rows=True, verbose=False)
        check("apply_rows=True 只删空项目行, **一个目录都不碰**",
              live(md), ["orphan_a", "orphan_b", "p_keep"])
        c = sqlite3.connect(str(tmp / "ohs.db"))
        ids_after_rows = sorted(r[0] for r in c.execute("SELECT id FROM project"))
        c.close()
        check("过期空项目的行确实被回收 (kept 仍在)", ids_after_rows, ["p_keep"])
        check("这一条的 dropped_dirs 必须仍是 0 (两把开关不许互相搭车)", o_r["dropped_dirs"], 0)

        o_d = pdb.cleanup_expired(apply_dirs=True, verbose=False)
        check("apply_dirs=True 才真的删孤儿目录", live(md), ["p_keep"])
        check("apply_dirs 计数口径: 真删 2 个 (此时库里只剩 p_keep)",
              (o_d["dropped_dirs"], o_d["would_drop_dirs"], o_d["apply_dirs"]), (2, 2, True))
        check("有名字/有数据的 kept 项目目录不受影响", (tmp / "materials" / "p_keep").is_dir(), True)
    finally:
        restore(saved)

with tempfile.TemporaryDirectory() as td5:
    tmp = Path(td5) / "d"
    md = build(tmp, [KEEP, STALE], ["p_keep"])
    saved = use_tree(tmp)
    try:
        out = pdb.cleanup_expired(verbose=False)
        c = sqlite3.connect(str(tmp / "ohs.db"))
        n = c.execute("SELECT COUNT(*) FROM project").fetchone()[0]
        c.close()
        check("dry-run 不删项目行 (TTL 回收也要显式开关)", n, 2)
        check("dry-run 只报不删 (TTL 回收也要显式开关): 检出 1 行、真删 0 行",
              (out["dropped_rows"], out["would_drop_rows"], n), (0, 1, 2))
        pdb.cleanup_expired(apply_rows=True, verbose=False)
        c = sqlite3.connect(str(tmp / "ohs.db"))
        ids = sorted(r[0] for r in c.execute("SELECT id FROM project"))
        n_job = c.execute("SELECT COUNT(*) FROM task_job").fetchone()[0]
        c.close()
        check("apply_rows 后只剩 kept, 过期行与其任务一起回收", (ids, n_job), (["p_keep"], 0))
    finally:
        restore(saved)

print("\n=== C2. 错配即拒绝 (这两条是 193MB 事故的正身) ===")
with tempfile.TemporaryDirectory() as td6:
    tmp = Path(td6) / "d"
    # 情形1: 库是空的 (副本库 / 挂错 volume / WAL 未回滚) 但材料目录里有真货
    md = build(tmp, [], ["p_keep", "orphan_a", "orphan_b"], keep_ids_table=False)
    c = sqlite3.connect(str(tmp / "ohs.db"))
    c.execute("CREATE TABLE project (id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL,"
              " status TEXT DEFAULT 'draft', owner_id TEXT DEFAULT '', created REAL, updated REAL)")
    c.execute("CREATE TABLE task_job (id INTEGER PRIMARY KEY, pid TEXT, kind TEXT)")
    c.commit()
    c.close()
    saved = use_tree(tmp)
    try:
        err = ""
        try:
            pdb.cleanup_expired(apply_dirs=True, verbose=False)
            err = "没报错(不该)"
        except pdb.LifecycleRefused:
            err = "拒绝"
        check("project 表读到 0 行 + 材料目录非空 → apply_dirs=True 必须拒绝", err, "拒绝")
        check("拒绝之后一个目录都没少", live(md), ["orphan_a", "orphan_b", "p_keep"])
        out = pdb.cleanup_expired(verbose=False)
        check("同一棵树默认不动手不报错, 但必须把拒绝原因报出来 (不许静默不扫)",
              (out["would_drop_dirs"], out["refused"] != "", live(md) == []), (0, True, False))
    finally:
        restore(saved)

with tempfile.TemporaryDirectory() as td7:
    tmp = Path(td7) / "d"
    outside = Path(td7) / "elsewhere" / "materials"
    outside.mkdir(parents=True)
    for d in ("p_keep", "orphan_a"):
        (outside / d).mkdir()
    build(tmp, [KEEP], [])
    saved = use_tree(tmp, materials_dir=outside)
    try:
        err = ""
        try:
            pdb.cleanup_expired(apply_dirs=True, verbose=False)
            err = "没报错(不该)"
        except pdb.LifecycleRefused:
            err = "拒绝"
        check("MATERIALS_DIR 不在 DATA_DIR 子树内 → apply_dirs=True 必须拒绝 (闸1)", err, "拒绝")
        check("拒绝之后外部目录完好", live(outside), ["orphan_a", "p_keep"])
    finally:
        restore(saved)

print("\n=== C3. 阳性对照: 旧逻辑 (无闸) 确实会把目录全删 ===")
with tempfile.TemporaryDirectory() as td8:
    tmp = Path(td8) / "d"
    md = build(tmp, [], ["p_real_1", "p_real_2", "p_real_3"], keep_ids_table=False)
    c = sqlite3.connect(str(tmp / "ohs.db"))
    c.execute("CREATE TABLE project (id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL,"
              " status TEXT DEFAULT 'draft', owner_id TEXT DEFAULT '', created REAL, updated REAL)")
    c.execute("CREATE TABLE task_job (id INTEGER PRIMARY KEY, pid TEXT)")
    c.commit()
    ids = {r[0] for r in c.execute("SELECT id FROM project")}   # 空库 → 空集, 正是事故现场
    c.close()
    check("对照前提: 空库里确实读不到任何 id", ids, set())
    import shutil
    for d in md.iterdir():                                      # 逐字照抄改前的循环
        if d.is_dir() and d.name not in ids:
            shutil.rmtree(d, ignore_errors=True)
    check("旧实现在同一棵树上删光了全部真实材料目录 (证明 C2 不是空转)", live(md), [])

print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 ===")
sys.exit(1 if n_fail else 0)
