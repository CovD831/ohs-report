#!/usr/bin/env python3
"""tools/detect_cache_drift.py 的判据自检 —— 每条绿灯都配一条"打在临时副本上、必然能红"的变异。

规矩 (与全仓一致, 配方借 tools/test_det_bucket.py):
    · 变异一律打在**临时副本**上: /tmp/ohs_drift_selftest (本文件专属临时根, 不复用他人),
      副本必须用 `sqlite3 .backup` 而不是 `cp` (直接拷主文件再把 -wal/-shm 软链回真实库,
      SQLite 会拿错索引 —— test_det_bucket 的注释记录了实测的 "unable to open database file");
      真实库全程只走只读 URI, **本文件与探测器都不给真实 data/ 开可写连接**。
    · 每条变异前先在**同一份未变异副本**上跑一遍探测器当**阳性对照** —— 证明"改之前它就是绿的
      (或目标记录本来就是那个类别)", 否则"红"可能来自临时树取景不完整, 不是来自变异。
    · 取景前提对表: 探测器自报的 db 路径必须 == 临时副本 (且 != 真实库); 可比表数必须 > 0
      (数出 0 在本项目一律先怀疑测量方法)。

四条判据 (目标全部从探测器基线 JSON + 副本数据派生, 无表名/项目号白名单):
    ① 把某张**基线一致**表的一格改成探针值 → 探测器必须点名到那张表/那一行/那一列/旧值=探针,
      且按泛化判据判"必然是旧产物" (探针形态在任何现算里都不存在)。
    ② 同一格只加空白 / 只交换自增序号 → 不许红 (不误伤格式与换号)。
    ③ 删掉一行 → 必须落到"行数不等"那一类, 而不是"内容不同"。
    ④ 把缓存清空 → 落到"缓存空"类, 且漂移总数恰好减去该表贡献 (不计为漂移)。
    ⑤(静态) 探测器源码的白名单形状检查: 判定函数里不许有含汉字词的 list/set/tuple 字面量、
      不许有 10 位项目号字面量、不许出现可写 sqlite3.connect (形状参考 tools/test_det_rows.py 的 whitelist_smells)。
"""
import ast
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.paths import DATA_DIR, DB_PATH          # noqa: E402  (真实库只读取景 + 写入门检查)

TMP_PREFIX = "ohs_drift_selftest"                     # 本文件专属临时根 (不复用他人)
DETECTOR = ROOT / "tools" / "detect_cache_drift.py"

n_pass = n_fail = n_skip = 0
CAT_SAME = "一致"
CAT_DIFF = "内容不同（行数相等，差异只在数据列）"
CAT_LEN = "行数不等"
CAT_EMPTY = "缓存空（导出本就回退现算，不计漂移）"


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


def skip(label, why):
    global n_skip
    n_skip += 1
    print(f"  – {label}\n     ⓘ {why}")


_TMP_DIRS: set[Path] = set()


def fresh_tree() -> Path:
    """真实库的一致性副本, 放在专属临时根 /tmp/ohs_drift_selftest* (mkdtemp 派生)。

    为什么用 mkdtemp 而不是裸拼固定目录: chain_audit 的"临时名放行"按**数据派生关系**判
    (tempfile/mkdtemp/TemporaryDirectory 的闭包, 见 _temp_names), 裸字符串拼的临时树
    会被当成硬拼路径点名 —— 而临时树本来就是"变异只能打在副本上"这条纪律的载体。
    只拷库: 探测器只读 project/官方层, 不碰材料/视觉缓存。
    """
    for leftover in Path("/tmp").glob(f"{TMP_PREFIX}*"):     # 清掉本前缀的历史残留 (专属前缀, 不动别人)
        shutil.rmtree(leftover, ignore_errors=True)
    troot = Path(tempfile.mkdtemp(prefix=TMP_PREFIX, dir="/tmp"))
    _TMP_DIRS.add(troot)
    data_dir = troot / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dst = sqlite3.connect(data_dir / "ohs.db")
    src.backup(dst)
    src.close()
    dst.close()
    (data_dir / "materials").mkdir(exist_ok=True)
    (data_dir / "vision_cache").mkdir(exist_ok=True)
    return data_dir


def run_detector(data_dir: Path):
    """子进程跑探测器 (--json) —— 环境级 OHS_DATA_DIR 隔离, 不碰本进程已加载的 paths。"""
    env = dict(os.environ, OHS_DATA_DIR=str(data_dir))
    p = subprocess.run([sys.executable, str(DETECTOR), "--json"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True)
    line = next((l for l in p.stdout.splitlines() if l.startswith('{"meta"')), None)
    if line is None:
        raise RuntimeError(f"探测器没出 JSON (rc={p.returncode}): {p.stdout[:300]}{p.stderr[:300]}")
    return p.returncode, json.loads(line)


def rec_of(out, pid, table):
    return next((r for r in out["records"] if r["pid"] == pid and r["table"] == table), None)


def edit_cached_rows(data_dir: Path, pid: str, table: str, fn) -> None:
    """把变异写在**临时副本**的行上 (可写连接只碰副本, 永不碰真实库)。"""
    w = sqlite3.connect(str(data_dir / "ohs.db"))
    raw = w.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()[0]
    d = json.loads(raw)
    d["built_tables"][table]["rows"] = fn([list(r) for r in d["built_tables"][table]["rows"]])
    w.execute("UPDATE project SET data=? WHERE id=?", (json.dumps(d, ensure_ascii=False), pid))
    w.commit()
    w.close()


def _set_cell(rows, i, j, v):
    rows[i][j] = v
    return rows


def is_int_seq(vals):
    try:
        ia = [int(str(v).strip()) for v in vals]
    except (TypeError, ValueError):
        return False
    s = sorted(ia)
    return len(set(ia)) == len(ia) and len(s) >= 3 and s == list(range(s[0], s[0] + len(s)))


def pick_targets(base):
    """(①/②/③ 的"基线一致"目标, ⑤ 的漂移目标, ②b 的序号列目标) —— 全从基线 JSON 派生。"""
    same = [r for r in base["records"] if r["category"] == CAT_SAME and r["cached_rows"] >= 3]
    same.sort(key=lambda r: (r["pid"], r["table"]))
    cell_t = id_t = None
    for r in same:
        rows = r.get("rows_dump") or []
        if not rows:
            continue
        if cell_t is None:
            for i, row in enumerate(rows):
                for j, c in enumerate(row):
                    if len(str(c)) >= 2 and not str(c).strip().isdigit():
                        cell_t = (r, i, j, str(c))
                        break
                if cell_t:
                    break
        if id_t is None and len(rows) >= 3:
            for j in range(len(rows[0])):
                if is_int_seq([row[j] for row in rows]):
                    id_t = (r, j)
                    break
        if cell_t and id_t:
            break
    drift = [r for r in base["records"] if r["category"] in (CAT_DIFF, CAT_LEN) and r["diffs"]]
    drift.sort(key=lambda r: (r["pid"], r["table"]))
    return cell_t, id_t, (drift[0] if drift else None)


def dump_rows(data_dir: Path, out):
    """把基线 records 附上其缓存 rows 的转储 (选靶用; 数据仍来自副本, 不是清单)。"""
    w = sqlite3.connect(str(data_dir / "ohs.db"))
    cache = {}
    for r in out["records"]:
        key = (r["pid"], r["table"])
        if key not in cache:
            raw = w.execute("SELECT data FROM project WHERE id=?", (r["pid"],)).fetchone()[0]
            bt = json.loads(raw).get("built_tables") or {}
            cache[key] = (bt.get(r["table"]) or {}).get("rows") or []
    w.close()
    for r in out["records"]:
        r["rows_dump"] = cache[(r["pid"], r["table"])]


def smell_check() -> list:
    """探测器源码的白名单形状检查 (族参 tools/test_det_rows.whitelist_smells)。"""
    src = DETECTOR.read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            b = getattr(node, "body", [])
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                doc_ids.add(id(b[0].value))

    def has_cjk(t):
        return any("\u4e00" <= ch <= "\u9fff" for ch in t)

    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_ids:
            if re.fullmatch(r"[0-9a-f]{10}", node.value):
                bad.append(f"项目号字面量白名单: {node.value!r} (行{node.lineno})")
        if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
            for e in node.elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str) and has_cjk(e.value):
                    bad.append(f"判定路径上的汉字串清单字面量: {type(node).__name__} 含 {e.value!r} (行{e.lineno})")
        if isinstance(node, ast.Compare):
            flat = [node.left, *node.comparators]
            has_name = any(isinstance(y, ast.Name) for y in flat)
            for x in flat:
                if isinstance(x, ast.Constant) and isinstance(x.value, str) and has_cjk(x.value) and has_name:
                    bad.append(f"汉字字面量与变量比较 (清单形状判据): {ast.unparse(node)[:80]} (行{node.lineno})")
    if "sqlite3.connect(" in src:
        bad.append("探测器源码出现可写连接形状 sqlite3.connect (只许 connect_ro)")
    return bad


def main() -> int:
    print(f"真实库(只读取景): {DB_PATH}")
    names_before = {p.name for p in DATA_DIR.iterdir()} if DATA_DIR.exists() else set()

    print("\n=== 0. 取景前提对表 (临时副本 + 非空取景) ===")
    data_dir = fresh_tree()
    rc, base = run_detector(data_dir)
    check("0a 副本基线 rc ∈ {0,1} (rc=2 = 注册表/结构常量脱节, 后面全是空谈)", rc in (0, 1), True)
    check("0b 探测器读的库 == 临时副本 (变异打不到真实库)",
          base["meta"]["db"], str((data_dir / "ohs.db").resolve()))
    check("0c 副本路径 ≠ 真实库路径", base["meta"]["db"] != str(DB_PATH), True)
    check(f"0d 取景非空: 可比缓存表 {base['meta']['nonempty_tables']} 张 > 0", base["meta"]["nonempty_tables"] > 0, True)
    dump_rows(data_dir, base)
    cell_t, id_t, drift_t = pick_targets(base)

    print("\n=== 1. 变异①: 一致表的一格改成探针值 → 探测器必须点名那一格 (能红) ===")
    if cell_t is None:
        skip("1 整节", "基线里找不到'行数≥3 且含文本格'的一致表 —— 本环境的副本取景里没有可变异对象, "
                       "不代表探测器不能红, 也不代表测过。")
    else:
        r0, ri, cj, old_v = cell_t
        probe = "漂移探针" + uuid.uuid4().hex[:8]
        rec = rec_of(base, r0["pid"], r0["table"])
        check(f"阳性对照: 变异前 [{r0['pid']}]·{r0['table']} 类别 = 一致", rec["category"], CAT_SAME)
        edit_cached_rows(data_dir, r0["pid"], r0["table"],
                         lambda rows: _set_cell(rows, ri, cj, probe))
        rc1, out1 = run_detector(data_dir)
        r1 = rec_of(out1, r0["pid"], r0["table"])
        d0 = (r1["diffs"] or [{}])[0]
        check("变异后类别 = 内容不同", r1["category"], CAT_DIFF)
        check(f"点名到格: 行{ri} 列{cj}", (d0.get("row"), d0.get("col")), (ri, cj))
        check("旧值就是探针原文 (看得见字, 不是哈希)", d0.get("old"), probe)
        check("泛化判据: 探针形态现算 anywhere 发不出 → 必然是旧产物", d0.get("verdict"), "stale_unique")
        check("漂移总数 = 基线 + 1 (只多这一处, 取景没连锁变红)",
              out1["summary"]["drift_units"], base["summary"]["drift_units"] + 1)

    print("\n=== 2. 变异②: 只改空白 / 只换自增序号 → 不许红 (不误伤) ===")
    if cell_t is None:
        skip("2a 空白", "同 1 节, 无一致表可当基线。")
    else:
        r0, ri, cj, old_v = cell_t
        data_dir = fresh_tree()
        rc, base = run_detector(data_dir)
        dump_rows(data_dir, base)
        edit_cached_rows(data_dir, r0["pid"], r0["table"], lambda rows: _set_cell(rows, ri, cj, f"  {old_v}  "))
        rc2, out2 = run_detector(data_dir)
        r2 = rec_of(out2, r0["pid"], r0["table"])
        check(f"2a 前后加空白: [{r0['pid']}]·{r0['table']} 仍判一致", r2["category"], CAT_SAME)
    if id_t is None:
        skip("2b 序号列", "基线一致表里没有'互异连续整数'的序号形列可换 —— 换靶会退化成品名清单, 宁缺毋滥。")
    else:
        r0, jcol = id_t
        data_dir = fresh_tree()
        rc, base = run_detector(data_dir)
        dump_rows(data_dir, base)
        rec = rec_of(base, r0["pid"], r0["table"])
        check(f"阳性对照: 变异前 [{r0['pid']}]·{r0['table']} 类别 = 一致", rec["category"], CAT_SAME)

        def swap(rows):
            rows[0][jcol], rows[1][jcol] = rows[1][jcol], rows[0][jcol]
            return rows
        edit_cached_rows(data_dir, r0["pid"], r0["table"], swap)
        rc3, out3 = run_detector(data_dir)
        r3 = rec_of(out3, r0["pid"], r0["table"])
        check(f"2b 交换列{jcol} (自增序号形) 两行 → 不许红", r3["category"], CAT_SAME)

    print("\n=== 3. 变异③: 删一行 → 必须落'行数不等', 不许串到'内容不同' ===")
    if cell_t is None:
        skip("3 整节", "同 1 节, 无一致表可删行。")
    else:
        r0 = cell_t[0]
        drop = max(1, r0["cached_rows"] // 2)
        data_dir = fresh_tree()
        rc, base = run_detector(data_dir)
        dump_rows(data_dir, base)
        check(f"阳性对照: 变异前 [{r0['pid']}]·{r0['table']} 类别 = 一致",
              rec_of(base, r0["pid"], r0["table"])["category"], CAT_SAME)
        edit_cached_rows(data_dir, r0["pid"], r0["table"], lambda rows: rows[:drop] + rows[drop + 1:])
        rc4, out4 = run_detector(data_dir)
        r4 = rec_of(out4, r0["pid"], r0["table"])
        check("删行后类别 = 行数不等", r4["category"], CAT_LEN)
        check("点名了'现算多出的行' (少了哪行看得见)", any(d.get("side") == "现算多出的行" for d in r4["diffs"]), True)

    print("\n=== 4. 变异④: 清空缓存 rows → 落'缓存空'且恰好不再贡献漂移 ===")
    if drift_t is None:
        skip("4 整节", "基线里一张漂移表都没有 —— 按项目纪律这先怀疑取景而不是直接判绿, "
                       "0d 的非空检查就是为这个立的。")
    else:
        data_dir = fresh_tree()
        rc, base = run_detector(data_dir)
        dump_rows(data_dir, base)
        dt = rec_of(base, drift_t["pid"], drift_t["table"])
        d_units = len(dt["diffs"])
        check(f"阳性对照: [{drift_t['pid']}]·{drift_t['table']} 基线漂移 {d_units} 处 (>0)", d_units > 0, True)
        edit_cached_rows(data_dir, drift_t["pid"], drift_t["table"], lambda rows: [])
        rc5, out5 = run_detector(data_dir)
        r5 = rec_of(out5, drift_t["pid"], drift_t["table"])
        check("清空后类别 = 缓存空 (导出回退现算)", r5["category"], CAT_EMPTY)
        check("漂移总数恰好减去该表贡献 (不计为漂移)",
              out5["summary"]["drift_units"], base["summary"]["drift_units"] - d_units)

    print("\n=== 5. 静态: 探测器不许长成白名单 (形状参考 test_det_rows.whitelist_smells) ===")
    check("判定路径无汉字清单字面量 / 无项目号字面量 / 无可写连接", smell_check(), [])

    print("\n=== 6. 真库 data/ 目录未被写入 (验收链只读取证) ===")
    names_after = {p.name for p in DATA_DIR.iterdir()} if DATA_DIR.exists() else set()
    new_names = names_after - names_before
    check(f"真实 data/ 没有出现新文件 (全程只读; 已有 -wal/-shm 属既有状态, 不虚构归因)",
          {n for n in new_names if n.startswith(("ohs.db-wal", "ohs.db-shm", "ohs.db-journal"))
           or n == "ohs.db"}, set())

    print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 / {n_skip} 跳过 ===")
    return 1 if n_fail else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        for d in _TMP_DIRS:
            shutil.rmtree(d, ignore_errors=True)
