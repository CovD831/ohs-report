#!/usr/bin/env python3
"""标准「代替关系」一致性判据 —— #45 双向对表 + #42 身份键/现行性遍历断言 (只读点名工具)。

⚠ 这是点名，不是迁移: 全程 `knowledge.oel.connect_ro()` (只读连接), 不 UPDATE、
   不灌结构化字段、不改 state。跑一百遍 standard_db 的字节都不会变。
   清单非空 ≠ 要有人去写库; 清单是**下一批迁移的输入**, 迁移另需授权。

为什么和 tools/test_std_supersede.py 不重叠:
   那支钉的是**单点回归** (GBZ230-2010 那一例: state 漏标但 replace_of 已写 → 双判据必须覆盖),
   号是写死在文件里的。本支做**全库遍历**: 每一行都过一遍表, 不预设哪个号有问题,
   也不靠"某个已知号"当证据。

五段判据 (A/B 属 #42, C/D/D' 属 #45):
  A. 身份键形态不变式 (硬断言) —— 同一行 code 的各种书写形态 (带斜杠/无斜杠/空格/
     全角破折号/大小写) 必须归一到同一个 std_key; 另给「非规范键行」点名栏 (A2)。
     规则**从形态派生** (族根 + 可选推荐性标记 + 编号 + 年份), 没有逐号同义词表。
     实测缺陷 (2026-10-16): 库里 54 行 code 是无斜杠形态 (GBZT189.9-2025 / WST368-2025 /
     GBT31716-2015 / GBZT300.*-2018 …), 旧 _base_code 只做 `.replace("GBZ/T","GBZ")`
     → 无斜杠形态永远配不上同一个键 → resolve_current('GBZ/T 189.9—2007') 把 2007 那行
     **自己当现行版**返回 (2025 版就在库里, name 明写「代替 GBZ/T 189.9—2007」)。
  B. 现行性遍历 (硬断言 B1 + 点名栏 B2) ——
     B1 按身份键分组: 若库里同键存在更晚且未废止的行, resolve_current(该行 code) 必须给出更晚那行。
     B2 按**库里自己的声明**分组 (replace_of ∪ name 主动语态), 这条与键函数无关 →
        它才是能抓到"键坏"的那只牙 (变异用例 M2)。
  C. 正向对表 —— replace_of 写了旧号 → 旧行必须在库里且 state='废止'; 不是就点名
     (两侧 state + 出处 source/url/effdate 一起给, 复核的人一眼能对上账)。
  D. 反向对表 —— name 写了「（代替 X）」而 replace_of 为空 → 点名 X (字段没接住 name 的话)。
  D'.方向对照 —— name 写「被 X 代替」→ 本行才是旧行 → 本行 state 必须是废止 (与 D 不混向)。

能力边界 (输出里也打印一遍, 免得下一轮误读这些数字):
  · 一手目录快照 acquire/raw/nhc_gbz/gbz_baseline.json 的 `status` 字段 988 条**全是"现行"**
    (实测 Counter) → 它只能判「号存在」, 完全不能判现行性。本工具 A/B/C 段的现行性口径
    只有 standard_db 的 state + replace_of (+ name), 不拿快照当现行证据。
  · name 是自由文本, 解析不可靠: 实测 592 行提到「代替」, 其中 181 行只是「（已被代替）」这类
    **不含号**的状态文本, 另有 60 行是「被 X 代替」的被动语态。所以 D 段**只判「号写了却没进
    字段」这一类**, 不判「该代替却没写在 name 里」那一类 —— 后者是一手目录的账, 本判据问不出来。
  · effdate 有脏值 (实测 612 行不是 `YYYY-MM-DD` 形态: `2020年11月1日` / `2008.09.01` /
    `2019 - 07 - 01` / `2013=08-01` / 8 位纯数字 …) → 「其实还没生效的旧版不该判废止」这类
    需要**日期归一**的结论, 本批**只点名规模、不下结论** (F 段, 不参与红绿)。

用法:
    .venv/bin/python tools/test_std_supersede_consistency.py          # 判据自检 + 清单计数
    .venv/bin/python tools/test_std_supersede_consistency.py --all    # 打印完整清单 (不截断)
    .venv/bin/python tools/test_std_supersede_consistency.py --strict # C/D/D' 清单非空也算失败

退出码口径 (2026-10-16 拍板): A/B1/M* 是**判据断言** → 决定退出码 (修完键之后必须绿);
  C/D/D'/B2 是**数据账** → 默认只点名, 要 --strict 才决定退出码。
  把两件事混在一个退出码里, 下一批就会为了"让它绿"而去动不该动的数据。

⚠ 本机跑, 不进 GUARD_TESTS: F 段的快照统计依赖 acquire/raw (被 quick_deploy.sh 的 _EXCL 排除),
  容器里那一栏只会打印「快照不可读」; A/B/C/D 本身只需要 standard_db。
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import knowledge.standard_version as sv                                    # noqa: E402
from knowledge.oel import connect_ro                                       # noqa: E402
from knowledge.standard_version import (STD_COLLECT_FAMS, STD_MARKER,      # noqa: E402
                                        STD_SEPS, _base_code, _year,
                                        fam_alt, norm_code, resolve_current, std_key)

SEP = "[" + STD_SEPS + "]"
# ── 从形态派生的 name 解析 (字符类, 不是号表) ──────────────────────────────────
# ⚠ 量词的花括号在 f-string 里必须**双写**: 单写会被当替换字段吃掉 → 正则长成
#    `[A-Z](2, 4)` 这种鬼东西 → 反向对表静默命中 0 行 (踩过, 所以钉在这里)。
_CODE_IN_NAME = (r"[A-Z]{2,4}(?:\s*/\s*[A-Z])?\s*\d+(?:\.\d+)?\s*" + SEP
                 + r"\s*(?:19|20)\d{2}")
ACT_RE = re.compile(r"(?<![被由])代\s*替\s*[《「\"']?(" + _CODE_IN_NAME + r")")
PASS_RE = re.compile(r"(?:被|由)\s*(" + _CODE_IN_NAME + r")\s*代\s*替")
SPLIT_RE = re.compile(r"[,，;；、]+")
DEAD = ("废止", "已废止")
SNAPSHOT = ROOT / "acquire" / "raw" / "nhc_gbz" / "gbz_baseline.json"
_FAM_ALT = fam_alt(STD_COLLECT_FAMS)
# 规范身份键形态 = 族根 + (指导性 /Z 保留) + 编号 —— 不带推荐性标记、不带年份尾巴。
#   Z 留着是既有口径: GB/Z 是「指导性技术文件」, 与强制性 GB 不是同一份文档 (#42 只并 T)。
_CANON_KEY = re.compile(r"^(" + _FAM_ALT + r")(?:/Z)?\d+(?:\.\d+)?$")
# 书写形态变体的生成规则 (同样只有字符类, 不出现任何具体号)
_BARE_MARK = re.compile(rf"^({_FAM_ALT})/({re.escape(STD_MARKER)})")
_MARK_BARE = re.compile(rf"^({_FAM_ALT})({re.escape(STD_MARKER)})(?=\d)")
_YEAR_TAIL = re.compile("(" + SEP + r")(\d{4})$")

n_pass = n_fail = 0


def check(label, cond, detail=""):
    global n_pass, n_fail
    if cond:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}" + ("" if cond else f"  {detail}"))


# ── 判据本体 (纯函数: 行集 + 键函数 → 违例; 变异用例注入坏键函数) ───────────────
def form_variants(code: str) -> list[str]:
    """一行 code → 它的各种**真实**书写形态 (形态规则派生, 不出现具体号):
       原样 / 归一 / 补斜杠 / 去斜杠 / 族与编号之间加空格(含全角空格) /
       连接号换成 em-dash、全角横线、横线两侧留空格 / 小写。

    ⚠ 不生成「年份前只留空格」的形态 —— 那在 norm_code 里会把连接号一起吃掉
      ('GB/T12801 2008' → 'GB/T128012008'), 那是编造的形态而不是成品里的写法。
    """
    s = norm_code(code)
    out = [code, s, s.replace("/", "/ "), s.replace("-", " - ")]
    m = _MARK_BARE.match(s)                       # 'GBZT189.9-2025' → 'GBZ/T189.9-2025'
    if m:
        out.append(f"{m.group(1)}/{m.group(2)}{s[m.end(2):]}")
    m2 = _BARE_MARK.match(s)                      # 反向: 把 '/T' 拆成裸 'T'
    if m2:
        out.append(f"{m2.group(1)}{m2.group(2)}{s[m2.end(2):]}")
    t = _YEAR_TAIL.search(s)
    if t:
        head = s[:t.start()]
        out += [f"{head}—{t.group(2)}", f"{head}－{t.group(2)}",
                f"{head} − {t.group(2)}", s.lower()]
    return out


def identity_census(rows, key=_base_code):
    """A 段: (形态变体归一违例, 非规范键行)。

    违例只统计**能落进规范形态的行** (key 可解析): 落不进的行归 A2 点名栏, 它们本来
    就是数据侧的非规范 code (两位年 / 多分部压缩 / 带括号的修改单), 不该由形态测试兜。
    """
    form_bad, noncanon = [], []
    for r in rows:
        code = r[0]
        if not code:
            continue
        if not _CANON_KEY.match(key(code)):
            noncanon.append((code, key(code)))
            continue
        ks = sorted({std_key(v) for v in form_variants(code)})
        if len(ks) > 1:
            form_bad.append((code, ks))
    return form_bad, noncanon


def key_groups(rows, key=std_key):
    """身份键 (不含年份) → 行; 年份从 code 尾巴取。"""
    g = collections.defaultdict(list)
    for r in rows:
        g[_base_of(r[0], key)].append(r)
    return g


def _base_of(code, key=std_key) -> str:
    """std_key('GBZ2.2-2007') → 'GBZ2.2' (去掉年份尾巴就是身份键)"""
    k = key(code)
    return k[:k.rfind("-")] if re.search(r"-\d{4}$", k) else k


def currentness_by_key(rows, resolve, key=std_key):
    """B1: 按身份键分组 —— 同键里有更晚且未废止的行时, resolve 必须给出更晚那行。"""
    by = key_groups(rows, key)
    out = []
    for r in rows:
        y = _year(r[0] or "")
        k = _base_of(r[0], key)
        if not k or not y:
            continue
        later = [x for x in by[k] if _year(x[0] or "") > y and (x[2] or "") not in DEAD]
        if not later:
            continue
        got = resolve(r[0]) or {}
        gc = got.get("code") or ""
        if _year(gc) <= y or (got.get("state") or "") in DEAD:
            out.append((r[0], r[2], max(_year(x[0]) for x in later), gc, got.get("state")))
    return out


def stated_pairs(rows):
    """库里**自己声明**的 (新行, 被代替号) 对子: replace_of ∪ name 主动语态。

    这一份与键函数无关 → 用它做现行性断言 (B2) 才有"抓坏键"的牙。
    """
    out = []
    for r in rows:
        toks, seen = [], set()
        for t in SPLIT_RE.split((r[3] or "").strip()):
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                toks.append(t)
        nm = r[1] or ""
        for m in ACT_RE.finditer(nm):
            if re.search(r"[被由]\s*$", nm[:m.start()]):
                continue                          # 被动语态交给 D'
            t = m.group(1).strip()
            if t not in seen:
                seen.add(t)
                toks.append(t)
        out += [(r, t) for t in toks]
    return out


def currentness_by_statement(rows, resolve, key=std_key):
    """B2: 每一对「库里写着 X 代替 Y」→ resolve_current(Y) 必须给出比 Y 更晚的行。

    只统计 Y 确实在库里的对子 (Y 不在库属 C 段的「旧号不在库」栏, 不在这儿重复记账)。
    """
    lib = {key(r[0]) for r in rows}
    out = []
    for new_row, tok in stated_pairs(rows):
        if key(tok) not in lib:
            continue
        y = _year(tok)
        got = resolve(tok) or {}
        gc = got.get("code") or ""
        if y and (_year(gc) <= y or (got.get("state") or "") in DEAD):
            out.append((new_row[0], tok, gc, got.get("state")))
    return out


def replace_of_tokens(row):
    return [t.strip() for t in SPLIT_RE.split((row[3] or "").strip()) if t.strip()]


def name_active_tokens(row):
    """name 里**主动**写出的被代替号 (排除「被 X 代替」的被动语态 —— 方向不能混)"""
    nm = row[1] or ""
    out = []
    for m in ACT_RE.finditer(nm):
        if re.search(r"[被由]\s*$", nm[:m.start()]):
            continue
        out.append(m.group(1).strip())
    return out


def forward_table(rows, key=std_key):
    """C 段: replace_of 写了旧号 → 旧行必须在库里, 且 state='废止'。"""
    by = collections.defaultdict(list)
    for r in rows:
        by[key(r[0])].append(r)
    pairs, in_lib, violations, missing = [], [], [], []
    for r in rows:
        for t in replace_of_tokens(r):
            pairs.append((r, t))
            cands = by.get(key(t)) or []
            if not cands:
                missing.append((r, t))
                continue
            in_lib.append((r, t))
            for x in cands:
                if (x[2] or "") not in DEAD:
                    violations.append((r, t, x))
    return pairs, in_lib, violations, missing


def backward_name_table(rows):
    """D 段: name 写了「（代替 X）」而 replace_of 为空 → 点名 X (没进字段的那个号)。"""
    out = []
    for r in rows:
        if (r[3] or "").strip():
            continue
        toks = name_active_tokens(r)
        if toks:
            out.append((r, toks))
    return out


def passive_table(rows, key=std_key):
    """D' 段: name 写「被 X 代替」→ 本行是旧行 → 本行 state 必须是废止。"""
    lib = {key(r[0]) for r in rows}
    out = []
    for r in rows:
        for m in PASS_RE.finditer(r[1] or ""):
            t = m.group(1)
            if (r[2] or "") in DEAD:
                continue
            newer = _year(t) > _year(r[0])
            if newer or key(t) in lib:
                out.append((r, t))
    return out


def legacy_base_code(code: str) -> str:
    """变异夹具: _base_code 退回 2026-10-16 之前的实现 (只 replace 'GBZ/T'/'GB/T')。

    ⚠ 这不是判据, 是**用来证明 A/B2 段真能红**的反例; 正常代码路径一行都不读它。
    """
    s = norm_code(code)
    s = re.sub(rf"-(?:19|20)\d{{2}}$", "", s)
    return s.replace("GBZ/T", "GBZ").replace("GB/T", "GB")


def legacy_std_key(code: str) -> str:
    s = norm_code(code)
    m = re.search(rf"{SEP}(\d{{4}})$", s)
    b = legacy_base_code(s[:m.start()] if m else s)
    return f"{b}-{m.group(1)}" if m else b


def _prov(r):
    """出处栏: source | url | effdate (清单必须可复核)"""
    return f"source={r[4] or '—'} | url={r[5] or '—'} | effdate={r[6] or '—'}"


def _cut(items, limit):
    return items if limit is None else items[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="打印完整清单 (默认各类只打前 15 行)")
    ap.add_argument("--strict", action="store_true",
                    help="C/D/D' 清单非空也算失败 (数据迁完再打开; 本批刻意不开)")
    args = ap.parse_args()
    limit = None if args.all else 15

    print("=" * 78)
    print("⚠ 这是点名，不是迁移 —— 全程只读 (connect_ro), 不写库 / 不改 state / 不灌字段")
    print("=" * 78)

    conn = connect_ro()          # 只读连接: 不设 row_factory → 一律下标取列
    try:
        rows = [tuple(r) for r in conn.execute(
            "SELECT code, name, state, replace_of, source, url, effdate FROM standard_db")]
        print(f"standard_db 行数: {len(rows)}   (state 分布: "
              f"{dict(collections.Counter((r[2] or '') for r in rows))})")

        # ── A. 身份键 ──────────────────────────────────────────────────────
        print("\n### A. 身份键: 书写形态必须并成一个 + 每行落进该族唯一规范写法 (#42)")
        form_bad, noncanon = identity_census(rows)
        slashless = [r[0] for r in rows if _MARK_BARE.match(norm_code(r[0] or ""))]
        check(f"A1 形态不变式: 每行 code 的 带斜杠/无斜杠/空格/全角/大小写 变体 → 同一个口径号"
              f" (违例 {len(form_bad)})", not form_bad, [x[0] for x in _cut(form_bad, limit)])
        check(f"A1' 被测对象非空: 库里确有「无斜杠推荐性形态」行 {len(slashless)} 行 "
              f"(否则 A1 是空测)", bool(slashless), len(slashless))
        fams = collections.Counter(re.match(rf"^({_FAM_ALT})", norm_code(c)).group(1)
                                   for c in slashless if re.match(rf"^({_FAM_ALT})", norm_code(c)))
        print(f"      A1 覆盖形态分布 (按族根, 不点名号): {dict(fams)}")
        print(f"      A2 落不进规范写法的 code: {len(noncanon)} 行 —— **数据账, 本批不迁**")
        for code, k in _cut(noncanon, limit):
            print(f"        · {code!r} → 键 {k!r}")
        # A3 同一个"号+年份"在库里有两种书写形态 = #42 归一的直接受益者 (库内重复行)
        dupg = collections.defaultdict(list)
        for r in rows:
            dupg[std_key(r[0])].append((r[0], r[2]))
        dups = {k: v for k, v in dupg.items() if len({c for c, _ in v}) > 1}
        print(f"      A3 同一口径号在库里并存多种写法 (重复行): {len(dups)} 组 —— 归一之后"
              f"才看得见, 本批只点名 (state 是否自洽一并给)")
        for k, v in dups.items():
            print(f"        · {k} ← {sorted({c for c, _ in v})} state={sorted({s for _, s in v})}")

        # ── B. 现行性遍历 ──────────────────────────────────────────────────
        print("\n### B. resolve_current 遍历: 库里有更晚版本时, 必须返回更晚那行 (#42)")
        sv.invalidate_caches()
        vio_b1 = currentness_by_key(rows, lambda c: resolve_current(conn, c))
        _groups = key_groups(rows)
        cand_b1 = sum(1 for r in rows
                      if _year(r[0]) and any(
                          _year(x[0]) > _year(r[0]) and (x[2] or "") not in DEAD
                          for x in _groups[_base_of(r[0])]))
        check(f"B1 按身份键遍历全库 (有晚版的旧行 {cand_b1} 行) → 违例必须为 0 (实得 {len(vio_b1)})",
              not vio_b1, [x[:2] for x in _cut(vio_b1, limit)])
        for x in _cut(vio_b1, limit):
            print(f"        · {x[0]} (state={x[1]}) 应升到 {x[2]} 年版, 实得 {x[3]!r}")
        pairs = stated_pairs(rows)
        vio_b2 = currentness_by_statement(rows, lambda c: resolve_current(conn, c))
        print(f"      B2 按「库自己声明的代替关系」遍历: 对子 {len(pairs)} / 去重被代替号 "
              f"{len({t for _, t in pairs})} 个 → 解析不出更晚行的 {len(vio_b2)} 对 —— "
              f"**数据账 (声明只写在 name 里时, replace_of 反查表看不见), 只点名**")
        for x in _cut(vio_b2, limit):
            print(f"        · 「{x[0]}」声明代替 {x[1]} → resolve_current({x[1]}) 实得 "
                  f"{x[2]!r} (state={x[3] or '—'}; 要么没跨过改号, 要么给出的仍是废止/自身)")

        # ── C. 正向对表 ────────────────────────────────────────────────────
        print("\n### C. 正向对表: replace_of 指向的旧行必须 state='废止' (#45)")
        c_pairs, c_inlib, vio_c, miss_c = forward_table(rows)
        old_codes = {v[0] for _, _, v in vio_c}
        print(f"      对子 {len(c_pairs)} | 旧行在库 {len(c_inlib)} | 旧号不在库 {len(miss_c)}"
              f" | **旧行漏标废止 {len(vio_c)} 对 / 去重旧号 {len(old_codes)} 个**")
        # C2 合表: 声明来源换成 replace_of ∪ name (任务书里那张 375/346/… 的表)
        sp = pairs                                  # 同一份声明集, 与 B2 共用 (不另算一遍)
        by_full = collections.defaultdict(list)
        for r in rows:
            by_full[std_key(r[0])].append(r)
        c2_inlib = [(r, t) for r, t in sp if by_full.get(std_key(t))]
        c2_vio = [(r, t, x) for r, t in c2_inlib for x in by_full[std_key(t)]
                  if (x[2] or "") not in DEAD]
        print(f"      C2 合表 (声明 = replace_of ∪ name 主动语态): 对子 {len(sp)}"
              f" | 旧行在库 {len(c2_inlib)} | 旧号不在库 {len(sp) - len(c2_inlib)}"
              f" | **漏标废止 {len(c2_vio)} 对 / 去重旧号 {len({x[0] for _, _, x in c2_vio})} 个**"
              f" (含 D 段那批「号只在 name 里」的声明)")
        print(f"      C2 state 分布: "
              f"{dict(collections.Counter((x[2] or '') for _, _, x in c2_vio))}")
        print(f"      漏标旧号的 state 分布 (只算 replace_of 一侧): "
              f"{dict(collections.Counter((v[2] or '') for _, _, v in vio_c))}")
        # C3 行级口径 (每行只取第一条声明: replace_of 优先, 否则 name) —— 给复核的人一个
        #    可以和"上一轮量到的 375/346 那组数"直接对表的口径
        c3 = []
        for r in rows:
            t = (replace_of_tokens(r) or name_active_tokens(r) or [None])[0]
            if t:
                c3.append((r, t))
        c3_in = [(r, t) for r, t in c3 if by_full.get(std_key(t))]
        c3_vio = [(r, t, x) for r, t in c3_in for x in by_full[std_key(t)]
                  if (x[2] or "") not in DEAD]
        print(f"      C3 行级口径 (每行只认第一条声明): 对子 {len(c3)} | 旧行在库 {len(c3_in)}"
              f" | 漏标废止 {len(c3_vio)} 对 / 去重旧号 "
              f"{len({x[0] for _, _, x in c3_vio})} 个 —— 与 C2 只是**记账粒度**不同, 判据同一份")
        for new, tok, old in _cut(vio_c, limit):
            print(f"        · 新行 {new[0]} (state={new[2]}) 的 replace_of 写着 {tok!r}")
            print(f"            旧行 {old[0]} state={old[2] or '—'} | {old[1] or '—'}")
            print(f"            {_prov(old)}")
        check(f"C1 正向对表有被测对象 (旧行在库 {len(c_inlib)} 对) —— 否则「违例 0」是空测",
              len(c_inlib) > 0, len(c_inlib))
        if miss_c:
            print(f"      旧号不在库 (要抓一手目录才能补, 本工具不判): {len(miss_c)} 对")
            for new, tok in _cut(miss_c, limit):
                print(f"        · {new[0]} ← {tok!r}")

        # ── D. 反向对表 ────────────────────────────────────────────────────
        print("\n### D. 反向对表: name 写了「（代替 X）」而 replace_of 为空 → 点名 X (#45)")
        vio_d = backward_name_table(rows)
        print(f"      命中 {len(vio_d)} 行, 点名「没进字段」的被代替号 {sum(len(t) for _, t in vio_d)} 个")
        for r, toks in _cut(vio_d, limit):
            print(f"        · {r[0]} state={r[2] or '—'} 缺 replace_of ← name 写了 {toks}")
            print(f"            {r[1]}")
            print(f"            {_prov(r)}")
        vio_dp = passive_table(rows)
        print(f"      D' 方向对照 (「被 X 代替」里本行才是旧行): {len(vio_dp)} 行 state≠'废止'")
        for r, t in _cut(vio_dp, limit):
            print(f"        · {r[0]} state={r[2] or '—'} 被 {t!r} 代替却没标废止 | {_prov(r)}")
        check(f"D1 反向判据确有牙齿 (命中 {len(vio_d)} 行) 且被动语态走的是 D' 不是 D",
              bool(vio_d), vio_d[:2])

        # ── E. 变异用例 ────────────────────────────────────────────────────
        print("\n### E. 变异用例 (改坏一侧判据必须红 —— 证明上面几条不是恒真)")
        # M1: 判据产地换回旧实现 → A1 形态不变式必须立刻炸 (证明 A1 测的是键函数本身)
        _saved = sv._base_code
        try:
            sv._base_code = legacy_base_code
            fb1, nc1 = identity_census(rows, key=legacy_base_code)
            sv.invalidate_caches()
            vio_b2_legacy = currentness_by_statement(rows, lambda c: resolve_current(conn, c),
                                                     key=legacy_std_key)
            vio_b1_legacy = currentness_by_key(rows, lambda c: resolve_current(conn, c),
                                               key=legacy_std_key)
        finally:
            sv._base_code = _saved
            sv.invalidate_caches()
        check(f"M1 键退回旧实现 → A1 形态不变式必须红 (实得违例 {len(fb1)} 行 / "
              f"规范键缺口 {len(nc1)} 行 vs 修好后 {len(noncanon)} 行)",
              len(fb1) > 0 and len(nc1) > len(noncanon), (len(fb1), len(nc1), len(noncanon)))
        check(f"M2 键退回旧实现 → B2 (按声明遍历) 必须比修好后点更多名 "
              f"({len(vio_b2)} → {len(vio_b2_legacy)})",
              len(vio_b2_legacy) > len(vio_b2), (len(vio_b2), len(vio_b2_legacy)))
        print(f"      M2' 关键对照: B1 用「坏键自己的分组」跑 → 自我失明 (违例 {len(vio_b1_legacy)}): "
              f"坏键把两版分成两组, 断言看不出「库里有更晚版本」这件事。")
        print("      M2' 所以 B1 只能防「同键分组里给错行」, 真正抓键坏的是 B2 (证据来自库自己的声明)。")
        # M3: 正向一侧 —— 合成库 (replace_of 写了旧号而旧行漏标废止) 必须点名
        mc = [("GBZT199.1-2025", "某某测定标准 第1部分", "实施中", "GBZ/T 199.1—2007", "s", "u", "2026-01-01"),
              ("GBZ/T199.1-2007", "某某测定标准 第1部分", "实施中", "", "s", "u", "2008-01-01")]
        _p, _inl, v3, _mis = forward_table(mc)
        check("M3 合成库: 新行是无斜杠形态 + 旧行带斜杠且漏标废止 → 正向必须点名",
              len(v3) == 1 and v3[0][2][0] == "GBZ/T199.1-2007", v3)
        # M3' 反向形态: 旧行才是无斜杠形态 —— 键退回旧实现时正向**瞎掉** (旧号查不到)
        mc2 = [("GBZ/T300.164-2025", "某某测定标准 第164部分", "实施中",
                "GBZ/T 300.164—2018", "s", "u", "2026-01-01"),
               ("GBZT300.164-2018", "某某测定标准 第164部分", "实施中", "", "s", "u", "2019-01-01")]
        v3c = forward_table(mc2)[2]
        _p2, _inl2, v3b, mis2 = forward_table(mc2, key=legacy_std_key)
        check("M3' 正向必须找到「无斜杠形态的旧行」(键修好后 1 对违例)",
              len(v3c) == 1 and v3c[0][2][0] == "GBZT300.164-2018", v3c)
        check("M3' 对照: 键退回旧实现时正向**瞎掉** (旧号落进「不在库」栏) —— 键是这张表的地基",
              v3b == [] and len(mis2) == 1, (v3b, mis2))
        # M4: 反向一侧 —— name 有号 / replace_of 空 → 必须点名; 补了字段就不再点名
        m4 = [("GBZ/T199.2-2025", "某某测定标准 第2部分（代替 GBZ/T 199.2—2007）",
               "实施中", "", "s", "u", "2026-01-01")]
        d4 = backward_name_table(m4)
        check("M4 name 写了代替号而 replace_of 为空 → 反向必须点名该号",
              len(d4) == 1 and d4[0][1] == ["GBZ/T 199.2—2007"], d4)
        check("M4 对照: 同一行补上 replace_of 后反向不再点名 (判据不重复记账)",
              backward_name_table([(m4[0][0], m4[0][1], "实施中", "GBZ/T 199.2—2007",
                                    "s", "u", "2026-01-01")]) == [], "还在点名")
        # M5: 状态文本/被动语态不得误伤
        m5 = [("WS296-2008", "麻疹诊断标准（已被代替）", "废止", "", "s", "u", "2009-01-01"),
              ("WS295-2008", "手足口病诊断标准（已废止）", "废止", "", "s", "u", "2009-01-01"),
              ("WST347-2011", "血细胞分析的校准指南（被 WS/T 347—2024 代替）",
               "实施中", "", "s", "u", "2012-01-01"),
              ("WS/T347-2024", "血细胞分析的校准指南", "现行", "", "s", "u", "2025-01-01")]
        check("M5 「（已被代替）」这类不含号的文本 → 反向不得点名 (解析可靠性的下界)",
              backward_name_table(m5) == [], backward_name_table(m5))
        check("M5 对照: 同一行的被动号必须被 D' 接住 (方向不混)",
              [x[0][0] for x in passive_table(m5)] == ["WST347-2011"], passive_table(m5))
        m5f = [(x[0], x[1], x[2],
                "WS/T 347—2011" if x[0] == "WS/T347-2024" else x[3], x[4], x[5], x[6])
               for x in m5]
        v5 = forward_table(m5f)[2]
        check("M5 对照: 把被动行的代替关系补进 replace_of 后, 正向必须点名旧行漏标废止 "
              "(旧行是无斜杠形态 'WST347-2011' → 只有 #42 的键才找得到它)",
              len(v5) == 1 and v5[0][2][0] == "WST347-2011", v5)
        # M6: 反向口径收窄是有意的 —— 放宽成「含代替就算」会把状态文本全捞进来
        loose = [r for r in rows if "代替" in (r[1] or "")]
        no_code = [r for r in loose
                   if not name_active_tokens(r) and not PASS_RE.search(r[1] or "")]
        check(f"M6 前提对表: 真库 name 含「代替」{len(loose)} 行, 反向只认 {len(vio_d)} 行, "
              f"其中 {len(no_code)} 行根本不含号 → 收窄不是恒真过滤",
              len(loose) > 3 * max(len(vio_d), 1) and len(no_code) > 0,
              (len(loose), len(vio_d), len(no_code)))

        # ── F. 能力边界 ────────────────────────────────────────────────────
        print("\n### F. 能力边界 (只点名, 不参与红绿)")
        try:
            snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
            cnt = collections.Counter(e.get("status") for e in snap if isinstance(e, dict))
            check(f"F1 一手快照 status 分布 = {dict(cnt)} → **只有一个取值**: 快照能判存在、"
                  f"判不了现行 (所以 A/B/C 的现行性只问 standard_db)",
                  len(cnt) <= 1, cnt)
            bare = [e.get("code") for e in snap
                    if _MARK_BARE.match(norm_code(e.get("code") or ""))]
            print(f"      F1 快照里同样有无斜杠形态 {len(bare)} 条 (如 {bare[:3]}) → "
                  f"两种写法都是一手目录自己给的, 所以归一只能是形态规则")
        except (OSError, ValueError) as e:
            print(f"      F1 一手快照不可读 ({type(e).__name__}) —— 容器里属正常 "
                  f"(raw 被 quick_deploy _EXCL 排除), 不影响 A/B/C/D")
        dirty = [r[6] for r in rows if r[6]
                 and not re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}$", str(r[6]))]
        shapes = collections.Counter(re.sub(r"\d", "#", str(d)) for d in dirty)
        print(f"      F2 effdate 非 `YYYY-MM-DD` 形态: {len(dirty)} 行 / 去重后 "
              f"{len(set(dirty))} 个取值; 形态 Top6: {shapes.most_common(6)}")
        print("      F2 「还没生效的旧版不该判废止」这类结论要先归一日期 —— 本批**只点名不结论**。")
        print("      F3 D 段只判「号写了却没进字段」;「该代替却没写在 name 里」是一手目录的账。")
        print("      F4 快照 status 全 '现行' + state 漏标 → 现行性判据的天花板就是 standard_db;")
        print("         要再往上, 得给 C/D 清单里每一条补一手详情页 URL (与 #43 同一动作)。")

        data_bad = bool(vio_c) or bool(vio_d) or bool(vio_dp) or bool(vio_b2) or bool(c2_vio)
        if args.strict:
            check("--strict: C/D/D'/B2 清单必须为空 (数据迁完之后才打开这个开关)", not data_bad,
                  f"C={len(vio_c)} 对, D={len(vio_d)} 行, D'={len(vio_dp)} 行, B2={len(vio_b2)} 对")
        else:
            print("\n      (默认模式: C/D/D'/B2 清单非空**不算失败** —— 这是点名。"
                  "数据迁完再跑 --strict 让它决定退出码。)")
    finally:
        conn.close()
        sv.invalidate_caches()

    print("\n" + "=" * 78)
    print("⚠ 再说明一次: 这是点名，不是迁移。上面 A2/B2/C/C2/D/D' 各张清单的每一行都是"
          "**数据侧待办**, 本工具没写库、没改 state、没灌字段。")
    print("=" * 78)
    print(f"通过 {n_pass} / 失败 {n_fail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
