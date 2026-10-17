#!/usr/bin/env python3
"""#54 的第一批: 把 GBZ/T 229.1/2/3—2010 与 GBZ/T 192.1/2—2007 五行按一手正文判废止,
   并把代替关系从 name 文本搬进 `replace_of` 字段。

为什么这批现在能动 (2026-10-17 复算, 逐条测得)
    上一窗口我把 #54 判成"先拿到 2025 一手正文再翻", 并写下顺序"一手正文 → 比对方法 → 决定实现
    → 再翻 state"。**这个前提当时就是错的**: 2025 那五份一手 PDF 一直躺在
    `acquire/raw/nhc_gbz/pdfs/` 里 (2026-08-22 抓的, 文件名 `GBZ_T_229.1_2025.pdf` 等), 而
    `knowledge/grade_engine.py` 的 #39 逐格核对注记引用的正是它们 —— 我没去 grep 仓库里已有的取证
    引用, 就宣布"未取得"。这是取景失败, 不是数据缺失。

一手依据 (本工具**运行时从 PDF 正文派生**, 不手写号与年份)
    封面/前言首段逐字含 "代替GBZ/T229.1—2010" 这类尾巴, 且 4.4.1/前言给出发布与实施日期:
      GBZ/T 229.1—2025 代替 GBZ/T 229.1—2010   发布 2025-08-20 / 实施 2026-02-01
      GBZ/T 229.2—2025 代替 GBZ/T 229.2—2010   同上
      GBZ/T 229.3—2025 代替 GBZ/T 229.3—2010   同上
      GBZ/T 192.1—2025 代替 GBZ/T 192.1—2007   发布 2025-09-04 / 实施 2026-02-01
      GBZ/T 192.2—2025 代替 GBZ/T 192.2—2007   同上
    今天 (执行日) 晚于全部 effdate → 旧版已实际被代替, 判废止是有据的, 不是"猜它该废止"。

内容耦合查过了 (这才允许翻)
    上一窗口的红线是"禁止只改标签": 翻 state 会让读出纠版链把成品里 `…—2010` 的引用改写成 `…—2025`,
    所以先量影响面 —— 带年份引用 (去空格逐字, 全角 `—` 与半角 `-` **分开数**, 混进一个字符类会静默碎):
      本机 68 份 `data/report_*.docx` (段落+格子 389711 条 / 5,702,495 字, 阳性对照
        `GBZ 2.1` 同尺子 2484 次/15 份) → 这五个号**带年份 0 次**;
        但 `GBZ/T 229.1` **无年份** 191 处 (5 种句子) —— 无年份号由
        `knowledge/standard_version.resolve_current` 按"未废止的最新版本"解析, 它今天就已经解析到
        2025, 所以本批对纸面的改动量 = 0。
      项目侧 (客户数据 + 已生成正文) : 本机 1 处 (`section_states.9.2.2.3.text`, 229.3—2010);
        生产实测 16 处 (全在 `section_states.*.text`, 即我们自己起草的正文, 不是甲方手填事实)。
        → 那 16 处属**存量正文改写**, 由 #17 那条链 (`tools/fix_project_std_text.py` 的 FIXUPS 思路)
          单独处理并逐处读上下文, 本工具**不碰 project 表**。

翻完之后实现不脱节
    `knowledge/grade_engine.py` 已是 2025 口径 (#39 逐格: WM 表1 = 1/2/4/6、五级含"Ⅰ级行动水平作业"、
    WB 用 `B≤1→0 / B>1→B`、式(1)/表4/表5)。所以"翻 state → 成品自动纠版到 2025"与现实现一致,
    不构成"用旧判据引新版" —— 这正是上一窗口拦住不翻的唯一理由, 现在被证伪了。

口径纠偏 (三个数, 别再引错)
    · 严格判据 (非废止 + 同族有更新年份 + 新版 name 逐字含"代替<旧号>") = **19 行**;
      放宽到"同族有更新年份即可" = **32 行**。本批只处理其中**有 PDF 封面"代替"直证**的 5 行。
    · `G=WD×WB×WL` / 式(4) / 表5 属 **229.2 (化学物)**, 不是 229.1; 229.1 是式(1)、因子 WM。
      (上一窗口我在任务描述里把两者混过一句, 已改。)
    · `standard_db.effdate` 形态不统一是系统性的: `test_std_supersede_consistency` F2 实测
      **1150 行**非 `YYYY-MM-DD` (`########` 388 / `####年#月#日` 368 / …)。本批靠一个容错日期
      解析器判"是否已生效", **解析不出就拒绝翻该行**, 不顺手归一那 1150 行 (那是独立任务)。

规矩 (与 tools/fix_oel_name_forms.py / add_samr_std_codes.py 同一套)
  · 默认 dry-run; `--apply` 才写, 且只写本地库 (进生产需用户单独批准);
  · 每条规则命中 ≥1 行, 否则点名 (僵尸规则); 已是目标值 → 幂等点名;
  · 写前原值落盘 DATA_DIR 并**打印出库外留一份**, 写完逐行读回 + `standard_db` 行数不变;
  · 只写 `state` 与 `replace_of` 两列, 不碰 name/pubdate/effdate —— 改了 name 就等于
    把"目录原文"改成"我的判断"。

用法:
    .venv/bin/python tools/retire_2025_batch.py            # 预演 (只读)
    .venv/bin/python tools/retire_2025_batch.py --proof    # 打印 PDF 派生的代替关系与影响面 (只读)
    .venv/bin/python tools/retire_2025_batch.py --apply    # 写本地库
"""
import os
import re
import sys
import json
import time
import sqlite3
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.paths import DB_PATH, DATA_DIR                 # noqa: E402  路径唯一产地
from knowledge.standard_version import norm_code, _base_code, _year   # noqa: E402  身份键单一产地

RAW = os.environ.get("OHS_GBZ21_RAW_DIR") or os.path.join(
    ROOT, "acquire", "raw", "nhc_gbz", "pdfs")

# 只列**文件**, 代替关系与日期一律从 PDF 正文派生 (不手写旧号/年份/日期)
PDFS = ("GBZ_T_229.1_2025.pdf", "GBZ_T_229.2_2025.pdf", "GBZ_T_229.3_2025.pdf",
        "GBZ_T_192.1_2025.pdf", "GBZT_192.2_2025.pdf")

SUPERSEDED = "废止"
LIVE_STATES = ("实施中", "现行", "待实施")


def pdf_head_text(path, pages=3):
    import pymupdf
    d = pymupdf.open(path)
    try:
        return "\n".join(d[i].get_text() for i in range(min(pages, len(d))))
    finally:
        d.close()


def parse_dates(text):
    """从 PDF 头部取 发布/实施 日期 (两种排版都见过: '2025-08-20发布2026-02-01实施' 与分行)"""
    t = re.sub(r"\s+", "", text)
    m = re.search(r"(20\d\d-\d\d-\d\d)发布(20\d\d-\d\d-\d\d)实施", t)
    if m:
        return m.group(1), m.group(2)
    m1 = re.search(r"发布日期[:：]?(20\d\d[-年]\d{1,2}[-月]\d{1,2}日?)", t)
    m2 = re.search(r"实施日期[:：]?(20\d\d[-年]\d{1,2}[-月]\d{1,2}日?)", t)
    return (m1.group(1) if m1 else None), (m2.group(1) if m2 else None)


def derive():
    """[(新号, 旧号, 发布日期, 实施日期, 逐字依据)] —— 旧号来自 PDF 自己的『代替』尾巴"""
    out, errs = [], []
    for f in PDFS:
        p = os.path.join(RAW, f)
        if not os.path.exists(p):
            errs.append(f"  一手 PDF 不在: {p} (读不到源头必须点名, 不许当成空集)")
            continue
        txt = pdf_head_text(p)
        ns = re.sub(r"\s+", "", txt)
        m = re.search(r"代替(GBZ/T\d+(?:\.\d+)?)[—\-–](\d{4})", ns)
        if not m:
            errs.append(f"  {f} 正文里读不到『代替GBZ/T…—年份』→ 该文件不处理")
            continue
        new_m = re.search(r"(GBZ/T\d+(?:\.\d+)?)[—\-–](\d{4})", ns)
        new_code, old_code = new_m.group(0), f"{m.group(1)}—{m.group(2)}"
        pub, eff = parse_dates(txt)
        if not eff:
            errs.append(f"  {f} 读不到实施日期 → 无法判'是否已生效', 该文件不处理")
            continue
        out.append({"file": f, "new": new_code, "old": old_code,
                    "pub": pub, "eff": eff, "why": ns[ns.find("代替"):ns.find("代替") + 40]})
    return out, errs


def norm_date(v):
    """容错日期解析: 只认能确定到'日'的形态; 解析不出返回 None (调用方拒绝翻该行)"""
    if not v:
        return None
    s = str(v).strip()
    m = re.match(r"^(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?$", s)
    if m:
        y, mo, dd = (int(x) for x in m.groups())
    else:
        m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
        if not m:
            return None
        y, mo, dd = (int(x) for x in m.groups())
    try:
        return datetime.date(y, mo, dd)
    except ValueError:
        return None


def citations(conn):
    """带年份引用的影响面 (全角/半角分开数; 去空格逐字) —— 只读, 不碰数据"""
    needles = {}
    for e in derive()[0]:
        for code in (e["old"], e["new"]):
            ns = re.sub(r"\s+", "", code)
            needles[code] = (ns, ns.replace("—", "-"))
    hits = {}
    for label, (full, half) in needles.items():
        n = 0
        for (_pid, data) in conn.execute("SELECT id, data FROM project"):
            if not data:
                continue
            s = data.replace(" ", "").replace("　", "")
            n += s.count(full) + (s.count(half) if half != full else 0)
        hits[label] = n
    return hits


def _nsd(s):
    """比 name 用: 去空白 + 三种破折号归一 (name 写 'GBZ/T 229.1—2010', 库里 code 是半角)"""
    return re.sub(r"\s+", "", str(s or "")).replace("—", "-").replace("–", "-")


def build_key_index(conn):
    """(身份键, 年份) → [(id, code, state, replace_of, name)] —— **按身份键查, 不按 code 逐字查**:
        库里同一族有 'GBZ/T229.1-2025' 与 'GBZT192.2-2025' 两种形态 (无斜杠裸 T),
        而 PDF 给的是全角破折号形态 —— 逐字相等会"查无此行", 于是把有据的修复报成"行不存在"。
        #42 立的口径就是这个: 存在性判断不该受书写形态影响。"""
    idx = {}
    for rid, code, state, rep, nm in conn.execute(
            "SELECT id, code, state, replace_of, name FROM standard_db"):
        idx.setdefault((_base_code(norm_code(code)), _year(code)), []).append(
            (rid, code, state, rep, nm))
    return idx


def find_one(idx, code, why_ctx):
    """恰 1 行才给落点; 0 行或多行 → (None, 点名) —— 多行时绝不猜哪一条"""
    hit = idx.get((_base_code(norm_code(code)), _year(code)), [])
    if len(hit) == 1:
        return hit[0], None
    if not hit:
        return None, f"  {why_ctx}: 号 {code!r} 按身份键在库里 0 行 → 不动 (不新造行)"
    return None, (f"  {why_ctx}: 号 {code!r} 按身份键命中 {len(hit)} 行 "
                  f"{[(h[0], h[1]) for h in hit]} → 歧义, 交人工, 本批不动")


def plan(rows, today):
    """→ (可改的代替关系, 不动清单); 日期解析不出或还没生效 → 该行不翻"""
    can, hold = [], []
    for e in rows:
        eff = norm_date(e["eff"])
        if eff is None:
            hold.append(f"  {e['file']}: 实施日期 {e['eff']!r} 解析不出 → 不翻 (宁可不动)")
            continue
        if eff > today:
            hold.append(f"  {e['old']}: 实施日 {eff} 还没到 (今天 {today}) → 旧版此刻仍现行, 不翻")
            continue
        can.append(e)
    return can, hold


def main() -> int:
    mode = ("--apply" if "--apply" in sys.argv else
            ("--proof" if "--proof" in sys.argv else "dry"))
    derived, errs = derive()
    if errs:
        print("❌ 一手依据取不到, 整批不执行:")
        for x in errs:
            print(x)
        return 1
    if len(derived) != len(PDFS):
        print(f"❌ 派生出 {len(derived)} 条代替关系, 预期 {len(PDFS)} 条 → 整批不执行")
        return 1
    today = datetime.date.today()
    can, hold = plan(derived, today)

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(standard_db)")]
    n_all = conn.execute("SELECT count(*) FROM standard_db").fetchone()[0]

    if mode == "--proof":
        print(f"派生自 {len(PDFS)} 份一手 PDF (今天 {today}):")
        for e in derived:
            print(f"  {e['new']}  ←  {e['old']}   发布 {e['pub']} / 实施 {e['eff']}"
                  f"   依据『{e['why']}』")
        print(f"  DB 侧核对 (按身份键, 不按 code 逐字):")
        kidx = build_key_index(conn)
        for e in derived:
            for code in (e["old"], e["new"]):
                one, msg = find_one(kidx, code, "核对")
                print(f"     {code:22s} -> {one if one else msg}")
        print(f"  影响面 (项目侧带年份引用, 全角/半角分开数): {citations(conn)}")
        conn.close()
        return 0

    kidx = build_key_index(conn)
    todo_old, todo_new, hold2 = [], [], []
    for e in can:
        ro, msg = find_one(kidx, e["old"], f"旧行 {e['old']}")
        if ro is None:
            hold2.append(msg)
            continue
        rn, msg = find_one(kidx, e["new"], f"新行 {e['new']}")
        if rn is None:
            hold2.append(msg + " (没有落点就不写代替关系)")
            continue
        rid, rcode, rstate, rrep, rnm = ro
        nid, ncode, nstate, nrep, nnm = rn
        if rstate == SUPERSEDED:
            hold2.append(f"  旧行 id={rid} {rcode} 已是『{SUPERSEDED}』(幂等)")
        elif rstate not in LIVE_STATES:
            hold2.append(f"  旧行 id={rid} {rcode} state={rstate!r} 不在已知 LIVE 取值里 → 不动")
        else:
            todo_old.append((rid, rcode, rstate, e))
        # replace_of 写**库里旧行自己的 code 原文** (不是 PDF 抄来的写法): 同一张表里
        # 半角/全角两种形态都有 (实测 104/129), 而我没权限新造第三种写法; 指向那一行的
        # 存储原文既能逐字回查, 护栏也按身份键匹配 (与形态无关)。
        # 前置: 新行 name 里必须真的写着这个旧号 (去空白 + 破折号归一后逐字对上) ——
        #        对不上就说明"代替关系只在 PDF 封面上, 目录行里没有", 那属另一件事, 本批不写。
        if _nsd(rcode) not in _nsd(nnm):
            hold2.append(f"  新行 id={nid} {ncode} 的 name={nnm!r} 里读不到旧号 {rcode!r} "
                         "(归一后也不含) → 不写 replace_of")
        elif nrep in (None, ""):
            todo_new.append((nid, ncode, nrep, rcode, e))
        elif norm_code(str(nrep)) != norm_code(rcode):
            hold2.append(f"  新行 id={nid} replace_of 已是 {nrep!r} (与本行旧号 {rcode!r} "
                         "不同身份, 不覆盖) → 交人工")
        else:
            hold2.append(f"  新行 id={nid} replace_of 已等于 {rcode!r} (幂等)")

    print(f"可改: 旧行判废止 {len(todo_old)} 行 + 新行补 replace_of {len(todo_new)} 行; "
          f"不动 {len(hold2) + len(hold)} 项")
    for rid, code, st, e in todo_old:
        print(f"  [standard_db.state] id={rid} {code!r}: {st!r} → {SUPERSEDED!r}   "
              f"依据: {e['file']} 封面『{e['why']}』, 新版 {e['new']} 实施 {e['eff']} 已过")
    for rid, code, _rep, old, e in todo_new:
        print(f"  [standard_db.replace_of] id={rid} {code!r}: NULL → {old!r}   "
              f"依据: 该行 name 里写着旧号 (去空白+破折号归一后逐字对上) + "
              f"{e['file']} 封面『{e['why']}』同证")
    for h in hold + hold2:
        print("  ⓘ", h)
    if mode != "--apply":
        print("\n(预演模式 — 未写库; 加 --apply 才写入本地库)")
        conn.close()
        return 0
    if not todo_old and not todo_new:
        print("没有任何可改 → 不写库")
        conn.close()
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    before = {"old": [{"id": r[0], "code": r[1], "state": r[2]} for r in todo_old],
              "new": [{"id": r[0], "code": r[1], "replace_of": r[2]} for r in todo_new]}
    bpath = DATA_DIR / f"retire_2025_before_{stamp}.json"
    bpath.write_text(json.dumps(before, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  ⓘ 写前原值已存 {bpath.name} (请另拷一份到库外)")

    w = sqlite3.connect(str(DB_PATH))
    try:
        w.execute("BEGIN IMMEDIATE")
        for rid, code, st, e in todo_old:
            cur = w.execute("UPDATE standard_db SET state=? WHERE id=? AND state=?",
                            (SUPERSEDED, rid, st))
            if cur.rowcount != 1:
                raise RuntimeError(f"id={rid} state affected={cur.rowcount} (期望 1)")
        for rid, code, rep, old, e in todo_new:
            cur = w.execute("UPDATE standard_db SET replace_of=? WHERE id=? "
                            "AND (replace_of IS NULL OR replace_of='')", (old, rid))
            if cur.rowcount != 1:
                raise RuntimeError(f"id={rid} replace_of affected={cur.rowcount} (期望 1)")
        for rid, code, st, e in todo_old:
            got = w.execute("SELECT state FROM standard_db WHERE id=?", (rid,)).fetchone()[0]
            if got != SUPERSEDED:
                raise RuntimeError(f"读回不一致 id={rid}: {got!r}")
        for rid, code, rep, old, e in todo_new:
            got = w.execute("SELECT replace_of FROM standard_db WHERE id=?", (rid,)).fetchone()[0]
            if norm_code(str(got)) != norm_code(old):
                raise RuntimeError(f"读回不一致 id={rid}: replace_of={got!r} 期望 {old!r}")
        n_after = w.execute("SELECT count(*) FROM standard_db").fetchone()[0]
        if n_after != n_all:
            raise RuntimeError(f"standard_db 行数从 {n_all} 变成 {n_after} —— 判废止不许增删行")
        w.execute("COMMIT")
        print(f"✅ 已写 {len(todo_old) + len(todo_new)} 行并逐行读回核对 "
              f"(行数仍 {n_after}; state→废止 {len(todo_old)} / replace_of {len(todo_new)})")
    except BaseException as exc:
        w.execute("ROLLBACK")
        print(f"⏹ 已回滚: {exc}")
        w.close()
        conn.close()
        return 1
    w.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
