#!/usr/bin/env python3
"""#53: 登记两个**住建部一手公告逐字读到过**的工程建设国标 (GB50009-2012 / GB50736-2012)

为什么这两条现在可以登记 (2026-10-17 主代理亲自点开一手页, 不是引用子代理结论)
    工程建设国标不在 SAMR/openstd 的收录范围 (`std.samr.gov.cn/gb/search/gbQueryPage?searchText=
    GB 50009` 与 `GB 50736` 都返回 `{"total":0}`, 同接口查 `GB 2811` 有行 → 接口活着, 是范围问题),
    也不在 NHC 卫生标准目录里 —— 于是存在性尺子 (tools/test_std_existence.py) 只能把它们报成
    "查无此号"。上一窗口我判"不登记, 保持红"的理由是**当时只拿到二手转述**; 这一轮一手公告页取得到了:

      GB50009-2012《建筑结构荷载规范》
        https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2012/art_17339_210754.html
        页上逐字读到: 公文名称=关于发布国家标准《建筑结构荷载规范》的公告;
        文号=中华人民共和国住房和城乡建设部公告第1405号; 发文日期 2012-05-28; 实施日期 2012-10-01;
        废止日期=空; 正文"编号为GB50009-2012…原《建筑结构荷载规范》GB50009-2001（2006版）同时废止"。
      GB50736-2012《民用建筑供暖通风与空气调节设计规范》
        https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2012/art_17339_209265.html
        页上逐字读到: 文号=第1270号公告; 发文 2012-01-21; 实施 2012-10-01; 废止日期=空;
        正文"编号为GB50736-2012…《采暖通风与空气调节设计规范》GB50019-2003 中相应条文同时废止"。

明确**不登记**的那一个 (不登记是决定, 不是漏掉)
    GB 50021-2001《岩土工程勘察规范》(含 2009 局部修订): 一手页在本机四条路径都取不到 ——
    `std.mohurd.gov.cn` TLS 失败且 DNS 解析不出 (DoH SERVFAIL, r.jina.ai 同样 EAI_AGAIN);
    `www.mohurd.gov.cn` 文件库检索 4 组关键词无本体; 2026 年废止目录附件里 grep `50021` 0 命中;
    SAMR 该族 total=0。**"取不到一手"不等于"号是假的", 也不等于"可以登记为已核实"** → 保持红,
    留在任务 #53。

两处只有注记、没有字段的账 (写在这里, 别让下一个人以为库里存了)
    · GB 50009-2012 的 13 条**强制性条文**已由 GB 55001-2021《工程结构通用规范》接管而废止
      (住建部公告2021年第70号附件2 第5项逐条列出 3.1.2/3.1.3/3.2.3/3.2.4/5.1.1/5.1.2/5.3.1/
      5.5.1/5.5.2/7.1.1/7.1.2/8.1.1/8.1.2)。`standard_db` 没有承载"部分条文废止"的列 ——
      本工具**不新开列** (加列属任务 #23 的"只加列不改列"白名单流程), 号仍按"现行"登记。
    · 两条的 `replace_of` 都留 NULL: 被代替的旧号 (GB50009-2001 / GB50019-2003) 不在我们目录里,
      指过去会让代替一致性尺子 (#45) 多出一条"指向不存在的行"的红 —— 那是新造的噪声, 不是证据。

规矩 (与 tools/add_samr_std_codes.py 同一套)
    · 默认 dry-run; `--apply` 只写本地库 (进生产需用户单独批准);
    · 幂等: 号按 `norm_code` 归一后已存在就跳过并点名;
    · 写完逐行读回 + 行数只 +N; 不过就回滚, 回滚记录写到 DATA_DIR 之外也要拷一份;
    · 每条都带 url + source='mohurd' + verified=1 —— 与 samr 那批同一口径, 尺子能认出"核实过"。

用法:
    .venv/bin/python tools/add_mohurd_std_codes.py            # 预演 (只读)
    .venv/bin/python tools/add_mohurd_std_codes.py --apply    # 写本地库
"""
import sys
import json
import time
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.paths import DB_PATH, DATA_DIR                    # noqa: E402
from knowledge.standard_version import norm_code                 # noqa: E402

# (code, name, state, pubdate, effdate, url, 一手核对说明)
ROWS = [
    ("GB50009-2012", "建筑结构荷载规范", "现行", "2012-05-28", "2012-10-01",
     "https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2012/art_17339_210754.html",
     "住建部公告第1405号页逐字读到: 发文 2012-05-28 / 实施 2012-10-01 / 废止日期空; "
     "原 GB50009-2001（2006版）同时废止; 13 条强条另由公告2021年第70号废止 (无字段承载, 见文件头)"),
    ("GB50736-2012", "民用建筑供暖通风与空气调节设计规范", "现行", "2012-01-21", "2012-10-01",
     "https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2012/art_17339_209265.html",
     "住建部公告第1270号页逐字读到: 发文 2012-01-21 / 实施 2012-10-01 / 废止日期空; "
     "GB50019-2003 相应条文同时废止 —— 那是『相应条文』级废止, 不写成本行的 replace_of"),
]


def plan(conn):
    have = {norm_code(r[0]) for r in conn.execute("SELECT code FROM standard_db")}
    todo, skip = [], []
    for row in ROWS:
        code = row[0]
        if norm_code(code) in have:
            cur = conn.execute("SELECT state, source, verified, url FROM standard_db WHERE code=?",
                               (code,)).fetchone()
            skip.append(f"  ⓘ {code} 已在目录里 (幂等, 现值 {cur})")
            continue
        todo.append(row)
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
                      " url, source, verified) VALUES (?,?,?,?,?,NULL,?, 'mohurd', 1)",
                      (code, name, state, pub, eff, url))
        n1 = w.execute("SELECT count(*) FROM standard_db").fetchone()[0]
        if n1 != n0 + len(todo):
            raise RuntimeError(f"行数 {n0} → {n1}, 期望 {n0}+{len(todo)} —— 不许顺手改动别的行")
        for code, name, state, pub, eff, url, _why in todo:
            got = w.execute("SELECT name, state, pubdate, effdate, replace_of, url, source,"
                            " verified FROM standard_db WHERE code=?", (code,)).fetchone()
            if got != (name, state, pub, eff, None, url, "mohurd", 1):
                raise RuntimeError(f"{code} 读回 {got}, 期望 {(name, state, pub, eff, None, url, 'mohurd', 1)}")
        w.execute("COMMIT")
    except BaseException as exc:
        w.execute("ROLLBACK")
        print(f"⏹ 已回滚: {exc}")
        w.close()
        return 1
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bpath = DATA_DIR / f"add_mohurd_std_codes_{stamp}.json"
    bpath.write_text(json.dumps([{"code": t[0], "delete": True} for t in todo],
                                ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 已补 {len(todo)} 行并逐行读回核对 (行数 {n0} → {n1}); 回滚记录 {bpath.name}")
    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
