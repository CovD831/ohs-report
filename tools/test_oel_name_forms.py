#!/usr/bin/env python3
"""#37 结构判据: 官方层的物质名必须是标准表格里"一个条目", 不许粘连/截断/括号不配平

测的三条尺子 (2026-10-17 取证, 全部从一手 PDF + 库里数据推, 不写物质名白名单):

  R1 `oel_limit.factor_name` 这一格的文本, 必须能在权威名单元格里对应到**一个完整条目**
     (前后是空格或格边; 空格差异不算差异 —— #40 已判"只差排版空格"的那族不改)。
     #37 的两个产物都在这儿红:
        '镍及其无机化合物(按Ni 计) 金属镍与难溶性镍化合'  ← 主名+子条目粘连, 且尾巴是半截
        '人造矿物纤维绝热棉粉尘（玻'                      ← 名单元格被截断
     而库里既有的同族写法必须绿 (钒/钼/铅/铜/铝尘/麻尘/石棉: 主名独立成条目, 子名进 conditions)。
  R2 `oel_limit.conditions` 非空且不是破折号时, 同样必须是权威里的一个条目 ——
     这条抓住 '物' (子条目被空格劈开后掉进 conditions 的残词) 和 1631 那格的 factor_name 复读。
  R3 名字/条件列的括号必须配平: '（'与'）' 数量相等, '('与')' 数量相等 (全角半角分别对表)。
     R3 **不需要 PDF** → 容器里也能跑; R1/R2 需要一手 PDF (`acquire/raw` 被 .dockerignore 的
     raw 挡在镜像外), 取不到时本测试**显式跳过并点名**, 不静默算绿。

判据的管辖边界 (写进文件, 不许只报绿灯)
  · 辖区 = `source_standard == SOURCE` (GBZ 2.1—2019) 的 562 行。GBZ 2.2—2007 的物理因素行
    (噪声/高温/微波辐射…实测 76 行) 不在本机这份 PDF 的表1 页段里 → R1/R2 不判它们, R3 判全表。
  · 唯一豁免 EXEMPT: rid=1594 '乙草胺' —— 出自 GBZ 2.1—2019 的**修改单** (#38 登记),
    修改单那行不在本机这份 2019 正文 PDF 里, 所以尺子看不见它。豁免做**双向对表**:
    登记的每一项必须仍然红 (僵尸豁免要点名), 任何未登记的红旗直接判失败。
  · 第 4 节"旧化石不得再现"只做**逐字相等**, 不做子串: 子串数会假红 ——
    '璃棉、矿渣棉、岩棉）' 是修复后正确全名 '…（玻璃棉、矿渣棉、岩棉）' 的子串。
    这条口径是本轮复算逼出来的 (上一版就是拿子串数把 68 份成品数出"残词 25 次")。

用法:  .venv/bin/python tools/test_oel_name_forms.py   (只读; 变异打在内存行副本上, 不碰库)
"""
import os
import json
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.paths import DB_PATH                                    # noqa: E402
from tools.fix_oel_name_forms import PDF, REPAIRS, new_value, entry_hit  # noqa: E402  尺子单一产地
from tools.fix_oel_subscripts import SOURCE, authority, GBZ21_PAGES     # noqa: E402

EXEMPT = {(1594, "factor_name")}      # 乙草胺: 名字出自修改单, 本机 PDF 表1 正文没有它 (#38)
N_SCOPE = 562                         # 2026-10-17 实测辖区行数
N_ROWS = 638
N_HEALTH = 357

n_pass = n_fail = n_skip = 0


def check(label, got, want, note=""):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      期望 {want!r} 实得 {got!r}  {note}")


def skip(label, why):
    global n_skip
    n_skip += 1
    print(f"  – {label}\n     ⓘ {why}")


def unbalanced(v):
    s = v or ""
    return (s.count("（") - s.count("）")) != 0 or (s.count("(") - s.count(")")) != 0


def run_r1_r2(rows, cells, exempt):
    """→ (未登记的红, 判过的格子数)。rows 是行副本, 变异只打在副本上"""
    fails, judged = [], 0
    for row in rows:
        if row["source"] != SOURCE:
            continue
        for col in ("factor_name", "conditions"):
            v = (row[col] or "").strip()
            if col == "conditions" and (not v or v in ("—", "―")):
                continue
            judged += 1
            if not entry_hit(v, cells):
                fails.append((row["rowid"], col, v))
    unregistered = [f for f in fails if (f[0], f[1]) not in exempt]
    return unregistered, judged


def run_r3(rows):
    return [(r["rowid"], col, r[col]) for r in rows
            for col in ("factor_name", "conditions") if unbalanced(r[col])]


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = [{"rowid": rid, "factor_name": fn or "", "conditions": cd or "",
             "note": nt or "", "source": src or ""}
            for rid, fn, cd, nt, src in conn.execute(
                "SELECT rowid, factor_name, conditions, note, source_standard FROM oel_limit")]

    print("=== 1. 数据前提 (先证明尺子真的取到了数) ===")
    check(f"oel_limit 行数 = {N_ROWS} (实得 {len(rows)})", len(rows), N_ROWS)
    n_scope = sum(1 for r in rows if r["source"] == SOURCE)
    check(f"辖区 {SOURCE} 行数 = {N_SCOPE} (实得 {n_scope})", n_scope, N_SCOPE,
          "对不上 = 辖区口径或数据变了, 后面的数字都不能引")
    check(f"辖区外行数 > 0 (实测有 GBZ 2.2 那族, 共 {len(rows) - n_scope} 行)",
          (len(rows) - n_scope) > 0, True)

    have_pdf = os.path.exists(PDF)
    cells = []
    if have_pdf:
        texts, _ev = authority(PDF, GBZ21_PAGES)
        cells = list(dict.fromkeys(texts.values()))
        print(f"\n=== 2. R1/R2 权威条目尺子 (PDF {len(cells)} 个单元格, "
              f"页段 {GBZ21_PAGES.start + 1}-{GBZ21_PAGES.stop}) ===")
        check("权威单元格数 ≥ 1000", len(cells) >= 1000, True)
        reds, judged = run_r1_r2(rows, cells, EXEMPT)
        check(f"判过的格子数 > 570 (实得 {judged})", judged > 570, True)
        check(f"R1/R2 未登记的红 = 0 条 (实得 {len(reds)}: {reds[:3]})", reds, [])
        still, _j = run_r1_r2(rows, cells, set())
        zombie = [k for k in EXEMPT if not any(rid == k[0] and col == k[1]
                                               for rid, col, _v in still)]
        check(f"豁免双向对表: 登记的 {len(EXEMPT)} 项必须仍然红 (实得僵尸 {zombie})", zombie, [])
    else:
        print("\n=== 2. R1/R2 权威条目尺子 ===")
        skip("R1/R2 未跑", f"一手 PDF 不在 {PDF} (镜像里没有 raw/) —— 这条**不算绿**; "
                           "要跑它就必须在带 OHS_GBZ21_PDF 的机器上跑")

    print("\n=== 3. R3 括号配平 (不需要 PDF, 容器里同样有效) ===")
    r3 = run_r3(rows)
    check(f"R3 不配平的格子 = 0 条 (实得 {len(r3)}: {r3[:3]})", r3, [])
    paired = sum(1 for r in rows if "（" in r["factor_name"] and "）" in r["factor_name"])
    check(f"合法对照: 库里带成对全角括号的名字 > 100 个 (实得 {paired})", paired > 100, True)

    print("\n=== 4. 修复的旧格值在 26 张官方表里不得再现 (旧串从修复表派生, 不手抄) ===")
    fossils = sorted({old for _t, _c, _r, old, src, _w in REPAIRS
                      if old and new_value(src) != old})
    tables = sorted(json.load(open(os.path.join(ROOT, "data_stamp.json"),
                                   encoding="utf-8"))["tables"])
    check(f"官方表清单 = 26 张 (实得 {len(tables)})", len(tables), 26)
    non_idem = [old for _t, _c, _r, old, src, _w in REPAIRS if old and new_value(src) != old]
    check(f"待查旧串覆盖全部非幂等规则 (非幂等 {len(non_idem)} 条 → 去重 {len(fossils)} 串)",
          all(o in fossils for o in non_idem), True)
    check(f"待查旧串 ≥ 4 个不同串 (实得 {len(fossils)})", len(fossils) >= 4, True)
    hits, cols_seen = [], 0
    for t in tables:
        for col in [x[1] for x in conn.execute(f'PRAGMA table_info("{t}")')]:
            vals = conn.execute(f'SELECT rowid, "{col}" FROM "{t}"').fetchall()
            cols_seen += 1
            for rid, val in vals:
                if isinstance(val, str) and val in fossils:
                    hits.append((t, col, rid, val[:24]))
    check(f"扫到的文本列数 > 200 (实得 {cols_seen} 列)", cols_seen > 200, True)
    check(f"旧格值逐字再现 = 0 次 (实得 {len(hits)}: {hits[:3]})", hits, [])

    print("\n=== 5. 变异: 每条尺子必须**稳定地能红**, 阳性对照不许误伤 ===")
    base = lambda: [dict(r) for r in rows]                      # noqa: E731  每轮从真行重做副本
    def mutate(rs, rid, col, val):
        for r in rs:
            if r["rowid"] == rid:
                r[col] = val
        return rs
    if have_pdf:
        e = base()
        mutate(e, 1631, "factor_name", "人造矿物纤维绝热棉粉尘（玻")
        got, _j = run_r1_r2(e, cells, EXEMPT)
        check("M1 把 1631 的名字改回截断形态 → R1 必须点名",
              any(x[0] == 1631 and x[1] == "factor_name" for x in got), True)
        e = mutate(base(), 1399, "conditions", "物")
        got, _j = run_r1_r2(e, cells, EXEMPT)
        check("M2 把 1399 的 conditions 改回残词 '物' → R2 必须点名",
              any(x[0] == 1399 and x[1] == "conditions" for x in got), True)
        e = mutate(base(), 1399, "factor_name", "镍及其无机化合物(按Ni 计) ")
        got, _j = run_r1_r2(e, cells, EXEMPT)
        check("M3 阳性对照: 同一格只差排版空格 → **不许**红 (#40 的口径)",
              any(x[0] == 1399 for x in got), False)
        e = mutate(base(), 1594, "factor_name", "丙酮")
        zombie = [k for k in EXEMPT if not any(rid == k[0] and col == k[1] for rid, col, _v in
                                               run_r1_r2(e, cells, set())[0])]
        check("M4 破坏豁免项 (1594 换成合法名) → 僵尸豁免必须被点名",
              zombie, [(1594, "factor_name")])
    else:
        skip("M1–M4 未跑", "没有 PDF 时 R1/R2 的变异无从谈起; R3 的变异在下一节单独跑")
    e = mutate(base(), 1404, "factor_name", "铅及其无机化合物（按 Pb 计")
    check("M5 给一个合法名字删掉右括号 → R3 必须点名 1404",
          any(x[0] == 1404 for x in run_r3(e)), True)
    e = mutate(base(), 1404, "factor_name", "铅及其无机化合物(按 Pb 计)")
    check("M6 阳性对照: 全角换成半角的**成对**括号 → R3 不许误伤",
          any(x[0] == 1404 for x in run_r3(e)), False)
    e = base()
    e.append({"rowid": 999999, "factor_name": "双（巯基乙酸）二辛基锡", "conditions": "",
              "note": "", "source": SOURCE})
    check("M7 合法对照: 嵌套成对括号的名字不被 R3 误伤",
          any(x[0] == 999999 for x in run_r3(e)), False)

    conn.close()
    print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 / {n_skip} 跳过 ===")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
