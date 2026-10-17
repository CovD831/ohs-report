#!/usr/bin/env python3
"""跨章只读缓存漂移探测器 —— built_tables 骨架 vs 当前代码现算 (dry-run, 不落一字)

为什么要有它 (2026-10 取证, 数字为本工具对真库实测, 见 README 级注释与报告)
    导出链是"骨架先行": `web/word_export.py:_tables_for_sub` 里, 只要
    `project.data.built_tables[<表名>].rows` 有行就沿用缓存, 现算只兜底空骨架。
    于是"代码修好了但纸没变"是结构性可能 —— 上一窗口修好并验证过的接触限值表
    确实已同步 (实测两项目 45/45、39/39 一致), 但其余表的缓存从未跨章对过账。
    `tools/fix_cached_limit_table.py` 只比了"章5 现算"; 本工具把取景换成**注册表驱动**
    的全章扫描: 每张缓存表的章归属从 `web.report_struct.CHAPTERS` +
    `web.word_export._SUB_TABLE_MAP` (挂载节 → 现算章) 推出, **不手抄章号** ——
    本项目栽过取景坑 (0 基 range 被手抄成 1 基, 少读一整页却量出"只差 0")。

四类判据 (对每张"缓存非空"的表, 缓存 rows vs 现算 rows)
    一致 / 内容不同 (行数相等, 差异只在数据列) / 行数不等 / 现算无此表 (章归属或该章不产这张表)。
    缓存 rows 为空的不算漂移 (导出本来就回退现算) —— 单独报数量, 让"数出 0"可被怀疑。
    每个差异点名到 表名 / 行号(0 基) / 第几列(带列名) / 旧值→新值 (单元格里的字, 不是哈希)。

比较口径 (从数据派生, 无表名/项目号白名单)
    · 单元格先做**空白归一** (split+join): 只差格式/空格的缓存不算漂移;
    · **自增序号列**不算内容差异: 某列在缓存与现算里都是互异整数且集合相同 (如行重排/换号),
      判为 id 形列并豁免 —— 从两列取值形态派生, 不认列名;
    · 行数不等时按 LCS 保序对齐, 点名"缓存多出的行"与"现算多出的行", 逐格给值。

旧产物 vs 客户手改 (下一步要不要重算的依据)
    `tools/fix_cached_limit_table.py` 用一句『未制定职业接触限值』当锚 —— 那是**泛化判据的一个实例**:
    泛化后 = "缓存里出现**当前代码在任何项目现算里都发不出**的取值形态 → 必然是旧产物"。
    本工具现算全部项目 × 注册表全部现算章的每一格, 得到"现算可发出的取值集合"(corpus,
    零硬编码句子清单); 差异格的缓存值不在 corpus = 判"必然是旧产物"。
    再叠两条同样从数据派生的细分证据:
      · 同一旧形态出现在 ≥2 个项目 = 跨项目重现 → 最强 (客户不可能手打出一样的长句);
      · 缓存值是现算值的**严格前缀** = 旧截断族 (与 `word_export._unfreeze_truncated_rows`
        同源判据) —— 管理制度检查表/类比危害分布表实测就是这个形状。
    盲区 (明说):
      ① 短形态 (数字、"—"、单字等级) 几乎必在 corpus 里 → "旧数值/旧等级"看不见归属, 只报差异;
      ② 客户手改的长句同样"现算发不出" —— 单项目不重现时与旧产物**不可分**, 报告里点名让人看;
      ③ corpus 只含**表格格** (fill_section 出口); 若某旧形态如今只在正文 (paragraph_gen) 还能
        发出, 会被误标旧产物 (方向是过度标记, 不掩盖漂移);
      ④ 导出侧的 std 版本护栏/截断解冻 (_refresh_stale_std_tables/_unfreeze) 能当场洗掉**部分**
        漂移 —— 本工具量的是"缓存 vs 裸现算", 成品纸面差异可能比这里小, 不会比这里大。

安全 (验收链不得写数据目录)
    默认 dry-run; 连接只走 `knowledge.oel.connect_ro` (uri mode=ro) —— 可写连接会在 data/
    留 -wal/-shm。所有路径经 `knowledge.paths` (DB_PATH/DATA_DIR, 认 OHS_DATA_DIR),
    不硬拼 ROOT/'data' (`tools/chain_audit.py` 点名硬拼路径)。
    **本文件对数据库零写入, 也不提供任何 --apply 开关。**

退出码
    有"内容不同"格或"行数不等"表 → rc=1 并打印总数; 全部可比表一致 → rc=0;
    注册表与结构常量脱节 (章号不在 CHAPTERS) → rc=2 (取景坏了, 别信任何输出)。
    输出头部固定给: 检查了多少张表 / 各类命中多少。

用法
    .venv/bin/python tools/detect_cache_drift.py            # 人读报告 (真库, 只读)
    .venv/bin/python tools/detect_cache_drift.py --json     # 机器可读 (tools/test_detect_cache_drift.py 用)
    .venv/bin/python tools/detect_cache_drift.py --max-print 60
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.paths import DB_PATH                      # noqa: E402
from knowledge.oel import connect_ro                     # noqa: E402
from knowledge.project_assess import assess_project      # noqa: E402
from web.report_struct import CHAPTERS                   # noqa: E402
from web.section_filler import fill_section              # noqa: E402
from web.word_export import _SUB_TABLE_MAP               # noqa: E402

# 类别与标签 = 输出用词 (判据本身不含它们); 集中一处, 免得散落成隐式白名单
CAT_SAME = "一致"
CAT_DIFF = "内容不同（行数相等，差异只在数据列）"
CAT_LEN = "行数不等"
CAT_NOFRESH = "现算无此表（章归属或该章不产这张表）"
CAT_EMPTY = "缓存空（导出本就回退现算，不计漂移）"
NO_CORR = "<缓存无此行>"
NO_FRESH_ROW = "<现算无此行>"

CACHE_ROW_MARK = "旧截断族：缓存值是现算值的严格前缀"
# 每处差异的归属判定 (机器可读, 汇总按此计数, 不靠标签字符串做子串匹配)
V_STALE_REPEAT = "stale_repeat"     # 现算 anywhere 发不出 + 跨项目重现 → 必然旧产物
V_STALE_UNIQUE = "stale_unique"     # 现算 anywhere 发不出 + 单项目 → 旧产物, 亦可能与手改不可分
V_TRUNC_PREFIX = "trunc_prefix"     # 缓存值是现算值的严格前缀 → 旧截断族
V_UNDECIDABLE = "undecidable"       # 此形态现算在他处可发出 → 旧产物/手改不可判
V_LABELS = {V_STALE_REPEAT: "必然是旧产物：当前代码现算不出此形态，且在多个项目重现",
            V_STALE_UNIQUE: "必然是旧产物：当前代码现算不出此形态（单项目，亦可能是客户手改，交人工看原文）",
            V_TRUNC_PREFIX: CACHE_ROW_MARK,
            V_UNDECIDABLE: "现算在他处可发出此值 → 旧产物/手改不可判（盲区①②）"}


def norm(v) -> str:
    """空白归一: 连续空白折成一个空格再去首尾 —— '只改格式/空格'不许判红"""
    return " ".join(str("" if v is None else v).split())


def nrow(r) -> tuple:
    return tuple(norm(c) for c in r)


def load_registry():
    """表名 → [(挂载节, 现算章)]; 章号一律从 _SUB_TABLE_MAP + CHAPTERS 取, 并对表校验。

    返回 (name_reg, fill_chs, 脱节清单)。脱节非空 = 取景本身坏了 → 调用方 rc=2。
    """
    name_reg = defaultdict(list)
    bad = []
    for mount_sub, entry in _SUB_TABLE_MAP.items():
        fill_ch, wanted = entry[0], entry[1]
        if fill_ch not in CHAPTERS:
            bad.append(f"{mount_sub} 的现算章 {fill_ch} 不在 CHAPTERS")
        if mount_sub.split(".")[0] not in CHAPTERS:
            bad.append(f"挂载节 {mount_sub} 的首段不在 CHAPTERS")
        for w in wanted:
            name_reg[w].append((mount_sub, fill_ch))
    fill_chs = sorted({fc for v in name_reg.values() for _, fc in v})
    return dict(name_reg), fill_chs, bad


def compute_fresh(conn, proj, fill_chs):
    """现算: 注册表涉及的全部章逐章 fill_section (与导出同一入口, 章号来自注册表)。

    → (每章 {表名: 表} 的 dict, 该项目全部现算单元格 norm 值的 set)。
    """
    assess = dict(assess_project(conn, proj))
    assess["_project_data"] = proj
    by_ch = {}
    cells = set()
    for ch in fill_chs:
        d = {}
        for t in fill_section(conn, ch, assess):
            d.setdefault(t.get("name"), t)          # 同名重复取首个 (导出按序 emit)
            for c in (t.get("cols") or []):
                cells.add(norm(c))
            for r in (t.get("rows") or []):
                for c in r:
                    cells.add(norm(c))
        by_ch[ch] = d
    return by_ch, cells


def lcs_pairs(a: list, b: list) -> list:
    """保序最长公共子序列对齐 (行数不等时用): → [(i缓存, j现算)]"""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def id_cols(crows: list, frows: list) -> set:
    """自增/序号列豁免: 两列都是**连续整数**且互异、集合相同 (换号、行重排) → 不算内容差异。

    从取值形态派生, 不认列名; 要求连号 (排序后公差 1) 而非任意整数集, 免得
    "恰好连号的数据列"被顺手豁免。行数不等时不调用 (另行处理)。
    """
    out = set()
    if not crows or not frows:
        return out
    width = max(max(len(r) for r in crows), max(len(r) for r in frows))

    def run_of_consecutive(vals):
        if len(vals) < 2 or any(v == "" for v in vals):
            return False
        try:
            ia = [int(v) for v in vals]
        except ValueError:
            return False
        if len(set(ia)) != len(ia):
            return False
        s = sorted(ia)
        return s == list(range(s[0], s[0] + len(s)))

    for j in range(width):
        ca = [norm(r[j]) if j < len(r) else "" for r in crows]
        fb = [norm(r[j]) if j < len(r) else "" for r in frows]
        if run_of_consecutive(ca) and run_of_consecutive(fb) and sorted(ca, key=int) == sorted(fb, key=int):
            out.add(j)
    return out


def col_name(j, cached, fresh) -> str:
    for src in (cached.get("cols") or [], (fresh or {}).get("cols") or []):
        if j < len(src):
            return str(src[j])
    return f"第{j}列"


def compare_table(cached: dict, fresh: dict | None, name: str) -> dict:
    """一张缓存表 → 记录 (类别 + 差异点名)。fresh=None 表示注册表内但该项目现算不产。"""
    crows = cached.get("rows") or []
    rec = {"table": name, "category": None, "cached_rows": len(crows),
           "fresh_rows": None if fresh is None else len(fresh.get("rows") or []),
           "diffs": [], "notes": []}
    if fresh is None:
        rec["category"] = CAT_NOFRESH
        return rec
    frows = fresh.get("rows") or []
    if not frows and crows:
        # 现算同名表存在但 0 行: 骨架补插会让缓存独占纸面 → 归"现算无此表" (有对照物会误导)
        rec["category"] = CAT_NOFRESH
        rec["notes"].append("现算同名表 0 行 (骨架补插使缓存独占纸面)")
        return rec
    ck = [nrow(r) for r in crows]
    fk = [nrow(r) for r in frows]
    if (cached.get("cols") or []) != (fresh.get("cols") or []):
        rec["notes"].append(f"表头不等: 缓存 {cached.get('cols')} vs 现算 {fresh.get('cols')}")
    if len(ck) != len(fk):
        rec["category"] = CAT_LEN
        pairs = lcs_pairs(ck, fk)
        mi = {i for i, _ in pairs}
        mj = {j for _, j in pairs}
        for i, r in enumerate(crows):
            if i in mi:
                continue
            for j, cell in enumerate(r):
                if norm(cell) == "":
                    continue
                rec["diffs"].append({"row": i, "col": j, "col_name": col_name(j, cached, fresh),
                                     "old": norm(cell), "new": NO_FRESH_ROW, "side": "缓存多出的行"})
        for j, r in enumerate(frows):
            if j in mj:
                continue
            for k, cell in enumerate(r):
                if norm(cell) == "":
                    continue
                rec["diffs"].append({"row": j, "col": k, "col_name": col_name(k, cached, fresh),
                                     "old": NO_CORR, "new": norm(cell), "side": "现算多出的行"})
        return rec
    skip = id_cols(crows, frows)
    if skip:
        rec["notes"].append(f"豁免的序号形列 (两列皆为互异整数且集合同): {sorted(skip)}")
    for i, (a, b) in enumerate(zip(crows, frows)):
        width = max(len(a), len(b))
        for j in range(width):
            if j in skip:
                continue
            av = norm(a[j]) if j < len(a) else "<缺格>"
            bv = norm(b[j]) if j < len(b) else "<缺格>"
            if av == bv:
                continue
            rec["diffs"].append({"row": i, "col": j, "col_name": col_name(j, cached, fresh),
                                 "old": av, "new": bv, "side": None})
    rec["category"] = CAT_SAME if not rec["diffs"] else CAT_DIFF
    return rec


def attribute(records: list, corpus: set) -> None:
    """给每个差异格打"旧产物 vs 手改"判定 (泛化判据, 见文件头第 3 节)。

    判定形态全部从现算 corpus 与两格取值本身派生: 不在 corpus / 严格前缀 / 跨项目重现,
    结论写进机器可读的 d["verdict"], 标签只是输出用词。
    """
    repeat = defaultdict(set)                                # 形态 → 出现项目集
    for r in records:
        for d in r["diffs"]:
            o = d["old"]
            if o and o != NO_CORR and o not in corpus:
                repeat[o].add(r["pid"])
    for r in records:
        for d in r["diffs"]:
            o, n = d["old"], d["new"]
            is_prefix = bool(n) and n != NO_FRESH_ROW and bool(o) and n.startswith(o) and len(n) > len(o)
            tags = []
            if o and o != NO_CORR and o not in corpus:
                npids = len(repeat.get(o, set()))
                d["verdict"] = V_STALE_REPEAT if npids >= 2 else V_STALE_UNIQUE
                if d["verdict"] == V_STALE_REPEAT:
                    tags.append(f"必然是旧产物：当前代码现算不出此形态，且在 {npids} 个项目重现")
                else:
                    tags.append(V_LABELS[V_STALE_UNIQUE])
            elif is_prefix:
                d["verdict"] = V_TRUNC_PREFIX
            if is_prefix:
                tags.append(CACHE_ROW_MARK)
            if "verdict" not in d and o and o != NO_CORR:
                d["verdict"] = V_UNDECIDABLE
                tags.append("现算在他处可发出此值 → 旧产物/手改不可判（盲区①②）")
            d["tags"] = tags


def scan(conn, verbose=True):
    """主扫描: 遍历全部项目 → corpus + 逐张非空缓存表比对。→ 结果 dict (纯只读)"""
    name_reg, fill_chs, bad = load_registry()
    if bad:
        return {"registry_broken": bad}
    rows = conn.execute("SELECT id, name, data FROM project ORDER BY id").fetchall()
    projects = []
    for pid, pname, raw in rows:
        try:
            d = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            continue
        d["name"] = pname
        projects.append((pid, d))
    corpus = set()
    fresh_cache = {}
    fresh_failed = []
    n_with_cache = 0
    for pid, proj in projects:
        if proj.get("built_tables"):
            n_with_cache += 1
        try:
            by_ch, cells = compute_fresh(conn, proj, fill_chs)
        except Exception as e:                       # 取景不许静默: 点名哪个项目算不出
            fresh_failed.append({"pid": pid, "error": f"{type(e).__name__}: {e}"[:160]})
            continue
        fresh_cache[pid] = by_ch
        corpus |= cells
    records = []
    empty_cache_n = 0
    for pid, proj in projects:
        bt = proj.get("built_tables") or {}
        if not bt:
            continue
        by_ch = fresh_cache.get(pid)
        for name, cached in sorted(bt.items()):
            crows = cached.get("rows") or []
            mounts = name_reg.get(name, [])
            info = {"pid": pid, "project": proj.get("name", ""), "table": name,
                    "mount_subs": [m for m, _ in mounts],
                    "fill_ch_registry": sorted({fc for _, fc in mounts}),
                    "fill_ch_used": None, "cache_section": cached.get("section")}
            if not crows:
                # 具名成"缓存空"记录 (而不是从视野里消失): 导出回退现算, 不计漂移,
                # 但"有多少张表是空的"必须是可数的事实 (数出 0 先怀疑取景)。
                records.append({**info, "category": CAT_EMPTY, "cached_rows": 0,
                                "fresh_rows": None, "diffs": [], "notes": []})
                empty_cache_n += 1
                continue
            if by_ch is None:
                continue                              # 该项目现算失败, 已点名, 不假装一致
            fresh_tab = None
            used = None
            for fc in dict.fromkeys(fc for _, fc in mounts):
                t = by_ch[fc].get(name)
                if t is not None:
                    fresh_tab, used = t, fc
                    break
            if fresh_tab is None and not mounts:
                # 注册表无此名: 全章扫精确名 / wanted 的前缀形 (镜像 _match_wanted 口径)
                for fc in fill_chs:
                    for tname, t in by_ch[fc].items():
                        if tname == name or str(tname).startswith(f"{name}(") \
                                or str(tname).startswith(f"{name}（"):
                            fresh_tab, used = t, fc
                            break
                    if fresh_tab is not None:
                        break
            rec = compare_table(cached, fresh_tab, name)
            rec.update({k: v for k, v in info.items() if k != "table"})
            rec["fill_ch_used"] = used
            records.append(rec)
    attribute(records, corpus)
    tally = defaultdict(int)
    cell_n = 0
    for r in records:
        tally[r["category"]] += 1
        if r["category"] in (CAT_DIFF, CAT_LEN):
            cell_n += len(r["diffs"])
    verdict_counts = defaultdict(int)
    for r in records:
        for d in r["diffs"]:
            verdict_counts[d.get("verdict", "none")] += 1
    return {
        "meta": {
            "db": str(DB_PATH),
            "chapters_defined": sorted(CHAPTERS.keys()),
            "fill_chapters": fill_chs,
            "projects_total": len(rows),
            "projects_parseable": len(projects),
            "projects_with_cache": n_with_cache,
            "fresh_failed": fresh_failed,
            "corpus_cells": len(corpus),
            "empty_cache_tables": empty_cache_n,
            "tables_checked": len(records),
            "nonempty_tables": len(records) - empty_cache_n,
        },
        "records": records,
        "summary": {"by_category": dict(tally), "drift_units": cell_n,
                    "verdicts": dict(verdict_counts)},
    }


def main(argv: list) -> int:
    as_json = "--json" in argv
    cap = 40
    for i, a in enumerate(argv):
        if a == "--max-print" and i + 1 < len(argv):
            cap = int(argv[i + 1])
    conn = connect_ro()                              # 只读连接: 不留 -wal/-shm, 不建表
    try:
        out = scan(conn)
    finally:
        conn.close()
    if "registry_broken" in out:
        print(f"❌ 注册表与结构常量脱节 (取景坏了, 下面任何数字都不可信): {out['registry_broken']}")
        return 2
    m, s = out["meta"], out["summary"]
    drift_tables = s["by_category"].get(CAT_DIFF, 0) + s["by_category"].get(CAT_LEN, 0)
    rc = 1 if (drift_tables or s["drift_units"]) else 0
    if as_json:
        out["meta"]["rc"] = rc
        print(json.dumps(out, ensure_ascii=False))
        return rc
    print("== 取景 ==")
    print(f"db (只读 connect_ro): {m['db']}")
    print(f"章结构: report_struct.CHAPTERS {len(m['chapters_defined'])} 章; "
          f"注册表涉及现算章 {m['fill_chapters']} (章号零手抄, 脱节即 rc=2)")
    print(f"项目: 共 {m['projects_total']} / 可解析 {m['projects_parseable']} / "
          f"带 built_tables {m['projects_with_cache']}")
    if m["fresh_failed"]:
        print(f"⚠ 现算失败(已点名, 其缓存表未参与比对): "
              f"{[(x['pid'], x['error']) for x in m['fresh_failed']]}")
    print(f"现算 corpus (当前代码全部项目×全部注册章可发出的格值形态): {m['corpus_cells']} 个")
    print(f"检查缓存表: {m['tables_checked']} 张 (非空可比 {m['nonempty_tables']} 张; "
          f"缓存空 {m['empty_cache_tables']} 张列出不计漂移)")
    print("\n== 逐项目明细 ==")
    cur = None
    for r in out["records"]:
        if r["pid"] != cur:
            cur = r["pid"]
            print(f"\n[{cur}] {r['project'][:36]}")
        head = (f"  {r['table']}: {r['category']} | 缓存 {r['cached_rows']} 行 vs 现算 {r['fresh_rows']} 行 | "
                f"挂载 {r['mount_subs'] or '(注册表无此名)'} 现算章 {r['fill_ch_used'] or r['fill_ch_registry']} "
                f"缓存自报节 {r['cache_section']}")
        print(head)
        for nt in r["notes"]:
            print(f"     · {nt}")
        shown = 0
        for d in r["diffs"]:
            if shown >= cap:
                print(f"     … 另有 {len(r['diffs']) - shown} 处 (--max-print 调大 / --json 看全量)")
                break
            print(f"     行{d['row']} 列{d['col']}[{d['col_name']}] "
                  f"{d['old'][:70]!r} → {d['new'][:70]!r}"
                  f"{'（' + d['side'] + '）' if d['side'] else ''}")
            for t in d.get("tags") or []:
                print(f"        判定: {t}")
    print("\n== 汇总 ==")
    for k in (CAT_SAME, CAT_DIFF, CAT_LEN, CAT_NOFRESH, CAT_EMPTY):
        print(f"  {k}: {s['by_category'].get(k, 0)} 张")
    print(f"  漂移单元 (内容不同格 + 行数不等行的格): {s['drift_units']} 处")
    vlabels = {V_STALE_REPEAT: "必然是旧产物·跨项目重现", V_STALE_UNIQUE: "必然是旧产物·单项目(与手改不可分)",
               V_TRUNC_PREFIX: "旧截断族(缓存为现算严格前缀)", V_UNDECIDABLE: "不可判(此形态现算在他处已存在)"}
    print("  旧产物判据分布: " + ", ".join(
        f"{vlabels.get(k, '行级/空值无标签')}={v}" for k, v in sorted(s["verdicts"].items())))
    print("  盲区: ①短形态数值/等级在 corpus 复现 → 只报差异不报旧产物; "
          "②单项目长句与旧产物不可分; ③corpus 只含表格格; "
          "④导出侧 std 护栏/截断解冻能当场洗掉部分漂移, 成品纸面差异 ≤ 本报告。")
    if rc:
        print(f"\n结论: 内容不同/行数不等共 {drift_tables} 张表、{s['drift_units']} 处漂移单元 → rc=1")
        return 1
    if m["nonempty_tables"] == 0:
        print("\n⚠ 可比缓存表 0 张 —— 数出 0 先怀疑取景 (项目集/注册表/库路径), 不判绿")
        return 0
    print("\n结论: 全部可比表一致 → rc=0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
