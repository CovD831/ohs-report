#!/usr/bin/env python3
"""成品行内容判据 (#50 非因素行被印上纸 / #51 名内多一个空格 → 假陈述)

立这一支的理由 (2026-10-16 用真实生产项目现算导出、逐格读字取证, §7.4):
  #50  检测结果表里印着 '/'、'接触的职业病危害因素'(表头文字)、'采样日期 ：2024.04.07'(注释行)
       三行"不是因素的因素"。根因在**导入侧**把表头/注释行塞进了 project.data.detections;
       导出侧的分桶四级链会**点名**它们 (`[det-bucket] … 行查不到标准库类别`) 却照旧印出去。
  #51  '石灰石粉尘（ 总尘）'、'沉淀二氧 化硅（ 总尘）'、'稀土粉尘（游离SiO2含量<10%）' 这类
       与标准库写法只差空格/下标位置的串, 在生产 oel_limit/hazard_factor 里**精确命中 0** →
       成品限值列印 '—' 且备注写"未制定职业接触限值" —— 而标准里明明给了限值。那是**假陈述**。
  #41  与 #51 是同一个格子, 所以这一支同时管住备注措辞: 只许说"标准库未登记/须人工确认"
       (我们能核实的事), 不许说"标准未制定限值" (我们核实不了的事)。

判据一律从**数据/结构**派生 (本仓固定要求, 不许物质名白名单):
  · 同形尺子 = knowledge/pdf_cells.nospace/digit_bag + 本仓 #36 的 _letters_only (字母汉字序列
    相同 + 数字多重集相同), 与 tools/fix_oel_subscripts.py 写侧同一口径;
  · 唯一命中才动, 多命中交人工 (不猜);
  · "采样方式词" (总尘/呼尘) 的词表来自 oel_limit.oel_type 列本身;
  · "非因素行"的三条判据 (官方层零锚 / 本行无测量载荷 / 同名异行也无测量载荷) 全读库与行结构。

规矩 (与全仓一致):
  · 真实库 `data/ohs.db` 只读, 变异打在 `sqlite3 backup()` 出的临时副本上 (OHS_DATA_DIR 指过去);
    本文件结束时对真实库的 (size, mtime_ns) 做前后对表 —— 动了就红。
  · "改前"不长在仓库里: 用**源码级变异**在当前文件上还原被摘掉的那两处 (见 _variant),
    所以每条绿灯都配一条"必然能红、且每次都红"的变异。

能力边界与被排除项 (2026-10-17 实测, 别把这四行当已解决):
  1) **本地库里的非因素行 = 0 条** (55 个不同检测名 / 373 行, 全部有锚或有测得值) —— #50 的三条
     形状只在生产项目里出现过, 所以这一支的 #50 部分是**注入**取证, 不是存量回归;
  2) 有测得值的行**永不**被 #50 删 (信息不丢失优先于版面干净) → 若导入侧给表头行塞了一个数值,
     本过滤会漏它 (真修法在导入侧, 不在这个文件的拥有权范围内);
  3) 物理/辅助项 (照度/噪声/高温…) 不参与零锚判据 (它们本来就不在 GBZ 2.1 化学限值 namespace 里,
     本地实测 1 行无值的 '照度' 会被误判成非因素行 → 已排除, 见 _det_nonfactor 的 ⚠ 段);
  4) #36 写侧的前提⑤"名字与限值同行"要读一手 PDF, 而生产容器不带 acquire/raw (§7.6) → 读侧只能
     退化为 CAS 交叉核对; 库内无 CAS 的粉尘行 (如 稀土粉尘) 这一环是空的, 只剩"唯一命中"兜住
     —— 第 6 节的**库级歧义变异**就是打在这条边界上: 造出两个同形规范名时, 成品必须两边都不印。

用法: .venv/bin/python tools/test_det_rows.py
"""
import ast
import contextlib
import importlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.paths import DB_PATH                      # noqa: E402  (真实库: 只读取景 + 收尾对表)
from knowledge.pdf_cells import digit_bag, nospace       # noqa: E402  (#36 尺子的唯一产地)
from tools.test_det_bucket import build_temp_tree        # noqa: E402  (同一份临时树配方, 不复述)

SF_SRC = (ROOT / "web" / "section_filler.py").read_text(encoding="utf-8")
# §7.4 在生产成品上逐格读到的原样 (作为注入样本; **只出现在测试里**, 生产代码不许有这些串)
HEADER_ROWS = ["/", "接触的职业病危害因素", "采样日期 ：2024.04.07"]
SPACE_VARIANTS = {"石灰石粉尘（ 总尘）": ("PC-TWA", "8"),     # 官方名 '石灰石粉尘' 总尘=8
                  "石灰石粉尘（ 呼尘）": ("PC-TWA", "4"),     #      同一行的呼尘=4 (不许印成 8)
                  "稀土粉尘（游离SiO2含量<10%）": ("PC-TWA", "2.5")}
# 生产代码里"改前"的锚点 (逐条必须命中 1 次, 命中不上 = 代码已变, 判据作废 → 红)
ANCHORS = {
    "drop50_by_type": "    drop = _det_nonfactor(conn, dets)          # #50: 表头/注释行 (判据见该函数注释)",
    "drop50_limit": "        _drop50 = _det_nonfactor(conn, dets) if dets else {}",
    "resolve_body": '    oel, hfcas, _univ = _name_index(conn)\n    n = nospace(fac)',
    "note_honest": '                _note = "未在标准库登记，须人工核对"',
    "note_false": '                _note = "未制定职业接触限值"',
    "unplaced_types": "            for _t in oels:\n                if _t not in set(_place.values()):\n                    _note_val(_t)",
}
# 被静态尺子盯住的判定函数 (新增的那几条, 不是整个文件)
DECISION_FUNCS = ("_resolve_oel_name", "_resolve_oel_sub", "_oel_rows_for", "_det_anchored",
                  "_det_payload", "_det_nonfactor", "_name_index", "_letters_only")

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


def anchor(key):
    """锚点必须**唯一命中**: 打补丁式测试最容易死在"锚点早被改了, 变异静默不生效"上"""
    seg = ANCHORS[key]
    got = SF_SRC.count(seg)
    check(f"锚点 {key} 在当前源码里命中 1 次 (否则变异是空转)", got, 1)
    return seg if got == 1 else None


def _variant(tag, subs, td):
    """把 web/section_filler.py 按 subs 打补丁后当独立模块加载 (真实文件与真实库都不动)"""
    src = SF_SRC
    for old, new in subs:
        if src.count(old) != 1:
            print(f"  ✗ 变异 {tag} 的锚点没命中: {old[:50]!r} (命中 {src.count(old)} 次)")
            return None
        src = src.replace(old, new)
    p = Path(td) / f"sf_{tag}.py"
    p.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"sf_{tag}", str(p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def before_variant(td):
    """"改前"= 当前源码 − (#50 过滤 + #51 解析 + #41 措辞 + 未落列类型进备注)"""
    a = [anchor(k) for k in ("drop50_by_type", "drop50_limit", "resolve_body",
                             "note_honest", "unplaced_types")]
    if not all(a):
        return None
    return _variant("before", [(a[0], "    drop = {}"),
                               (a[1], "        _drop50 = {}"),
                               (a[2], '    return None, None, "no_anchor"\n' + a[2]),
                               (ANCHORS["note_honest"], ANCHORS["note_false"]),
                               (a[4], "            for _t in []:\n                pass")], td)


def tables_for(mod, conn, dets, sec="5", extra=None):
    pd_ = {"detections": dets, "hazard_grid": [], "materials": [], "equipment": [],
           "products": [], "buildings": [], "staffing": [], "shifts": [], "ppe": [],
           "equipment_detail": [], "health_check": []}
    pd_.update(extra or {})
    assess = {"_project_data": pd_, "hazards": [], "judgements": []}
    return mod.fill_section(conn, sec, assess)


def rows_of(tables, pred):
    return [r for t in tables if pred(t["name"]) for r in t["rows"]]


def lim_row(tables, name):
    for t in tables:
        if t["name"] == "接触限值表":
            for r in t["rows"]:
                if str(r[0]) == name:
                    return r
    return None


def db_stat():
    st = os.stat(DB_PATH)
    return (st.st_size, st.st_mtime_ns)


# ── 静态尺子 (白名单修法 = 本仓固定禁止) ───────────────────────────────────────
def emitted_strings(src: str) -> list[str]:
    """模块里所有**参与判定/会被印出去**的字符串常量 (docstring 不算 —— 那是给人读的取证记录)"""
    tree = ast.parse(src)
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                doc_ids.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids]


def whitelist_smells(src: str, funcs=DECISION_FUNCS) -> list[str]:
    """判定函数里两种"白名单形状": ① 拿字面量与因素名比较; ② 装了汉字词的字符串集合字面量"""
    smells = []
    tree = ast.parse(src)

    def is_cjk(t):
        return any('\u4e00' <= ch <= '\u9fff' for ch in t)

    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in funcs):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Compare):
                consts = [x for x in [sub.left, *sub.comparators]
                          if isinstance(x, ast.Constant) and isinstance(x.value, str)]
                facs = [x for x in [sub.left, *sub.comparators]
                        if isinstance(x, ast.Name) and x.id == "fac"]
                if consts and facs:
                    smells.append(f"{node.name}: 字面量与因素名比较 → {ast.unparse(sub)}")
            if isinstance(sub, (ast.List, ast.Set, ast.Tuple)):
                strs = [e.value for e in sub.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if len([s for s in strs if is_cjk(s)]) >= 2:
                    smells.append(f"{node.name}: 汉字串集合字面量 (物质名白名单形状) → {strs}")
    return smells


def main() -> int:
    stat0 = db_stat()
    with tempfile.TemporaryDirectory() as td:
        data_dir = build_temp_tree(td)
        os.environ["OHS_DATA_DIR"] = str(data_dir)
        import knowledge.paths as paths
        importlib.reload(paths)
        from knowledge.oel import connect_ro
        c = connect_ro()
        got = Path(c.execute("PRAGMA database_list").fetchone()[2]).resolve()
        check("前提对表: 连接指向临时副本 (不是真实库 data/ohs.db)",
              got == Path(data_dir / "ohs.db").resolve(), True)

        import web.section_filler as sf

        print("\n=== 1. #51 正证 A: 官方层里带空格的名, 去掉空格后必须解析回原名并印出限值 ===")
        spaced = [r[0] for r in c.execute(
            "SELECT DISTINCT factor_name FROM oel_limit WHERE factor_name LIKE '% %' OR factor_name LIKE '%　%'")]
        variants = sorted({nospace(n) for n in spaced} - {n for n in spaced})
        variants = [v for v in variants if v not in spaced and
                    len(digit_bag(v)) >= 0 and sf._letters_only(v)]
        check(f"前提对表: 本地库里带空格的官方名 {len(spaced)} 个 → 得到空格变体 {len(variants)} 个 (≥20)",
              len(variants) >= 20, True)
        ok, bad = 0, []
        for v in variants:
            canon, mode, how = sf._resolve_oel_name(c, v)
            if canon and canon in spaced:
                ok += 1
            else:
                bad.append((v, canon, how))
        check(f"{len(variants)} 个空格变体全部唯一解析回**带空格的官方原写法** (多命中/落空都算坏)",
              (ok, bad[:3]), (len(variants), []))

        print("\n=== 2. #51 正证 B: §7.4 生产成品的三个原样, 限值列必须由 '—' 变成库里的数 ===")
        dets = [{"factor": k, "ctwa": "1.0"} for k in SPACE_VARIANTS]
        ts = tables_for(sf, c, dets)
        for name, (_col, want) in SPACE_VARIANTS.items():
            r = lim_row(ts, name)
            got = [str(x) for x in (r[1:5] if r else [])]
            check(f"{name!r} → 限值列印出库里那一档的数 (期望含 {want}), 备注不许是假陈述",
                  (want in got, r and "未制定职业接触限值" not in str(r[5])), (True, True))
        r = lim_row(ts, "石灰石粉尘（ 呼尘）")
        check("呼尘那一行不许印总尘的数 (8 是总尘 → 同名两档错位 = 新假陈述)",
              ("4" in [str(x) for x in r[1:5]], "8" not in [str(x) for x in r[1:3]]), (True, True))

        print("\n=== 3. #50: 三条表头/注释行不进成品, 且必须**点名** (不静默) ===")
        dets2 = [{"factor": h, "ctwa": "", "cstel": ""} for h in HEADER_ROWS] + \
                [{"factor": "苯乙烯", "ctwa": "5.0"}]
        for sec in ("4", "5"):        # 两章共用同一把尺子: 只在一章生效 = 复制粘贴回归
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ts2 = tables_for(sf, c, dets2, sec=sec)
            printed = [str(r[1]) for r in rows_of(ts2, lambda n: n.startswith("检测结果表"))]
            in_lim = [lim_row(ts2, h) for h in HEADER_ROWS]
            check(f"sec{sec}: 三条非因素行既不在检测结果表、也不在接触限值表",
                  ([h for h in HEADER_ROWS if h in printed], [x for x in in_lim if x]), ([], []))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sf._det_by_type(c, dets2)
        check("被排除的行必须在导出日志里点名 (静默删行 = 看不见)",
              all("det-nonfactor" in buf.getvalue() and h in buf.getvalue() for h in HEADER_ROWS), True)

        print("\n=== 4. 信息不丢失 (默认口径: 不静默删行) ===")
        # 真因素名 + 有测得值 + 标准库确实没有 → 行保留, 备注只说"未登记", 不说"标准没制定"
        dets3 = [{"factor": "新戊二醇", "ctwa": "0.5"}]
        ts3 = tables_for(sf, c, dets3)
        r3 = lim_row(ts3, "新戊二醇")
        check("未收录但有测得值的行**保留**, 备注=『未在标准库登记，须人工核对』",
              (r3 is not None, str(r3[5]) if r3 else None), (True, "未在标准库登记，须人工核对"))
        # 物理项豁免 (能力边界 3)
        check("物理/辅助项无值也不当非因素行 (本地实测 '照度' 有 1 行只有定性判定)",
              "照度" in sf._det_nonfactor(c, [{"factor": "照度", "ctwa": ""}]), False)
        # 同名异行有值 → 保行 (N3)
        check("同名有一行带测得值时, 无值那行也不被删 (N3 反向保护)",
              sf._det_nonfactor(c, [{"factor": "Xy不存在的因素名", "ctwa": "1"},
                                    {"factor": "Xy不存在的因素名", "ctwa": ""}]), {})

        print("\n=== 5. 变异: 摘掉过滤/解析, 判据必须能红 ===")
        before = before_variant(td)
        check("变异前提: 『改前』变体加载成功", before is not None, True)
        if before:
            mdets = dets2 + [{"factor": k, "ctwa": "1.0"} for k in SPACE_VARIANTS] + \
                    [{"factor": "丙酮氰醇（按CN计）", "ctwa": "2.0"}]
            tsb = tables_for(before, c, mdets)
            printed_b = [str(r[1]) for r in rows_of(tsb, lambda n: n.startswith("检测结果表"))]
            check("变异 1 (摘掉 #50 过滤) → 表头串**确实回到成品** (证明第 3 节不是空转)",
                  [h for h in HEADER_ROWS if h in printed_b], list(HEADER_ROWS))
            rb = [lim_row(tsb, k) for k in SPACE_VARIANTS]
            check("变异 2 (摘掉 #51 解析) → 空格变体回 '—' 且备注回假陈述 (证明第 2 节有牙齿)",
                  [[str(x) for x in r[1:5]].count("—") == 4 and "未制定职业接触限值" in str(r[5]) for r in rb],
                  [True, True, True])
            bb = lim_row(tsb, "丙酮氰醇（按CN计）")
            ba = lim_row(tables_for(sf, c, [{"factor": "丙酮氰醇（按CN计）", "ctwa": "2.0"}]),
                         "丙酮氰醇（按CN计）")
            check("变异 2 续: 没落进四列的限值类型 (库里 56 行用的是 'MAC') 改前**静默丢数**",
                  ("MAC" in str(bb[5]), "MAC 3 mg/m3" in str(ba[5])), (False, True))
            check("变异 3: 改前的检测表行数比改后多 (证明删行真的发生在过滤那一步)",
                  len(rows_of(tsb, lambda n: n.startswith("检测结果表"))) >
                  len(rows_of(ts2, lambda n: n.startswith("检测结果表"))), True)

        print("\n=== 6. 变异 (库级): 造出两个同形规范名 → 必须两边都不印, 不许猜 ===")
        with tempfile.TemporaryDirectory() as td2:
            dd2 = build_temp_tree(td2)
            w = sqlite3.connect(dd2 / "ohs.db")
            base = w.execute("SELECT factor_name, value FROM oel_limit "
                             "WHERE factor_name LIKE '稀土粉尘%'").fetchall()
            src_row = w.execute("SELECT factor_name, factor_type, cas, oel_type, value, unit, "
                                "conditions, note, source_standard, verified FROM oel_limit "
                                "WHERE factor_name LIKE '稀土粉尘%'").fetchone()
            twin = list(src_row)
            twin[0] = "稀土粉尘（游离SiO2  含量<10 %）"      # 多一个空格 = 另一个规范名, 同形
            twin[4] = "99"                                    # 限值故意不同 → 猜哪边都是错
            w.execute("INSERT INTO oel_limit (factor_name, factor_type, cas, oel_type, value, unit,"
                      " conditions, note, source_standard, verified) VALUES (?,?,?,?,?,?,?,?,?,?)", twin)
            w.commit()
            w.close()
            os.environ["OHS_DATA_DIR"] = str(dd2)
            importlib.reload(paths)
            c2 = connect_ro()
            import web.section_filler as sf2          # 同进程复用 (函数按 conn 取数, 无需重载模块)
            canon, mode, how = sf2._resolve_oel_name(c2, "稀土粉尘（游离SiO2含量<10%）")
            check("歧义库: 解析必须**拒绝**并说明原因 (canon=None, reason=ambiguous)",
                  (canon, how), (None, "ambiguous(2)"))
            tsx = tables_for(sf2, c2, [{"factor": "稀土粉尘（游离SiO2含量<10%）", "ctwa": "1.0"}])
            rx = lim_row(tsx, "稀土粉尘（游离SiO2含量<10%）")
            check("歧义行: 限值列全 '—' 且备注=多命中交人工 (2.5 与 99 都不许上纸)",
                  ([str(x) for x in rx[1:5]], "须人工确认" in str(rx[5]),
                   "2.5" in str(rx) or "99" in str(rx)),
                  (["—", "—", "—", "—"], True, False))
            c2.close()
            check("歧义库取证: 临时副本里确实存在 2 个同形规范名 (基准行 1 条 + 造出的孪生行)",
                  len(base), 1)

        print("\n=== 6.9 #37 子条目档: 名字是 conditions 里的子条目时, 必须取到**自己那一行**的数 ===")
        # 官方层把组名的子条目存在 conditions 列 (钒/钼/铅/铜/铝尘/麻尘/石棉/镍)。修完名字形状之后,
        # 读路径还得"从 conditions 那一侧也能取数", 否则 '金属镍与难溶性镍化合物' 照样查不到,
        # 成品只会把假陈述换成另一句假陈述 (#51 只解了空格族, 没解主子名分列)。
        oel_idx = sf._name_index(c)[0]
        subs = sorted(k for k, own in oel_idx["sub_owner"].items()
                      if oel_idx["sub_ok"].get(k) and len(own) == 1)
        check(f"前提对表: 库里的子条目名 (单主名 + 非片段) ≥ 15 个 (实得 {len(subs)})",
              len(subs) >= 15, True)
        wrong, n_resolved = [], 0
        cond_rows = [tuple(x) for x in c.execute(
            "SELECT oel_type, value, unit, conditions FROM oel_limit "
            "WHERE conditions IS NOT NULL").fetchall()]
        for k in subs:
            r4, _canon, _mode, status = sf._oel_rows_for(c, k)
            got = {(str(r[1]), str(r[2]), str(r[3])) for r in r4}
            # 期望集合直接从同一张表按**同一口径**算 (去空白后逐字相等的 conditions 行)
            want = {(str(o), str(v), str(u)) for o, v, u, cc in cond_rows if nospace(cc) == k}
            if status == "resolved":
                n_resolved += 1
                if got != want:
                    wrong.append((k, status, sorted(got), sorted(want)))
            elif status != "exact":       # exact = 这个名字本身就是 factor_name, 由第 1/2 节管
                wrong.append((k, status, sorted(got), sorted(want)))
        check(f"{len(subs)} 个子条目名: resolved 的取值必须逐字等于库里 conditions 那一行 "
              f"(走子条目档解析 {n_resolved} 个, 错配 {len(wrong)} 个)", wrong, [])
        check(f"新档真的在挡事: 靠子条目档解析的名字 ≥ 1 个 (实得 {n_resolved})",
              n_resolved >= 1, True)
        k0 = max(subs, key=lambda x: (len(sf._letters_only(x)), len(x)))
        check(f"取证样本 (最长的那个候选, 只是**打印**不作判据 —— 它落在 '干球温度' 这类"
              f"不进四列的档上): {k0!r}", k0 in subs, True)
        # #37 的原样回归 (成品取证: data/report_c8c7ff0a4d.docx 表32 行26 曾是 '—'×4 + 假陈述):
        #   同一主名下两档子条目各有一个数, 必须**各取各的**, 后写的不许盖掉前写的
        kA = "金属镍与难溶性镍化合物"
        for sub_name, want_v in ((kA, "1"), ("可溶性镍化合物", "0.5")):
            r_s = lim_row(tables_for(sf, c, [{"factor": sub_name, "ctwa": "1.0"}]), sub_name)
            check(f"#37 回归: {sub_name!r} 的 PC-TWA 列 = {want_v!r}、四列不全 '—'、"
                  f"备注不再说「未登记」 (整组两档不许互相覆盖)",
                  (str(r_s[2]), any(str(x) != "—" for x in r_s[1:5]),
                   any(w in str(r_s[5]) for w in ("未制定", "未在标准库登记"))),
                  (want_v, True, False))
        mA_ANCHOR = ('    srows, smain, sstat = _resolve_oel_sub(conn, fac)\n'
                     '    if sstat == "sub_resolved":\n'
                     '        return srows, smain, None, "resolved"')
        check("变异 A 的锚点在当前源码里命中 1 次 (否则变异是空转)", SF_SRC.count(mA_ANCHOR), 1)
        mA = _variant("mA", [(mA_ANCHOR, '    srows, smain, sstat = [], None, "no_sub"')], td)
        if mA:
            tsA = tables_for(mA, c, [{"factor": kA, "ctwa": "1.0"}])
            rA = lim_row(tsA, kA)
            check("变异 A (摘掉子条目档) → 该名字回 '—' 且备注=未在标准库登记 (证明这一档有牙齿)",
                  ([str(x) for x in rA[1:5]], "须人工核对" in str(rA[5])), (["—"] * 4, True))
        mB = _variant("mB", [('    sub_ok = {k: not any(k in other for other in _ns_names '
                              'if other != k) for k in sub_rows}',
                              '    sub_ok = {k: True for k in sub_rows}')], td)
        check("变异 B 前提: 现源码下 '粉尘' 不走子条目档 (它是片段, 不是专名)",
              sf._resolve_oel_sub(c, "粉尘")[2], "sub_generic")
        if mB:
            check("变异 B (关掉专名筛) → '粉尘' 被当成名用 → 证明 ③ 专名筛真在挡误绑",
                  mB._resolve_oel_sub(c, "粉尘")[2] != "sub_generic", True)
        real = sf._name_index(c)
        oel_f = dict(real[0])
        own_f = dict(oel_f["sub_owner"])
        own_f[kA] = {next(iter(own_f[kA])), "孪生主名（合成）"}
        oel_f["sub_owner"] = own_f
        _orig_index = sf._name_index                  # 存原函数, 撤补丁时**还原**而不是 del
        sf._name_index = lambda conn, _f=(oel_f, real[1], real[2]): _f
        try:
            st_amb = sf._resolve_oel_sub(c, kA)[2]
        finally:
            sf._name_index = _orig_index
        check("变异 C (给同一子名造第二个主名) → 必须判歧义, 不猜哪一边", st_amb, "sub_ambiguous")
        check("变异 C 前提对表: 撤掉补丁后同一调用回到 sub_resolved (绿的来源是真索引)",
              sf._resolve_oel_sub(c, kA)[2], "sub_resolved")

        print("\n=== 7. 静态: 新增判据不许是物质名/字符串白名单, 假陈述措辞不许有发射点 ===")
        smells = whitelist_smells(SF_SRC)
        check("判定函数里没有『字面量 vs 因素名』的比较、也没有汉字串集合字面量 (白名单形状)", smells, [])
        off = [s for s in emitted_strings(SF_SRC) if "未制定职业接触限值" in s]
        check("全模块**没有任何发射点**写『未制定职业接触限值』(只许留在注释/docstring 里当取证记录)",
              off, [])
        fake_src = ('def _det_nonfactor(conn, dets):\n'
                    '    bad = ("采样日期", "接触的职业病危害因素")\n'
                    '    out = {}\n'
                    "    for fac in [d['factor'] for d in dets]:\n"
                    '        if fac in bad or fac == "石灰石粉尘":\n'
                    '            out[fac] = "表头"\n'
                    '    return out\n')
        fake = whitelist_smells(fake_src)
        check("阳性对照: 把白名单式修法喂给同一把尺子 → 必须点名 (两种形状都抓到)",
              (len(fake) >= 2, any("集合字面量" in f for f in fake), any("比较" in f for f in fake)),
              (True, True, True))
        fake_off = [s for s in emitted_strings('X = "未制定职业接触限值"\n') if "未制定" in s]
        check("阳性对照: 发射点尺子喂一句假陈述 → 必须点名", bool(fake_off), True)

        c.close()

        print("\n=== 8. 取景汇报 (真实库只读; 是**数字**, 不做判据 —— 换环境会变, 别当结论) ===")
        rr = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        projs = rr.execute("SELECT id, data FROM project WHERE data LIKE '%\"detections\"%' "
                           "ORDER BY updated DESC, id").fetchall()
        rows_by_proj = [(pid, json.loads(data).get("detections") or []) for pid, data in projs]
        with_rows = [(p, dts) for p, dts in rows_by_proj if dts]
        n_rows = sum(len(dts) for _p, dts in rows_by_proj)
        names = {str(d.get("factor", "") or "") for _p, dts in rows_by_proj for d in dts}
        n_non = sum(len(sf._det_nonfactor(rr, dts)) for _p, dts in rows_by_proj)
        spaced = [r[0] for r in rr.execute(
            "SELECT DISTINCT factor_name FROM oel_limit WHERE factor_name LIKE '% %'")]
        print(f"  data 里有 detections 键的项目 {len(projs)} 个, 其中**真有检测行**的 {len(with_rows)} 个"
              f" · 检测行 {n_rows} 条 · 去重因素名 {len(names)} 个")
        print(f"  其中被 #50 判据认定的非因素行: {n_non} 条 (本地库 0 条 —— §7.4 那三条只在生产项目里, "
              f"所以 #50 半边是**注入**取证)")
        print(f"  官方层带空格的规范名 {len(spaced)} 个 → 它们的去空格变体 {len(variants)} 个, "
              f"唯一解析回原名 {ok} 个 (§7.4 的 '沉淀二氧 化硅（ 总尘）' 就落在这条链的 no_anchor 上 → 交人工)")
        rr.close()

    stat1 = db_stat()
    print("\n=== 9. 收尾: 真实库一个字节都没动 ===")
    check("真实库 data/ohs.db 的 (size, mtime_ns) 与开跑前一致 (只读取证)", stat1, stat0)
    print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 ===")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
