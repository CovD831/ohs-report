#!/usr/bin/env python3
"""官方层物质名的"下标位置"判据 (#36) —— 三段: 规则 / 现状 / 变异与边界

要防的事 (2026-10-16 取证):
  PyMuPDF 的 `find_tables().extract()` 按**基线**排序单元格内的 span, 而化学式下标是 6pt、
  基线比 9pt 正文低 1.6pt → 被挤到名字末尾。库里于是出现 '滑石粉尘（游离SiO 含量＜10%） 2'
  这种"字全对、位置错"的名字。它不报乱码, 只会让 `WHERE factor_name=?` 静默查不到
  (限值查表、分桶、CAS、别名链一起断)。

三段各管什么:
  A. **规则段 (合成 span, 不需要 PDF 也不需要库)** —— 下标拼接、上标不算下标、
     digit_bag 挡住 '1-硝基丙烷'/'2-硝基丙烷' 同骨架的歧义、形状正则不误伤合法名。
     任何环境都有牙齿, 所以 B 段退化时它不是"绿而没测"。
  B. **现状段 (真库 + 一手 PDF)** —— 官方层里不得再存在"数字被甩在末尾"的写法。
     检测器 `fossil_find` 与迁移工具 `tools/fix_oel_subscripts.py` 共用同一套判同规则
     (letters_only + digit_bag + 形状), 所以"工具说查无问题"和"本判据绿"是同一件事。
  C. **变异段 (全在内存)** —— 检测器喂化石值必须点名、喂改数字的值不许当同类、合法值不许误伤;
     PDF 不在 → 必须**短路成一条具名错误** (不许报 N 条"查无此名")。

能力边界 (写在这儿, 不藏在绿灯后):
  · 只管**数字位置**这一类。"只差排版空格"的写法 (迁移实测 oel_limit 里 factor_name 32 行 /
    english_name 11 行) 不在射程内 —— 它们去空格后与权威文本相等, 归一化匹配路径不受影响;
    要不要逐字对齐属产品口径, 由 fix 工具打印后单独判。
  · 只管 GBZ 2.1—2019 表1/表2 (PDF 页7-35)。GBZ 2.2—2007 的 76 行来自另一条 OCR 通道
    (`knowledge/gbz22_loader.py`), 表3 生物因素那 5 行是 `knowledge/bei_parser.py` 手写的,
    都不在本权威集合里 —— 由 EXEMPT 登记 + B3 反向对表管着, 不许静默扩大范围。
  · 拆过子条目的行 (conditions 非空, 实测 30 行) 存的是"主名+子名"的拼接产物, 与单元格不同形 →
    排除并打印数量; 它们自己的缺陷 (如 '镍及其无机化合物(按Ni 计) 金属镍与难溶性镍化合'
    + conditions='物') 记在 #37, 不在这里假装看见了。
  · **它看不见什么, 以及由谁补** (2026-10-16 对抗审查提出, 我逐条实测):
      ①把一个名字换成"标准里另一个真名" (错绑) → 本判据绿。补位: 迁移工具的**前提⑤ 同行绑定**
        (要求库里这格的限值出现在标准里目标名字所在行的单元格集合中; 实测喂 '99' 与 '6' 两种
        错绑都被拒), 但它对"两个物质限值恰好相同"的互换仍无感 (实测 煤尘4 / 谷物粉尘4)。
      ②两个物质名字**互换**、③**删行**、④**改值**、⑤english_name 写错、⑥GBZ 2.2 那 76 行改名
        → 本判据全绿。补位: `data_stamp --check` 对副本实测四种破坏 (删行/改值/错绑/改成不存在的名)
        **全部 rc=1 并点名 oel_limit**; 但它是"与 manifest 比漂移", 一旦带着错重打基线就再次绿,
        所以内在正确性仍要靠 #37/#40 的行级取证, 不能只靠它。
      ⑦B4 只校 `hazard_factor_alias.oel_name` 这一条键; `hazard_toxicity.factor_name` 今天就有
        7 个值不在 oel_limit 里 (多是标签/别名, 属 #22 逻辑键登记表的账), 所以这里不立它为零。
  · 跨表扫描用 `SELECT rowid, col`, WITHOUT ROWID 的表会**跳过** —— B2 打印"实际扫到多少值",
    数为 0 就是取景错, 不许把空结果当"全部干净"。

用法: .venv/bin/python tools/test_oel_subscripts.py
⚠ 只在本机跑, **不进 GUARD_TESTS** (边界见 C 段末尾)。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.oel import connect_ro                          # noqa: E402  只读: 本文件在 tools/test_*.py 扫描范围内
from knowledge.pdf_cells import (BASE_DROP, SUB_SIZE_RATIO,   # noqa: E402
                                digit_bag, nospace, spans_to_text)
from tools.fix_oel_subscripts import (GBZ21_PAGES, PDF,       # noqa: E402  判同规则只有产地一份
                                      SOURCE, TAIL_DIGIT, TAIL_ORPHAN,
                                      _letters_only, authority, official_tables)

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got!r}\n      期望: {want!r}")


def mkspans(items):
    """造 span 列表: [(文本, 字号, 基线)]"""
    return [{"text": t, "size": s, "origin": (0.0, b)} for t, s, b in items]


MIN_LETTERS = 2          # 参与判同的字符串至少要有 2 个"字母/汉字"


def build_index(texts):
    """权威文本按 (字母汉字序列, 数字多重集) 建索引 —— 跨表扫描是几万个值, 不能线性扫全集。

    ⚠ 只收 `字母/汉字` 数 ≥ MIN_LETTERS 的单元格: 表格里**限值列/序号列**也是单元格
    ('10'、'0.5'、页码…), 不设这条的话 'industry_class.code'='01' 会撞上权威值 '10'
    (字母序列都为空、数字多重集都是 01) 而变成假违规 —— 本轮实测真踩过这一条。
    """
    idx, auth_ns = {}, set()
    for t in set(texts.values()):
        if len(_letters_only(t)) < MIN_LETTERS:
            continue
        idx.setdefault((_letters_only(t), digit_bag(t)), set()).add(t)
        auth_ns.add(nospace(t))
    return idx, auth_ns


def fossil_find(names, idx, auth_ns):
    """{身份键: 现值} → 违规清单 [(键, 现值, 权威值)]。迁移工具与本判据共用这套判定。"""
    bad = []
    for key, val in names.items():
        if not val or nospace(val) in auth_ns:
            continue                       # 去空格后与权威一致 → 不是"数字甩尾"这类
        same = idx.get((_letters_only(val), digit_bag(val)))
        if same and TAIL_ORPHAN.search(val):
            bad.append((key, val, sorted(same)[0]))
    return bad


# 豁免登记: (表, 列, 值) → "kind: 理由"。必须带 kind 前缀 + 理由, 且**必须仍命中一个真值**
# (B3 反向对表), 否则行删掉/改名之后它会变成永久放行的僵尸条目。
EXEMPT = {
    ("oel_limit", "factor_name", "乙草胺"):
        "amendment: GBZ 2.1—2019 第2号修改单(2025-05-01) 新增序号 359, 原文里没有这一行, "
        "由 knowledge/oel_parser.apply_amendments 硬编码写入; 修改单在数据层没有留痕列 → 记 #38",
}


# ============ A. 规则段 ============
print("### A. 规则段: 下标拼接与判同边界 (合成 span, 不依赖 PDF/库)")
one, ev = spans_to_text(mkspans([("滑石粉尘（游离SiO", 9.0, 466.9), ("2", 6.0, 468.5),
                                 (" 含量＜10%）", 9.0, 466.9)]))
check("A1 下标紧贴拼回 SiO2", one, "滑石粉尘（游离SiO2 含量＜10%）")
check("A1 且登记了下标证据", [(e["text"], e["size"], e["dom_size"]) for e in ev],
      [("2", 6.0, 9.0)])
up, ev2 = spans_to_text(mkspans([("CO", 9.0, 466.9), ("2", 6.0, 460.0)]))
check("A2 基线更高的 2 不当下标 (方向排除, 不是'小字一律算')", ev2, [])
check("A3 两者字母汉字序列相同 (骨架同形)", _letters_only("1-硝基丙烷"), _letters_only("2-硝基丙烷"))
check("A3 数字多重集不同 → 不会被判成同一个名字",
      digit_bag("1-硝基丙烷") == digit_bag("2-硝基丙烷"), False)
check("A4 『空格+末尾数字』是化石形状", bool(TAIL_DIGIT.search("凝聚SiO 粉尘 2")), True)
check("A4 『紧跟右括号的末尾数字』也是 (health_effect 实测变体)",
      bool(TAIL_ORPHAN.search("碲化铋（按Bi 2Te 计）3")), True)
check("A4 合法名 '2-丁酮' 不被误伤", bool(TAIL_ORPHAN.search("2-丁酮")), False)
check("A5 阈值与实测分布留有余量 (正文9.0/下标6.0~4.6, 基线差0.5~1.6)",
      (SUB_SIZE_RATIO, BASE_DROP, round(6.0 / 9.0, 2), round(4.6 / 9.0, 2)), (0.8, 0.4, 0.67, 0.51))

# ============ B. 现状段 ============
print("\n### B. 现状段: 官方层里不得再有『数字被甩在末尾』的写法")
try:
    texts, _ev_map = authority(PDF, GBZ21_PAGES)
except FileNotFoundError as e:
    print(f"  ✗ 一手 PDF 读不到, B/C 段无从进行: {e}")
    print("     设计如此: 读不到来源时短路成**一条**点名, 不许报 N 条『查无此名』。")
    print("     容器里必踩这条 (`.dockerignore` 的 raw) —— 所以本文件只进 LOCAL_TESTS。")
    print(f"\n通过 {n_pass} / 失败 {n_fail}")
    sys.exit(1)

idx, auth_ns = build_index(texts)
auth_texts = set(texts.values())      # 权威单元格文本原样集合 (B5 的前缀对表用它)
conn = connect_ro()
conn.row_factory = __import__("sqlite3").Row      # connect_ro 不设 row_factory, 按列名取值要它
rows = conn.execute("SELECT id, factor_name, english_name, conditions FROM oel_limit "
                    "WHERE source_standard=?", (SOURCE,)).fetchall()
unsplit = {(r["id"], "factor_name"): r["factor_name"] for r in rows if r["conditions"] is None}
unsplit.update({(r["id"], "english_name"): r["english_name"]
                for r in rows if r["conditions"] is None and r["english_name"]})
split_n = sum(1 for r in rows if r["conditions"] is not None)
print(f"  权威单元格 {len(texts)} 个 / {SOURCE} 行 {len(rows)} (拆分行 {split_n} 已排除, "
      f"进入判定的行×列 {len(unsplit)})")
found = fossil_find(unsplit, idx, auth_ns)
viol = [x for x in found if ("oel_limit", x[0][1], x[1]) not in EXEMPT]
check(f"B1 oel_limit 未拆分行里数字甩尾 = 0 (豁免 {len(EXEMPT)} 条)",
      [(x[0], x[1], x[2]) for x in viol][:5], [])

# 阳性对照: 拿本环境里一条**真的**含内联数字的名字, 把那个数字搬到末尾, 检测器必须点名。
# (数出 0 之前先证明这把尺子量得出非 0 —— 见记忆"凡是一个数出 0 的比对先跑阳性对照")
flagged = {x[0] for x in found}
probe = None
for k, v in unsplit.items():
    if k in flagged or not re.search(r"\d", v or ""):
        continue                       # 只对"现在就干净"的名字做变异, 否则分不清是谁的红
    i = max((m.start() for m in re.finditer(r"\d", v)), default=None)
    fossil = v[:i] + v[i + 1:].rstrip() + " " + v[i]   # 把最后一个数字搬到末尾 (字与数字都不变)
    if fossil_find({k: fossil}, idx, auth_ns):
        probe = (v, fossil)
        break
check("B1 阳性对照: 真名的内联数字搬到末尾后必须被检测器点名", probe is not None, True)

# B2 跨表 (官方层其余表)
cross = {}
scanned_cols = 0
for tab in official_tables():
    if tab == "oel_limit":
        continue
    try:
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({tab})")]
    except Exception:
        continue
    for col in cols:
        try:
            vals = conn.execute(f"SELECT rowid, {col} FROM {tab}").fetchall()
        except Exception:
            continue                       # WITHOUT ROWID: 跳过, 由下面的"扫到多少值"自报兜住
        scanned_cols += 1
        for rid, v in vals:
            if isinstance(v, str) and v:
                cross[(tab, col, rid)] = v
cross_bad = [x for x in fossil_find(cross, idx, auth_ns)
             if (x[0][0], x[0][1], x[1]) not in EXEMPT]
print(f"  跨表扫到 {len(cross)} 个文本值 ({scanned_cols} 列 × {len(official_tables())-1} 张表)")
check("B2 官方层其余表的文本列里数字甩尾 = 0",
      [(x[0], x[1], x[2]) for x in cross_bad][:5], [])
check("B2 取景非空 (扫到的值 > 1000, 否则这条绿灯是取景错)", scanned_cols > 0 and len(cross) > 1000, True)

# B4 引用完整性 (对抗审查逼出来的尺子): `hazard_factor_alias.oel_name` 存的也是 oel_limit 的
#   因子名, 它是 `project_assess` 解析物料→因子的键。改 oel_limit 的名字而不同步这张表,
#   别名链会在**下一次改名时**静默断裂 (实测: 今天 0 破; 只改 14 个中文名不同步 → 破 1;
#   若把 22 个"只差空格"的名字也改掉 → 破 10)。所以这条不是装饰, 是改名批次的闸门。
alias_bad = [a for (a,) in conn.execute("SELECT DISTINCT oel_name FROM hazard_factor_alias")
             if a and a not in {v for v in unsplit.values() if isinstance(v, str)}
             and a not in {r["factor_name"] for r in rows}]
check("B4 hazard_factor_alias.oel_name 每个值都能精确命中 oel_limit.factor_name",
      alias_bad[:5], [])

# B5 拆分行 (#37 的账) 不许变成"看不见": 排除多少、其中多少本身就对不上标准, 必须打印并封顶。
#   对表口径 = "主名是某个权威单元格文本的前缀" (拆分行存的是 主名, 子名进了 conditions),
#   前缀式对不上才是真缺陷 (实测两条 '镍及其无机化合物(按Ni 计) 金属镍与难溶性镍化合' 就是这种)。
split_rows = conn.execute(
    "SELECT id, factor_name FROM oel_limit WHERE source_standard=? AND conditions IS NOT NULL",
    (SOURCE,)).fetchall()
exact, prefix_only, none_hit = [], [], []
for rid, n in split_rows:
    if nospace(n) in auth_ns:
        exact.append(rid)
        continue
    pre = [t for t in auth_texts if nospace(t).startswith(nospace(n))]
    (prefix_only if pre else none_hit).append((rid, n, pre[0] if pre else None))
print(f"  拆分行 {len(split_rows)} 条的处置账: 整格精确对上 {len(exact)} / "
      f"只能前缀对上 {len(prefix_only)} / 完全对不上 {len(none_hit)}  (复算: 本文件 B5)")
for rid, n, t in prefix_only[:3]:
    print(f"      · 前半截 id={rid} 库={n!r}\n        标准整格={str(t)[:70]!r}")
# 这张账为什么这么记 (复验过, 两个口径都量过):
#   · 拆分行存的是**主名**, 而标准那个单元格是"主名+各子名"连起来的 → 逐字相等只有 1 条,
#     "整格对不上"绝大多数是取行方式造成的, 不是数据错 (我一度按逐字相等数出 7 条, 那是尺子错)。
#   · 但"只能前缀对上"里确实混着**截断类真缺陷** (实测 2 条: factor_name 尾巴停在
#     '…难溶性镍化合', 而 '物' 被挤进了 conditions) —— 前缀式判据对它无感, 那条路归 #37。
#   所以这里只断言一件事: **本批没让任何拆分行从"能对上"退化成"完全对不上"**;
#   三类计数打印出来交人看, 不拿"29"这种本地数当判据 (生产库拆分行数量不同, 会假红)。
check("B5 完全对不上的拆分行 = 0 (有的话是本批制造的新失配)", [x[1] for x in none_hit][:4], [])
check("B5 三类计数之和 = 拆分行总数 (账要闭合, 不许有行没被归类)",
      len(exact) + len(prefix_only) + len(none_hit), len(split_rows))

# B3 反向对表: 豁免必须仍命中真值
live = {("oel_limit", col, val) for (_rid, col), val in unsplit.items()}
dead = [k for k in EXEMPT if k not in live]
check(f"B3 豁免 {len(EXEMPT)} 条全部仍命中真值 (无僵尸登记)", dead, [])
check("B3 每条豁免都带 kind 前缀 + 够长的理由",
      all(len(v) > 30 and re.match(r"^[a-z][a-z\-]*: ", v) for v in EXEMPT.values()), True)
conn.close()

# ============ C. 变异段 (全在内存) ============
print("\n### C. 变异段: 判据必须能红")
fake_texts = {nospace(k): k for k in ("滑石粉尘（游离SiO2 含量＜10%）", "沉淀SiO2（白炭黑）")}
fidx, fans = build_index(fake_texts)
check("C1 变异: 正确名字改回化石写法 → 必须点名",
      fossil_find({"k": "滑石粉尘（游离SiO 含量＜10%） 2"}, fidx, fans),
      [("k", "滑石粉尘（游离SiO 含量＜10%） 2", "滑石粉尘（游离SiO2 含量＜10%）")])
check("C2 变异: 数字被换成**别的数字** (2→3) 不按同类处理 (那是另一类失效)",
      fossil_find({"k": "滑石粉尘（游离SiO 含量＜10%） 3"}, fidx, fans), [])
check("C3 对照: 合法写法不报", fossil_find({"k": "滑石粉尘（游离SiO2 含量＜10%）"}, fidx, fans), [])
check("C4 对照: 合法名 '2-丁酮' 不被误伤", fossil_find({"k": "2-丁酮"}, fidx, fans), [])
check("C5 对照: 空值/None 不炸", fossil_find({"a": "", "b": None}, fidx, fans), [])

print("\n边界复述: B/C 段的权威集合来自 acquire/raw 的标准 PDF, 而它被 .gitignore 与 "
      ".dockerignore 的 `raw` 挡在仓库与镜像之外 (复算: git check-ignore -v "
      "acquire/raw/nhc_gbz/pdfs/GBZ_2.1_2019.pdf) → 本文件进 LOCAL_TESTS, 不进 GUARD_TESTS。")
print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
