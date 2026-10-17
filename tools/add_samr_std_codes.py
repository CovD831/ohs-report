#!/usr/bin/env python3
"""#52: 把 5 个"成品印了、目录里查无此号"的**真号**补进 standard_db (SAMR 一手逐号核过)

为什么这批要补 (#52 取证, 子代理逐号 + 主代理复算)
    存在性尺子 (tools/test_std_existence.py) 的"源头" = 一手 NHC 目录快照 ∪ standard_db 里
    source != 'report_evidence' 的行。成品印的 25 个未登记号里, 有一族**根本不在 NHC 的归口范围**
    (环保/地震/个体防护类国标) —— 快照不收它们, standard_db 里也没有行, 于是尺子把它们报成
    "查无此号"。这些是**真号误报**: 报红会淹没真正的幽灵号 (#17 那批), 也让评审看到一排假缺陷。

只补"我自己这一轮在 SAMR 状态页逐号读到过"的 5 条 (2026-10-17, std.samr.gov.cn/gb/search):
    GB 16297-1996  大气污染物综合排放标准      现行  1996-04-12 / 1997-01-01
    GB 18306-2015  中国地震动参数区划图        现行  2015-05-15 / 2016-06-01   (2001 版废止)
    GB 21148-2020  足部防护 安全鞋            现行  2020-07-23 / 2021-08-01   (2007 版废止, 已改名)
    GB 2811-2019   头部防护 安全帽            现行  2019-12-31 / 2020-07-01   (1989/2007 废止)
    GB 3838-2002   地表水环境质量标准          现行  2002-04-28 / 2002-06-01

明确**不补**的三类, 以及为什么 (不补是决定, 不是漏掉)
    · GB 50009-2012 / GB 50021-2001 / GB 50736-2012 (工程建设国标): 存在性无疑, 但"现行"细节只有
      住建部公告的**二手转述** (SAMR/openstd 不收工程建设标准) → 一手页未取得, 按红线不许登记为核实。
    · GB 14866-2006 → 2023 (《个人用眼护具技术要求》→《眼面防护具通用技术规范》): 新版**名称与
      适用范畴都变了**, 补进来会把成品里的旧引用自动纠版到新号 = 用旧判据引新版 (#17 定过的红线)。
      要动它必须连 PPE 判据内容一起复核 → 另立任务。
    · 11 个幽灵年份 (GBZ 1—2007/2011、2.2—2009、188—2019、GBZ/T 194 四个年份、195—2008、
      229.1—2024、GB 38144—2019): 目录里没有这些年号, 判红是**对的**, 补登记=糊护栏 → 一律不登记。

规矩
    · 默认 dry-run; `--apply` 只写本地库 (进生产需用户单独批准);
    · 幂等: 号已存在 (按 norm_code 归一对表) 就跳过并点名, 不重复插行;
    · 写完读回逐行对表 + 行数只 +5 (不许顺手改动别的行), 不过就回滚;
    · 每条的 url 指向 SAMR 检索页 (可点开复算), source='samr', verified=1 —— 与库里
      现有 67 行 samr 同一口径。

用法:
    .venv/bin/python tools/add_samr_std_codes.py            # 预演 (只读)
    .venv/bin/python tools/add_samr_std_codes.py --apply    # 写本地库
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
from knowledge.standard_version import norm_code                 # noqa: E402

SAMR = "https://std.samr.gov.cn/gb/search/gbQueryPage?searchText="

# (code, name, state, pubdate, effdate, url 关键词, 一手核对说明)
ROWS = [
    ("GB16297-1996", "大气污染物综合排放标准", "现行", "1996-04-12", "1997-01-01",
     "GB 16297", "2026-10-17 SAMR 检索页逐号读到: 现行, 发布 1996-04-12, 实施 1997-01-01"),
    ("GB18306-2015", "中国地震动参数区划图", "现行", "2015-05-15", "2016-06-01",
     "GB 18306", "2026-10-17 SAMR: 现行 (2015-05-15 / 2016-06-01); 同族 GB18306-2001 标废止"),
    ("GB21148-2020", "足部防护 安全鞋", "现行", "2020-07-23", "2021-08-01",
     "GB 21148", "2026-10-17 SAMR: 现行 (2020-07-23 / 2021-08-01); 2007 版《个体防护装备 安全鞋》废止"
     " —— 名字也变了, 成品若引旧名须连内容复核"),
    ("GB2811-2019", "头部防护 安全帽", "现行", "2019-12-31", "2020-07-01",
     "GB 2811", "2026-10-17 SAMR: 现行 (2019-12-31 / 2020-07-01); 1989/2007 两版废止, 无更新版"),
    ("GB3838-2002", "地表水环境质量标准", "现行", "2002-04-28", "2002-06-01",
     "GB 3838", "2026-10-17 SAMR: 现行 (2002-04-28 / 2002-06-01); openstd 不收环保类国标, 非否证"),
]


def plan(conn):
    have = {norm_code(r[0]) for r in conn.execute("SELECT code FROM standard_db")}
    todo, skip = [], []
    for code, name, state, pub, eff, q, why in ROWS:
        if norm_code(code) in have:
            skip.append(f"  ⓘ {code} 已在目录里 (幂等, 不重复插行)")
            continue
        todo.append((code, name, state, pub, eff, SAMR + q.replace(" ", "%20"), why))
    return todo, skip


def main() -> int:
    mode = "--apply" if "--apply" in sys.argv else "dry"
    ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    n0 = ro.execute("SELECT count(*) FROM standard_db").fetchone()[0]
    todo, skip = plan(ro)
    ro.close()
    print(f"待补 {len(todo)} 行 / 跳过 {len(skip)} 行 (standard_db 现有 {n0} 行)")
    for code, name, state, pub, eff, url, why in todo:
        print(f"  + {code}  {name}  [{state}]  {pub} / {eff}\n      一手: {why}\n      url: {url}")
    for s in skip:
        print(s)
    if not todo:
        print("没有要补的行 (已是目标状态) — 幂等退出")
        return 0
    if mode != "--apply":
        print("\n(预演模式 — 未写库; 加 --apply 才写入本地库)")
        return 0

    w = sqlite3.connect(str(DB_PATH))
    try:
        w.execute("BEGIN IMMEDIATE")
        for code, name, state, pub, eff, url, _why in todo:
            w.execute("INSERT INTO standard_db (code, name, state, pubdate, effdate, replace_of,"
                      " url, source, verified) VALUES (?,?,?,?,?,NULL,?,?,1)",
                      (code, name, state, pub, eff, url, "samr"))
        n1 = w.execute("SELECT count(*) FROM standard_db").fetchone()[0]
        if n1 != n0 + len(todo):
            raise RuntimeError(f"行数 {n0} → {n1}, 期望 {n0}+{len(todo)} —— 不许顺手改动别的行")
        for code, _n, state, _p, _e, _u, _w2 in todo:
            got = w.execute("SELECT state, source, verified FROM standard_db WHERE code=?",
                            (code,)).fetchone()
            if got != (state, "samr", 1):
                raise RuntimeError(f"{code} 读回 {got}, 期望 {(state, 'samr', 1)}")
        w.execute("COMMIT")
    except BaseException as exc:
        w.execute("ROLLBACK")
        print(f"⏹ 已回滚: {exc}")
        w.close()
        return 1
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bpath = DATA_DIR / f"add_samr_std_codes_{stamp}.json"
    bpath.write_text(json.dumps([{"code": t[0], "delete": True} for t in todo],
                                ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 已补 {len(todo)} 行并逐行读回核对 (行数 {n0} → {n1}); 回滚记录 {bpath.name}")
    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
