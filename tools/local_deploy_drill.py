#!/usr/bin/env python3
"""本地部署演练 (S1c′ D3) —— 真起 uvicorn, 而不是只 import 代码。

为什么需要它 (2026-10-15):
  D0 把生命周期回收从 import 期挪进启动钩子之后, 守卫套能证明的只是
  「`_bootstrap()` 这个函数被调用时干了什么」。生产跑的是
  `uvicorn web.app:app --workers 2` (Dockerfile CMD), 里面还有三件事守卫**结构上看不见**:
    1. lifespan 钩子在真实 ASGI 启动里到底有没有被触发 (on_event/lifespan 写法差别);
    2. **两个 worker** 各自跑一次 `_bootstrap` → 两次并发写库 (SQLite busy/锁);
    3. 环境变量 → 行为 的映射 (`OHS_LIFECYCLE_ROWS/DIRS` 默认值) 只有真进程能验。
  本轮演练正是这样发现过头空 volume 首启崩溃 (data/ 目录不存在 → sqlite 打不开) 的。

做法: 用真实生产库的**只读副本** (sqlite backup API, 一致快照) + 材料目录**骨架**
      当 volume, 在临时 DATA_DIR 里起服务, 逐场景检查启动日志与 HTTP 响应。
      真实 data/ 全程只做只读取证, 结束时核对它一个字节都没被改 (G 段)。

场景:
  A 空 volume 首启             —— compose 挂新卷的形态: 不许崩, /login 必须 200
  B 生产副本 volume + 默认开关 —— ROWS=1/DIRS=0: 回收到期空项目行, **一个材料目录都不删**,
                                  官方层一字不变, 留下的项目行一字不变
  C 显式 DIRS=1                —— 孤儿目录这条 opt-in 路径真的通 (env → 行为, 函数级守卫测不到)
  D 不设任何 env               —— 代码默认必须与 compose 默认一致 (会删行 / 不删目录)
  E 真实读接口                 —— /login / 首页 / 项目数据 / 材料列表 / 概览, 审计确实写进库
  F 变异对照                   —— M1 DIRS 打开后尺子必须「看见」目录消失;
                                  M2 空库 + 有材料目录 + DIRS=1 必须被闸2 拒绝, 目录不许少
  H 镜像形状的守卫套            —— 按 Dockerfile 的 COPY 集裁一棵树 + 挂副本卷, 在里面跑
                                  GUARD_TESTS 全套: 全绿且副本卷一格不变 (S1c″ D4′ 收尾新增,
                                  它复现的是"本地全绿、容器里 FileNotFoundError"那一类事故)
  G 真实 data/ 零改动          —— 演练不许碰生产数据本身

用法: .venv/bin/python tools/local_deploy_drill.py [--keep]
      --keep 保留临时卷 (含 logs/ 与 vol_prod/) 便于事后手工看。

⚠ 这是**部署演练**, 不是守卫: 它要起服务器、复制 6MB 库、跑几十秒, 还要占 18099 端口。
  所以它进 quick_deploy.sh 的 MD5_FILES (交付链完整性), 但**不进** GUARD_TESTS/LOCAL_TESTS ——
  容器里跑它等于自己跟自己抢端口, 而且它会读宿主机 data/。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # 以 tools/xxx.py 直接执行时 sys.path[0] 是 tools/, 不是仓库根
# H 段用的两把尺子与守卫名单都取自 chain_audit (单一真源): 判据范围 = Dockerfile 的 COPY 集,
# 名单 = quick_deploy.sh 的 GUARD_TESTS —— 都在这里被**读出来**, 不抄第二份。
from tools.chain_audit import guard_tests, guard_write_opens, image_paths  # noqa: E402
REAL_DATA = ROOT / "data"
PYBIN = sys.executable
PORT = 18099                      # 演练端口: 日常/生产的 18000 绝不占用
WAIT_READY = 150                  # 两个 worker 各自 import 全套 web 层的预算
LIFECYCLE_VARS = ("OHS_LIFECYCLE_ROWS", "OHS_LIFECYCLE_DIRS", "SEED_DEMO")
RUNTIME_TABLES = {"project", "task_job", "users", "audit_log", "sqlite_sequence"}
# 游客身份每次运行现生成。写死一个 cookie 值会撞上生产库里已有的真实游客行 ——
# 2026-10-16 就撞上了 (库里真有一个 guest-a1b2c3 的项目), 于是"游客首访自动建报告"
# 那条判据读到的是别人的旧行, 既证明不了首访建项目, 又把"新增行"的账算错。
GUEST_ID = f"guest-{secrets.token_hex(3)}"

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


def note(msg: str):
    print(f"     ⓘ {msg}")


# ---------------- 取证尺子 ----------------
def file_tree(base: Path) -> dict:
    """名称 → (类型, 大小, mtime_ns); 只对 ohs.db 另记 sha1。249MB 材料只 stat 不读。"""
    out = {}
    if not base.exists():
        return out
    for p in sorted(base.rglob("*")):
        st = p.stat()
        rel = str(p.relative_to(base))
        rec = f"{'DIR' if p.is_dir() else 'FILE'} {st.st_size} {st.st_mtime_ns}"
        if p.name == "ohs.db":
            rec += " " + hashlib.sha1(p.read_bytes()).hexdigest()[:12]
        out[rel] = rec
    return out


def diff_tree(before: dict, after: dict) -> dict:
    keys = set(before) | set(after)
    return {"added": sorted(k for k in keys if k not in before),
            "removed": sorted(k for k in keys if k not in after),
            "changed": sorted(k for k in keys if k in before and k in after
                              and before[k] != after[k]),
            "before": before, "after": after}


def content_changed(d: dict) -> list[str]:
    """changed 里筛掉「只是 mtime 变了」的条目: 真实 data/ 必须没被写过。
    ohs.db 用 sha1 判 (file_tree 只对它记哈希), 其余条目 size 变了才算内容变动。"""
    out = []
    for k in d["changed"]:
        b, a = d["before"][k].split(), d["after"][k].split()
        if k == "ohs.db":
            if b[-1] != a[-1]:
                out.append(k)
        elif b[1] != a[1]:
            out.append(k)
    return out


def wal_sidecar(rel: str) -> bool:
    """是不是 SQLite WAL 的副件 (`ohs.db-wal` / `ohs.db-shm`)。

    为什么"条目增删"判据要把它们排除在外 (2026-10-16 两轮实测, 不是推测):
      · 副本卷: 只跑全套**只读**守卫, 结果 added=['ohs.db-wal'] changed=['ohs.db-shm'];
      · 真实 data/: 演练前若没有副件, 演练中任何一支读真实库的守卫都会把 -wal/-shm 建出来,
        而**只读连接无权删除**它们 → G 段会因"环境本来干净不干净"而一次红一次绿。
    时红时绿的判据不能当证据, 所以这里按性质排除。

    ⚠ 能力边界 (写在这里而不是藏在心里): 排除之后, "条目增删"这条**管不到**"开过可写连接但
      没改数据"。那一半由另两条尺子管: ① `ohs.db` 本体 sha1/字节 (实测: 可写连接开过就会动本体
      字节, 哪怕一行都没改) ② 静态接线判据 chain_audit.guard_write_opens() (读函数里不许出现
      写侧工厂)。三条各抓一段, 谁也不许宣称自己能抓全部。
    """
    return rel.endswith(("-wal", "-shm"))


def ro(db: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def table_names(db: Path) -> list[str]:
    c = ro(db)
    t = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    c.close()
    return t


def official_tables(db: Path) -> list[str]:
    """官方数据层 = 全部表 - 运行时表。**不写死清单**: 加一张官方表时演练自动覆盖它,
    写死的话新表漂移会被漏掉 (这正是看板行号腐烂的同一种病)。"""
    return [t for t in table_names(db) if t not in RUNTIME_TABLES]


def table_dump(db: Path, only: list[str]) -> dict:
    """逻辑内容指纹: 每表全部行 → sha1。用字节哈希当尺子会把合法启动判成红
    (可写连接 checkpoint 会回写页头), 见 tools/test_import_purity.py 的 db_dump 说明。"""
    c = ro(db)
    out = {}
    for t in only:
        rows = [list(map(str, r)) for r in c.execute(f'SELECT * FROM "{t}" ORDER BY rowid')]
        out[t] = hashlib.sha1(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:12]
    c.close()
    return out


def rows_by_id(db: Path, table: str = "project") -> dict:
    c = ro(db)
    out = {r[0]: hashlib.sha1("|".join(map(str, r[1:])).encode()).hexdigest()[:12]
           for r in c.execute(f'SELECT * FROM {table} ORDER BY id')}
    c.close()
    return out


def count(db: Path, table: str, where: str = "") -> int:
    c = ro(db)
    try:
        n = c.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]
    except sqlite3.Error:
        n = -1
    c.close()
    return int(n)


def dir_names(md: Path) -> list[str]:
    return sorted(p.name for p in md.iterdir()) if md.exists() else []


def first_cell(db: Path, sql: str):
    """读一个标量 (只读连接)。查不到或表不存在 → None, 由调用方的判据去红。"""
    c = ro(db)
    try:
        r = c.execute(sql).fetchone()
    except sqlite3.Error:
        r = None
    c.close()
    return r[0] if r else None


# ---------------- 服务器驱动 ----------------
class Server:
    """按 Dockerfile 的 CMD 起 uvicorn (--workers 2), env 完全照 compose 的形状给。"""

    def __init__(self, data_dir: Path, env: dict, tag: str, log_dir: Path):
        self.data_dir, self.tag = data_dir, tag
        self.log_path = log_dir / f"{tag}.log"
        self.env = {k: v for k, v in env.items() if v is not None}
        self.proc = None
        self.log = ""

    def start(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        # 与 Dockerfile 的 ENV 对齐: 不设 PYTHONUNBUFFERED 时子进程 stdout 是**块缓冲**,
        # worker 打的 [lifecycle] 行要等缓冲区满或退出才落盘 —— 演练会在日志里"看不见"
        # 第二个 worker 的启动记录 (2026-10-16 就是这么把 B 段判成 0 次 startup complete 的)。
        env["PYTHONUNBUFFERED"] = "1"
        for k in LIFECYCLE_VARS:
            env.pop(k, None)
        env["OHS_DATA_DIR"] = str(self.data_dir)
        env.update(self.env)
        self._fh = self.log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [PYBIN, "-m", "uvicorn", "web.app:app", "--host", "127.0.0.1",
             "--port", str(PORT), "--workers", "2", "--log-level", "info"],
            cwd=str(ROOT), env=env, stdout=self._fh, stderr=subprocess.STDOUT,
            start_new_session=True, text=True)
        RUNNING.append(self)

    def get(self, path: str, timeout: int = 30):
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                     headers={"Cookie": f"ohs_uid={GUEST_ID}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def wait_ready(self, timeout: int = WAIT_READY):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return "exited", self.proc.returncode
            try:
                if self.get("/login", timeout=5)[0] == 200:
                    return "ready", 0
            except Exception:
                pass
            time.sleep(0.5)
        return "timeout", -1

    def wait_workers(self, n: int = 2, timeout: int = 60) -> int:
        """等**全部** worker 报完成启动再往下走。

        为什么必须有这一步: `wait_ready()` 只要有一个 worker 应答就算就绪, 而
        `--workers 2` 下第二个 worker 可能还在 `_bootstrap` 里排队拿启动锁 ——
        这时去做断言, 读到的是"半台服务"。(这不是编出来的场景: 演练第一版就是这样
        把"两个 worker 都完成启动"读成 0 次的。)
        """
        deadline = time.time() + timeout
        got = 0
        while time.time() < deadline:
            got = self.log_path.read_text(encoding="utf-8", errors="replace").count(
                "Application startup complete")
            if got >= n:
                return got
            if self.proc.poll() is not None:
                return got
            time.sleep(0.3)
        return got

    def stop(self) -> str:
        if self.proc.poll() is None:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            for _ in range(60):
                if self.proc.poll() is not None:
                    break
                time.sleep(0.25)
            else:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        self.proc.wait(timeout=15)
        self._fh.close()
        self.log = self.log_path.read_text(encoding="utf-8", errors="replace")
        return self.log

    def tail(self) -> str:
        """直接读日志文件 —— 服务还在跑的时候也能读到已落盘的部分。
        (第一版 `faults()` 读的是 self.log, 而那只在 stop() 后才被赋值 →
         启动期调它等于对空串做检查, 是一条**必然绿**的假判据。2026-10-16 自己踩到的。)"""
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def faults(self) -> list[str]:
        """启动日志里的真实故障。口径收紧到能定位问题的串, 不抓 uvicorn 的正常提示。"""
        log = self.tail()
        return [k for k in ("Traceback (most recent call last)", "database is locked",
                            "unable to open database file", "OperationalError",
                            "LifecycleRefused", "IntegrityError", "ERROR:    Traceback")
                if k in log]


RUNNING: list[Server] = []


def kill_residual():
    """演练必须收干净: 中途抛异常留下的进程会占着 18099 和临时卷, 下次跑就"看起来超时"。
    只收自己起的 pid (绝不 pkill uvicorn —— 开发机的 18000 服务同属一个命令名)。"""
    for s in RUNNING:
        try:
            if s.proc and s.proc.poll() is None:
                os.killpg(os.getpgid(s.proc.pid), signal.SIGTERM)
                time.sleep(1.0)
                if s.proc.poll() is None:
                    os.killpg(os.getpgid(s.proc.pid), signal.SIGKILL)
        except OSError:
            pass


def port_free() -> bool:
    import socket
    with socket.socket() as sk:
        sk.settimeout(0.5)
        return sk.connect_ex(("127.0.0.1", PORT)) != 0


# ---------------- volume 准备 ----------------
def clone_production_volume(dst: Path, cat_keys: set[str]) -> tuple[str, int, int]:
    """真实库只读副本 + 材料目录骨架 + 其余数据文件。

    返回 (深拷的项目 id, 该项目的文件总数, 其中落在已知类别目录里的文件数)。

    为什么材料只搬目录名: 孤儿判定看的是**目录名 vs 库里的项目 id**, 与里面那 249MB
    PDF 无关; 全搬会把演练耗时都花在复制上。深拷挑"最小的、且至少有一个类别目录文件"的
    项目, 这样 `/materials` 接口读到的是真实文件, 计数还能与文件系统对表。
    """
    s = sqlite3.connect(f"file:{REAL_DATA / 'ohs.db'}?mode=ro", uri=True)
    t = sqlite3.connect(str(dst / "ohs.db"))
    s.backup(t)
    t.close()
    s.close()
    for f in sorted(REAL_DATA.iterdir()):
        if f.is_file() and not f.name.startswith("ohs.db"):
            shutil.copy2(f, dst / f.name)
    md_src, md_dst = REAL_DATA / "materials", dst / "materials"
    md_dst.mkdir(parents=True, exist_ok=True)
    all_dirs = [d for d in sorted(md_src.iterdir()) if d.is_dir()]
    for d in all_dirs:                          # 骨架照搬**全部**目录: 孤儿目录也要在副本里,
        (md_dst / d.name).mkdir(exist_ok=True)   # 否则 F 段的孤儿回收判据就没了被测对象

    # 深拷只挑**副本库里有行**的目录。2026-10-16 实测: 不按这个条件筛时挑中的是孤儿目录
    # (库里没有对应 project 行) → `/api/projects/<pid>/materials` 返回 404、
    # "有名字/有材料的项目没被误删" 判成 False, 两条红都是演练自己的取景错, 不是服务的错。
    have = set()
    probe_db = ro(dst / "ohs.db")
    for r in probe_db.execute("SELECT id FROM project"):
        have.add(r[0])
    probe_db.close()
    src_dirs = [d for d in all_dirs if d.name in have]

    def tally(d: Path) -> tuple[int, int]:
        files = [f for f in d.rglob("*") if f.is_file()]
        n_cat = sum(1 for f in files if f.parent.name.split("_", 1)[0] in cat_keys)
        return len(files), n_cat

    deep, deep_all, deep_cat = "", 0, 0
    for d in src_dirs:
        n_all, n_cat = tally(d)
        if n_cat and (deep_cat == 0 or n_all < deep_all):
            deep, deep_all, deep_cat = d.name, n_all, n_cat
    if deep:
        shutil.copytree(md_src / deep, md_dst / deep, dirs_exist_ok=True)
    return deep, deep_all, deep_cat


def dry_run_prediction(data_dir: Path) -> dict:
    """在服务之外先用函数自己的 dry-run 拿「应该回收多少」的预测值 (只读连接, 不动手)。"""
    code = "import json, web.projects_db as p;" \
           "print(json.dumps(p.cleanup_expired(verbose=False)))"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["OHS_DATA_DIR"] = str(data_dir)
    for k in LIFECYCLE_VARS:
        env.pop(k, None)
    r = subprocess.run([PYBIN, "-c", code], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError(f"dry-run 预测失败: {r.stderr[-300:]}")
    return json.loads(r.stdout.strip())


def pick_read_project(db: Path) -> tuple[str, str]:
    """挑一个「有名字、data 最大」的项目做读接口取证 —— 它一定不会被 TTL 回收。"""
    c = ro(db)
    r = c.execute("SELECT id, name FROM project WHERE name NOT LIKE '未命名%' "
                  "ORDER BY length(data) DESC LIMIT 1").fetchone()
    c.close()
    return (r["id"], r["name"]) if r else ("", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时 volume")
    args = ap.parse_args()

    if not (REAL_DATA / "ohs.db").exists():
        print(f"❌ 找不到 {REAL_DATA}/ohs.db —— 演练用真实生产库的副本, 缺库没法演练")
        return 1
    if not port_free():
        print(f"❌ 端口 {PORT} 已被占用 —— 先手工收干净再跑 (演练绝不去 kill 别人的进程)")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="ohs_drill_"))
    log_dir = tmp / "logs"
    log_dir.mkdir()
    real_before = file_tree(REAL_DATA)
    print(f"演练临时根目录: {tmp}\n服务命令: {shlex.join(['uvicorn', 'web.app:app', '--host', '127.0.0.1', '--port', str(PORT), '--workers', '2'])}")

    try:
        # ============ A. 空 volume 首启 ============
        print("\n=== A. 空 volume 首启 (compose 挂新卷的形态) ===")
        empty = tmp / "vol_empty"
        empty.mkdir()
        s = Server(empty, {}, "A_first_boot", log_dir)
        s.start()
        check("空 volume 上服务起来并就绪 (不靠预置库兜住)", s.wait_ready()[0], "ready")
        check("两个 worker 都完成启动 (改前这里只有 1 个: 另一个撞在 users 唯一键上退了)",
              s.wait_workers(), 2)
        check("GET /login → 200", s.get("/login")[0], 200)
        check("GET / → 200 (游客首页)", s.get("/")[0], 200)
        check("GET /api/reports → 200 (空库上的读接口不许 500)", s.get("/api/reports")[0], 200)
        s.stop()
        check("启动日志无故障 (崩溃/锁/打不开库)", s.faults(), [])
        db = empty / "ohs.db"
        check("首启确实建出库 + 运行时表 + 官方层表",
              db.exists() and {"project", "users", "task_job", "oel_limit"}
              <= set(table_names(db)), True)
        fresh_tabs, real_tabs = set(table_names(db)), set(table_names(REAL_DATA / "ohs.db"))
        note(f"空 volume 首启只建出 {len(fresh_tabs)} 张表; 真实库有 {len(real_tabs)} 张, "
             f"差集 {sorted(real_tabs - fresh_tabs)} → 官方层那 "
             f"{len(real_tabs - fresh_tabs)} 张表不是 DDL 建的, 是装载脚本灌的。"
             f"挂错卷的后果不是「数据是空的」, 而是「表都不在」(读口按 OfficialSchemaMissing 响)。")
        n0 = count(db, "project")
        s = Server(empty, {}, "A_second_boot", log_dir)
        s.start()
        check("同一空卷重启第二次也就绪", s.wait_ready()[0], "ready")
        check("第二次启动两个 worker 也都完成", s.wait_workers(), 2)
        s.stop()
        check("第二次启动日志无故障", s.faults(), [])
        check("第二次启动没多写项目行 (没有到期可删的东西, 也就不该有删除动作)",
              count(db, "project"), n0)

        # ============ B. 生产副本 volume + compose 默认开关 ============
        print("\n=== B. 生产库副本 + 材料骨架, ROWS=1/DIRS=0 (compose 默认) ===")
        vol = tmp / "vol_prod"
        vol.mkdir()
        t0 = time.time()
        from web.uploads import CATEGORIES            # 只读导入: 类别 key 的单一真源
        cat_keys = {c["key"] for c in CATEGORIES}
        pid_deep, n_files_deep, n_files_cat = clone_production_volume(vol, cat_keys)
        db = vol / "ohs.db"
        pid_read, name_read = pick_read_project(db)
        md = vol / "materials"
        check("前提对表: 深拷项目在副本库里**有行** (挑到孤儿目录时下面的 materials/删除判据全是取景错)",
              bool(pid_deep), True)
        note(f"卷就绪 ({time.time() - t0:.1f}s): {count(db, 'project')} 项目行, "
             f"{len(dir_names(md))} 材料目录, 深拷项目 {pid_deep} "
             f"(文件 {n_files_deep}, 其中类别目录下 {n_files_cat}), 读取证项目 {pid_read}/{name_read[:16]}")
        pred = dry_run_prediction(vol)
        dirs_before = dir_names(md)
        guest_pid_before = first_cell(db, f"SELECT id FROM project WHERE owner_id='{GUEST_ID}'")
        check(f"前提对表: 本次游客身份 {GUEST_ID} 在副本库里原本不存在 (否则下面的'首访建报告'是空判)",
              guest_pid_before, None)
        # 孤儿集按「目录名不在项目 id 集合里」独立算一遍 —— 这是孤儿的**定义**, 不是抄生产实现。
        # 必须先登记: 生产库副本里本来就留着真人产生的孤儿目录, DIRS=1 会连它们一起删,
        # 不预先算好就会把"删对了"看成"删多了"。
        proj_before = rows_by_id(db)
        orphans0 = sorted(d for d in dirs_before if d not in set(proj_before))
        rows_before = count(db, "project")
        off_before = table_dump(db, official_tables(db))
        rt_before = table_dump(db, ["task_job", "users"])
        note(f"服务外的 dry-run 预测: 到期空项目行 {pred['would_drop_rows']}, "
             f"孤儿材料目录 {pred['would_drop_dirs']}")
        check("dry-run 预测与演练独立算出的孤儿集一致 (两把尺子对表, 不是各说各话)",
              len(orphans0), pred["would_drop_dirs"])
        note(f"副本里真实遗留的孤儿目录: {orphans0}")

        s = Server(vol, {"OHS_LIFECYCLE_ROWS": "1", "OHS_LIFECYCLE_DIRS": "0"},
                   "B_default_flags", log_dir)
        s.start()
        check("生产副本卷上服务就绪 (--workers 2 并发跑 _bootstrap)", s.wait_ready()[0], "ready")
        check("两个 worker 都完成启动 (启动锁串行化后仍然一个不少)", s.wait_workers(), 2)
        check("两个 worker 并发启动的日志无故障 (SQLite 锁/唯一键/崩溃)", s.faults(), [])
        check("GET /login → 200", s.get("/login")[0], 200)
        # 首页 index.html 里并不内嵌 pid (`{{ pid }}` 不存在), 所以"首页该出现项目名/pid"
        # 这种期望是错的。身份→报告的那条链要用 `/api/report` 验: 它按当前身份取回唯一报告。
        check("GET / → 200 (游客首页, 状态码级)", s.get("/")[0], 200)
        code, body = s.get("/api/report")
        guest_pid = first_cell(db, f"SELECT id FROM project WHERE owner_id='{GUEST_ID}'")
        try:
            api_pid = json.loads(body).get("pid")
        except Exception:
            api_pid = None
        check(f"游客首访自动建报告 ({GUEST_ID}), 且 /api/report 按身份取回的就是这一份",
              (code, bool(guest_pid), api_pid == guest_pid), (200, True, True))
        code, page = s.get(f"/projects/{pid_read}")
        check(f"GET /projects/<pid> → 200 且页面渲染出真实项目名 ({name_read[:8]})",
              (code, name_read[:8] in page), (200, True))
        code, body = s.get(f"/api/projects/{pid_read}/data")
        try:
            ok = code == 200 and isinstance(json.loads(body), dict) and len(body) > 50
        except Exception:
            ok = False
        check("GET /api/projects/<pid>/data → 200 且是非空 JSON (读门真的通)", ok, True)
        code, body = s.get(f"/api/projects/{pid_deep}/materials")
        got_files = 0
        try:
            for c_ in json.loads(body)["categories"]:
                got_files += len(c_["files"])
        except Exception:
            pass
        check("GET /api/projects/<pid>/materials → 200, 返回文件数 = 类别目录下的真实文件数",
              (code, got_files), (200, n_files_cat))
        if n_files_deep != n_files_cat:
            note(f"该接口按类别目录 (C1_/C6_…) 分组返回, 落在旧命名目录 (如 A2c_) 里的 "
                 f"{n_files_deep - n_files_cat} 个文件不进 categories —— 这是既有语义 "
                 f"(list_materials 全量, 接口按类别组织), 演练如实记账, 不把它算成失败")
        check("GET /api/projects/<pid>/overview → 403 (游客≠owner 的既有身份隔离, 必须仍然成立)",
              s.get(f"/api/projects/{pid_read}/overview")[0], 403)
        s.stop()
        log_b = s.log
        note("lifecycle 行: " + str([l for l in log_b.splitlines() if "lifecycle" in l][:2]))

        rows_after, proj_after = count(db, "project"), rows_by_id(db)
        gone = sorted(set(proj_before) - set(proj_after))
        added = sorted(set(proj_after) - set(proj_before))
        check(f"回收掉的行集大小 = 服务外 dry-run 的预测值 ({pred['would_drop_rows']})",
              len(gone), pred["would_drop_rows"])
        check("演练期间新增的行只有一种: 游客首访自动建的那份报告 (上面那条差值才没被它污染)",
              added, [guest_pid] if guest_pid else [])
        check("DIRS=0 → 一个材料目录都没被删 (这就是生产默认的全部含义)",
              dir_names(md), dirs_before)
        check("官方数据层逻辑内容一字不变 (演练只该动 project/task_job/audit_log)",
              table_dump(db, official_tables(db)), off_before)
        check("users/task_job 一字不变", table_dump(db, ["task_job", "users"]), rt_before)
        # 留下的行必须一字未改。游客那一份要排除: 每次 GET / 都会刷新它的 updated,
        # 那是演练自己的请求造成的, 不是回收逻辑动了别人的数据。
        kept_bad = sorted(i for i in set(proj_before) & set(proj_after)
                          if proj_after[i] != proj_before[i] and i != guest_pid)
        check(f"回收只删行: 留下的 {len(set(proj_before) & set(proj_after))} 行一字未改",
              kept_bad, [])
        note(f"回收 {len(gone)} 行 {gone}; 项目行数 {rows_before} → {rows_after} "
             f"(演练请求新增 {len(added)} 行: {added})")
        check("有名字/有材料的项目没被误删",
              pid_read in proj_after and pid_deep in proj_after, True)

        # ============ C. 显式 DIRS=1 ============
        print("\n=== C. 显式 OHS_LIFECYCLE_DIRS=1 (孤儿目录这条 opt-in 必须真的通) ===")
        orphan = "zz_drill_orphan"
        (md / orphan).mkdir()
        s = Server(vol, {"OHS_LIFECYCLE_ROWS": "1", "OHS_LIFECYCLE_DIRS": "1"},
                   "C_dirs_on", log_dir)
        s.start()
        check("DIRS=1 卷上服务就绪", s.wait_ready()[0], "ready")
        check("DIRS=1: 两个 worker 都完成启动", s.wait_workers(), 2)
        s.stop()
        check("DIRS=1 的启动日志无故障", s.faults(), [])
        dirs_c = dir_names(md)
        check("DIRS=1 删的正好是「孤儿集 + 我注入的孤儿」, 一个不多一个不少",
              dirs_c, sorted(set(dirs_before) - set(orphans0) - {orphan}))
        check("人为造的孤儿目录被删掉了 (env → 行为 的映射不是注释)", orphan in dirs_c, False)
        kept_ids = sorted(i for i in rows_by_id(db) if i in set(dirs_before))
        check(f"有项目行对应的 {len(kept_ids)} 个项目目录一个都没少 (DIRS=1 只咬孤儿)",
              [i for i in kept_ids if i not in set(dirs_c)], [])

        # ============ D. 不设 env: 代码默认必须与 compose 默认一致 ============
        print("\n=== D. 不设任何 OHS_LIFECYCLE_* (代码默认 vs compose 默认) ===")
        c = sqlite3.connect(str(db))
        now = time.time()
        c.execute("INSERT INTO project VALUES (?,?,?,?,?,?,?)",
                  ("drill_stale", "未命名报告", "{}", "draft", "",
                   now - 40 * 86400, now - 40 * 86400))
        c.commit()
        c.close()
        (md / "zz_drill_orphan2").mkdir()
        rows_d0 = count(db, "project")
        s = Server(vol, {}, "D_no_env", log_dir)
        s.start()
        check("无 env 也能就绪", s.wait_ready()[0], "ready")
        check("无 env: 两个 worker 都完成启动", s.wait_workers(), 2)
        s.stop()
        check("无 env 启动日志无故障", s.faults(), [])
        check("代码默认 = ROWS 1: 新塞的到期空项目行被回收 (与 compose 的 :-1 一致)",
              (count(db, "project"), count(db, "project", "WHERE id='drill_stale'")),
              (rows_d0 - 1, 0))
        check("代码默认 = DIRS 0: 孤儿目录**没**被删 (与 compose 的 :-0 一致)",
              "zz_drill_orphan2" in dir_names(md), True)

        # ============ E. 服务确实是个能干活的服务器 ============
        print("\n=== E. 服务真的在处理请求 (不只是把进程拉起来) ===")
        s = Server(vol, {}, "E_read", log_dir)
        s.start()
        check("就绪", s.wait_ready()[0], "ready")
        check("E: 两个 worker 都完成启动", s.wait_workers(), 2)
        a0 = count(db, "audit_log")
        for path in ("/", "/api/reports", f"/api/projects/{pid_read}/data"):
            check(f"GET {path} → 200", s.get(path)[0], 200)
        s.stop()
        check("请求结束后 audit_log 增加了 (中间件在跑)", count(db, "audit_log") > a0, True)
        check("这一轮日志无故障", s.faults(), [])

        # ============ F. 变异对照 ============
        print("\n=== F. 变异对照 (上面那些绿灯不是空转) ===")
        (md / "zz_mutant").mkdir()
        s = Server(vol, {"OHS_LIFECYCLE_DIRS": "1"}, "F_mutation_dirs_on", log_dir)
        s.start()
        s.wait_ready()
        s.stop()
        check("M1 DIRS=1 → 尺子必须「看见」目录消失 (否则 C 段是空转)",
              "zz_mutant" in dir_names(md), False)
        (md / "zz_mutant2").mkdir()
        s = Server(vol, {"OHS_LIFECYCLE_DIRS": "0"}, "F_mutation_dirs_off", log_dir)
        s.start()
        s.wait_ready()
        s.stop()
        check("M1b 同一棵树显式 DIRS=0 → 目录必须还在 (判据方向不能反)",
              (md / "zz_mutant2").exists(), True)

        # 阳性对照: 空库 + 有材料目录 + DIRS=1 → 闸2 必须拒绝, 一个目录都不许少
        bad = tmp / "vol_bad"
        (bad / "materials").mkdir(parents=True)
        b = sqlite3.connect(str(bad / "ohs.db"))
        bsrc = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        bsrc.backup(b)
        bsrc.close()
        b.execute("DELETE FROM project")
        b.commit()
        b.close()
        for name in dir_names(md)[:3]:
            (bad / "materials" / name).mkdir()
        before_bad = dir_names(bad / "materials")
        s = Server(bad, {"OHS_LIFECYCLE_ROWS": "0", "OHS_LIFECYCLE_DIRS": "1"},
                   "F_gun_on_empty_db", log_dir)
        s.start()
        ready_bad = s.wait_ready(timeout=60)
        s.stop()
        check("M2 库里读不到任何 id + 材料目录非空 + DIRS=1 → 必须拒绝 (193MB 事故的正身)",
              dir_names(bad / "materials"), before_bad)
        check("M2 拒绝必须**可见** (日志里要有拒绝原因, 不许静默不扫)",
              ("拒绝" in s.log) or ("LifecycleRefused" in s.log), True)
        note(f"M2 实测服务状态={ready_bad} (启动期抛错→进程退出是**期望**行为)")

        # ============ H. 镜像形状的守卫套 (部署第 4 步的真形状) ============
        print("\n=== H. 按 Dockerfile 的 COPY 集裁出镜像形状, 在里面跑 GUARD_TESTS 全套 ===")
        # 为什么要有这一段: 第 4 步不是"在仓库里跑守卫", 而是 `docker run --rm ohs-report:latest
        # python tools/x.py` —— 镜像里只有 Dockerfile COPY 的那几个路径 + 挂进来的 data 卷。
        # 2026-10-16 的实测事故正是这一类: chain_audit 的判据范围从 quick_deploy.sh 读,
        # 而镜像里**没有**这个文件 → test_dataset_door 在容器里 FileNotFoundError,
        # 整条守卫套崩, 本地却全绿。这一段把"镜像里有什么"当成输入重放那个形状。
        # 诚实的边界: 这棵树是镜像的**超集** —— 它按 COPY 集取文件, 不重跑打包时的 tar 排除
        # (knowledge/*.json 那一类不在这里暴露); tar 那一半由 quick_deploy 第 1 步 +
        # chain_audit.delivery_chain_gaps() (MD5_FILES 必须被 COPY 集覆盖) 管。
        img = tmp / "image"
        idirs, ifiles = image_paths(ROOT)
        check("前提对表: Dockerfile 的 COPY 集读得到 (空集会让这一段永远绿)",
              (len(idirs) >= 4, len(ifiles) >= 3, "tools" in idirs, "web" in idirs,
               "quick_deploy.sh" in ifiles), (True, True, True, True, True))
        for d in idirs:
            shutil.copytree(ROOT / d, img / d, ignore=shutil.ignore_patterns("__pycache__"))
        for f in ifiles:
            shutil.copy2(ROOT / f, img / f)
        vol = img / "data"                      # 容器里 /app/data 就是挂进来的生产卷
        vol.mkdir()
        clone_production_volume(vol, set())
        db_h = vol / "ohs.db"
        # 这里比 official_tables() 宽: 用**全部表**。守卫若写进 audit_log/project 这类运行期表,
        # 只比官方层会漏 (本轮实测: 官方层 dump 对 audit_log 的写入完全无感)。
        tabs_h = table_names(db_h)
        tree0, dump0 = file_tree(vol), table_dump(db_h, tabs_h)

        def run_guard(name: str) -> tuple[int, str]:
            # env 照容器的形状: 不设 PYTHONPATH (容器里也没有), cwd=镜像根, 数据卷就是 ./data
            env = {k: v for k, v in os.environ.items()
                   if k not in ("PYTHONPATH", "OHS_DATA_DIR") + LIFECYCLE_VARS}
            env["PYTHONUNBUFFERED"] = "1"
            r = subprocess.run([PYBIN, f"tools/{name}.py"], cwd=str(img), env=env,
                               capture_output=True, text=True, timeout=900)
            tail = ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]
            return r.returncode, tail

        gt = guard_tests(ROOT)
        bad = [f"{n}: rc={rc} 末行={tail[:110]}" for n in gt for rc, tail in [run_guard(n)] if rc]
        check(f"GUARD_TESTS {len(gt)} 支在镜像形状里全绿 (崩/红都要点名)", bad, [])
        for b in bad:
            print("      ", b)
        tree1, dump1 = file_tree(vol), table_dump(db_h, tabs_h)
        check("跑完全套守卫, 卷里**逻辑内容**一格没变 (逐表 dump 比对, 全表不只官方层)",
              dump1 == dump0, True)
        dh = diff_tree(tree0, tree1)
        # -wal/-shm 的口径走单一尺子 wal_sidecar() (副本卷与 G 段的真实 data/ 必须同一条线)
        check("跑完全套守卫, 卷里没有条目增删 (-wal/-shm 这两个只读连接也会建的副件除外)",
              [sorted(k for k in dh_ if not wal_sidecar(k)) for dh_ in (dh["added"], dh["removed"])],
              [[], []])
        # ohs.db 本体一个字节的动都不得有; -shm 允许动 (本轮实测: 只读连接也会改 -shm 的内容,
        # 它只是 WAL 的共享内存索引, 不是数据)。这条区别是测出来的, 不是猜的。
        check("跑完全套守卫, **ohs.db 本体**字节没变 (字节变=有可写连接开过, 哪怕没改数据)",
              [k for k in dh["changed"] if k == "ohs.db"], [])
        note(f"守卫在副本卷上留下的变化: added={dh['added']} removed={dh['removed']} "
             f"changed={dh['changed']} (changed 里只有 -shm 是允许的)")

        # 变异 (所以上面几条绿不是空转): 把 quick_deploy.sh 从镜像形状里删掉 → 必须红
        (img / "quick_deploy.sh").unlink()
        rc_mut, tail_mut = run_guard("test_dataset_door")
        check("变异: 删掉镜像里的 quick_deploy.sh → test_dataset_door 必须非零退出并点名它 "
              "(本轮事故的复现, 也证明上面那 19 支不是空转)",
              (rc_mut != 0, "quick_deploy.sh" in tail_mut), (True, True))
        if rc_mut == 0:
            print("       实得:", tail_mut[:150])
        shutil.copy2(ROOT / "quick_deploy.sh", img / "quick_deploy.sh")
        rc_back, tail_back = run_guard("test_dataset_door")
        check("对照: 文件放回去后必须回到绿 (上面那条红不是永久红)",
              (rc_back, "0 红" in tail_back), (0, True))
        # 变异 2: 给一支守卫注入一次真写 → 副本卷的内容 dump 必须变 (第 4 步的红要看得见)
        victim = img / "tools" / "test_toxicity_match.py"
        victim.write_text(victim.read_text(encoding="utf-8") +
                          "\nfrom knowledge.oel import connect as _w\n_c = _w()\n"
                          "_c.execute(\"INSERT INTO audit_log(user, method, path, status) "
                          "VALUES('__drill','X','/y',1)\")\n_c.commit(); _c.close()\n",
                          encoding="utf-8")
        t2 = file_tree(vol)
        a_before = count(db_h, "audit_log")
        rc_v, _ = run_guard("test_toxicity_match")
        t3 = file_tree(vol)
        changed_keys = sorted(k for k in t3 if k in t2 and t3[k] != t2[k])
        static_hit = [x for x in guard_write_opens(img) if "test_toxicity_match" in x]
        check("变异: 守卫里注入一次真写 → audit_log 必须多一行、ohs.db 本体字节必须变",
              (count(db_h, "audit_log") - a_before, "ohs.db" in changed_keys), (1, True))
        check("变异: 同一次注入必须被**静态接线判据**抓到 (guard_write_opens 点名它)",
              len(static_hit) >= 1, True)
        note(f"注入写的实测: 守卫自身退出码={rc_v} —— 写成功时它照样绿, 所以行为尺子(dump/字节) "
             f"与静态尺子(guard_write_opens) 两条都得有; 变动条目={changed_keys[:4]}")

        # ============ G. 真实 data/ 未被演练触碰 ============
        print("\n=== G. 真实 data/ 全程只读 ===")
        d = diff_tree(real_before, file_tree(REAL_DATA))
        # -wal/-shm 走和副本卷同一个尺子: 演练里读真实库的守卫会把它们建出来, 而只读连接删不掉
        #   → 不排除的话这条判据就成了"环境干净不干净"的函数, 一次红一次绿 = 假证据。
        check(f"真实 data/ 没有新增/删除条目 ({len(real_before)} 条目; -wal/-shm 除外, 见 wal_sidecar)",
              [sorted(k for k in d["added"] if not wal_sidecar(k)),
               sorted(k for k in d["removed"] if not wal_sidecar(k))], [[], []])
        check("真实 data/ 没有任何**内容**变化 (ohs.db 按 sha1, 其余按 size; mtime 允许变)",
              content_changed(d), [])
        if any(wal_sidecar(k) for k in d["added"]):
            note(f"演练中真实 data/ 出现了 {[k for k in d['added'] if wal_sidecar(k)]} —— "
                 "只读连接建的合法副件 (只读连接删不掉它们)。它**不代表**开过可写连接: "
                 "那一半由上面 ohs.db 的 sha1 + chain_audit.guard_write_opens() 管 (三条尺子各抓一段)。")
        # 尺子自检 (不真往 data/ 写东西, 只喂构造的 before/after): 排除副件不等于把整条判据废掉。
        fab = diff_tree({"ohs.db": "FILE 1 1 abc", "materials": "DIR 0 0"},
                        {"ohs.db": "FILE 1 1 abc", "materials": "DIR 0 0",
                         "ohs.db-wal": "FILE 0 1", "materials/x.docx": "FILE 5 1"})
        check("尺子自检: 新增一个**真数据条目** (materials/x.docx) 必须被点名 (排除副件≠放过新增)",
              [k for k in fab["added"] if not wal_sidecar(k)], ["materials/x.docx"])
        fab2 = diff_tree({"ohs.db": "FILE 1 1 abc"}, {"ohs.db": "FILE 1 1 def"})
        check("尺子自检: ohs.db 的 sha1 变了必须被点名 (这条才是『可写连接开过』的正面证据)",
              content_changed(fab2), ["ohs.db"])
        if content_changed(d):
            print("      变动条目:", {k: (d['before'][k], d['after'][k])
                                       for k in content_changed(d)})
    finally:
        kill_residual()
        if args.keep:
            print(f"\n     ⓘ 临时卷保留在 {tmp} (logs/ 与 vol_prod/ 都在)")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 ===")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
