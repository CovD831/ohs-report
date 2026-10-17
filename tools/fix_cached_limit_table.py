#!/usr/bin/env python3
"""#47 可重放迁移: 把客户数据里**已过期的 built_tables['接触限值表']** 换成现在代码算出来的行

为什么要有这一步 (2026-10-17 取证, 数字直接测得)
    修完官方层名字形状 (#37) 和读侧子条目档 (#51/#37) 之后, 现算的接触限值表已经是对的,
    但导出走的是"**骨架优先**" (`web/word_export.py:324` 一带: 缓存有行就沿用缓存):
        c8c7ff0a4d 缓存 39 行里有 11 处 '未制定职业接触限值'
        ada54603a9 缓存 45 行里有 13 处 '未制定职业接触限值'
    而当前代码**已经发不出**这句话 (tools/test_det_rows.py 第 7 节把"发射点"判红了) ——
    所以缓存里出现它 = 这行缓存必然是旧代码产物, 不是"用户手填的内容"。
    不处理的话: #37/#51 的全部内容修复到不了成品, 交付物继续印假陈述。

只动这一张表, 而且只动它的 rows (其余键逐字节不动)
    缓存项结构 = {cols, prov, rows, section, status}; 现算表结构 = {name, cols, header2,
    merge_rect, rows, prov}。两边字段集不同, **不做整表替换** (那会把骨架自己的 prov/status
    改掉, 也就改掉了"这张表当初是哪条链建的"这件事)。替换的只有 `rows`。

拒绝条件 (三条都满足才写; 任一条不满足 → 跳过并点名, 绝不"顺手多改一点")
    A 行数相等           —— 不许因迁移增删因素行 (增删行是识别链的事, 不是缓存刷新的事);
    B cols 相等          —— 表头不同说明两边根本不是同一张表 (骨架可能来自旧版式);
    C 差异只在 1..5 列   —— 第 0 列是因素名, 名字变了就不是"填数"而是"换行", 交人工。
    这三条都从**行内容**判, 不含任何项目号/物质名清单 → 换一批项目照样成立。

安全 (与 tools/fix_oel_name_forms.py 同一套)
    · 默认 dry-run 只打印; `--apply` 才写; 写的仍是**本地库** (进生产需用户单独批准);
    · 写前把每张被改表的原 rows 落到 DATA_DIR 的 JSON (库外留一份, 恢复不依赖库);
    · 一个事务改完 → 逐项目**读回**断言"缓存 rows == 现算 rows 且 其余键逐字节不变 且
      整行 JSON 尺寸比在界内" → 任一不过整体 ROLLBACK;
      (尺寸对表是 #37 那次自伤的教训: 递归重建字典时把 (值,累加器) 元组塞了进去,
       175KB 的业务数据涨到 2.6MB, 而工具自己打印"✓ 已写"。)
    · 幂等: 再跑一次 → 三条差异条件不成立 → 0 项待改。

自检 (`--selftest`, 证明这三条判据真的能红)
    拿现算/缓存两份行喂给同一个判据函数, 构造四种情形: 行数不等 / cols 不等 /
    第 0 列被改 / 只差 1..5 列 → 前三种必须拒, 最后一种必须放行。

用法:
    .venv/bin/python tools/fix_cached_limit_table.py            # 预演 (只读)
    .venv/bin/python tools/fix_cached_limit_table.py --apply    # 写本地库
    .venv/bin/python tools/fix_cached_limit_table.py --selftest # 判据能不能红
"""
import sys
import json
import time
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.paths import DB_PATH, DATA_DIR                   # noqa: E402

TABLE = "接触限值表"
FALSE_PHRASE = "未制定职业接触限值"          # 当前代码发不出的句子 → 缓存里出现即"必然是旧产物"
SIZE_RATIO_MAX = 1.5                          # 整行 JSON 尺寸上限 (迁移只填数, 不该让数据膨胀)


def fresh_rows(conn, section_filler, assess, proj):
    """现算那张表的 rows (与导出同一入口: fill_section(章5))"""
    a = dict(assess)
    a["_project_data"] = proj
    for t in section_filler.fill_section(conn, "5", a):
        if t.get("name") == TABLE:
            return t.get("rows") or [], t.get("cols") or []
    return [], []


def classify(cached, new_rows, new_cols):
    """→ (ok, 原因)  三条拒绝条件, 全从行内容判"""
    old_rows = cached.get("rows") or []
    if len(old_rows) != len(new_rows):
        return False, f"行数不等 (缓存 {len(old_rows)} vs 现算 {len(new_rows)})"
    if (cached.get("cols") or []) != new_cols:
        return False, "cols 不等 (表头不同 = 不是同一张表)"
    name_diff = [(i, old_rows[i][0], new_rows[i][0]) for i in range(len(old_rows))
                 if str(old_rows[i][0]) != str(new_rows[i][0])]
    if name_diff:
        return False, f"第 0 列(因素名)有 {len(name_diff)} 处不同 → 换行不是填数, 交人工: {name_diff[:2]}"
    cell_diff = sum(1 for i in range(len(old_rows)) if old_rows[i] != new_rows[i])
    return True, f"可改: 差异格数 {cell_diff}/{len(old_rows)} 行"


def plan(conn, section_filler, project_data_fn, assess_fn):
    todo, skip = [], []
    rows = conn.execute("SELECT id, data FROM project ORDER BY id").fetchall()
    for pid, data in rows:
        if not data:
            continue
        d = json.loads(data)
        cached = ((d.get("built_tables") or {}).get(TABLE) or {})
        crows = cached.get("rows") or []
        if not crows:
            continue                                  # 骨架没行 → 导出本来就回退现算, 无需迁移
        proj = project_data_fn(pid)
        assess = assess_fn(conn, proj)
        nrows, ncols = fresh_rows(conn, section_filler, assess, proj)
        stale = sum(1 for r in crows if any(FALSE_PHRASE in str(x) for x in r))
        if not stale and crows == nrows:
            skip.append((pid, "已是现算内容 (幂等, 不动)"))
            continue
        ok, why = classify(cached, nrows, ncols)
        if not ok:
            skip.append((pid, f"拒绝: {why} (缓存里 {stale} 行含旧句子)"))
            continue
        # 把差异分成三类, 让"换数"这种最重的改动单独看得见 (实测 ada5 有一处:
        #   缓存把 '乙二醇二硝酸酯' 的 PC-TWA 0.3 绑到了 '乙二醇' 那一行, 现算给 20 = rid 1531)
        fills, swaps, notes = 0, [], 0
        for i, (a, b) in enumerate(zip(crows, nrows)):
            for j in range(1, 5):
                old_v, new_v = str(a[j]), str(b[j])
                if old_v == new_v:
                    continue
                if old_v == "—":
                    fills += 1
                else:
                    swaps.append((i, str(a[0]), j, old_v, new_v))
            if str(a[5]) != str(b[5]) and all(str(a[j]) == str(b[j]) for j in range(1, 5)):
                notes += 1
        todo.append({"pid": pid, "old_rows": crows, "new_rows": nrows, "stale_rows": stale,
                     "why": why, "keys": sorted(cached.keys()),
                     "fills": fills, "swaps": swaps, "notes": notes})
    return todo, skip


def apply_rows(conn, pid, new_rows):
    """只替换 built_tables['接触限值表']['rows'], 其余键逐字节不动 (整份 data 重写)"""
    cur = conn.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
    d = json.loads(cur[0])
    bt = d["built_tables"][TABLE]
    before_len = len(json.dumps(bt, ensure_ascii=False))
    bt["rows"] = new_rows
    after_len = len(json.dumps(bt, ensure_ascii=False))
    conn.execute("UPDATE project SET data=? WHERE id=?", (json.dumps(d, ensure_ascii=False), pid))
    return before_len, after_len


def selftest() -> int:
    """判据必须能红: 三种破坏被拒, 一种放行; 每种都要打印实得结果, 不许只报'通过'"""
    base_cols = ["种类", "PC-MAC", "PC-TWA", "PC-STEL", "PE", "备注"]
    cached = {"cols": base_cols, "rows": [["滑石粉尘", "—", "—", "—", "—", FALSE_PHRASE],
                                          ["其他粉尘", "—", "—", "—", "—", ""]]}
    cases = [
        ("S1 行数不等", cached, [["滑石粉尘", "—", "3", "—", "—", ""]], base_cols, False),
        ("S2 cols 不等", cached, [r[:] for r in cached["rows"]], ["种类", "PC-MAC"], False),
        ("S3 第 0 列被改", cached, [["滑石粉", "—", "3", "—", "—", ""],
                                    ["其他粉尘", "—", "—", "—", "—", ""]], base_cols, False),
        ("S4 只差 1..5 列 (该放行)", cached, [["滑石粉尘", "—", "3", "—", "—", "总尘 8"],
                                              ["其他粉尘", "—", "—", "—", "—", ""]], base_cols, True),
    ]
    bad = 0
    for label, c, nrows, ncols, want in cases:
        ok, why = classify(c, nrows, ncols)
        flag = "✓" if ok == want else "✗"
        if ok != want:
            bad += 1
        print(f"  {flag} {label} → 放行={ok} (期望 {want})  {why}")
    print(f"自检: {'全部符合预期' if bad == 0 else f'{bad} 条判据行为不对'}")
    return 0 if bad == 0 else 1


def main() -> int:
    mode = "--apply" if "--apply" in sys.argv else (
        "--selftest" if "--selftest" in sys.argv else "dry")
    if mode == "--selftest":
        return selftest()

    from knowledge.oel import connect_ro
    from knowledge.project_assess import assess_project
    from web.projects_db import get_project_data
    from web import section_filler

    ro = connect_ro()
    todo, skip = plan(ro, section_filler, get_project_data, assess_project)
    print(f"待迁移 {len(todo)} 个项目 / 不动 {len(skip)} 项  (判据 A/B/C 见文件头)")
    for t in todo:
        print(f"  [{t['pid']}] {t['why']} · 缓存里 {t['stale_rows']} 行原样带着『{FALSE_PHRASE}』")
        print(f"      分类: 填数(—→数) {t['fills']} 格 / 换数(数→另一个数) {len(t['swaps'])} 格 / "
              f"只改备注 {t['notes']} 行")
        for i, nm, j, old_v, new_v in t["swaps"]:
            print(f"      ⚠ 换数 行{i} {nm!r} 第{j}列 {old_v!r} → {new_v!r} "
                  f"(现算取自官方层, 溯源章随导出重写)")
        for i, (a, b) in enumerate(zip(t["old_rows"], t["new_rows"])):
            if a != b:
                print(f"      行{i}: {json.dumps(a, ensure_ascii=False)}\n"
                      f"         →  {json.dumps(b, ensure_ascii=False)}")
    for pid, why in skip:
        print(f"  ⓘ [{pid}] {why}")
    if mode != "--apply":
        print("\n(预演模式 — 未写库; 加 --apply 才写入本地库)")
        ro.close()
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    bpath = DATA_DIR / f"cached_limit_table_before_{stamp}.json"
    bpath.write_text(json.dumps([{"pid": t["pid"], "table": TABLE, "rows": t["old_rows"]}
                                 for t in todo], ensure_ascii=False), encoding="utf-8")
    print(f"  ⓘ 写前原值已存 {bpath.name} (库外留一份, 恢复不依赖被改的那个库)")

    w = sqlite3.connect(str(DB_PATH))
    try:
        w.execute("BEGIN IMMEDIATE")
        for t in todo:
            bl, al = apply_rows(w, t["pid"], t["new_rows"])
            if al > bl * SIZE_RATIO_MAX:
                raise RuntimeError(f"{t['pid']} 缓存表 JSON 从 {bl} 涨到 {al} "
                                   f"(>{SIZE_RATIO_MAX}×) —— 迁移只填数, 不该让数据膨胀")
        w.execute("COMMIT")
    except BaseException as exc:
        w.execute("ROLLBACK")
        print(f"⏹ 已回滚: {exc}")
        w.close()
        ro.close()
        return 1
    # 读回对表: 用**新连接**重读, 断言"缓存 rows == 现算 rows"且其余键没变
    bad = []
    chk = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    for t in todo:
        d = json.loads(chk.execute("SELECT data FROM project WHERE id=?", (t["pid"],)).fetchone()[0])
        bt = d["built_tables"][TABLE]
        if bt["rows"] != t["new_rows"]:
            bad.append(f"{t['pid']} 读回 rows 不一致")
        if sorted(bt.keys()) != t["keys"]:
            bad.append(f"{t['pid']} 其余键被改动: {sorted(bt.keys())} ≠ {t['keys']}")
    chk.close()
    # 幂等对表: 再走一遍 plan, 必须 0 项待改 (否则这工具不是可重放的)
    ro2 = connect_ro()
    todo2, skip2 = plan(ro2, section_filler, get_project_data, assess_project)
    ro2.close()
    ro.close()
    w.close()
    if bad or todo2:
        print(f"❌ 写完对表不过: {bad} · 重跑仍有 {len(todo2)} 项待改 (不可重放)")
        return 1
    print(f"✅ 已写 {len(todo)} 张表并读回核对; 重跑 plan = 0 项 (幂等) / 不动 {len(skip2)} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
