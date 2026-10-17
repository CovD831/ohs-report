#!/usr/bin/env python3
"""S1c′ D2 守卫: "验收链只读" 必须是能证伪的行为判据, 不是注释 (2026-10-15)

为什么重写: `tools/test_gate_sensitivity.py` 第 7 节原来那条是**字符串级**的
    "UPDATE " not in src and '.execute("INSERT' not in src
它查不出 `oel.connect()` 里的 `executescript(DDL)`, 查不出 `ALTER TABLE`, 也查不出
WAL 副作用 —— 而它保护的对象当时正开着可写连接跑 DDL。假绿了一整轮 (计划里的 W6)。

四把尺子, 全部在**跑完真实验收链**之后重算:
  1) `ohs.db` 内容哈希不变            —— 数据本身没被写
  2) `ohs.db-wal` 内容哈希不变         —— WAL 模式下"写"先落在这里
  3) 只读连接的 `PRAGMA data_version` 不变 —— 库内容版本 (同连接自写不变, 他连接提交才变)
  4) data/ 里**数据条目**的增删与大小不变 —— 抓 mkdir、写文件、删材料 (W5 那类事故)
     ⚠ `-wal` / `-shm` 的增删不参与判定: 实测它不可靠 (可写连接关闭有时 checkpoint 掉、
       有时留着), 而且只读连接打开 WAL 库也会**建出**这两个文件。留一个"时有时无"的证据
       = 假判据 (计划 §4 原来写的 `find data -newer` 同理会被 mtime 误报拖垮)。
     ⚠ `PRAGMA data_version` 对"0 行 UPDATE""改一行再改回"这类**净不变写**不敏感 (实测),
       所以它只是第三把尺子, 不能当唯一判据。
     结论: "开了可写连接但没改数据" 文件层判不出来 —— 那部分由第 2/4 节的**静态接线判据**
     (取数函数里不许出现 connect()/_conn()/写类 SQL) 负责, 两条合起来才完整。

变异用例 (全在临时 DATA_DIR + 真库副本上做, 不碰真实 data/):
  M7 新增数据条目 (建目录 / 建文件)   → 必须红 (W5 那类"删错/写错目录"事故的判据形状)
  M8 真改一行数据                    → 必须红 (内容哈希或 data_version 至少一条报出来)
  M5 注入一次建表 (schema 写)         → 必须红, 且报的必须是"ohs.db 内容哈希变了"
  M3 纯 connect_ro 读同样多的量       → 必须绿 (判据不是"任何连接都算写"的误报机器)
  M2 **能力边界如实记录**: 只开可写连接、不改数据 → 文件层判不出 → 由第 2/4 节静态判据负责
  M6 整条验收链在副本库上跑一遍       → 必须绿 (真实库上那条结论可复现, 不是本地巧合)
  M4 接线判据: 把链上一个 connect_ro 换回写侧工厂 / 把取数函数改成 _conn() → 必须红
  3′ 节 (S1c″ D4′): 6 个真读验收库的**守卫脚本自己**跑完, 副本库四把尺子必须不变,
     且缺库时必须非零退出; 配套 M9 (对副本真插一行 → 判据必须红) 与
     M10 (旧写法 if exists/else 跳过 → 必须被这条判据判红) 证它不是空转。
  (M1 "0 行 UPDATE" 被实测否掉 —— 同一句 SQL 两次跑一次有痕一次无痕, 不能当变异证据;
   理由写在第 3 节原处, 不许悄悄删)
用法: .venv/bin/python tools/test_acceptance_readonly.py    (退出码 0 = 全绿)
"""
import hashlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

DATA = ROOT / "data"
HASH_FILES = ("ohs.db", "ohs.db-wal")
PID = "ada54603a9"          # 验收链的一个项目就够判"写没写"; 全 4 项目由主门负责
# 接线判据的单一真源在 tools/chain_audit.py (test_gate_sensitivity 第 7 节也用同一把尺子)
from tools.chain_audit import CHAIN_READS, chain_write_sites  # noqa: E402
n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


# ============ 判据本体 ============
# 下面四条尺子不是拍出来的, 是 /tmp 真库副本上实测出来的 (2026-10-15, 同一份 ohs.db 复制多份):
#   A 只读连接查 20+ 行        → 新建 -shm/-wal(0 字节); db 哈希与 data_version 都不变
#   B oel.connect() 不执行 SQL → 有时把 -wal/-shm checkpoint 删掉, 有时留着 (两次结果相反)
#   C 0 行 UPDATE (WHERE 1=0)  → 同样一次有痕、一次无痕 ⇒ 不能当变异证据 (见第 3 节 M1)
#   D CREATE TABLE __probe     → ohs.db 哈希变了 (28e27307c4c2 → c8fe29fc1aff) ⇒ 可靠
#   E 改一行再改回              → db 哈希与 data_version 都不变 (净不变写) ⇒ 尺子3 的边界
# 由此定的判据: 内容哈希 (1/2) + data_version (3) + **数据**条目增删与大小 (4);
# WAL 辅助文件的增删剔除在外 (B 不可靠), mtime 整个不看 (A 会让合法读变红)。
WAL_SIDE = ("ohs.db-wal", "ohs.db-shm")


def entries(base: Path) -> dict:
    """data/ 全树: 每个条目的 (类型, 大小)。mtime 不入判据 (理由见上)。"""
    out = {}
    for p in sorted(base.rglob("*")):
        st = p.stat()
        out[str(p.relative_to(base))] = f"{'D' if p.is_dir() else 'F'}:{st.st_size}"
    return out


def hashes(base: Path) -> dict:
    """内容哈希。⚠ `-wal` 的"不存在"与"0 字节"视为**同一个状态** `<empty>`:
    只读连接会建出空 -wal, 可写连接关闭又会把它删掉 —— 这两种都不算写库, 只有
    -wal 里真的留下帧 (非空且内容变了) 才是写的证据。`ohs.db` 不适用这条归一。"""
    out = {}
    for name in HASH_FILES:
        p = base / name
        if not p.exists():
            out[name] = "<empty>" if name == "ohs.db-wal" else "<absent>"
        else:
            b = p.read_bytes()
            out[name] = "<empty>" if (not b and name == "ohs.db-wal") else hashlib.sha1(b).hexdigest()[:16]
    return out


def data_version(base: Path) -> int:
    """用只读连接取新鲜度计数 (mode=ro, 本身不写库)"""
    c = sqlite3.connect(f"file:{base / 'ohs.db'}?mode=ro", uri=True)
    try:
        return c.execute("PRAGMA data_version").fetchone()[0]
    finally:
        c.close()


def state(base: Path) -> dict:
    return {"entries": entries(base), "hash": hashes(base), "dv": data_version(base)}


def violations(before: dict, after: dict) -> list[str]:
    """四把尺子的合成判据: 返回空列表 = 这一轮真的没写"""
    e0, e1 = before["entries"], after["entries"]
    out = []
    # 尺子1/2: 数据文件内容
    for name in HASH_FILES:
        if before["hash"][name] != after["hash"][name]:
            out.append(f"{name} 内容哈希变了 ({before['hash'][name]} → {after['hash'][name]})")
    # 尺子3: 库内容版本
    if before["dv"] != after["dv"]:
        out.append(f"PRAGMA data_version 变了 ({before['dv']} → {after['dv']})")
    # 尺子4: 数据条目增删/大小变化。WAL 辅助文件 (-wal/-shm) 的增删**不参与判定**:
    # 实测它不可靠 —— `oel.connect()` 关闭有时把 -wal/-shm checkpoint 掉、有时留着不动
    # (本轮 M2 两次跑结果相反)。留着一个"时有时无"的证据 = 假判据。
    # "开了可写连接但没改任何数据"这件事**文件层判不出来**, 那是第 2/4 节静态接线判据的
    # 职责 (connect() / _conn() 出现在取数函数里 = 红), 两条合起来才完整。
    other = lambda k: k not in WAL_SIDE
    added = sorted(k for k in set(e1) - set(e0) if other(k))
    removed = sorted(k for k in set(e0) - set(e1) if other(k))
    sized = sorted(k for k in set(e0) & set(e1) if other(k) and e0[k] != e1[k])
    if added:
        out.append(f"多出条目 {added[:6]}")
    if removed:
        out.append(f"数据条目消失 {removed[:6]}")
    if sized:
        out.append(f"大小变了 {sized[:6]}")
    return out


def run_py(code: str, data_dir: Path | None = None, timeout: int = 600):
    """在子进程里跑一段取证代码; data_dir 通过 OHS_DATA_DIR 生效 (D0 的单一产地)。
    返回 (退出码, stdout+stderr 尾部) —— 判据只认这两样, 不接受"看起来成功了"。"""
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("OHS_DATA_DIR", None)
    env.pop("OHS_LIFECYCLE_ROWS", None)
    env.pop("OHS_LIFECYCLE_DIRS", None)
    if data_dir is not None:
        env["OHS_DATA_DIR"] = str(data_dir)      # D0 的单一产地: 整棵树一起搬
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()



def run_file(rel: str, data_dir: Path | None = None, timeout: int = 300):
    """在子进程里跑一个**真守卫脚本** (tools/x.py), OHS_DATA_DIR 指到给定目录。
    用于 D4′: 判据要能证明"守卫读的是我给的库", 而不只是"守卫没崩"。"""
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    for k in ("OHS_DATA_DIR", "OHS_LIFECYCLE_ROWS", "OHS_LIFECYCLE_DIRS"):
        env.pop(k, None)
    if data_dir is not None:
        env["OHS_DATA_DIR"] = str(data_dir)
    r = subprocess.run([sys.executable, str(ROOT / rel)], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


print("=== 1. 真实验收链跑完, data/ 必须零写入 ===")
before = state(DATA)
rc, out = run_py(
    "from pathlib import Path\n"
    "from tools.export_fingerprint import export, digest\n"
    "import tempfile\n"
    "with tempfile.TemporaryDirectory() as td:\n"
    f"    fp = export({PID!r}, Path(td))\n"
    "    d = digest(fp)\n"
    "    print('chain ok', d['n_paras'], d['n_tables'], d['chars'], len(d['cell_hashes']))\n")
check(f"验收链本身跑通 (取数+导出, 退出码 {rc})", rc, 0)
print("     ", out.strip().splitlines()[-1] if out.strip() else "")
v = violations(before, state(DATA))
check(f"data/ 四把尺子全部不变 ({len(before['entries'])} 条目)", v, [])
if v:
    print("     实际:", v)

print("\n=== 2. 接线: 取数函数里没有写动作 (AST, 含 tools/) ===")
check(f"验收链 {len(CHAIN_READS)} 个取数函数无 connect()/_conn()/写类 SQL", chain_write_sites(ROOT), [])

print("\n=== 3. 变异: 判据必须能红 (临时 DATA_DIR + 真库副本, 不碰真实 data/) ===")
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td) / "data"
    tmp.mkdir()
    shutil.copy(DATA / "ohs.db", tmp / "ohs.db")

    def probe(extra: str):
        """先把 -wal/-shm 落到"存在"状态再取基线 (只读连接就能建出来), 这样"尺子4 的方向差"
        与顺序无关。实测教训: 不 pre-warm 时冷副本上的一次 0 行 UPDATE **看不见** ——
        可写连接建了 -wal 又 checkpoint 删掉, 净变化为零; 而真实 data/ 里这两个文件本来
        就在, 那种写会被抓到。pre-warm 让临时副本与真实场景同构, 判据才不是顺序巧合。"""
        rc0, t0 = run_py("from knowledge.oel import connect_ro\nconnect_ro().close()\n",
                         data_dir=tmp)
        assert rc0 == 0, f"pre-warm 失败: {t0[-200:]}"
        s0 = state(tmp)
        rc, tail = run_py(extra, data_dir=tmp)
        assert rc == 0, f"探针脚本自身失败: {tail[-250:]}"
        return violations(s0, state(tmp))

    # M1 (0 行 UPDATE) 当变异用例被**否掉**, 理由留在原处 (不是偷懒, 是它自己不一致):
    #   同一句 `UPDATE project SET status=status WHERE 1=0` 在 /tmp 预演里让 -wal/-shm
    #   从有到无 (判得出), 在本文件的临时副本 + pre-warm 场景里四次跑都**完全无痕**
    #   (判不出)。`WHERE 1=0` 不弄脏任何页, 提交是否落盘取决于 SQLite 当时的 WAL 状态。
    #   拿一条"时红时绿"的变异当证据 = 自欺。换成两条必然有痕迹的: M2 (可写连接关闭
    #   必然 checkpoint 掉 -wal/-shm) 与 M5 (schema 写直接改 ohs.db 内容哈希)。
    v_m2 = probe("from knowledge.oel import connect\nconnect().close()\n")
    check("边界如实记录: 只开可写连接、不改任何数据 → 文件层**判不出** (所以必须有第 2/4 节的静态判据)",
          v_m2, [])

    v_m7 = probe("from knowledge.paths import DATA_DIR\n"
                 "(DATA_DIR / 'materials' / '__probe_dir').mkdir(parents=True, exist_ok=True)\n"
                 "(DATA_DIR / 'probe.docx').write_bytes(b'x')\n")
    check("M7 数据条目被新增 (建目录/建文件) → 必须红 (这就是 W5 那类事故的判据)",
          bool(v_m7) and any("多出条目" in x for x in v_m7), True)
    print("     实得:", v_m7)

    v_m8 = probe("import web.projects_db as pdb\n"
                 "c = pdb._conn()\n"
                 "c.execute('UPDATE project SET updated=updated+1 WHERE id=("
                 "SELECT id FROM project ORDER BY id LIMIT 1)')\n"
                 "c.commit(); c.close()\n")
    check("M8 真改一行数据 → 必须红 (内容哈希或 data_version 至少一条报出来)",
          bool(v_m8) and any(("内容哈希" in x) or ("data_version" in x) for x in v_m8), True)
    print("     实得:", v_m8)

    v_m3 = probe("from knowledge.oel import connect_ro\n"
                 "c = connect_ro()\n"
                 "c.execute('SELECT COUNT(*) FROM oel_limit').fetchone()\n"
                 "c.execute('SELECT id FROM project LIMIT 50').fetchall()\n"
                 "c.close()\n")
    check("M3 纯只读连接做同样多的查询 → 必须绿 (判据不许把合法读当写; 实测 -wal/-shm 只增不减)",
          v_m3, [])

    v_m5 = probe("import web.projects_db as pdb\n"
                 "c = pdb._conn()\n"
                 "c.execute('CREATE TABLE IF NOT EXISTS __probe(x)'); c.commit(); c.close()\n")
    check("M5 注入一次建表 (schema 写) → 必须红, 且报的是 **ohs.db 内容哈希变了**",
          bool(v_m5) and any("内容哈希" in x for x in v_m5), True)
    print("     实得:", v_m5)

    v_chain = probe("from pathlib import Path\n"
                    "from tools.export_fingerprint import export, digest\n"
                    "import tempfile\n"
                    f"with tempfile.TemporaryDirectory() as td2:\n"
                    f"    d = digest(export({PID!r}, Path(td2)))\n"
                    "    print(d['n_paras'])\n")
    check("整条验收链在副本库上跑一遍 → 也必须绿 (链本身只读, 与真实库结论一致)", v_chain, [])

print("\n=== 3′. 守卫套自己: 真读验收库的守卫跑完, 副本库四把尺子不变 + 缺库必须响 (D4′) ===")
# 上面第 1 节只证了**导出链**只读。部署第 4 步是拿新镜像在容器里跑 GUARD_TESTS,
# 而容器里 ROOT/data 就是挂进去的生产库 —— 上一版有 6 个守卫直接对验收库开可写连接
# (3 处 sqlite3.connect 硬编码 + 3 处 oel.connect()), 其中 test_std_guard 还写成
# `if db.exists() … else: print("跳过")`: 缺库时那两条 check 从没跑过仍是绿。
# 这一节把两件事钉成行为判据: ① 跑完守卫, 副本库四把尺子不变; ② 库不存在时必须非零退出。
GUARDS = ("test_health_table_header", "test_std_guard", "test_toxicity_cell",
          "test_std_ref_parity", "test_std_currentize", "test_toxicity_match",
          # test_table_mounts 是本轮 D4′ 从"硬编码路径 + 可写连接"改过来的:
          # 它同时是"缺库 print 一句 return 0"那条反例的当事文件, 行为判据必须把它包进来。
          # test_fresh_import **不进来** (如实说明): 它按设计要写 (create_project/save_upload/
          # update_project 走生产写路径, 已登记 write-runtime), 且要用户桌面的私有材料包 —
          # 拿它当"副本库不变"的证据会得到一条必然的红, 而那条红和只读性无关。
          "test_table_mounts")
with tempfile.TemporaryDirectory() as td_g:
    gdir = Path(td_g) / "data"
    gdir.mkdir()
    s = sqlite3.connect(f"file:{DATA / 'ohs.db'}?mode=ro", uri=True)
    t = sqlite3.connect(str(gdir / "ohs.db"))
    s.backup(t)
    t.close()
    s.close()
    (gdir / "materials").mkdir()
    rc0, out0 = run_py("from knowledge.oel import connect_ro\nconnect_ro().close()\n",
                       data_dir=gdir)
    assert rc0 == 0, f"pre-warm 失败: {out0[-200:]}"        # 让 -wal/-shm 先落到"存在"态
    base_g = state(gdir)
    for g in GUARDS:
        rc, tail = run_file(f"tools/{g}.py", gdir)
        v = violations(base_g, state(gdir))
        # 旧版在这里写死 `rc == 0`: 一支**本来就红**的守卫 (test_table_mounts 的既有 3 项失败)
        # 会在这一节报成"守卫破坏了只读性" —— 判据把两件事混成一件。改成与真实库的 rc 对齐:
        # 既有红 = 一致, 新引入的红 = 只有副本上非零 (那才说明问题出在只读改造上)。
        rcb = rc
        if rc != 0:
            rcb, _ = run_file(f"tools/{g}.py", None)
            note = (f"退出码与真实库一致 ({rc})" if rcb == rc
                    else f"★只在副本上非零 ({rc} vs 真实库 {rcb})")
        else:
            note = "退出码 0"
        check(f"守卫 {g}: {note}, 且副本库四把尺子不变", (rc == rcb, v), (True, []))
        if v:
            print("     实际:", v, tail.splitlines()[-1:] or "")
        with tempfile.TemporaryDirectory() as td_e:
            ed = Path(td_e) / "data"
            ed.mkdir()
            rc2, tail2 = run_file(f"tools/{g}.py", ed)
            loud = ("OfficialSchemaMissing" in tail2) or ("不可读" in tail2)
            check(f"  缺库时 {g} 必须**非零退出并说明原因** (旧写法: print(跳过) + rc 0)",
                  (rc2 != 0, loud), (True, True))
            if rc2 == 0:
                print("     实得:", tail2[-160:])

    # 变异对照 (所以上面 12 条绿不是空转):
    # M9 —— 对副本库真改一行, 四把尺子必须报红。实测 data_version **不**变 (见第 3 节边界),
    #       所以这里认的是 ohs.db 内容哈希。
    rc9, _ = run_py("import sqlite3\n"
                    f"c = sqlite3.connect({str(gdir / 'ohs.db')!r})\n"
                    "c.execute(\"INSERT INTO audit_log(user, method, path, status) "
                    "VALUES('__probe','X','/y',1)\")\n"
                    "c.commit(); c.close()\n", data_dir=gdir)
    v9 = violations(base_g, state(gdir))
    check("M9 对副本库真插一行 → 这条判据必须红 (所以上面 %d 条'不变'是测出来的)" % len(GUARDS),
          (rc9, len(v9) > 0, "ohs.db 内容哈希" in v9[0]), (0, True, True))

    # M10 —— 把 D4′ 修掉的旧写法照原样摆回来 (硬编码路径 + if exists / else 跳过),
    #        它缺库时 rc=0 且不响; 上面那条"必须非零退出并说明原因"的判据必须判它红。
    old_shape = (gdir.parent / "guard_old_shape.py")
    old_shape.write_text(
        "import sqlite3\n"
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parent\n"
        "db = ROOT / 'data' / 'ohs.db'\n"
        "if db.exists():\n"
        "    rc = sqlite3.connect(db); rc.close(); print('checked')\n"
        "else:\n"
        "    print('    (无 data/ohs.db, 跳过)')\n", encoding="utf-8")
    with tempfile.TemporaryDirectory() as td_o:
        ed2 = Path(td_o) / "nodata"
        ed2.mkdir()
        rc10, tail10 = run_file(str(old_shape), ed2)
        check("M10 旧写法在缺库时 rc=0 且不响 → 证明上面那条判据能红 (不是它运气好)",
              (rc10, "不可读" in tail10 or "OfficialSchemaMissing" in tail10), (0, False))

print("\n=== 4. 变异: 接线判据必须能红 (临时副本里把 connect_ro 换回写侧工厂) ===")
with tempfile.TemporaryDirectory() as td2:
    tmp2 = Path(td2)
    for d in ("tools", "web", "knowledge"):
        shutil.copytree(ROOT / d, tmp2 / d)
    b0 = chain_write_sites(tmp2)
    check("副本基线为空 (判据不是常量真)", b0, [])
    tgt = tmp2 / "web" / "word_export.py"
    old_line = [ln for ln in tgt.read_text(encoding="utf-8").splitlines()
                if "conn = connect_ro()" in ln and "只读:" in ln]
    check("变异前提: export_docx 里那行带注释的 connect_ro() 恰好一行 (另一行在 CLI main 里)",
          len(old_line), 1)
    tgt.write_text(tgt.read_text(encoding="utf-8").replace(
        old_line[0], "    from knowledge.oel import connect\n    conn = connect()  # 注入的变异"),
        encoding="utf-8")
    b1 = chain_write_sites(tmp2)
    check("export_docx 里换回写侧工厂 → 必须红",
          any(x.startswith("web/word_export.py:") and "写侧工厂" in x for x in b1), True)
    # 第二型变异: 不叫 connect, 而是偷偷调 projects_db 的可写工厂
    tgt2 = tmp2 / "web" / "projects_db.py"
    t = tgt2.read_text(encoding="utf-8").replace(
        "def get_project(pid: str) -> dict | None:\n    conn = _ro()",
        "def get_project(pid: str) -> dict | None:\n    conn = _conn()")
    tgt2.write_text(t, encoding="utf-8")
    b2 = chain_write_sites(tmp2)
    check("取数函数改用 _conn() (可写工厂) → 必须红",
          any("projects_db.py" in x and "_conn" in x for x in b2), True)

print("\n=== 5. 前提检查: 那条字符串级假断言确实已经从守卫里换掉了 ===")
gs = (ROOT / "tools" / "test_gate_sensitivity.py").read_text(encoding="utf-8")
check("test_gate_sensitivity 不再用 \"UPDATE \" not in src.upper() 冒充只读判据",
      '"UPDATE " not in door_src.upper()' in gs, False)
check("它改为调用 chain_audit 那把尺子",
      "from tools.chain_audit import chain_write_sites" in gs, True)

print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 ===")
sys.exit(1 if n_fail else 0)
