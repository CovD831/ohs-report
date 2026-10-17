#!/usr/bin/env python3
"""检测结果表分桶判据 (#34): 粉尘行不得被印进化毒物表, 且分桶必须**从数据派生**。

为什么立这一支 (2026-10-16 取证):
    `web/section_filler.py` 的分桶查的是 `hazard_factor WHERE name LIKE '<检测名>%'` ——
    方向反了: 检测报告名带采样方式后缀 ("滑石粉尘(呼尘)"), 标准库名才是短的一侧,
    于是永远查不到 → 落默认"化学毒物"。实测 4 个项目 11 个含尘/烟的检测名里 **7 个错桶**,
    成品里「检测结果表(化学毒物)」30 行中混着 其他粉尘(树脂)/滑石粉尘(呼尘)/碳酸钙/游离二氧化硅。
    修法是把分桶改成查库的四级链 (见 _det_bucket), 所以这里的判据必须能区分
    "真的在查库" 与 "名字里猜" —— 用**改库里的类别, 桶必须跟着变**来证明。

规矩 (与全仓一致):
    · 变异一律打在**临时副本**上 (`sqlite3 backup` + `OHS_DATA_DIR` 指过去), 真实库只读;
    · 打补丁式测试要加**前提对表**: 先证明连接真的指向临时树, 否则测的是生产数据;
    · 每条绿灯配一条"必然能红、且每次都红"的变异; 判据读的源头必须在执行环境里存在。

用法: .venv/bin/python tools/test_det_bucket.py

⚠ 只在本机跑, **不进 GUARD_TESTS** (交付链能力边界): 第 2 节的"7 个名字"是从本地 4 个验证项目
   的实际检测记录里取的, 生产容器里的项目集合不同 → 那份名单可能一个都不存在, 判据会变成空转。
   它仍进 MD5_FILES (改动要送得进去、md5 要能比), 且被 chain_audit 的两条静态尺子覆盖
   (它自己就是 tools/test_*.py, 不许给验收库开可写连接、不许硬拼 data/ 路径)。

2026-10-16 生产窗口实测 (第 1 节的取景原先确实是写死 pid 的, 已改): 在生产的同一份库上跑出
   **8 绿 / 2 红**, 两条红都指取景 —— `前提对表: 真实库里有可测的检测名 (0 个)` 和
   `对照: 0 个化学名仍落 化学毒物 (实得 set() / 期望 {'化学毒物'})`。
   那 4 个 pid 是本地验证项目, 生产库里一个都不在 (逐 id 查 project 全 None)。
   红本身是**诚实的** (反向那条不肯把空集合当真), 但它把"取景错"报成了"分桶可疑",
   读的人得先看环境才知道该不该慌 —— 这正是 2026-10-14 `test_toxicity_cell` 硬读本地行
   在容器里 TypeError 的同款形状。所以取景改成**从数据派生** (带 detections 记录的项目,
   按 updated 新→旧, 上限 60 个), 而"这个环境真的没有检测记录"退化为**具名跳过**并说明
   2/3/4 节仍然有牙齿 —— 跳过不等于测过, 也不等于代码错。
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.paths import DATA_DIR, DB_PATH   # noqa: E402  真实库/数据目录 (只读取样本)
from web.section_filler import _det_bucket, _det_by_type   # noqa: E402

# 取景**从库里派生**, 不写死 pid —— 理由与实测数字见文件头 ⚠ 那段 (2026-10-16 生产 8 绿/2 红)。
# 上限 60 个项目: 生产 3828 行 project 逐行 json.loads 不便宜, 而 60 个项目已经能取到
# 上百个检测名 (本地 4 个项目就有 21 个), 足够让第 1 节有牙齿。
# 排序键用 (updated DESC, id) 而不是随机 —— 判据要可重放, 同一份库两次跑出同一批名字。
SAMPLE_CAP = 60
n_pass = n_fail = n_skip = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


def skip(label, why):
    """具名跳过: 这条在本环境**没测**, 但也不算失败 —— 打印的理由要能让人判断
    "是环境没有数据"还是"取景写错了", 不许只写"跳过"。"""
    global n_skip
    n_skip += 1
    print(f"  – {label}\n     ⓘ {why}")


def sample_pids(conn):
    """带 `detections` 记录的项目 id (新→旧, 上限 SAMPLE_CAP)"""
    return [r[0] for r in conn.execute(
        'SELECT id FROM project WHERE data LIKE ? ORDER BY updated DESC, id LIMIT ?',
        ('%"detections"%', SAMPLE_CAP)).fetchall()]


def samples(conn):
    """真实库里**实际检测过**的因素名 (去重、保序) —— 取样项目由 sample_pids() 从数据派生"""
    out = []
    for pid in sample_pids(conn):
        row = conn.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
        if not row:
            continue
        for d in (json.loads(row[0]).get("detections") or []):
            f = str(d.get("factor", "") or "")
            if f and f not in out:
                out.append(f)
    return out



def build_temp_tree(td: str) -> Path:
    """临时数据树: 真实库的**一致性副本** + 材料/缓存目录。

    ⚠ 副本必须用 `sqlite3 backup` 而不是 `cp data/ohs.db*`: 直接拷主文件再把 -wal/-shm
      用软链指回真实库, SQLite 会拿错索引 —— 实测报 "unable to open database file"。
    """
    data_dir = Path(td) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dst = sqlite3.connect(data_dir / "ohs.db")
    src.backup(dst)
    src.close()
    dst.close()
    for d in ("materials", "vision_cache"):
        if (DATA_DIR / d).exists():
            shutil.copytree(DATA_DIR / d, data_dir / d)
    return data_dir


def main() -> int:
    real = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    facs = samples(real)
    proj_n = len(sample_pids(real))
    dust_names = [f for f in facs if "粉尘" in f or "烟尘" in f]
    print(f"\n取景: 带 detections 记录的项目 {proj_n} 个 (上限 {SAMPLE_CAP}) → "
          f"检测名 {len(facs)} 个, 其中含「粉尘/烟尘」{len(dust_names)} 个")

    print("\n=== 1. 成品口径: 含「粉尘/烟尘」的检测名必须全落进 粉尘 桶 ===")
    if not facs:
        # 具名跳过而不是判红: "这个环境没有检测记录"与"分桶代码错了"是两件事
        # (2026-10-14 那次把前者报成后者, 部署时白排查半天)。
        skip("1 节整段 (逐名核对粉尘桶/化学桶)",
             f"本环境取到 0 个检测名 —— 带 detections 记录的项目 {proj_n} 个但里面没有 factor 名。"
             "不代表分桶代码错, **也不代表测过了**; 2/3/4 节走常量名 + 改库变异, 与项目数据无关, 仍然有牙齿。")
    else:
        check(f"前提对表: 真实库里有可测的检测名 ({len(facs)} 个, 其中含粉尘/烟尘 {len(dust_names)} 个)",
              len(facs) >= 20, True)
        if len(dust_names) < 5:
            skip(f"粉尘侧 ({len(dust_names)} 个名, 少于 5 个)",
                 "样本太少, 这一条在本环境不构成「全量粉尘名都对上了」的证据; 逐名核对仍跑 (下面那条), "
                 "但结论要连着 2/3 节的常量名单一起看。")
        bad = []
        for f in dust_names:
            dtype, basis = _det_bucket(real, f)
            if dtype != "粉尘" or basis is None:
                bad.append((f, dtype, basis))
        check(f"{len(dust_names)} 个粉尘名全部分到 粉尘 桶 (且依据非空)", bad, [])
        for x in bad[:6]:
            print("      ", x)
        # 反向: 真化学物不许被"含尘字样"之类的名字规则误伤 (证明不是靠字面猜)
        chem = [f for f in facs if any(k in f for k in ("苯", "丙酮", "甲醇", "硫酸雾", "氟化物"))]
        if not chem:
            skip("化学侧反向对照", "本环境的检测名里没有苯/丙酮/甲醇/硫酸雾/氟化物这类真化学物 —— "
                                  "空集合上的 `set() == {'化学毒物'}` 是假红, 不是证据。")
        else:
            check(f"对照: {len(chem)} 个化学名仍落 化学毒物 (桶不是「名字含尘就算粉尘」那种字面规则)",
                  {_det_bucket(real, f)[0] for f in chem}, {"化学毒物"})
    real.close()


    with tempfile.TemporaryDirectory() as td:
        data_dir = build_temp_tree(td)
        # 前提对表: 连接必须真的指到临时树 (打补丁式测试踩过: patch 了 A.DB 而读路径用 B.DB_PATH)
        import os
        os.environ["OHS_DATA_DIR"] = str(data_dir)
        import importlib
        import knowledge.paths as paths
        importlib.reload(paths)
        from knowledge.oel import connect_ro
        c = connect_ro()
        got_file = Path(c.execute("PRAGMA database_list").fetchone()[2]).resolve()
        check("前提对表: 变异用的连接确实指向临时副本 (不是真实库)",
              got_file == Path(data_dir / "ohs.db").resolve(), True)
        print("\n=== 2. 旧口径对照 (在任何变异之前跑): 反向前缀漏掉的 7 个名字, 新口径必须全中 ===")
        names = ["滑石粉尘(呼尘)", "石灰石粉尘（ 总尘）", "石灰石粉尘（ 呼尘）",
                 "其他粉尘", "其他粉尘(树脂)", "其他粉尘(环氧树脂)", "其他粉尘（环氧树脂）"]
        w = sqlite3.connect(data_dir / "ohs.db")
        old_style = [n for n in names
                     if not w.execute("SELECT category FROM hazard_factor WHERE name LIKE ? LIMIT 1",
                                      (n + "%",)).fetchone()]
        check("旧口径 (标准名以检测名开头) 对这 7 个名字**确实查不到** → 说明修前必然错桶",
              len(old_style), 7)
        new_bad = [n for n in names if _det_bucket(c, n)[0] != "粉尘"]
        check("新口径: 7 个名字全部落 粉尘 桶", new_bad, [])

        print("\n=== 3. 变异: 分桶必须跟着**库里的类别**走 (证明是查库不是猜名) ===")
        probe = "滑石粉尘"
        before = _det_bucket(c, probe + "(呼尘)")
        check("变异前: 滑石粉尘(呼尘) → 粉尘 且依据来自 hazard_factor (先确认起点)",
              (before[0], "hazard_factor" in (before[1] or "")), ("粉尘", True))
        w = sqlite3.connect(data_dir / "ohs.db")
        w.execute("UPDATE hazard_factor SET category='化学因素' WHERE name='滑石粉尘'")
        w.commit()
        w.close()
        c.close()
        c = connect_ro()
        after = _det_bucket(c, probe + "(呼尘)")
        check("变异 A: 把标准库类别改成 化学因素 → 桶必须跟着变成 化学毒物 (数据派生的正面证据)",
              after[0], "化学毒物")
        print("      实得:", after)

        print("\n=== 4. 变异: 查不到类别时必须**点名**, 不许静默默认 ===")
        w = sqlite3.connect(data_dir / "ohs.db")
        w.execute("DELETE FROM hazard_factor WHERE name='滑石粉尘'")
        # ⚠ 必须按前缀删: 库里的名字是 "滑石粉尘（游离SiO 含量＜10%） 2" (OCR 把下标 2 甩到了末尾),
        #   精确名删不掉 → 变异 B 会"看起来没生效"。这条本身就是 #36 要登记的脏名缺陷。
        w.execute("DELETE FROM oel_limit WHERE factor_name LIKE '滑石粉尘%'")
        w.commit()
        w.close()
        c.close()
        c = connect_ro()
        dtype, basis = _det_bucket(c, "滑石粉尘(呼尘)")
        check("变异 B: 两处源头都删掉 → dtype 落默认化学毒物 **且 basis=None** (调用方据此点名)",
              (dtype, basis), ("化学毒物", None))
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _det_by_type(c, [{"factor": "滑石粉尘(呼尘)", "ctwa": "1"}])
        printed = buf.getvalue()
        check("变异 B 续: 这一行进 _det_by_type 时必须在导出日志里点名 (静默=看不见)",
              ("det-bucket" in printed, "滑石粉尘" in printed), (True, True))
        print("      日志实得:", printed.strip()[:110])

        c.close()

    print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 / {n_skip} 跳过 ===")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
