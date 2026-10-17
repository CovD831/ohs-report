"""把官方层里"下标被甩到名字末尾"的物质名改回标准自己的排版 —— 预演/应用/导出 SQL 三模式

缺陷与根因 (取证全文见 knowledge/pdf_cells.py 的 docstring):
  库里  '滑石粉尘（游离SiO 含量＜10%） 2'   标准  '滑石粉尘（游离SiO2 含量＜10%）'
  库里  '锰及其无机化合物（按 MnO 计） 2'   标准  '锰及其无机化合物（按MnO2 计）'
  库里  '碲化铋（按Bi 2Te 计） 3'          标准  '碲化铋（按Bi2Te3 计）'
  字都没读错, 错在**位置**: `find_tables().extract()` 按基线排序, 6pt 的下标基线比正文低 1.6pt,
  就被挤到单元格文本末尾。后果是 `WHERE factor_name=?` 一律查不到 —— 它不报乱码, 只会静默失配。

纪律 (每条为什么在那儿):
  - 权威值**只从一手 PDF 取** (GBZ 2.1—2019 表1/表2), 不手写对照表;
    "某个数字是下标"用的是字号/基线几何 (见 pdf_cells.SUB_SIZE_RATIO / BASE_DROP)。
  - oel_limit 那一轮四条前提同时满足才改, 任一不满足 → 不改并点名 (宁可留化石, 不许猜):
      ① 汉字/字母序列完全相同 (去掉所有数字与空白后相等);
      ② 数字**内容**相同 (digit_bag 相等) —— 只许挪位置, 不许改数值;
        (这条是被反例逼出来的: 用"去数字骨架"当身份会把 '1-硝基丙烷' 与 '2-硝基丙烷' 判成同名)
      ③ 目标文本在 PDF 里确有下标证据; ④ 命中唯一 (多命中交人工)。
  - **跨表同步**: `hazard_factor_alias.oel_name` / `material_dictionary.oel_factor` /
    `health_effect.factor` 这些列存的也是同一批物质名 (实测各有 1~2 行是旧化石或其变体),
    只改 oel_limit 会把别名链/健康影响表读断 —— 所以同一份 canon 也打在官方层其余表上
    (范围 = data_stamp.json 清单里那 26 张; `project`/`users` 等客户数据一律不扫不动)。
  - 本批**不动**"只差排版空格"的写法 (oel_limit 里 35 行 + 别表若干): 它们去空格后与权威文本相等,
    不影响任何做归一化的匹配路径; 改掉反而可能让"现在恰好逐字能命中"的项目侧名字失配。
  - 预演走只读连接; `--apply` 才写; 写完逐行读回对表 + "旧化石串在官方层任何列里都不得再现"；
    行数不得变, 不一致 rc=1 并打印回滚语句。
  - 一手 PDF 被 .gitignore/.dockerignore 的 `raw` 挡在仓库和镜像外 (实测 `git check-ignore -v`),
    生产上跑不了本工具 → `--emit-sql` 把工具算出来的 UPDATE 落成仓库里的生成物,
    生产执行那份**生成物** (字节进 MD5_FILES), 而不是任何人手写的 SQL。

用法:
  .venv/bin/python tools/fix_oel_subscripts.py                       # 预演 (只读)
  .venv/bin/python tools/fix_oel_subscripts.py --apply               # 写本地库 + 读回对表
  .venv/bin/python tools/fix_oel_subscripts.py --emit-sql tools/golden/oel_subscript_fix.sql
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from knowledge.oel import connect, connect_ro        # noqa: E402
from knowledge.pdf_cells import (authority, digit_bag, nospace,  # noqa: E402
                                row_map, value_in_row)

# 一手 PDF 的位置。默认在开发机的 acquire/raw (那里被 .gitignore/.dockerignore 的 `raw`
# 挡着, 既不进公开仓库也不进镜像 —— 标准原文有版权, 这是刻意的)。
# 生产上要做这次迁移时用 OHS_GBZ21_PDF 指过去: 部署窗口里把这份 2MB PDF scp 到服务器的
# data/ 下 (不进镜像), 跑完迁移再删掉。复算路径与删除要求写在进生产清单。
PDF = os.environ.get("OHS_GBZ21_PDF") or os.path.join(
    ROOT, "acquire", "raw", "nhc_gbz", "pdfs", "GBZ_2.1_2019.pdf")
GBZ21_PAGES = range(6, 35)          # 表1 页7-31 + 表2 页32-35 (解析器用的就是这两段)
SOURCE = "GBZ 2.1—2019"
TAIL_DIGIT = re.compile(r"\s\d{1,2}\s*$")           # 『末尾孤立数字』(空格分隔, oel_limit 实测形状)
TAIL_ORPHAN = re.compile(r"(?:^|[\s）)\]])\d{1,2}$")  # 更宽: 紧跟右括号的孤立数字 (别表的变体形状)


def _letters_only(s):
    """去掉所有数字与空白后的"字母/汉字序列" —— 差别**只许是数字位置**时用这条判"""
    return re.sub(r"\d", "", nospace(s))


def proposals(texts, ev, rows, col, rmap=None):
    """oel_limit 的行 × 某一列 → (可改清单, 交人工清单, 只差空格的行数)"""
    can, hold, spacing, exact = [], [], 0, 0
    for r in rows:
        db = r[col]
        if not db:
            continue
        if nospace(db) in texts:           # 去空格后已等于权威文本 → 本批不动, 但要计数报出来
            if db == texts[nospace(db)]:
                exact += 1                 # 逐字就一致
            else:
                spacing += 1               # 只差排版空格
            continue
        cand = sorted({t for t in texts.values()
                       if _letters_only(t) == _letters_only(db)
                       and digit_bag(t) == digit_bag(db)
                       and nospace(t) != nospace(db)})
        if not cand:
            hold.append((r["id"], db, "PDF 里找不到同字同数的单元格文本 (不猜)"))
            continue
        if len(cand) > 1:
            hold.append((r["id"], db, f"多命中, 交人工: {cand}"))
            continue
        t = cand[0]
        if not ev.get(t):
            hold.append((r["id"], db, f"目标文本 {t!r} 在 PDF 里没有下标几何证据, 不动"))
            continue
        if not TAIL_DIGIT.search(db):
            hold.append((r["id"], db, f"现值 {db!r} 不是『末尾孤立数字』形状, 不在本工具适用范围内"))
            continue
        # 前提⑤ **同行绑定**: 库里这一格的限值必须出现在标准里"目标名字所在行"的单元格集合里。
        # 挡的是错绑 —— 把 '煤尘(限值4)' 改成另一个真实存在的名字 '谷物粉尘…(限值6)',
        # 形状与存在性两条判据都会绿, 而数值已经错了 (对抗审查提出的构造, 这里用排版关系堵掉)。
        if rmap is not None and col == "factor_name":
            if not value_in_row(r["value"], rmap.get(nospace(t), ())):
                hold.append((r["id"], db, f"目标名 {t!r} 在标准同一行里找不到本行的限值 "
                                          f"{r['oel_type']}={r['value']} —— 疑似错绑, 不动"))
                continue
        can.append({"table": "oel_limit", "col": col, "rid": r["id"],
                    "factor": r["factor_name"], "old": db, "new": t,
                    "evidence": ev[t][:2], "why": "下标被 extract() 按基线挤到末尾"})
    return can, hold, spacing, exact


def official_tables():
    """官方层的范围 = 版本戳清单里那 26 张 (从 data_stamp.json 读, 不抄第二份名单)"""
    return sorted(json.loads(open(os.path.join(ROOT, "data_stamp.json"),
                                  encoding="utf-8").read())["tables"])


def cross_table(conn, canon):
    """把同一批 canon 打到官方层其余表的文本列上 → (要同步改的, 只差空格只点名的)"""
    news = set(canon.values())
    must, spacing = [], []
    for tab in official_tables():
        if tab == "oel_limit":
            continue
        try:
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info({tab})")]
            conn.execute(f"SELECT count(*) FROM {tab}")
        except sqlite3.Error:
            continue
        for col in cols:
            try:
                vals = conn.execute(f"SELECT rowid, {col} FROM {tab}").fetchall()
            except sqlite3.Error:
                continue                       # WITHOUT ROWID 之类: 该列测不到就点名不了, 见 docstring 边界
            for rid, v in vals:
                if not isinstance(v, str) or not v:
                    continue
                if v in canon:
                    must.append({"table": tab, "col": col, "rid": rid, "old": v,
                                 "new": canon[v], "why": "逐字旧化石名 (引用没跟着走)"})
                    continue
                if v in news:
                    continue
                same = sorted({n for n in news
                               if _letters_only(n) == _letters_only(v)
                               and digit_bag(n) == digit_bag(v)})
                if not same:
                    continue
                if nospace(v) in {nospace(n) for n in same}:
                    # 只差排版空格 → 不是本缺陷的形态, 不动; 但必须**报出来**,
                    # 否则"我们改了一部分、留了一部分"这件事就只存在于我的注释里。
                    spacing.append((tab, col, rid, v, same[0]))
                    continue
                if TAIL_ORPHAN.search(v):
                    must.append({"table": tab, "col": col, "rid": rid, "old": v,
                                 "new": same[0], "why": "同字同数, 数字仍被甩在末尾"})
    return must, spacing


def emit_sql(path, must, note):
    lines = ["-- 由 tools/fix_oel_subscripts.py --emit-sql 生成, 不要手改也不要手加条目。",
             f"-- 来源: {os.path.relpath(PDF)} (GBZ 2.1—2019 表1/表2, span 顺序 + 下标字号/基线证据)",
             "-- 生产容器里没有这份 PDF (.dockerignore 第 5 行的 raw), 所以生产执行的是这份**生成物**;",
             "--   复算路径 = 在有 PDF 的开发机上重跑同一条 --emit-sql, 与本文件逐字节比对。",
             note, "BEGIN;"]
    for p in sorted(must, key=lambda x: (x["table"], x["col"], x["rid"])):
        esc = p["new"].replace("'", "''")
        lines.append(f"UPDATE {p['table']} SET {p['col']}='{esc}' "
                     f"WHERE rowid={p['rid']};  -- 旧: {p['old']} ({p['why']})")
    lines += ["COMMIT;",
              "-- 校验: 下面这条应返回 0 行 (官方层限值表里不再有『空格+数字』结尾的化石名)",
              "SELECT rowid, factor_name FROM oel_limit",
              " WHERE source_standard='GBZ 2.1—2019'",
              "   AND (factor_name GLOB '* [0-9]' OR factor_name GLOB '* [0-9][0-9]');"]
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"\n已导出 SQL 生成物 → {os.path.relpath(path, ROOT)} ({len(lines)} 行)")


def main() -> int:
    apply = "--apply" in sys.argv
    emit = None
    if "--emit-sql" in sys.argv:
        emit = sys.argv[sys.argv.index("--emit-sql") + 1]

    texts, ev = authority(PDF, GBZ21_PAGES)
    rmap = row_map(PDF, GBZ21_PAGES)
    print(f"权威单元格文本: {len(texts)} 个 / 同行绑定表 {len(rmap)} 个名字 "
          f"(源: {os.path.relpath(PDF)} 页7-35)")

    conn = connect() if apply else connect_ro()     # 预演 = 只读, 不建表、不给验收库开可写连接
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, factor_name, english_name, value, oel_type FROM oel_limit "
                        "WHERE source_standard=?", (SOURCE,)).fetchall()
    print(f"oel_limit 里 {SOURCE} 的行: {len(rows)}")

    all_can, all_hold, spacing_cnt, exact_cnt = [], [], {}, {}
    for col in ("factor_name", "english_name"):
        can, hold, sp, ex = proposals(texts, ev, rows, col, rmap)
        all_can += can
        all_hold += hold
        spacing_cnt[col] = sp
        exact_cnt[col] = ex

    # canon 收**两列**的映射: 别的表里也存英文名 (实测 health_effect.english 有一条
    # 'Bismuth telluride, as Bi Te2 3' 的同类化石), 只带中文名走就是漏改 —— 这条是守卫 B2 抓出来的。
    canon = {p["old"]: p["new"] for p in all_can}
    must_x, spacing_x = cross_table(conn, canon)

    print(f"\n── oel_limit: 可改 {len(all_can)} 处 (行×列), 交人工 {len(all_hold)} 处, "
          f"只差排版空格不动的 {spacing_cnt} 行, 逐字一致 {exact_cnt} 行 ──")
    for p in sorted(all_can, key=lambda x: (x["col"], x["old"])):
        print(f"  [{p['col']:<12}] rowid={p['rid']:<5} {p['old']!r}\n"
              f"       → {p['new']!r}  下标证据={[(e['text'], e['size'], e['dom_size'], e['origin'], e['dom_origin']) for e in p['evidence']]}")
    for i, db, why in all_hold:
        print(f"  [交人工] id={i} 现值={db!r} —— {why}")

    print(f"\n── 官方层其余表 (范围 = data_stamp 清单 {len(official_tables())} 张, 客户数据表不扫) ──")
    print(f"  必须同步改 {len(must_x)} 处:")
    for p in must_x:
        print(f"    {p['table']}.{p['col']} rowid={p['rid']}: {p['old']!r} → {p['new']!r}  ({p['why']})")
    print(f"  只差空格 (本批不动, 只点名) {len(spacing_x)} 处:")
    for tab, col, rid, v, n in spacing_x[:12]:
        print(f"    {tab}.{col} rowid={rid}: {v!r}  (权威={n!r})")
    if len(spacing_x) > 12:
        print(f"    … 共 {len(spacing_x)} 处")

    must = all_can + must_x
    if emit:
        emit_sql(os.path.join(ROOT, emit) if not os.path.isabs(emit) else emit, must,
                 f"-- 本次: oel_limit {len(all_can)} 处 + 跨表 {len(must_x)} 处")

    if not apply:
        print("\n(预演模式 — 未写库; 加 --apply 才写入)")
        conn.close()
        return 0

    n_before = conn.execute("SELECT count(*) FROM oel_limit").fetchone()[0]
    for p in must:
        conn.execute(f"UPDATE {p['table']} SET {p['col']}=? WHERE rowid=?", (p["new"], p["rid"]))
    conn.commit()

    bad = []
    if conn.execute("SELECT count(*) FROM oel_limit").fetchone()[0] != n_before:
        bad.append("oel_limit 行数变了")
    for p in must:
        got = conn.execute(f"SELECT {p['col']} FROM {p['table']} WHERE rowid=?",
                           (p["rid"],)).fetchone()[0]
        if got != p["new"]:
            bad.append(f"{p['table']}.{p['col']} rowid={p['rid']} 读回={got!r} 期望={p['new']!r}")
    still = [r[0] for r in conn.execute(
        "SELECT factor_name FROM oel_limit WHERE source_standard=?", (SOURCE,))
        if TAIL_DIGIT.search(r[0] or "")]
    if still:
        bad.append(f"oel_limit 仍有『末尾孤立数字』: {still}")
    residue = [(p["old"],) for p in must
               if conn.execute(f"SELECT count(*) FROM {p['table']} WHERE {p['col']}=?",
                               (p["old"],)).fetchone()[0]]
    if residue:
        bad.append(f"旧化石串仍出现在官方层: {residue}")
    if bad:
        print("\n❌ 读回对表不一致:")
        for b in bad:
            print("   ", b)
        print("   回滚 (逐条反向):")
        for p in must[:3]:
            print(f"     UPDATE {p['table']} SET {p['col']}='{p['old']}' WHERE rowid={p['rid']};")
        conn.close()
        return 1
    print(f"\n✅ 已写 {len(must)} 处 (oel_limit {len(all_can)} + 跨表 {len(must_x)}) 并逐行读回核对")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
